"""day_summary 生成与调度（§6.5）。

dirty 发现（discover，flush 组 day_summary stage 内运行——非 poll loop；
stage 是否执行由 watcher._flush_due 门控，其待办条件复用
has_actionable_work()，与 discover 同源判定）：
- day_scan_state 增量游标（每次只查 events.updated_at > last_scan_at；
  新时区首扫全历史；游标写扫描开始时刻，不漏并发）；
- 逐日核对（无 day_summary 行 OR events.updated_at > generated_at）；
- 失败重试（status='failed' AND fail_count < 3 AND 退避期满）；
- 单轮上限 day_summary_max_per_poll（day_force 已于 session_json_refactor 裁决 A 删除）。

生成（work loop 全局 stage）：
- 先取输入快照时刻 T0；输入 = 当日 events（ts_ms BETWEEN 日界 AND
  updated_at <= T0，排序 (ts_ms, EVENT_PRIORITY, event_id)）；**输入过滤
  （audio_analysis.md §10）：audio 事件且 payload.transcript 非空 → 跳过
  （文字已由 chatlog 铸造成的 chat/transcribe/translate 事件覆盖，避免重复喂
  同一段话）；transcript 空（value=high 的环境/事件声）→ 以其 summary 进入；
  过滤同样作用于 stats/event_count——audio 自身更新不再触发本日重生成；
  stats.audio_segments_total 记录当日 audio 事件真实总数（含被排除项）**；
- LLM 渲染（user 消息含 local_date/timezone）+ json_object → 校验必需键
  （summary/events/topics）→ upsert day_summary（generated_at = T0 快照语义）；
  summary 仅取 LLM 总述、events 独立落 doc（§14，以后单独消费）；stats 为本地
  计算口径，不由 LLM 产出；
- 失败：status='failed' + fail_count+1 + generated_at=尝试时刻（重试水位）。
- 0 事件不落行。
"""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import structlog

from ..config import LvdataMcpConfig
from ..consts import EVENT_PRIORITY
from ..language import output_language_rule
from ..providers import ApiError, LlmClient, parse_json
from ..timeutil import (LocalDateOutOfRange, day_bounds_ms, ms_to_local_hhmm,
                        now_iso, resolve_timezone, safe_local_date)
from .timezones import active_timezones

log = structlog.get_logger("lvdata_mcp.day_summary")

MAX_FAILURES = 3
BACKOFF_DELAYS = (60, 300, 900)  # 与 v1 BACKOFF_DELAYS 对齐（§5.4）

REQUIRED_KEYS = ("summary", "events", "topics")


def _is_duplicated_audio(etype: str, payload: str | None) -> bool:
    """audio 事件且 transcript 非空 → 文字已由 chatlog 事件覆盖（§10 输入去重）。

    该事件仍在 FTS/向量索引中（检索不受影响），仅不再进入 day_summary 输入。
    """
    if etype != "audio" or not payload:
        return False
    try:
        p = json.loads(payload)
    except (ValueError, TypeError):
        return False
    transcript = p.get("transcript") if isinstance(p, dict) else None
    return isinstance(transcript, str) and transcript.strip() != ""

# events.type → stats 键名映射（2026-08-26 修复：原实现直接 `type in counts`
# 键名不匹配导致 stats 类型计数恒 0）；transcribe/translate 计入 chat_messages
# （文本类，stats 结构不变，展示零改动——recording_data_process.md §2.5）
TYPE_MAP = {"chat": "chat_messages", "transcribe": "chat_messages",
            "translate": "chat_messages", "audio": "audio_segments",
            "video": "video_frames", "window": "video_windows",
            "agent_job": "agent_jobs"}

# LLM 输入行标签（§11）：chat 由 payload.role 渲染 chat(<role>)（缺 role →
# 裸 chat）；window/video 用展示名；未列出的类型原样透传（未来新增类型安全）
TYPE_LABEL = {"window": "video window", "video": "video frame"}


def _payload_role(payload: str | None) -> str:
    if not payload:
        return ""
    try:
        p = json.loads(payload)
    except (ValueError, TypeError):
        return ""
    role = p.get("role") if isinstance(p, dict) else None
    return role.strip() if isinstance(role, str) else ""


def _event_label(etype: str, payload: str | None) -> str:
    if etype == "chat":
        role = _payload_role(payload)
        return f"chat({role})" if role else "chat"
    return TYPE_LABEL.get(etype, etype)


def _render_blocks(rows: list[sqlite3.Row], tz: str,
                   max_chars: int) -> str:
    """事件块渲染（§12）：多设备按首现序分段（段头 `设备 <uuid8>：`，段间空行）；
    单设备无段头、无空行，与旧格式逐字一致。"""
    groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:          # rows 已按 (ts_ms, EVENT_PRIORITY, event_id) 排序
        groups.setdefault(r["device_uuid"], []).append(r)
    segs: list[str] = []
    for dev, grp in groups.items():      # dict 保序 = 首现序
        lines = []
        for r in grp:
            hhmm = ms_to_local_hhmm(r["ts_ms"], tz)
            text = (r["summary"] or "")[:max_chars]
            lines.append(
                f"[{hhmm}] {_event_label(r['type'], r['payload'])}: {text}")
        seg = "\n".join(lines)
        segs.append(f"设备 {dev[:8]}：\n{seg}" if len(groups) > 1 else seg)
    return "\n\n".join(segs)

DAY_SYSTEM_PROMPT = (
    "你是生活数据日总结引擎。你只输出 JSON，不输出任何其他文本或解释。\n"
    "用户会给出本地日期（local_date）、时区（timezone），以及该日的多模态事件"
    "摘要（单设备时按时间排序；多设备时按设备分段、段内按时间排序，段头为"
    "「设备 <id>：」）。严格按照以下 JSON Schema 输出：\n"
    "{\"summary\": \"这一天的整体总结（2-3 句）\", "
    "\"events\": [\"当天最重要的事件或时刻（每条一句，按时间先后"
    "排列，至少 1 条、最多 10 条）\"], "
    "\"topics\": [{\"topic\": \"主题名\", \"search_words\": \"搜索词，可以直接搜索到重点事件消息\"}]}\n"
    "输出示例：\n"
    "{\"summary\": \"这一天用户主要在排查应用启动慢的问题，下午完成了一次方案讨论。\", "
    "\"events\": [\"上午用户反馈启动耗时变长\", \"下午与同事讨论了性能优化方案\"], "
    "\"topics\": [{\"topic\": \"启动优化\", \"search_words\": \"启动耗时\"}]}\n"
    "要求：summary/events/topics 必须基于提供的事件与日期，不要编造；"
    "events 至少 1 条；条目不要写具体时间；"
    "topics 每项的 topic 是主题名（可概括），search_words 必须是从事件摘要里直接提取的"
    "原文连续片段（不做任何修改和拼接），保证用它搜索能直接命中该主题的重点事件；"
    "不要输出事件类型标签（如 chat、translate、agent_job、video frame），不要照抄整句；"
    "最多 5 个，可以没有；"
    "不同设备的事件来源不同，不要把不同设备的事件串联成同一段对话或同一叙事；"
    "数组字段必须存在；只输出 JSON，不要 Markdown 代码块。"
)


def _day_system_prompt(language: str) -> str:
    target = output_language_rule(language)
    return DAY_SYSTEM_PROMPT + (
        f"\n\n输出语言要求：summary、events、topics[].topic 使用 {target} 书写；"
        "topics[].search_words 必须保持事件摘要原文连续片段，不翻译；"
        "JSON 字段名与统计字段保持不变。"
    )


class DaySummary:
    def __init__(self, cfg: LvdataMcpConfig, client: LlmClient | None = None):
        self.cfg = cfg
        self.db_path = cfg.data.resolved_db_path
        self.client = client or LlmClient(
            cfg.providers.summary.resolved("summary"), cfg.watcher.llm_timeout_s)
        # discover（to_thread 线程）与 generate（事件循环）跨 loop 共享，
        # 用 threading.Lock 保护（临界区微秒级）
        self._lock = threading.Lock()
        self._pending: list[tuple[str, str]] = []  # [(tz, local_date)] 待生成
        # ok 日 + debounce 中（新事件已入库但静默窗口未过）的再发现载体是
        # day_deferred 表（§5.7b）——游标恒推进后这类日不再出现在候选事件里；
        # 落表而非内存：崩溃重启不丢（原则 8）。非 ok 行（占位/failed）由
        # day_summary 行状态兜底，不入此表。

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

    # ── dirty 发现（§6.5，poll loop 尾部）────────────────────────────────

    def _active_timezones(self, conn: sqlite3.Connection) -> list[str]:
        """活跃集 = 最近使用的 enabled 时区 + 配置默认时区（若 enabled），有序去重（§8.2）。

        至多 2 个：多时区场景 LLM 调用量有界。非活跃时区不被遍历——
        游标/占位行原样保留，再激活续跑（§8.3）。

        实现已提为共享 helper（`summary/timezones.py`）：day_stats stage 的目标时区
        必须与本判定一致（docs/bff_service_interfaces.md §7.2.3-1）。
        """
        return active_timezones(conn, self.cfg.watcher.timezone)

    def discover(self) -> bool:
        """发现 dirty day 并入队（内部暂存，上限 day_summary_max_per_poll）。"""
        limit = self.cfg.watcher.day_summary_max_per_poll
        with self._lock:
            self._pending = []
        queued: set[tuple[str, str]] = set()
        with self._conn() as conn:
            for tz in self._active_timezones(conn):
                cur = conn.execute(
                    "SELECT last_scan_at FROM day_scan_state WHERE timezone = ?",
                    (tz,)).fetchone()
                cur = (cur[0] if cur else "") or ""
                t_scan = now_iso()
                candidates = conn.execute(
                    "SELECT ts_ms, updated_at FROM events WHERE updated_at > ?",
                    (cur,)).fetchall()
                dirty: set[str] = set()
                if candidates:
                    dirty |= self._check_event_days(conn, tz, candidates)
                dirty |= self._check_pending_retry(conn, tz)  # 占位/failed 兜底
                # day_deferred 兜底（ok 行 + debounce 场景，§5.7b）：游标已
                # 推进，该日事件不再是候选；仍待生成（有更新）则保留，已生成
                # 且无更新则移除（表即持久化，崩溃重启不丢）
                for r in conn.execute(
                        "SELECT local_date FROM day_deferred "
                        "WHERE timezone = ?", (tz,)).fetchall():
                    d = r["local_date"]
                    row = conn.execute(
                        "SELECT status, generated_at FROM day_summary "
                        "WHERE timezone = ? AND local_date = ?", (tz, d)).fetchone()
                    if row is None or row["status"] != "ok":
                        # 非 ok 行由 _check_pending_retry/_check_event_days 兜底
                        conn.execute(
                            "DELETE FROM day_deferred "
                            "WHERE timezone = ? AND local_date = ?", (tz, d))
                        continue
                    if not self._day_up_to_date(conn, tz, d, row["generated_at"]):
                        dirty.add(d)
                    else:
                        conn.execute(
                            "DELETE FROM day_deferred "
                            "WHERE timezone = ? AND local_date = ?", (tz, d))
                # 无事件日不落行（§6.5）：事件被清理（如静音判定收紧删除）后，
                # pending/failed 残留每轮判 dirty → debounce 查无事件 → 落
                # day_deferred 永久忙循环（08-24 案例）。discover 层直接清理，
                # 与 _generate_one 的 no_events 分支同语义（更强：含 failed）。
                for d in list(dirty):
                    if self._day_has_events(conn, tz, d):
                        continue
                    conn.execute(
                        "DELETE FROM day_summary WHERE timezone = ? "
                        "AND local_date = ? AND status != 'ok'", (tz, d))
                    conn.execute(
                        "DELETE FROM day_deferred WHERE timezone = ? "
                        "AND local_date = ?", (tz, d))
                    dirty.discard(d)
                for d in sorted(dirty, reverse=True):  # 最近日优先（§8.2）
                    # dirty day 一律先落 pending 占位行：崩溃恢复 + 批限溢出
                    # （_check_pending_retry 下轮重查）
                    self._ensure_pending_row(conn, tz, d)
                    if (tz, d) in queued:
                        continue
                    if len(self._pending) >= limit:
                        continue  # 溢出：占位行已落，下轮重查，分批消化
                    if self._day_generatable(conn, tz, d):
                        with self._lock:
                            self._pending.append((tz, d))
                        queued.add((tz, d))
                        # 已入队：从 defer 表移除（生成失败走 failed 行兜底重试）
                        conn.execute(
                            "DELETE FROM day_deferred "
                            "WHERE timezone = ? AND local_date = ?", (tz, d))
                    else:
                        # debounce 未过/退避中：落 day_deferred（游标照常推进，
                        # 不再每轮全量重扫候选 + 逐事件 PK 查询；持久化载体，
                        # 崩溃重启后仍能再发现，§6.5/原则 8）
                        conn.execute(
                            "INSERT OR IGNORE INTO day_deferred "
                            "(timezone, local_date) VALUES (?, ?)", (tz, d))
                self._advance_scan(conn, tz, t_scan)  # 恒推进，不漏并发（§5.6）
        if self._pending:
            log.info("day dirty found", days=self._pending)
            return True
        return False

    def has_actionable_work(self) -> bool:
        """flush 门控用（§5.C）：active tz 内是否存在本轮 discover 会入队/清理的日。

        与 discover 同源判定（同 helpers）：非 ok 行退避已过且有事件（可入队）
        /无事件（待清理）；deferred 行有新事件且 debounce 已过（可入队）。
        discover 只在本 stage 内运行：门控恒关则 deferred/占位行永久 starvation
        （09-20 案例：事件停流后卡到新事件到达才生成）；活跃录制期间事件新鲜 →
        恒 False，节流不受影响；非活跃时区不参与（discover 也不遍历）。
        """
        with self._conn() as conn:
            for tz in self._active_timezones(conn):
                for d in self._check_pending_retry(conn, tz):
                    if not self._day_has_events(conn, tz, d):
                        return True           # 无事件残留：discover 直接清理
                    if self._day_generatable(conn, tz, d):
                        return True
                for r in conn.execute(
                        "SELECT local_date FROM day_deferred "
                        "WHERE timezone = ?", (tz,)).fetchall():
                    d = r["local_date"]
                    row = conn.execute(
                        "SELECT generated_at FROM day_summary "
                        "WHERE timezone = ? AND local_date = ?",
                        (tz, d)).fetchone()
                    if self._day_up_to_date(
                            conn, tz, d, row["generated_at"] if row else None):
                        continue
                    if self._day_generatable(conn, tz, d):
                        return True
        return False

    def _day_generatable(self, conn: sqlite3.Connection, tz: str, d: str) -> bool:
        """统一生成条件：failed 需退避期满；否则 debounce。"""
        if not self._debounce_ok(conn, tz, d):
            return False
        row = conn.execute(
            "SELECT fail_count, generated_at FROM day_summary "
            "WHERE timezone = ? AND local_date = ? AND status = 'failed'",
            (tz, d)).fetchone()
        if row is not None:
            delay = BACKOFF_DELAYS[min(row["fail_count"] - 1,
                                       len(BACKOFF_DELAYS) - 1)]
            if row["generated_at"] and row["generated_at"] > _shift_iso(
                    now_iso(), -delay):
                return False
        return True

    @staticmethod
    def _day_up_to_date(conn: sqlite3.Connection, tz: str, d: str,
                        generated_at: str | None) -> bool:
        """该日无 updated_at > generated_at 的事件（deferred 清理判定）。"""
        if not generated_at:
            return False
        start, end = day_bounds_ms(d, tz)
        n = conn.execute(
            "SELECT COUNT(*) FROM events WHERE ts_ms BETWEEN ? AND ? "
            "AND updated_at > ?", (start, end, generated_at)).fetchone()[0]
        return n == 0

    def _check_event_days(self, conn: sqlite3.Connection, tz: str,
                          candidates: list[sqlite3.Row]) -> set[str]:
        """候选事件 → 逐日核对：无行 or events.updated_at > generated_at（§6.5）。"""
        dirty: set[str] = set()
        for c in candidates:
            try:
                d = safe_local_date(c["ts_ms"], tz)
            except LocalDateOutOfRange:
                continue
            row = conn.execute(
                "SELECT generated_at FROM day_summary "
                "WHERE timezone = ? AND local_date = ?", (tz, d)).fetchone()
            if row is None or not row["generated_at"] or c["updated_at"] > row["generated_at"]:
                dirty.add(d)
        return dirty

    def _check_pending_retry(self, conn: sqlite3.Connection, tz: str) -> set[str]:
        """重试（§5.4/崩溃恢复）：failed 走退避；pending 占位行立即重试（达 3 暂停）。"""
        dirty: set[str] = set()
        for r in conn.execute(
                "SELECT local_date, status, fail_count, generated_at FROM day_summary "
                "WHERE timezone = ? AND status IN ('failed', 'pending')", (tz,)).fetchall():
            if r["fail_count"] >= MAX_FAILURES:
                continue
            if r["status"] == "failed":
                delay = BACKOFF_DELAYS[min(r["fail_count"] - 1, len(BACKOFF_DELAYS) - 1)]
                if r["generated_at"] and r["generated_at"] > _shift_iso(now_iso(), -delay):
                    continue  # 退避窗口未过
            dirty.add(r["local_date"])
        return dirty

    @staticmethod
    def _day_has_events(conn: sqlite3.Connection, tz: str, d: str) -> bool:
        start, end = day_bounds_ms(d, tz)
        return conn.execute(
            "SELECT 1 FROM events WHERE ts_ms BETWEEN ? AND ? LIMIT 1",
            (start, end)).fetchone() is not None

    def _ensure_pending_row(self, conn: sqlite3.Connection, tz: str, d: str) -> None:
        """pending 占位行（崩溃恢复载体）：不覆盖已有行。"""
        conn.execute(
            """INSERT OR IGNORE INTO day_summary (timezone, local_date,
               utc_offset_min, day_start_ms, day_end_ms, day_summary, status,
               event_count, fail_count, generated_at)
               VALUES (?, ?, 0, 0, 0, '{}', 'pending', 0, 0, NULL)""",
            (tz, d))

    def _advance_scan(self, conn: sqlite3.Connection, tz: str, t_scan: str) -> None:
        """游标写扫描开始时刻（§5.6：扫描期间并发落入的事件下轮必见）。"""
        conn.execute(
            "INSERT INTO day_scan_state (timezone, last_scan_at) VALUES (?, ?) "
            "ON CONFLICT(timezone) DO UPDATE SET last_scan_at = excluded.last_scan_at",
            (tz, t_scan))

    def _debounce_ok(self, conn: sqlite3.Connection, tz: str, d: str) -> bool:
        """§6.5 debounce：当天最新事件年龄 < summarize_quiet_s 则跳过。"""
        start, end = day_bounds_ms(d, tz)
        row = conn.execute(
            "SELECT MAX(ts_ms) AS latest FROM events WHERE ts_ms BETWEEN ? AND ?",
            (start, end)).fetchone()
        if row is None or row["latest"] is None:
            return False
        age_s = (datetime.now(timezone.utc).timestamp() * 1000 - row["latest"]) / 1000
        return age_s >= self.cfg.watcher.summarize_quiet_s

    # ── 生成（§6.5，work loop 全局 stage）────────────────────────────────

    async def generate(self, target: str | None = None,
                       force: bool = False) -> dict:
        if force:
            # 重置失败/占位行；force 同时覆盖 ok 行（语言迁移需要）
            with self._conn() as conn:
                conn.execute(
                    "UPDATE day_summary SET fail_count = 0, status = 'pending', "
                    "generated_at = NULL WHERE status != 'ok'")
                conn.commit()
            days = self._all_rows()
        else:
            with self._lock:
                days = self._pending
                self._pending = []
        days = list(dict.fromkeys(days))
        # 有界并发（§3.2.5：外部调用即有界并发；与 audio/vision 同口径）。
        # 单日串行最坏会独占 work loop 数十小时（10 天 × 3 次重试 × 300s）。
        sem = asyncio.Semaphore(self.cfg.watcher.concurrency)

        async def one(item: tuple[str, str]) -> dict:
            async with sem:
                return await self._generate_one(item[0], item[1], force=force)

        results = await asyncio.gather(*(one(d) for d in days))
        return {"days": len(results),
                "ok": sum(1 for r in results if r["status"] == "ok"),
                "failed": sum(1 for r in results if r["status"] == "failed"),
                "skipped": sum(1 for r in results if r["status"] == "skipped")}

    def _all_dirty(self) -> list[tuple[str, str]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT timezone, local_date FROM day_summary "
                "WHERE status != 'ok'").fetchall()
            return [(r["timezone"], r["local_date"]) for r in rows]

    def _all_rows(self) -> list[tuple[str, str]]:
        """force 用：所有 day_summary 行（含 ok），用于语言迁移重生成。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT timezone, local_date FROM day_summary").fetchall()
            return [(r["timezone"], r["local_date"]) for r in rows]

    async def _generate_one(self, tz: str, d: str, *,
                            force: bool = False) -> dict:
        t0 = now_iso()
        start, end = day_bounds_ms(d, tz)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT type, ts_ms, summary, event_id, updated_at, payload, "
                "device_uuid "
                "FROM events WHERE ts_ms BETWEEN ? AND ? AND updated_at <= ?",
                (start, end, t0)).fetchall()
            prev = conn.execute(
                "SELECT status, generated_at FROM day_summary "
                "WHERE timezone = ? AND local_date = ?", (tz, d)).fetchone()
        # 输入去重（§10）：audio 且 transcript 非空 → 排除（文字已在 chatlog
        # 事件中）；LLM 输入 / stats / event_count 统一基于过滤后集合，
        # up_to_date 护栏随之——audio 自身更新不再触发本日重生成。
        # audio_segments_total：当日 audio 事件真实总数（含被排除的带文字段），
        # 与 audio_segments（进入日总结的非语音段数）互为"事实/输入"两个口径。
        audio_segments_total = sum(1 for r in rows if r["type"] == "audio")
        rows = [r for r in rows
                if not _is_duplicated_audio(r["type"], r["payload"])]
        if not rows:
            # 0 事件：删除占位行（§5.4 不落行）
            with self._conn() as conn:
                conn.execute(
                    "DELETE FROM day_summary WHERE timezone = ? AND local_date = ? "
                    "AND status = 'pending'", (tz, d))
                conn.commit()
            return {"tz": tz, "date": d, "status": "skipped", "reason": "no_events"}
        if (not force and prev is not None and prev["status"] == "ok"
                and prev["generated_at"]
                and all((r["updated_at"] or "") <= prev["generated_at"]
                        for r in rows)):
            # 幂等护栏：无 updated_at > generated_at 的新事件 → 无需重生成。
            # 重复入队（discover 与 generate 竞态）不再浪费 LLM 调用
            return {"tz": tz, "date": d, "status": "skipped", "reason": "up_to_date"}
        # 排序约定（§5.1）：(ts_ms, EVENT_PRIORITY, event_id)
        rows.sort(key=lambda r: (r["ts_ms"], EVENT_PRIORITY.get(r["type"], 9),
                                 r["event_id"]))
        # 渲染 LLM 输入（§6.5 步骤 3 / §11 / §12：每事件 [HH:MM] label: summary，
        # 截断；多设备按先出现序分段，单设备不分段）
        counts = {"chat_messages": 0, "audio_segments": 0,
                  "video_frames": 0, "video_windows": 0, "agent_jobs": 0}
        for r in rows:
            key = TYPE_MAP.get(r["type"])
            if key is not None:
                counts[key] += 1
        block = _render_blocks(rows, tz,
                               self.cfg.watcher.max_chars_per_event)

        parsed, usage, error = await self._summarize(block, tz, d)
        if error or parsed is None:
            return self._finish_failed(tz, d, t0, error)
        # §14/§15：summary 仅取 LLM 总述（不拼接）；LLM 的 events（重要时刻）
        # 以 doc 字段 `moments` 落库（避开 stats.events 计数同名歧义），
        # 以后单独消费（当前无消费者）
        moments = parsed.get("events", [])[:10]
        if not moments:
            log.warning("day summary moments empty", tz=tz, date=d)
        doc = {
            "schema_version": 1,
            "timezone": tz,
            "local_date": d,
            "generated_at": t0,
            "model": self.client.model,
            "stats": {"events": len(rows), **counts,
                      "audio_segments_total": audio_segments_total},
            "summary": parsed.get("summary", ""),
            "moments": moments,
            "topics": parsed.get("topics", []),
            "usage": usage or {},
        }
        utc_offset_min = self._utc_offset_min(tz, d)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO day_summary (timezone, local_date, utc_offset_min,
                   day_start_ms, day_end_ms, day_summary, model, status,
                   event_count, fail_count, error, generated_at)
                   VALUES (?,?,?,?,?,?,?, 'ok', ?, 0, NULL, ?)
                   ON CONFLICT(timezone, local_date) DO UPDATE SET
                   utc_offset_min = excluded.utc_offset_min,
                   day_start_ms = excluded.day_start_ms,
                   day_end_ms = excluded.day_end_ms,
                   day_summary = excluded.day_summary,
                   model = excluded.model, status = 'ok',
                   event_count = excluded.event_count, fail_count = 0,
                   error = NULL, generated_at = excluded.generated_at""",
                (tz, d, utc_offset_min, start, end,
                 json.dumps(doc, ensure_ascii=False), self.client.model,
                 len(rows), t0))
            conn.execute("DELETE FROM day_deferred WHERE timezone = ? AND local_date = ?",
                         (tz, d))
            conn.commit()
        log.info("day summary ok", tz=tz, date=d, events=len(rows))
        return {"tz": tz, "date": d, "status": "ok", "event_count": len(rows)}

    async def _summarize(self, block: str, tz: str,
                         d: str) -> tuple[dict | None, dict | None, str | None]:
        user_content = [
            {"type": "text",
             "text": (f"local_date: {d}\ntimezone: {tz}\n"
                      "以下是该本地日期的事件摘要，"
                      "请按系统提示的 JSON Schema 生成日总结：\n\n"
                      f"{block}")},
        ]
        error: str | None = None
        usage: dict | None = None
        for attempt in range(self.client.max_attempts):
            try:
                # 关闭思考（同 audio.py 先例）：qwen3.8 思考默认 xhigh，大 prompt 下思考
                # 吃满输出预算 → JSON 被截断（2026-09-21 实测 09-18/09-20 响应未闭合）；
                # 关闭后输出完整且快约 10×（~40s → ~3s/次）
                content, usage = await self.client.chat(
                    _day_system_prompt(self.cfg.language), user_content,
                    enable_thinking=False)
                parsed = parse_json(content)
                missing = [k for k in REQUIRED_KEYS if k not in parsed]
                if missing:
                    if attempt < self.client.max_attempts - 1:
                        continue
                    return self._normalize(parsed), usage, f"schema missing: {missing}"
                return self._normalize(parsed), usage, None
            except ApiError as e:
                return None, usage, f"api: {e}"
            except json.JSONDecodeError as e:
                if attempt >= self.client.max_attempts - 1:
                    return None, usage, f"{type(e).__name__}: {e}"
                # 模型输出坏 JSON：重试（瞬态错误重试归属 LlmClient.chat）
                await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
        return None, usage, error

    @staticmethod
    def _normalize(parsed: dict) -> dict:
        if not isinstance(parsed.get("summary"), str):
            parsed["summary"] = ""
        events = parsed.get("events")
        parsed["events"] = ([e.strip() for e in events
                             if isinstance(e, str) and e.strip()]
                            if isinstance(events, list) else [])
        # topics 归一化为对象数组 [{topic, search_words}]（docs/android_timeline_topics.md §10）：
        # 展示名与搜索词解耦；str（legacy/模型未给 search_words）→ {topic: s, search_words: None}；
        # 空/畸形条目丢弃。存量行的字符串形态由 bff 读取侧兼容（topic_items）。
        topics = parsed.get("topics")
        items: list[dict] = []
        for t in topics if isinstance(topics, list) else []:
            if isinstance(t, str) and t.strip():
                items.append({"topic": t.strip(), "search_words": None})
            elif isinstance(t, dict) and str(t.get("topic") or "").strip():
                sw = t.get("search_words")
                items.append({
                    "topic": str(t["topic"]).strip(),
                    "search_words": sw.strip() if isinstance(sw, str) and sw.strip() else None,
                })
        parsed["topics"] = items
        return parsed

    def _finish_failed(self, tz: str, d: str, t0: str, error: str | None) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT fail_count FROM day_summary WHERE timezone = ? AND local_date = ?",
                (tz, d)).fetchone()
            fail_count = (row["fail_count"] if row else 0) + 1
            conn.execute(
                """INSERT INTO day_summary (timezone, local_date, utc_offset_min,
                   day_start_ms, day_end_ms, day_summary, status, event_count,
                   fail_count, error, generated_at)
                   VALUES (?,?,0,0,0,'{}','failed',0,?,?,?)
                   ON CONFLICT(timezone, local_date) DO UPDATE SET
                   status = 'failed', fail_count = excluded.fail_count,
                   error = excluded.error, generated_at = excluded.generated_at""",
                (tz, d, fail_count, (error or "")[:500], t0))
            conn.commit()
        log.warning("day summary failed", tz=tz, date=d, error=error)
        return {"tz": tz, "date": d, "status": "failed", "error": error}

    @staticmethod
    def _utc_offset_min(tz: str, d: str) -> int:
        """该 local_date 日首的 UTC 偏移（分钟；DST 按日期正确）。

        用 `resolve_timezone` 而非 `ZoneInfo`：timezones 表规范形含固定偏移
        （"+08:00"），`ZoneInfo("+08:00")` 抛 ZoneInfoNotFoundError → 生成阶段
        整轮失败（2026-09-25 复核修复，同 summary/day_stats.py）。
        """
        naive = datetime.strptime(d, "%Y-%m-%d")
        return int(naive.replace(tzinfo=resolve_timezone(tz)).utcoffset().total_seconds() / 60)


def _shift_iso(iso: str, delta_s: float) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (dt + timedelta(seconds=delta_s)).astimezone(timezone.utc) \
        .isoformat(timespec="milliseconds").replace("+00:00", "Z")
