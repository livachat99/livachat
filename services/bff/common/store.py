"""LvdataStore — v2 只读 facade（lvdata_mcp_service_v2.md §9.1）。

数据面 = events + day_summary + files + recording_sessions（无 derived 查询
JSON）；session 维度读法降级为内部实现（媒体路径解析、时间范围定位），不再对
上层暴露 session 维度 API/字段。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path

from shared.config.models import BffConfig, RecordingConfig

log = logging.getLogger(__name__)

# events 表排序优先级（lvdata_mcp_service_v2.md §5.1；2026-09-15 重排，
# recording_data_process.md §2.5——与 lvdata consts.py 同步，漏改则同 ts 排序错乱）
EVENT_PRIORITY: dict[str, int] = {
    "chat": 0, "transcribe": 1, "translate": 2,
    "window": 3, "video": 4, "audio": 5, "agent_job": 6,
}
EVENT_TYPES: tuple[str, ...] = tuple(EVENT_PRIORITY.keys())

# media 文件名安全约束：uuid7 前缀 + 受支持扩展名
_MEDIA_FILENAME_RE = re.compile(r"^[0-9a-f-]{8,64}\.(ogg|jpg|m4a)$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# 由 EVENT_PRIORITY 生成（单一出处同款；ELSE 桶 = 未知类型排最后，须随重排更新）
_PRIO_SQL = (
    "CASE type "
    + " ".join(f"WHEN '{t}' THEN {p}" for t, p in EVENT_PRIORITY.items())
    + f" ELSE {len(EVENT_PRIORITY)} END"
)


def _page_sql(where_clause: str, windowed: bool) -> str:
    """events 翻页 SELECT（**带时间窗时强制 `idx_events_ts`**，见 docs/get_timeline_optimize.md §8）。

    原因：`ORDER BY` 第二键 `_PRIO_SQL` 是 CASE 表达式，索引无法覆盖完整排序键——planner 默认
    选 `idx_events_type`/`idx_events_rsession_ts` 后会整段 `TEMP B-TREE FOR ORDER BY`（`LIMIT`
    失效）。带窗时强制 ts 索引只剩"同 ms 小段"排序，`LIMIT` 可提前停；实测 200k 行 89 → 0.06 ms。

    无窗**不加**提示：类型占比低时类型索引更优（同文档 §2.3 反作用表）。
    ⚠️ 硬提示依赖 `idx_events_ts` 存在（lvdata-mcp 的 `ddl.py` 建表保证）；索引被删会直接报错。
    🔁 MCP 侧同款查询在 `tools/lvdata-mcp/src/lvdata_mcp/store.py`（`_timeline_page_sql`）——**改动须双副本同步**。
    """
    hint = " INDEXED BY idx_events_ts" if windowed else ""
    return (f"SELECT * FROM events{hint}"
            + (f" WHERE {where_clause}" if where_clause else "")
            + f" ORDER BY ts_ms, {_PRIO_SQL}, event_id")


class BffError(Exception):
    """Base for BFF-domain errors mapped to API error envelope codes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class LvdataStore:
    """Read-only facade over the lvdata tree: SQLite（events/day_summary/files）+ media files."""

    def __init__(
        self,
        data_root: str,
        db_path: str | None = None,
        device_uuids: list[str] | None = None,
    ):
        self.data_root = Path(data_root)
        self.db_path = Path(db_path or self.data_root / "lvdata_inventory.db")
        self.device_uuids = list(device_uuids or [])

    @classmethod
    def from_config(cls, bff: BffConfig, recording: RecordingConfig) -> LvdataStore:
        data_root = bff.data_root or recording.base_dir
        return cls(
            data_root=data_root,
            db_path=bff.lvdata_db_path or None,
            device_uuids=bff.device_uuids,
        )

    # -- SQLite（v2 数据面）-------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise BffError("internal", f"lvdata inventory db not found: {self.db_path}")
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _table_exists(self, conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,)).fetchone() is not None

    def timezone_known(self, tz: str) -> bool | None:
        """timezones 表缺失或为空 → None（全新部署，按现状放行）；否则返回是否存在该行。

        timezone-support.md §7.1：表缺失/空表不退化（lvdata-mcp 从未运行过的
        部署按现状放行请求时区）；「在表中」按行存在判定，不看 enabled。
        DB 异常（损坏/权限）→ None 放行：G3 回退是旁路查询，绝不让主查询 500。
        """
        if not self.db_path.exists():
            return None
        try:
            with self._connect() as conn:
                if not self._table_exists(conn, "timezones"):
                    return None
                if conn.execute("SELECT 1 FROM timezones LIMIT 1").fetchone() is None:
                    return None
                return conn.execute(
                    "SELECT 1 FROM timezones WHERE timezone = ?",
                    (tz,)).fetchone() is not None
        except (BffError, sqlite3.Error):
            log.warning("timezone_known query failed, treating as unknown", exc_info=True)
            return None

    # -- recording sessions（内部：媒体定位/时间范围）------------------------

    def list_sessions(self, device_uuid: str | None = None) -> list[dict]:
        """内部实现：媒体路径解析与时间范围定位用（§9.1.1，不再对上层暴露）。"""
        sql = "SELECT * FROM recording_sessions"
        clauses: list[str] = []
        params: list[object] = []
        if self.device_uuids:
            clauses.append("device_uuid IN ({})".format(",".join("?" * len(self.device_uuids))))
            params.extend(self.device_uuids)
        if device_uuid:
            clauses.append("device_uuid = ?")
            params.append(device_uuid)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC"
        with self._connect() as conn:
            out = []
            for row in conn.execute(sql, params):
                d = dict(row)
                d["session_id"] = d["rsession_id"]  # 内部兼容别名
                out.append(d)
            return out

    def get_session(self, rsession_id: str) -> dict | None:
        """内部实现；白名单语义与 list_sessions 一致。"""
        if not self.db_path.exists():
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM recording_sessions WHERE rsession_id = ?",
                (rsession_id,)).fetchone()
            if not row:
                return None
            data = dict(row)
            if self.device_uuids and data["device_uuid"] not in self.device_uuids:
                return None
            return data

    # -- events（timeline 数据源，§9.1.1）-----------------------------------

    def timeline_events(self, session_id: str,
                        start_ms: int | None = None,
                        end_ms: int | None = None) -> list[dict] | None:
        """查 events 表（rsession_id 过滤 + 可选时间窗），(ts_ms, prio, event_id) 排序。"""
        if not self.db_path.exists():
            return None
        try:
            with self._connect() as conn:
                if not self._table_exists(conn, "events"):
                    return None
                where = ["rsession_id = ?"]
                params: list[object] = [session_id]
                if start_ms is not None:
                    where.append("ts_ms >= ?")
                    params.append(start_ms)
                if end_ms is not None:
                    where.append("ts_ms <= ?")
                    params.append(end_ms)
                rows = conn.execute(
                    f"SELECT * FROM events WHERE {' AND '.join(where)} "
                    f"ORDER BY ts_ms, {_PRIO_SQL}, event_id", params).fetchall()
        except sqlite3.Error:
            return None
        return [self._event_out(r) for r in rows]

    def events_by_refs(self, refs: list[str]) -> list[dict]:
        """按 event_ref 取事件行（remove 的可见性/类型预检，docs/bff_service_remove.md §2.1）。

        只读查询；DB 文件/表缺失或查询异常 → 空列表（所有 ref 视为不存在，
        走 skipped: not_found）。
        """
        if not refs or not self.db_path.exists():
            return []
        try:
            with self._connect() as conn:
                if not self._table_exists(conn, "events"):
                    return []
                placeholders = ",".join("?" * len(refs))
                rows = conn.execute(
                    f"SELECT event_ref, type, device_uuid FROM events "
                    f"WHERE event_ref IN ({placeholders})", refs).fetchall()
        except sqlite3.Error:
            log.warning("events_by_refs query failed", exc_info=True)
            return []
        return [dict(r) for r in rows]

    def _events_where(self, start_ms: int | None, end_ms: int | None,
                      types: set[str] | None,
                      devices: list[str] | None) -> tuple[list[str], list[object]] | None:
        """events 查询公共 where（时间窗/类型/设备白名单）；None = 结果恒空。"""
        where: list[str] = []
        params: list[object] = []
        if start_ms is not None:
            where.append("ts_ms >= ?")
            params.append(start_ms)
        if end_ms is not None:
            where.append("ts_ms <= ?")
            params.append(end_ms)
        if types:
            placeholders = ",".join("?" * len(types))
            where.append(f"type IN ({placeholders})")
            params.extend(types)
        if devices is not None:
            if not devices:
                return None  # 无可见设备 → 无事件（空列表 ≠ 不过滤）
            placeholders = ",".join("?" * len(devices))
            where.append(f"device_uuid IN ({placeholders})")
            params.extend(devices)
        return where, params

    def events_in_range(self, start_ms: int | None, end_ms: int | None,
                        types: set[str] | None = None,
                        devices: list[str] | None = None,
                        limit: int | None = None,
                        offset: int | None = None) -> list[dict]:
        """跨 session 事件查询（timeline 全局视图，§9.1.1）。

        devices = 白名单 + 请求 device 过滤（安全语义，§5.5 对齐）。
        limit/offset：分页下推 SQL（§9.1.1；历史累积后避免全量载入内存）。
        """
        with self._connect() as conn:
            if not self._table_exists(conn, "events"):
                return []
            w = self._events_where(start_ms, end_ms, types, devices)
            if w is None:
                return []
            where, params = w
            sql = _page_sql(" AND ".join(where),
                            start_ms is not None or end_ms is not None)
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
                if offset is not None:
                    sql += " OFFSET ?"
                    params.append(offset)
            return [self._event_out(r) for r in conn.execute(sql, params)]

    def count_events(self, start_ms: int | None, end_ms: int | None,
                     types: set[str] | None = None,
                     devices: list[str] | None = None) -> int:
        """events 计数（timeline 分页 total；与 events_in_range 同过滤条件）。"""
        with self._connect() as conn:
            if not self._table_exists(conn, "events"):
                return 0
            w = self._events_where(start_ms, end_ms, types, devices)
            if w is None:
                return 0
            where, params = w
            sql = ("SELECT COUNT(*) FROM events"
                   + (f" WHERE {' AND '.join(where)}" if where else ""))
            return conn.execute(sql, params).fetchone()[0]

    @staticmethod
    def _event_out(r: sqlite3.Row) -> dict:
        out = dict(r)
        out["session_id"] = r["rsession_id"]  # 内部兼容别名（媒体定位等）
        try:
            payload = json.loads(r["payload"] or "{}")
            if isinstance(payload, dict):
                for k, v in payload.items():
                    if k not in out:
                        out[k] = v
                # 私有保留：payload 原始 dict——列与 payload 同名字段时列优先（如 audio 的
                # summary 列是检索拼接串、遮蔽 payload 的 LLM 摘要），消费方需真值时从这里取；
                # bff 出口（_to_timeline_event）显式剥离，不上 wire。
                out["payload_dict"] = payload
        except ValueError:
            pass
        return out

    # -- day_summary（日卡数据源，§9.1.2）-----------------------------------

    def day_summaries(self, timezone: str, date_from: str | None,
                      date_to: str | None, *, before: str | None = None,
                      limit: int | None = None) -> list[dict]:
        """按 (timezone, local_date) 读 day_summary 行。

        `doc` = 解析后的 day_summary JSON（失败/缺省 → None）；`day_stats` = 解析后的
        日卡派生 JSON（counts/hot_slots/images，docs/bff_service_interfaces.md §7.2.2）
        ——**列缺失/坏 JSON 一律回落 `{}`**（交叉版本兼容：BFF 可能先于 lvdata-mcp 升级，
        §7.2.6）。

        `before`/`limit` = overview 分页的 keyset 窗口（§8.2.3）：`local_date < before`
        （严格早于，不含该日；PK (timezone, local_date) 索引 range scan）+ `LIMIT limit`。
        缺省（None）恒为现行为——graph 等既有调用不受影响。
        """
        if not self.db_path.exists():
            return []
        with self._connect() as conn:
            if not self._table_exists(conn, "day_summary"):
                return []
            where = ["timezone = ?"]
            params: list[object] = [timezone]
            if date_from:
                where.append("local_date >= ?")
                params.append(date_from)
            if date_to:
                where.append("local_date <= ?")
                params.append(date_to)
            if before:
                where.append("local_date < ?")
                params.append(before)
            sql = (f"SELECT * FROM day_summary WHERE {' AND '.join(where)} "
                   "ORDER BY local_date DESC")
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            if item.get("day_summary"):
                try:
                    item["doc"] = json.loads(item["day_summary"])
                except ValueError:
                    item["doc"] = None
            else:
                item["doc"] = None
            try:
                stats = json.loads(item.get("day_stats") or "{}")
                item["day_stats"] = stats if isinstance(stats, dict) else {}
            except ValueError:
                item["day_stats"] = {}
            out.append(item)
        return out

    def count_days(self, timezone: str) -> int:
        """该时区 day_summary 行数（overview 分页的 `total`，§8.2.2）。

        PK (timezone, local_date) 覆盖索引 range scan，量级 = 天卡数（与数据量无关）。
        表缺失/DB 异常 → 0（读侧容错口径同 §6；与 `latest_timezone` 同款 fail-open）。
        """
        if not self.db_path.exists():
            return 0
        try:
            with self._connect() as conn:
                if not self._table_exists(conn, "day_summary"):
                    return 0
                row = conn.execute(
                    "SELECT COUNT(*) FROM day_summary WHERE timezone = ?",
                    (timezone,)).fetchone()
                return int(row[0]) if row else 0
        except (BffError, sqlite3.Error):
            log.warning("count_days query failed, treating as 0", exc_info=True)
            return 0

    def latest_timezone(self) -> str | None:
        """`timezones` 表最新（updated_at DESC, timezone）的 enabled 行；不可得 → None。

        镜像 lvdata-mcp `summary/day.py` 的「最近使用」判定（其 `_active_timezones`，
        即 day_summary / day_stats 实际生成的时区）——**改动需两侧同步**
        （docs/bff_service_interfaces.md §7.3 E3/E10）。None（表缺失/空表/DB 异常）
        时调用方按现状 fail-open 放行请求时区。
        """
        if not self.db_path.exists():
            return None
        try:
            with self._connect() as conn:
                if not self._table_exists(conn, "timezones"):
                    return None
                row = conn.execute(
                    "SELECT timezone FROM timezones WHERE enabled = 1 "
                    "ORDER BY updated_at DESC, timezone LIMIT 1").fetchone()
                return row[0] if row else None
        except (BffError, sqlite3.Error):
            log.warning("latest_timezone query failed, treating as unknown",
                        exc_info=True)
            return None

    # -- search（FTS 检索源 = lvdata-mcp 的 search_docs，docs/bff_search_fts.md）--

    def has_search_docs(self) -> bool:
        """FTS 派生索引是否就绪（由 lvdata-mcp reindex 建表）；缺 → /search 返回空。"""
        if not self.db_path.exists():
            return False
        try:
            with self._connect() as conn:
                return self._table_exists(conn, "search_docs")
        except sqlite3.Error:
            return False

    def search_fts(self, fts_query: str, types: tuple[str, ...],
                   devices: list[str] | None, limit: int, offset: int) -> list[dict]:
        """search_docs 检索：`bm25()` 升序（FTS5 中越小越相关），tie 按 ts 倒序 + event_ref 稳定。

        `devices=None` 不过滤；**空列表 = 无可见设备 → 返回空**（对齐 `_events_where`
        「空列表 ≠ 不过滤」，安全关键）。
        """
        if devices is not None and not devices:
            return []
        if not self.db_path.exists():
            return []
        where, params = self._fts_where(fts_query, types, devices)
        try:
            with self._connect() as conn:
                if not self._table_exists(conn, "search_docs"):
                    return []
                rows = conn.execute(
                    "SELECT event_ref, session_id, device_uuid, type, ts_ms, "
                    "orig_text, bm25(search_docs) AS score "
                    f"FROM search_docs WHERE {where} "
                    "ORDER BY score, ts_ms DESC, event_ref LIMIT ? OFFSET ?",
                    [*params, limit, offset]).fetchall()
        except sqlite3.Error:
            log.warning("search_fts query failed", exc_info=True)
            return []
        return [dict(r) for r in rows]

    def count_fts(self, fts_query: str, types: tuple[str, ...],
                  devices: list[str] | None) -> int:
        """search_docs 命中总数（与 `search_fts` 同过滤条件，供分页 total）。"""
        if devices is not None and not devices:
            return 0
        if not self.db_path.exists():
            return 0
        where, params = self._fts_where(fts_query, types, devices)
        try:
            with self._connect() as conn:
                if not self._table_exists(conn, "search_docs"):
                    return 0
                row = conn.execute(
                    f"SELECT COUNT(*) AS n FROM search_docs WHERE {where}",
                    params).fetchone()
        except sqlite3.Error:
            log.warning("count_fts query failed", exc_info=True)
            return 0
        return int(row["n"]) if row else 0

    @staticmethod
    def _fts_where(fts_query: str, types: tuple[str, ...],
                   devices: list[str] | None) -> tuple[str, list[object]]:
        where = ["search_docs MATCH ?"]
        params: list[object] = [fts_query]
        if types:
            where.append("type IN ({})".format(",".join("?" * len(types))))
            params.extend(types)
        if devices is not None:
            where.append("device_uuid IN ({})".format(",".join("?" * len(devices))))
            params.extend(devices)
        return " AND ".join(where), params

    # -- Media --------------------------------------------------------------

    def resolve_media(self, device: str, session: str, kind: str, filename: str) -> Path:
        """Resolve a media file under <data_root>/<device>/<session>/<kind>/ with path-traversal guards."""
        if not _UUID_RE.match(device or "") or not _UUID_RE.match(session or ""):
            raise BffError("bad_request", "device/session must be a UUID")
        if self.device_uuids and device not in self.device_uuids:
            raise BffError("not_found", "media file not found")
        if kind not in ("audio", "video", "image"):
            raise BffError("bad_request", f"kind must be audio|video|image, got {kind!r}")
        if not _MEDIA_FILENAME_RE.match(filename or ""):
            raise BffError("bad_request", "invalid media filename")

        base = (self.data_root / device / session).resolve()
        if not str(base).startswith(str(self.data_root.resolve()) + "/"):
            raise BffError("bad_request", "invalid media path")
        path = (base / kind / filename).resolve()
        if not str(path).startswith(str(base) + "/"):
            raise BffError("bad_request", "invalid media path")
        if not path.is_file():
            raise BffError("not_found", "media file not found")
        return path
