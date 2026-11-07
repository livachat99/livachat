"""ChatStore — chat server 唯一 DB（devices / messages / recording_sessions /
agent_jobs / hermes_device_sessions / recording_files）。

chatserver_sqlite_store.md §3.3。工程契约（对齐 job_persistence.py §2.4）：
1. 有界 asyncio.Queue + 单 writer task；同步钩子 put_nowait O(1)；队列满 →
   丢弃 + metric，永不阻塞热路径；**例外：recording_files 登记
   （record_file/touch_file）走带超时阻塞入队**——登记行丢弃 = 永久孤儿文件，
   背压必须传导（recording_data_process.md §2.1）；
2. WAL 是库级持久属性只在 init 时设一次；每个写连接都设 busy_timeout ≥ 5000；
3. schema 用 CREATE TABLE/INDEX IF NOT EXISTS 幂等建；
4. 时间戳一律规范格式（ISO 8601 UTC、'T'、毫秒、'Z'）；
5. 停机限时排空（stop(drain_timeout_s=5.0)）。

fail-fast（§3.1）：start() 校验 db 父目录存在 → _init_db → prune → writer task；
任一失败抛异常，由 lifespan 层无 try/except 上抛，uvicorn 启动失败退出。
运行时写路径 best-effort：单条失败只 log + metric，不阻断实时链路。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from shared.observability.metrics import get_registry
from shared.timeutil import resolve_tzinfo

logger = logging.getLogger("livachat.persistence.chat_store")

# 契约 2：WAL 库级属性 + busy_timeout（每个写连接都设，重复设置 journal_mode 会与
# 其他写者争抢独占锁——job_persistence.py:103-111 教训）
_BUSY_TIMEOUT_MS = 5000
_PRUNE_INTERVAL_S = 3600
_DAY_MS = 24 * 60 * 60 * 1000

CHAT_STORE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS devices (
    device_uuid    TEXT PRIMARY KEY,
    user_id        TEXT,
    platform       TEXT,
    client_version TEXT,
    timezone       TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_uuid TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    images_json TEXT,
    ref_id      TEXT NOT NULL DEFAULT '',
    ts_ms       INTEGER NOT NULL,
    utc         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_device_ts ON messages(device_uuid, ts_ms, id);
CREATE INDEX IF NOT EXISTS idx_messages_session   ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS recording_sessions (
    device_uuid          TEXT NOT NULL,
    session_date         TEXT NOT NULL,
    recording_session_id TEXT NOT NULL,
    base_path            TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    PRIMARY KEY (device_uuid, session_date)
);

-- agent_jobs：agent job 状态机真源（chatserver_no_redis.md §3.5，原 lvdata DB
-- 镜像表迁入；schema 与 job_persistence.py AGENT_JOBS_SCHEMA_SQL 同构）
CREATE TABLE IF NOT EXISTS agent_jobs (
    job_key              TEXT PRIMARY KEY,
    agent_name           TEXT NOT NULL DEFAULT 'hermes',
    device_uuid          TEXT NOT NULL,
    session_id           TEXT NOT NULL,
    recording_session_id TEXT,
    hermes_task_id       TEXT,
    status               TEXT NOT NULL,
    summary              TEXT NOT NULL DEFAULT '',
    result_text          TEXT,
    fail_reason          TEXT,
    requested_ts_ms      INTEGER NOT NULL,
    requested_at         TEXT NOT NULL,
    started_at           TEXT,
    finished_at          TEXT,
    delivered_at         TEXT,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aj_device_time ON agent_jobs(device_uuid, requested_ts_ms);
CREATE INDEX IF NOT EXISTS idx_aj_rec_session ON agent_jobs(recording_session_id);
CREATE INDEX IF NOT EXISTS idx_aj_status_finished ON agent_jobs(status, finished_at);
CREATE INDEX IF NOT EXISTS idx_aj_updated ON agent_jobs(updated_at, job_key);
CREATE INDEX IF NOT EXISTS idx_aj_task ON agent_jobs(hermes_task_id);

-- hermes device→session 映射（chatserver_no_redis.md §3.6，req 5 重启恢复）
CREATE TABLE IF NOT EXISTS hermes_device_sessions (
    device_uuid       TEXT PRIMARY KEY,
    hermes_session_id TEXT NOT NULL DEFAULT '',
    stored_session_id TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL
);

-- recording_files：录制文件登记（recording_data_process.md §2.1，唯一事实源；
-- lvdata-mcp 只读轮询此表，不再扫文件系统）
-- kind: session|chat_history|chat_transcribe|chat_translate|audio|video
--   （image 不登记，doc §5.B）
-- updated_at 不变式：固定毫秒格式 + 严格单调递增（keyset 水位
--   (updated_at, file_id) 对相等值隐形）
CREATE TABLE IF NOT EXISTS recording_files (
    file_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_uuid          TEXT NOT NULL,
    recording_session_id TEXT NOT NULL,
    kind                 TEXT NOT NULL,
    rel_path             TEXT NOT NULL UNIQUE,
    size_bytes           INTEGER NOT NULL,
    ts_ms                INTEGER,
    duration_ms          INTEGER,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rf_updated ON recording_files(updated_at, file_id);
CREATE INDEX IF NOT EXISTS idx_rf_session ON recording_files(recording_session_id);
"""

_DEVICE_UPSERT_SQL = """
INSERT INTO devices (device_uuid, user_id, platform, client_version, timezone,
    created_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(device_uuid) DO UPDATE SET
    user_id = excluded.user_id,
    platform = excluded.platform,
    client_version = excluded.client_version,
    timezone = excluded.timezone,
    last_seen_at = excluded.last_seen_at
"""

_MESSAGE_INSERT_SQL = """
INSERT INTO messages (device_uuid, session_id, role, kind, content, images_json,
    ref_id, ts_ms, utc)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# 5 元组精确删除（4xx 失败回滚本轮 user 记录；fd_pipeline_llm_v2.md）。
# 五值全部为写入时值：同 session 同 kind/role 不存在第二个相同 ts_ms 的行，删 0 行即幂等。
_MESSAGE_DELETE_SQL = (
    "DELETE FROM messages WHERE device_uuid=? AND session_id=? "
    "AND kind=? AND role=? AND ts_ms=?"
)

_PRUNE_SQL = "DELETE FROM messages WHERE ts_ms < ?"

_REC_GET_OR_CREATE_SQL = """
INSERT OR IGNORE INTO recording_sessions (device_uuid, session_date,
    recording_session_id, base_path, created_at)
VALUES (?, ?, ?, ?, ?)
"""

_REC_SELECT_SQL = (
    "SELECT recording_session_id, base_path FROM recording_sessions "
    "WHERE device_uuid = ? AND session_date = ?"
)

_LOAD_DAY_SQL = (
    "SELECT id, role, kind, content, images_json, ref_id FROM messages "
    "WHERE device_uuid = ? AND ts_ms >= ? AND ts_ms < ? "
    "ORDER BY id DESC LIMIT ?"
)

# -- recording_files（recording_data_process.md §2.1）------------------------

_RECORDING_FILE_KINDS = frozenset({
    "session", "chat_history", "chat_transcribe", "chat_translate",
    "audio", "video",
})

# 登记类写入的阻塞入队上限（背压传导；超时 = writer 停滞，须告警）
_RECORDING_ENQUEUE_TIMEOUT_S = 5.0

_RF_INSERT_SQL = """
INSERT OR IGNORE INTO recording_files (device_uuid, recording_session_id, kind,
    rel_path, size_bytes, ts_ms, duration_ms, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_RF_SELECT_UPDATED_SQL = "SELECT updated_at FROM recording_files WHERE rel_path = ?"

_RF_UPDATE_SQL = (
    "UPDATE recording_files SET size_bytes = ?, updated_at = ? WHERE rel_path = ?"
)


def _dt_ms(ts_ms: int) -> str:
    """epoch 毫秒 → 规范时间戳（ISO 8601 UTC 'T' 毫秒 'Z'）。"""
    return (datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def _now_ms() -> int:
    """当前 UTC epoch 毫秒（规范格式时间戳的源）。"""
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def _bump_iso(prev: str, now: str) -> str:
    """updated_at 严格单调 bump（recording_data_process.md §2.1 不变式 2）。

    keyset 水位 `(updated_at, file_id)` 对 re-bump 到相等值隐形 → 每次 bump 取
    `max(now, prev+1ms)`。写侧格式恒定（固定宽度毫秒），字符串序即时间序。
    """
    if now > prev:
        return now
    dt = datetime.fromisoformat(prev.replace("Z", "+00:00")) + timedelta(milliseconds=1)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _resolve_tz(tz: str):
    """规范形 → tzinfo；空串/非法回退 UTC（chatserver_sqlite_store.md §2.3）。"""
    if not tz:
        return UTC
    try:
        return resolve_tzinfo(tz)
    except ValueError:
        return UTC


def local_day_ms_range(tz: str) -> tuple[int, int]:
    """给定时区下"今天"的 [start_ms, end_ms)（本地日 00:00 起的 UTC 毫秒）。

    顶层函数（非下划线）：单测直接验证时区换算；'' 回退 UTC。
    """
    tzinfo = _resolve_tz(tz)
    now_local = datetime.now(tzinfo)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ms = int(start_local.timestamp() * 1000)
    return start_ms, start_ms + _DAY_MS


class ChatStore:
    """chat server 三表 SQLite 存储（§3.3）。"""

    def __init__(self, db_path: str, *, max_queue: int = 1024) -> None:
        self._db_path = str(db_path)
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task | None = None
        # prune 缺省时区来源：最近一次 on_device 上报的 session.start timezone
        self._last_tz: str = ""

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """fail-fast（§3.1）：路径非空 → 父目录存在 → _init_db（WAL+busy_timeout+
        schema，一次）→ _prune() → writer task。任一失败抛异常。"""
        if not self._db_path:
            raise ValueError(
                "chat_server_db_path is required (fail-fast: refusing to run "
                "without a configured chat server database)"
            )
        parent = Path(self._db_path).parent
        if not parent.is_dir():
            raise FileNotFoundError(
                f"chat server db parent dir does not exist: {parent} "
                "(fail-fast: refusing to create the database on a wrong filesystem)"
            )
        self._init_db()
        self._prune()
        self._task = asyncio.create_task(self._run(), name="chat-store-writer")

    async def stop(self, *, drain_timeout_s: float = 5.0) -> None:
        """限时排空 → cancel writer。"""
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=drain_timeout_s)
        except TimeoutError:
            logger.warning(
                "chat store writer drain timeout", extra={"pending": self._queue.qsize()}
            )
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def drain(self) -> None:
        """等待写队列清空（测试/CLI 排空用；stop() 内含限时排空）。"""
        await self._queue.join()

    def _init_db(self) -> None:
        """契约 2/3：WAL 设一次 + busy_timeout + 幂等建表。"""
        with sqlite3.connect(self._db_path, timeout=10) as conn:
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(CHAT_STORE_SCHEMA_SQL)

    # -- sync hooks（热路径，O(1)，永不抛）--------------------------------

    def on_device(self, *, device_uuid: str, user_id: str | None,
                  client_version: str, timezone: str) -> None:
        """devices upsert；同时更新 self._last_tz（prune 缺省时区来源）。"""
        self._last_tz = timezone or self._last_tz
        now = _dt_ms(_now_ms())
        self._enqueue(("device", {
            "device_uuid": device_uuid,
            "user_id": user_id,
            "platform": None,
            "client_version": client_version or None,
            "timezone": timezone,
            "created_at": now,
            "last_seen_at": now,
        }))

    def on_message(self, *, device_uuid: str, session_id: str, kind: str,
                   role: str, content: str, ref_id: str = "", ts_ms: int,
                   images: list[str] | None = None) -> None:
        self._enqueue(("message", {
            "device_uuid": device_uuid,
            "session_id": session_id,
            "kind": kind,
            "role": role,
            "content": content,
            "ref_id": ref_id,
            "ts_ms": ts_ms,
            "utc": _dt_ms(ts_ms),
            "images_json": json.dumps(list(images), ensure_ascii=False) if images else None,
        }))

    def delete_message(self, *, device_uuid: str, session_id: str, kind: str,
                       role: str, ts_ms: int) -> None:
        """按 5 元组删除一条 messages 行（fd_pipeline_llm_v2.md）。

        与 on_message 共用 best-effort 队列：FIFO 保证 insert 未落盘时 delete 在其后
        执行（"如果进入，则删除"）；insert 被丢弃或键不匹配时删 0 行、天然幂等。
        """
        self._enqueue(("message_delete", {
            "device_uuid": device_uuid,
            "session_id": session_id,
            "kind": kind,
            "role": role,
            "ts_ms": ts_ms,
        }))

    def _enqueue(self, item: tuple[str, dict[str, Any]]) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            get_registry().chat_store_total().labels(result="queue_dropped").inc()
            logger.warning(
                "chat store queue full, item dropped",
                extra={"op": item[0], "pending": self._queue.qsize()},
            )

    # -- recording_files 登记（doc §2.1；阻塞入队例外于契约 1）--------------

    async def record_file(self, *, device_uuid: str, recording_session_id: str,
                          kind: str, rel_path: str, size_bytes: int,
                          ts_ms: int | None = None,
                          duration_ms: int | None = None,
                          enqueue_timeout_s: float = _RECORDING_ENQUEUE_TIMEOUT_S,
                          ) -> None:
        """登记一条录制文件行（首次落盘完成后调用；INSERT OR IGNORE 幂等）。

        与 messages 的 best-effort 丢弃不同：登记行丢弃 = 永久孤儿文件
        （lvdata 不可见），故带超时阻塞入队（背压传导给 recording writer
        task）；超时抛 TimeoutError，由调用方告警 + 走 reconcile 路径（§2.1）。
        """
        if kind not in _RECORDING_FILE_KINDS:
            raise ValueError(f"unknown recording file kind: {kind!r}")
        now = _dt_ms(_now_ms())
        await self._enqueue_blocking(("recording_file", {
            "device_uuid": device_uuid,
            "recording_session_id": recording_session_id,
            "kind": kind,
            "rel_path": rel_path,
            "size_bytes": size_bytes,
            "ts_ms": ts_ms,
            "duration_ms": duration_ms,
            "created_at": now,
            "updated_at": now,
        }), timeout_s=enqueue_timeout_s)

    async def touch_file(self, *, rel_path: str, size_bytes: int,
                         enqueue_timeout_s: float = _RECORDING_ENQUEUE_TIMEOUT_S,
                         ) -> None:
        """文件内容变更后更新 size/updated_at（jsonl append；session.json 静态化）。

        逐条 bump（trailing-edge 的逐条实现档，§5.E 参数待定）；bump 严格单调
        （§2.1 不变式 2）。行不存在（touch 先于 record）→ 告警 + 跳过，由
        backfill CLI reconcile 兜底。
        """
        await self._enqueue_blocking(("recording_file_touch", {
            "rel_path": rel_path,
            "size_bytes": size_bytes,
        }), timeout_s=enqueue_timeout_s)

    async def _enqueue_blocking(self, item: tuple[str, dict[str, Any]], *,
                                timeout_s: float) -> None:
        """登记类写入专用队列入口：带超时阻塞（≠ messages 的 put_nowait 丢弃）。"""
        try:
            await asyncio.wait_for(self._queue.put(item), timeout=timeout_s)
        except TimeoutError:
            get_registry().chat_store_total().labels(result="enqueue_timeout").inc()
            logger.error(
                "chat store enqueue timeout, file registration dropped",
                extra={"op": item[0], "pending": self._queue.qsize()},
            )
            raise

    # -- recording_sessions（await，非热路径；每 WS 连接至多一次）----------

    async def get_or_create_recording_session(
        self, *, device_uuid: str, session_date: str, recording_session_id: str,
        base_path: str,
    ) -> dict:
        """按 (device_uuid, session_date) INSERT OR IGNORE 后 SELECT，
        返回权威行 {recording_session_id, base_path}。to_thread 执行。"""
        return await asyncio.to_thread(
            self._get_or_create_recording_session_sync,
            device_uuid=device_uuid, session_date=session_date,
            recording_session_id=recording_session_id, base_path=base_path,
        )

    async def get_recording_session(self, *, device_uuid: str,
                                    session_date: str) -> dict | None:
        """recording_sessions 权威行只读查询（§3.5.3；recording 恢复校验 + job
        create 时 rsession 解析共用）。只读 SELECT，不 get-or-create。"""
        return await asyncio.to_thread(
            self._get_recording_session_sync,
            device_uuid=device_uuid, session_date=session_date,
        )

    def _get_or_create_recording_session_sync(
        self, *, device_uuid: str, session_date: str, recording_session_id: str,
        base_path: str,
    ) -> dict:
        # device_uuid lower 归一（§3.5.3/§3.6 对称纪律）：写入与读取共用同一
        # 归一点。写侧 as-is、读侧 lower 的旧实现对混合大小写设备（iOS
        # UUID().uuidString 即大写）必 miss——INSERT OR IGNORE 落混合大小写行、
        # 读侧全小写精确匹配读不到。归一后大小写变体合并到同一行。
        device_uuid = device_uuid.lower()
        now = _dt_ms(_now_ms())
        with sqlite3.connect(self._db_path, timeout=10) as conn:
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            conn.execute(_REC_GET_OR_CREATE_SQL, (
                device_uuid, session_date, recording_session_id, base_path, now,
            ))
            # IGNORE 冲突（同设备变体先插入过）后 SELECT 必须同归一点，否则
            # 精确查询 miss → 调用方拿到 None 崩溃
            row = conn.execute(_REC_SELECT_SQL, (device_uuid, session_date)).fetchone()
        return {"recording_session_id": row[0], "base_path": row[1]}

    def _get_recording_session_sync(self, *, device_uuid: str,
                                    session_date: str) -> dict | None:
        # device_uuid lower 归一（§3.5.3 纪律）：真实数据存在大小写不一致，归一
        # 后与 job create / recording 恢复共用同一匹配点
        with sqlite3.connect(self._db_path, timeout=10) as conn:
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            row = conn.execute(_REC_SELECT_SQL, (device_uuid.lower(), session_date)).fetchone()
        if row is None:
            return None
        return {"recording_session_id": row[0], "base_path": row[1]}

    # -- hermes device→session 映射（§3.6；同步直写 + to_thread，不走队列）-----

    async def load_hermes_session(self, device_uuid: str) -> dict | None:
        """返回 {hermes_session_id, stored_session_id, updated_at}；无行 → None。"""
        return await asyncio.to_thread(self._load_hermes_session_sync, device_uuid)

    async def save_hermes_session(self, *, device_uuid: str,
                                  hermes_session_id: str, stored_session_id: str) -> None:
        """UPSERT 映射（新 create/resume 成功覆盖旧行）。"""
        await asyncio.to_thread(
            self._save_hermes_session_sync,
            device_uuid=device_uuid, hermes_session_id=hermes_session_id,
            stored_session_id=stored_session_id,
        )

    async def delete_hermes_session(self, device_uuid: str) -> None:
        await asyncio.to_thread(self._delete_hermes_session_sync, device_uuid)

    def _load_hermes_session_sync(self, device_uuid: str) -> dict | None:
        with sqlite3.connect(self._db_path, timeout=10) as conn:
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            row = conn.execute(
                "SELECT hermes_session_id, stored_session_id, updated_at "
                "FROM hermes_device_sessions WHERE device_uuid = ?",
                (device_uuid.lower(),),
            ).fetchone()
        if row is None:
            return None
        return {
            "hermes_session_id": row[0],
            "stored_session_id": row[1],
            "updated_at": row[2],
        }

    def _save_hermes_session_sync(self, *, device_uuid: str,
                                  hermes_session_id: str, stored_session_id: str) -> None:
        with sqlite3.connect(self._db_path, timeout=10) as conn:
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            conn.execute(
                "INSERT INTO hermes_device_sessions (device_uuid, hermes_session_id, "
                "stored_session_id, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(device_uuid) DO UPDATE SET "
                "hermes_session_id = excluded.hermes_session_id, "
                "stored_session_id = excluded.stored_session_id, "
                "updated_at = excluded.updated_at",
                (device_uuid.lower(), hermes_session_id, stored_session_id, _dt_ms(_now_ms())),
            )

    def _delete_hermes_session_sync(self, device_uuid: str) -> None:
        with sqlite3.connect(self._db_path, timeout=10) as conn:
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            conn.execute(
                "DELETE FROM hermes_device_sessions WHERE device_uuid = ?",
                (device_uuid.lower(),),
            )

    # -- 读取（restore；同步 sqlite3 ro 连接，调用方走 to_thread）----------

    def load_day(self, *, device_uuid: str, timezone: str,
                 limit: int = 20) -> list[dict]:
        """今天 = timezone（规范形，'' 回退 UTC）下的本地日；按 ts_ms 范围查询，
        返回按 id 升序（对话序）的行列表。"""
        start_ms, end_ms = local_day_ms_range(timezone)
        with sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                _LOAD_DAY_SQL, (device_uuid, start_ms, end_ms, limit)
            ).fetchall()
        rows.reverse()  # DESC limit 后翻转为升序
        out: list[dict] = []
        for r in rows:
            images = None
            if r[4] is not None:
                try:
                    images = json.loads(r[4])
                except (ValueError, TypeError):
                    images = None
            out.append({
                "role": r[1],
                "kind": r[2],
                "content": r[3],
                "images": images,
                "ref_id": r[5],
            })
        return out

    # -- writer task -------------------------------------------------------

    async def _run(self) -> None:
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=_PRUNE_INTERVAL_S)
            except TimeoutError:
                # 每小时 prune tick（超时不视为错误）
                self._prune()
                continue
            except asyncio.CancelledError:
                raise
            try:
                self._apply(item)
                get_registry().chat_store_total().labels(result="written").inc()
            except asyncio.CancelledError:
                raise
            except Exception:
                get_registry().chat_store_total().labels(result="write_error").inc()
                logger.warning(
                    "chat store write failed",
                    extra={"op": item[0], "db_path": self._db_path},
                    exc_info=True,
                )
            finally:
                self._queue.task_done()

    def _apply(self, item: tuple[str, dict[str, Any]]) -> None:
        op, payload = item
        with sqlite3.connect(self._db_path, timeout=10) as conn:
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            if op == "device":
                conn.execute(_DEVICE_UPSERT_SQL, (
                    payload["device_uuid"], payload["user_id"], payload["platform"],
                    payload["client_version"], payload["timezone"],
                    payload["created_at"], payload["last_seen_at"],
                ))
            elif op == "message":
                conn.execute(_MESSAGE_INSERT_SQL, (
                    payload["device_uuid"], payload["session_id"], payload["role"],
                    payload["kind"], payload["content"], payload["images_json"],
                    payload["ref_id"], payload["ts_ms"], payload["utc"],
                ))
            elif op == "message_delete":
                conn.execute(_MESSAGE_DELETE_SQL, (
                    payload["device_uuid"], payload["session_id"], payload["kind"],
                    payload["role"], payload["ts_ms"],
                ))
            elif op == "recording_file":
                conn.execute(_RF_INSERT_SQL, (
                    payload["device_uuid"], payload["recording_session_id"],
                    payload["kind"], payload["rel_path"], payload["size_bytes"],
                    payload["ts_ms"], payload["duration_ms"],
                    payload["created_at"], payload["updated_at"],
                ))
            elif op == "recording_file_touch":
                row = conn.execute(_RF_SELECT_UPDATED_SQL,
                                   (payload["rel_path"],)).fetchone()
                if row is None:
                    get_registry().chat_store_total().labels(
                        result="touch_missing_row").inc()
                    logger.warning(
                        "recording file touch on missing row (skipped)",
                        extra={"rel_path": payload["rel_path"]},
                    )
                    return
                conn.execute(_RF_UPDATE_SQL, (
                    payload["size_bytes"],
                    _bump_iso(row[0], _dt_ms(_now_ms())),
                    payload["rel_path"],
                ))
            else:
                logger.warning("chat store unknown op", extra={"op": op})
                return

    def _prune(self) -> None:
        """"只存今天"：cutoff = 今天 00:00（本地日起点）的 UTC ms；时区取
        self._last_tz（无任何连接前回退 UTC）。"""
        start_ms, _ = local_day_ms_range(self._last_tz)
        try:
            with sqlite3.connect(self._db_path, timeout=10) as conn:
                conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
                conn.execute(_PRUNE_SQL, (start_ms,))
            get_registry().chat_store_total().labels(result="prune_ok").inc()
        except Exception:
            get_registry().chat_store_total().labels(result="prune_error").inc()
            logger.warning("chat store prune failed", exc_info=True)
