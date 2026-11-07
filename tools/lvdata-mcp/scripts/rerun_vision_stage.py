"""沙箱/排障：对指定数据根整体 force 重跑 video stage（filter + extract 全链）。

写入方单一原则（§7.5）：不得对 lvdata-mcp serve 运行中的同一数据根执行——本
脚本内置护栏，root_dir 解析为 /opt/lvdata 时拒绝运行；生产全量重跑走
REST POST /api/analysis/trigger（main.py stage 子命令刻意不开放分析类）。

用法（仓库根；沙箱 = root 覆盖到副本目录）：
    set -a && source .env && set +a
    LVDATA_MCP__DATA__ROOT_DIR=/tmp/lvdata_sandbox \
    LVDATA_MCP__WATCHER__CONCURRENCY=2 \
    .venv/bin/python3.12 tools/lvdata-mcp/scripts/rerun_vision_stage.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lvdata_mcp.analysis.vision import VisionAnalyzer
from lvdata_mcp.config import load_config


async def main() -> None:
    cfg = load_config()
    root, db = cfg.data.root_dir, cfg.data.resolved_db_path
    print(f"root={root}", flush=True)
    print(f"db={db}", flush=True)
    print(f"concurrency={cfg.watcher.concurrency}", flush=True)
    if Path(root).resolve() == Path("/opt/lvdata").resolve():
        print("拒绝：root_dir=/opt/lvdata（写入方单一原则，请用副本）",
              file=sys.stderr)
        sys.exit(2)
    if not Path(db).is_file():
        print(f"DB 不存在: {db}", file=sys.stderr)
        sys.exit(1)
    analyzer = VisionAnalyzer(cfg)
    t0 = time.perf_counter()
    res = await analyzer.process(force=True)
    print(f"done in {time.perf_counter() - t0:.0f}s: {res}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
