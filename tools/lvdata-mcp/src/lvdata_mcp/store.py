"""DataStore — 只读访问 SQLite（设计文档 §6.1/§9.1）。

所有 MCP tools 只经 DataStore 读数据；写入只发生在 watcher/analysis 进程内
模块（无 subprocess，v2）。连接按调用打开只读连接（SQLite 打开成本 ~µs，
天然线程安全）。
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import VERSION
from .config import LvdataMcpConfig
from . import index as index_mod
from .consts import EVENT_PRIORITY, EVENT_TYPES, PRIORITY_FALLBACK, PRIORITY_SQL
from .timeutil import (date_window_ms, day_bounds_ms, ms_to_local,
                       ms_to_local_date, ms_to_local_or_none, normalize_date,
                       resolve_timezone, try_resolve_timezone)


_ENERGY_KEYS = ("energy", "energy_v", "frame_ms", "db_min", "db_max")


def _timeline_page_sql(where_sql: str, windowed: bool) -> str:
    """`get_timeline` 的翻页 SQL（**带日期窗时强制 `idx_events_ts`**，见 docs/get_timeline_optimize.md §8）。

    原因：`ORDER BY` 第二键是 `PRIORITY_SQL`（CASE 表达式），任何索引都无法覆盖完整排序键——
    planner 默认选 `idx_events_type` 后会做整段 `TEMP B-TREE FOR ORDER BY`（`LIMIT` 失效）。
    带窗时强制 ts 索引，排序只剩"同 ms 小段"（`TEMP B-TREE FOR LAST 2 TERMS`），`LIMIT` 可提前停。
    实测（200k 行）：首页/游标页 89 ms → 0.06/0.07 ms。

    无窗**不加**提示：类型占比低时（如 `agent_job` 10/200k）类型索引更优（同文档 §2.3）。

    ⚠️ 硬提示依赖 `idx_events_ts` 存在（`ddl.py` 建表保证）；索引被删会直接报错（fail loud，非静默退化）。
    🔁 BFF 侧同款查询在 `services/bff/common/store.py`（`_page_sql`）——**改动须双副本同步**。
    """
    hint = "INDEXED BY idx_events_ts " if windowed else ""
    return (f"SELECT * FROM events {hint}WHERE {where_sql} "
            f"ORDER BY ts_ms, {PRIORITY_SQL}, event_id LIMIT ?")


def strip_energy(d: dict) -> dict:
    """剔除能量包络字段（docs/android_timeline_audio_gallery.md §10 E3）。

    能量是给 App 画包络的（BFF 侧保留）；MCP 面向 LLM Agent，2.5 KB/段的 base64
    只会污染上下文（一页最多 1000 条）。**当前唯一出口是 `get_event` 的 `detail`**
    ——列表/邻域（`get_timeline`/`get_recent_events`/`search`/`semantic_search` 的
    `context_events`）自 §9/§12 起一律轻量投影、不含 payload，能量自然不会出现。
    将来若新增透出 payload 的出口，必须在此登记并调用本函数。
    """
    for k in _ENERGY_KEYS:
        d.pop(k, None)
    return d


class StoreError(Exception):
    """带结构化错误码的查询错误，tools 层据此返回错误对象。"""

    def __init__(self, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


class DataStore:
    def __init__(self, settings: LvdataMcpConfig):
        self.settings = settings
        self.root = Path(settings.data.root_dir)
        self.db_path = Path(settings.data.resolved_db_path)

    # ── 基础 ──────────────────────────────────────────────────────────────

    def db_ok(self) -> bool:
        return self.db_path.is_file()

    def connect(self, readonly: bool = True) -> sqlite3.Connection:
        if not self.db_ok():
            raise StoreError(
                "database_not_initialized",
                f"Database not initialized: {self.db_path}",
                hint="Run lvdata_inventory.py or wait for the watcher to create tables",
            )
        uri = f"file:{self.db_path}?mode=ro" if readonly else str(self.db_path)
        conn = sqlite3.connect(uri, uri=readonly, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _safe_join(self, *parts: str) -> Path:
        """路径拼接统一入口：必须落在 root_dir 内（防 ../ 穿越）。"""
        base = self.root.resolve()
        p = base.joinpath(*parts)
        if os.path.commonpath([str(base), str(p)]) != str(base):
            raise StoreError("invalid_path",
                             f"Path escapes data root: {'/'.join(parts)}")
        return p

    def session_derived_dir(self, device_uuid: str, session_id: str) -> Path:
        return self._safe_join("derived", device_uuid, session_id)

    # ── session ───────────────────────────────────────────────────────────

    def _session_row(self, conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM recording_sessions WHERE rsession_id = ?", (session_id,)
        ).fetchone()

    def session_exists(self, session_id: str) -> bool:
        with self.connect() as conn:
            return self._session_row(conn, session_id) is not None

    def effective_tz(self, timezone: str | None) -> tuple[str, str]:
        """请求时区（**无效/为空 → 服务配置**，§8.7）→ (渲染用串, 输出用真实标识)。

        输出标识取解析后的规范形式（IANA 名 / `UTC` / `UTC+08:00`）；规范形式不能回喂
        `resolve_timezone`（固定偏移串会解析失败再落 UTC），故渲染始终用原始生效串。
        """
        tz = timezone if (timezone and try_resolve_timezone(timezone)) \
            else self.settings.watcher.timezone
        return tz, str(resolve_timezone(tz))

    # ── timeline（§12：直查 events 表，无产物文件）──────────────────────

    def get_timeline(
        self,
        types: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        timezone: str | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict:
        """日期窗时间线，按日分组输出（§9：日升序、日内升序，轻量投影）。

        start_date/end_date 为 `YYYY-MM-DD` **含端点**（起始日 00:00:00 ~ 结束日
        23:59:59.999，本地日界）；缺省 = 不限。游标为 [ts_ms, type, event_id] 三元组
        keyset（event_id 全序兜底）——分页可能把同一天切成两页。total = 窗口内事件
        总数（不随翻页变化）；无事件的日不出现。
        """
        tz, tz_out = self.effective_tz(timezone)
        try:
            start_ms, end_ms = date_window_ms(start_date, end_date, tz)
        except ValueError as e:
            raise StoreError("invalid_param", str(e))
        if types is not None and not types:
            # 显式空数组 ≠ "不传"（后者 = 全部类型）：静默当全类型会让 agent 的过滤失效（I-7）
            raise StoreError("invalid_param",
                             "`types` must not be empty; omit it to query all types")
        type_set = set(types) if types else set(EVENT_TYPES)
        placeholders = ",".join("?" * len(type_set))
        date_from = normalize_date(start_date) if start_date else None   # I-6：回显归一
        date_to = normalize_date(end_date) if end_date else None
        where = [f"type IN ({placeholders})"]
        params: list[Any] = list(type_set)
        if start_ms is not None:
            where.append("ts_ms >= ?"); params.append(start_ms)
        if end_ms is not None:
            where.append("ts_ms <= ?"); params.append(end_ms)
        page_where, page_params = list(where), list(params)
        if cursor:
            cts, ctype, ceid = self._parse_cursor(cursor)
            cprio = EVENT_PRIORITY.get(ctype, PRIORITY_FALLBACK)
            page_where.append(
                f"(ts_ms > ? OR (ts_ms = ? AND {PRIORITY_SQL} > ?) "
                f"OR (ts_ms = ? AND {PRIORITY_SQL} = ? AND event_id > ?))")
            page_params += [cts, cts, cprio, cts, cprio, ceid]
        with self.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM events WHERE {' AND '.join(where)}",
                params).fetchone()[0]
            rows = conn.execute(
                _timeline_page_sql(" AND ".join(page_where),
                                   start_ms is not None or end_ms is not None),
                page_params + [limit + 1]).fetchall()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            payload = json.dumps([last["ts_ms"], last["type"], last["event_id"]])
            next_cursor = base64.urlsafe_b64encode(payload.encode()).decode()
        # 升序取数 → dict 插入序即日升序；日内顺序天然升序（无需反转）
        by_day: dict[str, list[dict]] = {}
        for r in page:
            by_day.setdefault(ms_to_local_date(r["ts_ms"], tz), []).append(
                self._event_brief(r, tz))
        return {
            "timezone": tz_out,
            "date_from": date_from,
            "date_to": date_to,
            "total": total,
            "next_cursor": next_cursor,
            "days": [{"date": d, "events": evs} for d, evs in by_day.items()],
        }

    @staticmethod
    def _event_brief(r: sqlite3.Row, tz: str) -> dict:
        """轻量投影（§3.2/§9，get_recent_events 与 get_timeline 共用）。

        不含 payload；summary 截断 256 字；时间为本地可读格式（时区语义见响应
        顶层 timezone 字段；`ts_ms=0` 的无时间戳事件 → `None`，§11 D6）。详情由
        调用方按 event_ref 走 get_event。
        """
        return {
            "event_ref": r["event_ref"], "type": r["type"],
            "timestamp": ms_to_local_or_none(r["ts_ms"], tz), "source": r["source"],
            "summary": (r["summary"] or "")[:256],
        }

    @staticmethod
    def _parse_cursor(cursor: str) -> tuple[int, str, int]:
        """[ts_ms, type, event_id] 三元组游标。"""
        try:
            raw = json.loads(base64.urlsafe_b64decode(cursor).decode())
            if isinstance(raw, list) and len(raw) == 3:
                return int(raw[0]), str(raw[1]), int(raw[2])
        except Exception:
            pass
        raise StoreError("invalid_param", f"Invalid cursor: {cursor}")

    # ── event（§12：查 events 表）─────────────────────────────────────────

    def get_event(self, event_ref: str,
                  timezone: str | None = None) -> tuple[dict, tuple[bytes, str] | None]:
        """事件详情 + 可选关键帧字节（video 且帧文件存在）。

        图片有无与图片字节**同源**（同一路径解析、同一次 stat/read）——`related.
        image_available` 即 `img is not None`，两者不可能互相矛盾。定位只用
        `event_ref` 后缀（铸造时的 frame_id，`vision.py` 与 ref 同源写入）；不读
        payload.frame_id——2026-09-18 前的行该字段残留窗口内局部索引（"0"），
        据此定位会"JSON 报无图、却按 ref 找到了图"。
        """
        parts = event_ref.split(":")
        if len(parts) != 3 or not all(parts):
            raise StoreError(
                "invalid_param",
                f"event_ref must be in the form {{session_id}}:{{type}}:{{source_id}}: "
                f"{event_ref}")
        _, etype, source_id = parts
        tz, tz_out = self.effective_tz(timezone)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE event_ref = ?", (event_ref,)).fetchone()
        if row is None:
            raise StoreError(
                "not_found", f"Event not found: {event_ref}",
                hint="chat rows need chatlog processing; audio/video/agent_job need "
                     "analysis completion")
        try:
            detail = json.loads(row["payload"] or "{}")
            if not isinstance(detail, dict):
                detail = {}
        except ValueError:
            detail = {}
        detail = strip_energy(detail)      # §10 E3：Agent 出口剔能量（BFF 保留）
        img: tuple[bytes, str] | None = None
        related: dict[str, Any] = {}
        if etype == "video":
            related = {"window_id": detail.get("window_id")}
            try:
                path = self._video_image_path(row["device_uuid"], row["rsession_id"],
                                              source_id)
                if path.is_file():
                    img = (path.read_bytes(), "image/jpeg")
            except (StoreError, OSError):   # 路径越界 / stat 与 read 之间文件被删 → 视为无图
                img = None
            related["image_available"] = img is not None
        out = {
            "event_ref": event_ref,
            "type": etype,
            "timestamp": ms_to_local_or_none(row["ts_ms"], tz),
            "timezone": tz_out,
            "detail": detail,
            "related": related,
        }
        return out, img

    def _video_image_path(self, device_uuid: str, rsession_id: str,
                          frame_id: str) -> Path:
        return self._safe_join(device_uuid, rsession_id, "video", f"{frame_id}.jpg")

    # ── recent events（get_recent_events：近 N 个本地日清单）───────────────

    def get_recent_events(self, days: int = 1, timezone: str | None = None,
                          limit: int = 200) -> dict:
        """近 N 个本地日（含今天，days=1 即今天）的事件清单，按日组织（§3.2）。

        日倒序（最新在前）、日内时间正序（早的在先）；轻量投影：不含 payload、
        summary 截断 256 字——详情由调用方按 event_ref 走 get_event。时间只给本地
        可读格式（date_from/date_to/timestamp），不输出 epoch ms。无事件的日不出现
        在 days 里。timezone 无效/为空 → 服务配置（§8.7）；输出 timezone 为真实生效
        标识。total = 窗口内事件总数（走 idx_events_ts），truncated 提示清单被页大小
        截断（截掉的总是更早的事件）。
        """
        tz, tz_out = self.effective_tz(timezone)
        today = datetime.now(resolve_timezone(tz)).date()
        date_from = today - timedelta(days=days - 1)
        start_ms, _ = day_bounds_ms(date_from.isoformat(), tz)
        _, end_ms = day_bounds_ms(today.isoformat(), tz)
        with self.connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM events WHERE ts_ms BETWEEN ? AND ?",
                (start_ms, end_ms)).fetchone()[0]
            rows = conn.execute(
                "SELECT event_ref, type, ts_ms, source, summary FROM events "
                "WHERE ts_ms BETWEEN ? AND ? "
                "ORDER BY ts_ms DESC, event_id DESC LIMIT ?",
                (start_ms, end_ms, limit)).fetchall()
        # rows 降序 → dict 插入序即日倒序；日内降序追加后再 reverse 成正序
        by_day: dict[str, list[dict]] = {}
        for r in rows:
            by_day.setdefault(ms_to_local_date(r["ts_ms"], tz), []).append(
                self._event_brief(r, tz))
        return {
            "timezone": tz_out,
            "date_from": date_from.isoformat(),
            "date_to": today.isoformat(),
            "total": total,
            "truncated": total > len(rows),
            "days": [
                {"date": d, "events": list(reversed(evs))}
                for d, evs in by_day.items()
            ],
        }

    # ── summary ───────────────────────────────────────────────────────────

    # ── status ────────────────────────────────────────────────────────────

    def get_status(self, uptime_s: float) -> dict:
        out: dict[str, Any] = {
            "service": {"uptime_s": round(uptime_s), "version": VERSION},
            "watcher": {
                "last_poll_at": None, "poll_interval_s": self.settings.watcher.poll_interval_s,
                "pipeline_running": None, "started": False,
                "round_pending": False, "pending_files": None, "sync": {},
            },
            "data": {"sessions_total": 0, "summaries_ok": 0,
                     "index": {"docs": 0, "last_rebuild_at": None, "lag_sessions": []}},
            "recent_runs": [], "errors": [],
        }
        if not self.db_ok():
            out["data"] = {"error": "database_not_initialized"}
            return out
        with self.connect() as conn:
            out["data"]["sessions_total"] = conn.execute(
                "SELECT COUNT(*) FROM recording_sessions").fetchone()[0]
            try:
                # v2：日摘要完成数（v1 session 摘要列已废弃，§9.1.1）
                out["data"]["summaries_ok"] = conn.execute(
                    "SELECT COUNT(*) FROM day_summary WHERE status = 'ok'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass
            try:
                out["data"]["index"]["docs"] = conn.execute(
                    "SELECT COUNT(*) FROM search_docs").fetchone()[0]
            except sqlite3.OperationalError:
                pass
            # 新鲜度（v2）：事件水位 vs reindex 水位（index_state：per-session /
            # '' 全局，T0 快照语义）——任一覆盖即视为已索引；口径与 reindex
            # stage 取件共用（index.lag_sessions 单一出处）；表缺失时不报滞后
            try:
                out["data"]["index"]["last_rebuild_at"] = conn.execute(
                    "SELECT MAX(last_reindex_at) FROM index_state").fetchone()[0]
                out["data"]["index"]["lag_sessions"] = index_mod.lag_sessions(conn)
            except sqlite3.OperationalError:
                pass
            try:
                runs = conn.execute(
                    "SELECT * FROM mcp_pipeline_runs ORDER BY run_id DESC LIMIT 10"
                ).fetchall()
                out["recent_runs"] = [
                    {"stage": r["stage"], "target": r["target"],
                     "status": r["status"], "finished_at": r["finished_at"]}
                    for r in runs
                ]
                out["errors"] = [
                    {"target": r["target"], "stage": r["stage"],
                     "error": r["error"], "retry_at": None}
                    for r in runs if r["status"] == "failed"
                ]
            except sqlite3.OperationalError:
                pass
            # §12 get_status 契约：pending_files{audio,video} / failed_files /
            # day_summary{pending,failed} / last_day_summary_at（嵌套结构）
            try:
                analysis = out["data"].setdefault("analysis", {})
                pf = analysis.setdefault("pending_files", {})
                ff = analysis.setdefault("failed_files", {})
                for kind in ("audio", "video"):
                    pf[kind] = conn.execute(
                        "SELECT COUNT(*) FROM files WHERE file_type = ? "
                        "AND analysis_status = 'pending'", (kind,)).fetchone()[0]
                    ff[kind] = conn.execute(
                        "SELECT COUNT(*) FROM files WHERE file_type = ? "
                        "AND analysis_status = 'failed'", (kind,)).fetchone()[0]
                ds = analysis.setdefault("day_summary", {})
                ds["pending"] = conn.execute(
                    "SELECT COUNT(*) FROM day_summary WHERE status = 'pending'"
                ).fetchone()[0]
                ds["failed"] = conn.execute(
                    "SELECT COUNT(*) FROM day_summary WHERE status = 'failed'"
                ).fetchone()[0]
                analysis["last_day_summary_at"] = conn.execute(
                    "SELECT MAX(generated_at) FROM day_summary WHERE status = 'ok'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                pass
        return out
