"""删除 video 事件及其级联面（docs/bff_service_remove.md）。

级联顺序（§3）：T1 主事务（events + window 归零连带 + search_docs + day_stats
重算，同库原子）→ T2 向量删除 → 文件面（jpg + files 行，先文件后行）。
残余面自愈：向量靠 `sync_changed` 孤儿清扫；文件面靠既有 video prune
（watcher._video_prune_once——事件已删 → 帧进入其候选）。

同步阻塞实现（T1 SQL 事务 + T2 + 文件 IO）——HTTP 调用方（server.api_remove）
必须经 `asyncio.to_thread` 卸载，勿在事件循环内联（§3 执行载体）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import structlog

from .config import LvdataMcpConfig
from .summary.day_stats import DayStats
from .timeutil import LocalDateOutOfRange, now_iso, safe_local_date
from .vec import VecIndex

log = structlog.get_logger("lvdata_mcp.remove")


class EventRemover:
    """一批 video 事件的删除器：部分成功语义（skipped 逐条报告，D7）。"""

    def __init__(self, cfg: LvdataMcpConfig, vec_index: VecIndex | None = None):
        self.cfg = cfg
        self.root = Path(cfg.data.root_dir).resolve()
        self.db_path = cfg.data.resolved_db_path
        self.vec_index = vec_index
        self.day_stats = DayStats(cfg)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.row_factory = sqlite3.Row
        return conn

    # ── 入口 ──────────────────────────────────────────────────────────────

    def remove(self, refs: list[str]) -> dict:
        """删除一批 video 事件；返回响应字段（§2.1/§2.2，不含 schema_version）。"""
        # 去重（保序）：重复 ref 只处理一次，响应中每个 ref 至多出现一次（§2.1）
        uniq: list[str] = []
        for ref in refs:
            if ref not in uniq:
                uniq.append(ref)
        refs = uniq
        t0 = now_iso()
        skipped: list[dict] = []
        with self._conn() as conn:
            rows = {r["event_ref"]: r for r in conn.execute(
                "SELECT event_ref, device_uuid, rsession_id, type, ts_ms, payload "
                f"FROM events WHERE event_ref IN ({','.join('?' * len(refs))})",
                refs)}
            deletable: list[sqlite3.Row] = []
            for ref in refs:
                row = rows.get(ref)
                if row is None:
                    skipped.append({"event_ref": ref, "reason": "not_found"})
                elif row["type"] != "video":
                    skipped.append({"event_ref": ref, "reason": "not_video"})
                else:
                    deletable.append(row)
            for s in skipped:
                log.warning("event remove skipped", event_ref=s["event_ref"],
                            reason=s["reason"])
            if not deletable:
                log.info("events remove done", removed=0, skipped=len(skipped),
                         windows=0, images=0, images_pending=0, days=0,
                         vectors_pending=False)
                return self._result([], [], [], [], [], skipped, False)

            drefs = [r["event_ref"] for r in deletable]
            ph = ",".join("?" * len(drefs))
            conn.execute(f"DELETE FROM events WHERE event_ref IN ({ph})", drefs)
            win_rows = self._delete_empty_windows(conn, deletable)

            all_refs = drefs + [ref for ref, _ in win_rows]
            if self._table_exists(conn, "search_docs"):
                ph_all = ",".join("?" * len(all_refs))
                conn.execute(
                    f"DELETE FROM search_docs WHERE event_ref IN ({ph_all})",
                    all_refs)
            ts_list = [int(r["ts_ms"]) for r in deletable] + [ts for _, ts in win_rows]
            days_recomputed = self._recompute_days(conn, ts_list, t0)
            conn.commit()

        vectors_pending = self._remove_vectors(all_refs)
        images_removed, images_pending = self._delete_images(deletable)

        log.info("events remove done", removed=len(drefs), skipped=len(skipped),
                 windows=len(win_rows), images=len(images_removed),
                 images_pending=len(images_pending),
                 days=len(days_recomputed), vectors_pending=vectors_pending)
        return self._result(drefs, [ref for ref, _ in win_rows], images_removed,
                            images_pending, days_recomputed, skipped,
                            vectors_pending)

    # ── T1 内部步骤 ────────────────────────────────────────────────────────

    @staticmethod
    def _delete_empty_windows(conn: sqlite3.Connection,
                              deletable: list[sqlite3.Row]) -> list[tuple[str, int]]:
        """window 归零连带删除（§3.1-3）：在删 video 行之后、事务内计数。

        跨 session：关联键 = payload.window_id（窗口分组按 device、session 不参与）。
        返回被删的 (window event_ref, ts_ms)；payload 无 window_id 的旧行跳过。
        """
        wids: list[str] = []
        for r in deletable:
            wid = EventRemover._payload_field(r["payload"], "window_id")
            if wid and wid not in wids:
                wids.append(wid)
        if not wids:
            return []
        remaining = dict.fromkeys(wids, 0)
        for r in conn.execute("SELECT payload FROM events WHERE type='video'"):
            wid = EventRemover._payload_field(r["payload"], "window_id")
            if wid in remaining:
                remaining[wid] += 1
        gone = {wid for wid in wids if remaining[wid] == 0}
        out: list[tuple[str, int]] = []
        if gone:
            # 必须物化后再删：遍历同表游标时 DELETE 是 SQLite 未定义行为
            # （可能跳过行 → window 行漏删）
            window_rows = conn.execute(
                "SELECT event_ref, ts_ms, payload FROM events "
                "WHERE type='window'").fetchall()
            for r in window_rows:
                wid = EventRemover._payload_field(r["payload"], "window_id")
                if wid in gone:
                    conn.execute("DELETE FROM events WHERE event_ref = ?",
                                 (r["event_ref"],))
                    out.append((r["event_ref"], int(r["ts_ms"])))
                    gone.discard(wid)
                    if not gone:
                        break
        return out

    def _recompute_days(self, conn: sqlite3.Connection, ts_list: list[int],
                        t0: str) -> list[dict]:
        """受影响 (时区, 本地日) 的 day_stats 重算（§3.1-5）。

        目标时区 = 在 day_summary 中存在行的**所有**时区（含非活跃时区——删除对
        stage 不可见，只重算活跃时区会让旧时区残留行永久 stale）。
        """
        if not ts_list:
            return []
        tzs = [r[0] for r in conn.execute(
            "SELECT DISTINCT timezone FROM day_summary")]
        out: list[dict] = []
        for tz in tzs:
            days_set: set[str] = set()
            for ts in ts_list:
                try:
                    days_set.add(safe_local_date(ts, tz))
                except LocalDateOutOfRange:
                    continue
            days = sorted(days_set)
            for d in self.day_stats.recompute_days(conn, tz, days, t0):
                out.append({"timezone": tz, "local_date": d})
        return out

    # ── T2（D10：异常吞掉 + vectors_pending） ─────────────────────────────

    def _remove_vectors(self, refs: list[str]) -> bool:
        if self.vec_index is None:
            log.warning("vector removal skipped: embedding not configured")
            return False
        try:
            self.vec_index.remove_refs(refs)
        except Exception:  # noqa: BLE001 — T1 已提交；残留交给 sync_changed 孤儿清扫
            log.warning("vector removal failed", exc_info=True)
            return True
        return False

    # ── 文件面（§3.3：先文件后行；失败留行给 video prune 自愈） ─────────────

    def _delete_images(self, deletable: list[sqlite3.Row]) -> tuple[list[str], list[str]]:
        """移除目标帧的 jpg + files 行（先文件后行）。

        整段容错（D9/D10 同哲学扩展）：T1 已提交，文件面任何异常不得 500——
        未完成帧计入 images_pending，交给既有 video prune 自愈（事件已删 →
        帧进入其候选；行在文件不在 → missing_ok 重删）。异常时 `with` 回滚已
        发出的 files 行删除 → 该批全部按「未完成」上报。
        """
        removed: list[str] = []
        pending: list[str] = []
        guard_skipped: list[str] = []   # 无行/越 root：跳过语义，不计 pending
        try:
            with self._conn() as conn:
                for r in deletable:
                    frame_id = r["event_ref"].rsplit(":", 1)[-1]
                    row = conn.execute(
                        "SELECT file_id, file_path FROM files WHERE file_type='video' "
                        "AND rsession_id = ? AND file_path LIKE '%/' || ? || '.jpg'",
                        (r["rsession_id"], frame_id)).fetchone()
                    if row is None:
                        guard_skipped.append(frame_id)  # 无 files 行（已被 prune 清过）
                        continue
                    p = Path(row["file_path"]).resolve()
                    if not p.is_relative_to(self.root):
                        log.warning("event remove path outside root, skipped",
                                    path=row["file_path"])
                        guard_skipped.append(frame_id)
                        continue
                    try:
                        p.unlink(missing_ok=True)
                    except OSError as e:
                        log.warning("event remove unlink failed", path=str(p),
                                    error=str(e))
                        pending.append(frame_id)
                        continue
                    conn.execute("DELETE FROM files WHERE file_id = ?",
                                 (row["file_id"],))
                    removed.append(frame_id)
                conn.commit()
        except (sqlite3.Error, OSError):
            log.warning("image cleanup aborted, left to video prune", exc_info=True)
            # 已发出的 files 行删除随 `with` 回滚；未确证的帧（含回滚帧）连同
            # 既有 unlink 失败帧一起按未完成上报，交 video prune 自愈
            done = set(pending) | set(guard_skipped)
            pending = pending + [r["event_ref"].rsplit(":", 1)[-1]
                                 for r in deletable
                                 if r["event_ref"].rsplit(":", 1)[-1] not in done]
            removed = []
        return removed, pending

    # ── 工具 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,)).fetchone() is not None

    @staticmethod
    def _payload_field(payload: str | None, key: str) -> str | None:
        try:
            data = json.loads(payload or "{}")
        except ValueError:
            return None
        return data.get(key) if isinstance(data, dict) else None

    @staticmethod
    def _result(removed: list[str], windows: list[str], images_removed: list[str],
                images_pending: list[str], days: list[dict],
                skipped: list[dict], vectors_pending: bool) -> dict:
        out = {
            "removed": removed,
            "window_events_removed": windows,
            "images_removed": images_removed,
            "images_pending": images_pending,
            "days_recomputed": days,
            "skipped": skipped,
        }
        if vectors_pending:
            out["vectors_pending"] = True
        return out
