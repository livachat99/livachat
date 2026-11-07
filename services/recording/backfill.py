"""存量 recording_files 回填 CLI（recording_data_process.md §2.1/§2.6）。

用法（**离线模式：必须先停 livachat-server**）::

    python -m recording.backfill --db <chat_server.db> --base-dir <data root>
    python -m recording.backfill --db ... --base-dir ... --dry-run

- 单写者纪律：服务在跑时唯一写者是其 ChatStore，本 CLI 直连 SQLite ——
  必须停服务后执行（服务态请勿运行，help 已声明）；
- 幂等：`INSERT OR IGNORE`（rel_path UNIQUE），重复运行不产生新行；
- 只登记 lvdata 消费的 kind（audio/video/session/chat_*）；image 不登记
  （doc §5.B）；`.part/.tmp` 中间态跳过；
- created_at/updated_at 取文件 mtime（离线=诚实时间；lvdata 据此增量消费）。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from recording.file_registry import uuid7_to_ms
from shared.persistence.chat_store import CHAT_STORE_SCHEMA_SQL

_CHAT_KINDS = {
    "history.jsonl": "chat_history",
    "transcribe.jsonl": "chat_transcribe",
    "translate.jsonl": "chat_translate",
}

_INSERT_SQL = """
INSERT OR IGNORE INTO recording_files (device_uuid, recording_session_id, kind,
    rel_path, size_bytes, ts_ms, duration_ms, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@dataclass
class BackfillRow:
    device_uuid: str
    recording_session_id: str
    kind: str
    rel_path: str
    size_bytes: int
    ts_ms: int | None
    duration_ms: int | None
    updated_at: str


def _mtime_iso(path: Path) -> str:
    return (datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def _row_for(path: Path, base_dir: Path) -> BackfillRow | None:
    """路径 → 登记行；不消费的文件返回 None。

    布局 = <base>/<device>/<session>/...：session.json 在会话根（父目录 2 段），
    media/jsonl 在子目录（父目录 ≥3 段，parts[2] 为类型目录）。
    """
    name = path.name
    if name.endswith((".part", ".tmp")):
        return None
    parts = path.parent.relative_to(base_dir).parts
    if len(parts) < 2:
        return None
    device_uuid, recording_session_id = parts[0], parts[1]
    kind: str | None = None
    ts_ms: int | None = None
    if len(parts) == 2:
        if name == "session.json":
            kind = "session"
    else:
        top = parts[2]
        if top in ("audio", "video"):
            if top == "audio" and path.suffix != ".ogg":
                return None
            if top == "video" and path.suffix != ".jpg":
                return None
            kind = top
            ts_ms = uuid7_to_ms(path.stem)
        elif top == "chat" and name in _CHAT_KINDS:
            kind = _CHAT_KINDS[name]
    if kind is None:
        return None
    return BackfillRow(
        device_uuid=device_uuid,
        recording_session_id=recording_session_id,
        kind=kind,
        rel_path=str(path.relative_to(base_dir)),
        size_bytes=path.stat().st_size,
        ts_ms=ts_ms,
        duration_ms=None,
        updated_at=_mtime_iso(path),
    )


def scan_data_root(base_dir: Path) -> list[BackfillRow]:
    """遍历 <base>/<device>/<session>/，产出全部可登记文件行（有序、幂等）。"""
    base_dir = Path(base_dir)
    rows: list[BackfillRow] = []
    if not base_dir.is_dir():
        return rows
    for device_dir in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        if device_dir.name == "derived":
            continue  # lvdata 派生产物命名空间（audio/video 审计 JSON），非录制数据
        for sess_dir in sorted(p for p in device_dir.iterdir() if p.is_dir()):
            for path in sorted(sess_dir.rglob("*")):
                if not path.is_file():
                    continue
                row = _row_for(path, base_dir)
                if row is not None:
                    rows.append(row)
    return rows


def run_backfill(db_path: str, rows: list[BackfillRow]) -> int:
    """离线直写（服务已停；本进程 = 唯一写者）。返回新增行数。"""
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = 'recording_files'"
        ).fetchone():
            conn.executescript(CHAT_STORE_SCHEMA_SQL)
        before = conn.total_changes
        conn.executemany(_INSERT_SQL, [
            (r.device_uuid, r.recording_session_id, r.kind, r.rel_path,
             r.size_bytes, r.ts_ms, r.duration_ms, r.updated_at, r.updated_at)
            for r in rows
        ])
        return conn.total_changes - before


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m recording.backfill",
        description="存量录制文件回填 recording_files 表。"
                    "【必须先停 livachat-server】——服务运行中唯一写者是它的 "
                    "ChatStore，本 CLI 直连 SQLite，勿在服务态运行。",
    )
    parser.add_argument("--db", required=True, help="chat_server.db 路径")
    parser.add_argument("--base-dir", required=True, help="录制 data root")
    parser.add_argument("--dry-run", action="store_true",
                        help="只扫描统计，不写库")
    args = parser.parse_args(argv)

    try:
        rows = scan_data_root(Path(args.base_dir))
    except OSError as e:
        print(f"scan failed: {e}", file=sys.stderr)
        return 1

    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))

    if args.dry_run:
        print(f"dry-run: scanned={len(rows)} ({summary})")
        return 0

    inserted = run_backfill(args.db, rows)
    print(f"backfill done: scanned={len(rows)} inserted={inserted} "
          f"skipped={len(rows) - inserted} ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
