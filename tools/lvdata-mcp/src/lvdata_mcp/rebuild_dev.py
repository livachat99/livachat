"""dev 全链重建 / 核数 CLI（recording_data_process_plan.md WS4）。

    # 0) 先看存量规模（只读；不建库）
    .venv/bin/python -m lvdata_mcp.rebuild_dev --base-dir /opt/lvdata --dry-run

    # 1) 备份 + 清空 lvdata 两库 + backfill 登记存量文件（需确认，破坏性）
    .venv/bin/python -m lvdata_mcp.rebuild_dev --base-dir /opt/lvdata --yes

    # 2) 重启 lvdata-mcp（watcher 首轮 poll 全链重建）后核数
    .venv/bin/python -m lvdata_mcp.rebuild_dev --base-dir /opt/lvdata --verify

前置：lvdata-mcp 与 chat server（旧代码）均已停止；dev 数据可弃（备份仅作
回滚保险）。backfill 依赖服务端包 `recording.backfill`（同 venv 安装）；缺失时
脚本打印等价命令。音频/视频重建会走真实 LLM——先用 --dry-run 看文件数。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

_DB_FILES = ("lvdata_inventory.db", "lvdata_inventory.db-wal",
             "lvdata_inventory.db-shm",
             "lvdata_index.db", "lvdata_index.db-wal", "lvdata_index.db-shm")


def running_service_pids() -> list[str]:
    """检测可能占用 lvdata/chat 库的进程（粗略；--force 跳过）。

    排掉自身：本脚本 argv 含 "lvdata_mcp"（python -m lvdata_mcp.rebuild_dev），
    pgrep -f 会自匹配 → 误判拒绝。
    """
    try:
        out = subprocess.run(
            ["pgrep", "-fl", "lvdata_mcp|livachat-server|livachat_server"],
            capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in out.splitlines()
            if line.strip() and "rebuild_dev" not in line]


def _scan_counts(base_dir: Path) -> dict:
    try:
        from recording.backfill import scan_data_root
    except ImportError:
        print("（服务端包未安装：跳过存量扫描。等价命令："
              f"python -m recording.backfill --db <chat_server.db> "
              f"--base-dir {base_dir} --dry-run）", file=sys.stderr)
        return {}
    rows = scan_data_root(base_dir)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    counts["total"] = len(rows)
    return counts


def _db_counts(base_dir: Path) -> dict:
    import sqlite3
    inv = base_dir / "lvdata_inventory.db"
    out: dict = {"inventory_exists": inv.is_file()}
    if inv.is_file():
        with sqlite3.connect(f"file:{inv}?mode=ro", uri=True) as conn:
            for name, sql in (
                ("events", "SELECT COUNT(*) FROM events"),
                ("files_pending", "SELECT COUNT(*) FROM files WHERE analysis_status='pending'"),
                ("files_failed", "SELECT COUNT(*) FROM files WHERE analysis_status='failed'"),
                ("day_ok", "SELECT COUNT(*) FROM day_summary WHERE status='ok'"),
                ("day_pending", "SELECT COUNT(*) FROM day_summary WHERE status!='ok'"),
            ):
                try:
                    out[name] = conn.execute(sql).fetchone()[0]
                except sqlite3.OperationalError:
                    out[name] = None
    rag = base_dir / "lvdata_index.db"
    out["index_exists"] = rag.is_file()
    if rag.is_file():
        try:
            import sqlite_vec
            with sqlite3.connect(f"file:{rag}?mode=ro", uri=True) as conn:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                out["chunks"] = conn.execute(
                    "SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
        except Exception:  # noqa: BLE001 — 核数尽力而为
            out["chunks"] = None
    return out


def verify(base_dir: Path) -> dict:
    counts = _db_counts(base_dir)
    print("== lvdata 核数 ==")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    return counts


def backup_and_wipe(base_dir: Path, *, keep_backup: bool = True) -> Path | None:
    """备份 lvdata 两库（含 wal/shm）→ 删除。返回备份目录（未备份为 None）。"""
    backup_dir = None
    existing = [base_dir / n for n in _DB_FILES if (base_dir / n).exists()]
    if existing and keep_backup:
        ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        backup_dir = base_dir / f"rebuild_backup_{ts}"
        backup_dir.mkdir()
        for p in existing:
            shutil.copy2(p, backup_dir / p.name)
        print(f"备份完成：{backup_dir}（{len(existing)} 个文件）")
    for p in existing:
        p.unlink()
    print(f"已清空 lvdata 库：{len(existing)} 个文件")
    return backup_dir


def run_backfill(base_dir: Path, chat_server_db: Path) -> int:
    try:
        from recording.backfill import main as backfill_main
    except ImportError:
        print("服务端包未安装，无法自动 backfill。请执行：\n"
              f"  .venv/bin/python -m recording.backfill --db {chat_server_db} "
              f"--base-dir {base_dir}", file=sys.stderr)
        return 2
    return backfill_main(["--db", str(chat_server_db),
                          "--base-dir", str(base_dir)])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m lvdata_mcp.rebuild_dev",
        description="dev 全链重建：备份清库 → backfill → 重启 watcher 重建 → 核数")
    p.add_argument("--base-dir", default="/opt/lvdata")
    p.add_argument("--chat-server-db", default=None,
                   help="默认 <base-dir>/chat_server.db")
    p.add_argument("--dry-run", action="store_true", help="只读：扫描规模 + 当前计数")
    p.add_argument("--verify", action="store_true", help="只读：重建后核数")
    p.add_argument("--yes", action="store_true", help="确认破坏性操作（清库）")
    p.add_argument("--force", action="store_true", help="跳过运行中进程检查")
    p.add_argument("--no-backup", action="store_true")
    args = p.parse_args(argv)

    base = Path(args.base_dir)
    cs_db = Path(args.chat_server_db) if args.chat_server_db \
        else base / "chat_server.db"

    if args.dry_run:
        print(f"== 存量扫描（{base}）==")
        for k, v in sorted(_scan_counts(base).items()):
            print(f"  {k}: {v}")
        verify(base)
        return 0

    if args.verify:
        verify(base)
        return 0

    pids = [] if args.force else running_service_pids()
    if pids:
        print("检测到可能在运行的进程（--force 跳过检查）：", file=sys.stderr)
        for line in pids:
            print(f"  {line}", file=sys.stderr)
        return 1
    if not args.yes:
        print("破坏性操作（清空 lvdata 库）需 --yes 确认；先跑 --dry-run 看规模。",
              file=sys.stderr)
        return 1

    backup_and_wipe(base, keep_backup=not args.no_backup)
    rc = run_backfill(base, cs_db)
    if rc != 0:
        return rc
    print("\n下一步：启动 lvdata-mcp（watcher 首轮 poll 全链重建，"
          "音频/视频会走真实 LLM）：\n"
          "  .venv/bin/lvdata-mcp serve\n"
          "随后核数：python -m lvdata_mcp.rebuild_dev --base-dir "
          f"{base} --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
