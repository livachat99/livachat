"""入口：`lvdata-mcp serve`（默认）/ `lvdata-mcp reindex` / `lvdata-mcp eval`。

CLI 仅保留 --config 与子命令（§4.1）；配置主体来自 lvdata_mcp.yaml
（env override 经 LVDATA_MCP__ 前缀）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import sqlite3
import sys
import time
from pathlib import Path

import structlog
import uvicorn

from . import ddl
from .config import (
    LogFileConfig,
    LvdataMcpConfig,
    _resolve_path,
    effective_summary,
    load_config,
)
from .store import DataStore
from .timeutil import now_iso


class _CliParser(argparse.ArgumentParser):
    """reset --json 的参数错误也输出统一 JSON error（review3 issue 5）。"""

    json_errors = False

    def error(self, message: str) -> None:
        if self.json_errors:
            print(json.dumps(
                {"error": {"code": "invalid_args", "message": message}},
                ensure_ascii=False, indent=2))
            self.exit(2)
        super().error(message)


def _command_from_argv(raw: list[str]) -> str | None:
    """结构性预解析真实子命令：跳过全局 --config 的值与选项。

    不能用 `"reset" in raw`：`--config reset` 这类参数值会误判命令，
    从而给非 reset 命令套用 JSON 错误格式（review4 issue 4）。
    """
    skip_value = False
    for token in raw:
        if skip_value:
            skip_value = False
            continue
        if token == "--config":
            skip_value = True
            continue
        if token.startswith("-"):
            continue
        return token
    return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw = list(argv) if argv is not None else sys.argv[1:]
    command = _command_from_argv(raw)
    json_errors = command == "reset" and "--json" in raw
    p = _CliParser(prog="lvdata-mcp", description="LvData MCP Service v2",
                   allow_abbrev=False)
    p.json_errors = json_errors
    p.add_argument("--config", type=Path, default=None,
                   help="path to lvdata_mcp.yaml (default: LVDATA_MCP_CONFIG -> "
                        "configs/<env>/lvdata_mcp.yaml -> CWD)")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("serve", allow_abbrev=False,
                   help="start the MCP server (default command)")

    reindex = sub.add_parser("reindex", allow_abbrev=False,
                             help="rebuild the full FTS index from events")
    reindex.add_argument("--vectors", action="store_true",
                         help="also rebuild the vector index "
                              "(requires an embedding provider)")

    eval_ = sub.add_parser(
        "eval", allow_abbrev=False,
        help="golden-query regression evaluation "
             "(FTS/vector/hybrid recall@10)")
    eval_.add_argument("--golden", required=True, type=Path,
                       help="JSON: [{\"query\": ..., \"expected\": [event_ref, ...]}]")
    eval_.add_argument("--topk", type=int, default=10)

    stage = sub.add_parser(
        "stage", allow_abbrev=False,
        help="run one stage over all local data (inventory / day_stats); "
             "only stages with zero LLM usage; use REST "
             "POST /api/analysis/trigger for analysis stages")
    stage.add_argument("stage_name", choices=["inventory", "day_stats"],
                       help="stage to run")
    stage.add_argument("--force", action="store_true",
                       help="bypass watermarks and run over all data (idempotent; "
                            "does not trigger analysis or reset fail_count)")

    reset = sub.add_parser(
        "reset", allow_abbrev=False,
        help="reset analysis artifacts by date "
             "(the server must be stopped by default)")
    reset.add_argument("--date", required=True,
                       help="YYYY / YYYY-MM / YYYY-MM-DD "
                            "(local date, strictly validated)")
    reset.add_argument("--dry-run", action="store_true",
                       help="print the plan only; no writes")
    reset.add_argument("--json", action="store_true", dest="json_output",
                       help="output a machine-readable plan/result")
    reset.add_argument("--yes", action="store_true",
                       help="actually perform deletions/resets")
    reset.add_argument("--allow-running-server", action="store_true",
                       help="execute even if the server is running "
                            "(accepts stale derived-data risk)")

    for parser in sub.choices.values():
        parser.json_errors = json_errors
    args = p.parse_args(argv)
    if args.command is None:
        args.command = "serve"
    return args


def _setup_logging(level: str, file: LogFileConfig | None = None,
                   stream=None) -> None:
    """structlog 收敛到 stdlib 渲染链（docs/server_logging.md §7/§8）；file 非空且 enabled →
    **仅**进程内 RotatingFileHandler（serve 运行态只写文件；目录不可用 fail-fast）；
    未激活（CLI / 测试）→ stdout 兜底（§8 D1）。"""
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        # exc_info → "exception" 字段：log.exception 的 traceback 落盘，
        # 而不是只输出 exc_info: true（2026-08-29 空音频 400 排障时
        # 根因不可见的教训）。
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    if file is not None and file.enabled:
        # serve 运行态：仅写文件（docs/server_logging.md §8——进程不再输出 stdout）
        log_dir = _resolve_path(file.dir or "logs")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                log_dir / "lvdata_mcp.log", maxBytes=file.max_bytes, backupCount=file.backup_count, encoding="utf-8"
            )
        except OSError as exc:
            raise RuntimeError(f"log dir not usable: {log_dir} ({exc})") from exc
        handler.setFormatter(formatter)
        handlers: list[logging.Handler] = [handler]
    else:
        # file 未激活（CLI / 测试）→ stdout 兜底（§8 D1），CLI 输出行为零变更；
        # reset --json 传入 stderr，避免日志污染机器可读 stdout
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(formatter)
        handlers = [handler]
    root = logging.getLogger()
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


# uvicorn 族 logger 交回 root 统一渲染（docs/server_logging.md §7.2 勘误）：默认
# LOGGING_CONFIG 会在 uvicorn.run 时经 dictConfig 清掉/接管这些 logger，且
# log_config=None 会被 run() 映射回默认（不可用）→ 显式声明 propagate=True、不带自有 handler。
# 注：本 dict 取代了 uvicorn 默认配置（故无 formatters/handlers 键）——若未来给
# uvicorn.run 显式传 use_colors，需同时补 "formatters": {"default": {}, "access": {}}
_UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "loggers": {"uvicorn": {"propagate": True}, "uvicorn.access": {"propagate": True}},
}


def _conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(cfg: LvdataMcpConfig) -> None:
    """启动建表 + timezones seed（§7.1）。"""
    with _conn(cfg.data.resolved_db_path) as conn:
        ddl.ensure_schema(conn, cfg.watcher.timezone, cfg.watcher.timezones)
        conn.commit()


def run_reindex(cfg: LvdataMcpConfig, with_vectors: bool = False) -> None:
    from . import index as index_mod
    from .vec import VecIndex, build_provider

    log = structlog.get_logger("lvdata_mcp.main")
    _ensure_schema(cfg)
    store = DataStore(cfg)
    if not store.db_ok():
        print(f"Database not initialized: {store.db_path}", file=sys.stderr)
        sys.exit(1)
    t0 = time.perf_counter()
    t0_iso = now_iso()
    with _conn(store.db_path) as conn:
        total = index_mod.rebuild_all(conn, store)
        # 水位（与 watcher force 同语义）：'' + 清会话行；同 with 块提交——
        # rebuild_all 内部先行 commit，崩在两次 commit 之间水位未推进（安全方向）
        index_mod.set_watermark(conn, "", t0_iso)
        index_mod.clear_session_watermarks(conn)
    log.info("reindex fts done", docs=total,
             elapsed_s=round(time.perf_counter() - t0, 1))
    print(f"reindex done: {total} docs")
    if with_vectors:
        provider = build_provider(cfg)
        if provider is None:
            print("Embedding provider is not configured; skipping vector rebuild",
                  file=sys.stderr)
            return
        vec = VecIndex(cfg, store, provider)
        rows = None
        with _conn(store.db_path) as conn:
            rows = conn.execute(
                "SELECT rsession_id FROM recording_sessions").fetchall()

        async def _rebuild_vec():
            total_v = 0
            for i, r in enumerate(rows, 1):
                n = await vec.rebuild_session(r["rsession_id"])
                total_v += n
                log.info("reindex vec session", session_id=r["rsession_id"],
                         chunks=n, done=f"{i}/{len(rows)}")
            return total_v

        t1 = time.perf_counter()
        total_v = asyncio.run(_rebuild_vec())
        swept = vec.sweep_orphans()
        log.info("reindex vec done", chunks=total_v, swept=swept,
                 elapsed_s=round(time.perf_counter() - t1, 1))
        print(f"vectors done: {total_v} chunks (swept {swept} orphans)")


def run_serve(cfg: LvdataMcpConfig) -> None:
    from .analysis.audio import AudioAnalyzer
    from .analysis.chatlog import ChatlogProcessor
    from .analysis.vision import VisionAnalyzer
    from .pipeline import Pipeline, PollBudget
    from .recording_watch import RecordingWatch
    from .server import create_app
    from .summary.day import DaySummary
    from .summary.day_stats import DayStats
    from .vec import VecIndex, build_provider
    from .watcher import Watcher

    store = DataStore(cfg)
    vec_index = None
    provider = build_provider(cfg)
    if provider is not None:
        vec_index = VecIndex(cfg, store, provider)
    # audio/vision 各自独立的 poll 预算（§4.2「各处理的最大文件数」；
    # watcher 每轮 reset_budget 分别重置）
    pipeline = Pipeline(
        cfg, store, RecordingWatch(cfg), vec_index,
        audio=AudioAnalyzer(
            cfg, budget=PollBudget(cfg.watcher.max_files_per_poll)),
        vision=VisionAnalyzer(
            cfg, budget=PollBudget(cfg.watcher.max_files_per_poll)),
        day_summary=DaySummary(cfg),
        chatlog=ChatlogProcessor(cfg),
        day_stats=DayStats(cfg))
    watcher = Watcher(cfg, store, pipeline)
    app = create_app(cfg, watcher=watcher, vec_index=vec_index)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port,
                log_level=cfg.server.log_level.lower(),
                log_config=_UVICORN_LOG_CONFIG)


def run_stage(cfg: LvdataMcpConfig, stage_name: str, force: bool) -> None:
    """CLI 全量触发（§stage）：只允许本地零 LLM 用量的 stage。

    用量安全保证：
    - inventory / day_stats 纯本地（SQLite），无 API 调用；force 忽略水位全量重放，
      upsert 幂等（不覆盖分析状态与处理游标，不触发分析链）；
    - 不启动 watcher、不跑分析链、不重置 files fail_count——分析类
      （audio/video/embed/day_summary）全量入口刻意不提供，走 REST
      POST /api/analysis/trigger 增量控制。
    - day_stats 全量完成后自推进游标（docs/bff_service_interfaces.md §7.3 E13），
      因此本入口也是**回填入口**（§7.2.6：先 CLI 回填，再启动 serve）。
    """
    from .recording_watch import RecordingWatch

    log = structlog.get_logger("lvdata_mcp.main")
    _ensure_schema(cfg)
    store = DataStore(cfg)
    if not store.db_ok():
        print(f"Database not initialized: {store.db_path}", file=sys.stderr)
        sys.exit(1)
    t0 = time.perf_counter()
    if stage_name == "day_stats":
        from .summary.day_stats import DayStats

        stats = DayStats(cfg).run(force=force)
        log.info("stage day_stats done", force=force, **stats,
                 elapsed_s=round(time.perf_counter() - t0, 1))
        print(f"day_stats done: days={stats['days']} timezones={stats['timezones']}")
        return
    stats = RecordingWatch(cfg).check(force=force)
    log.info("stage inventory done", force=force, **stats,
             elapsed_s=round(time.perf_counter() - t0, 1))
    print(f"inventory done: files={stats['files']} sessions={stats['sessions']} "
          f"chat_files={stats['chat_files']}")
    print("file rows are synced; chat events and indexes are produced by the "
          "watcher pipeline (use `lvdata-mcp reindex [--vectors]` to rebuild)")


def run_eval(cfg: LvdataMcpConfig, golden_path: Path, topk: int) -> None:
    import json

    from . import index as index_mod
    from .vec import VecIndex, build_provider, rrf_merge

    log = structlog.get_logger("lvdata_mcp.main")
    _ensure_schema(cfg)
    store = DataStore(cfg)
    if not store.db_ok():
        print(f"Database not initialized: {store.db_path}", file=sys.stderr)
        sys.exit(1)
    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    async def _main() -> None:
        vec_index = None
        provider = build_provider(cfg)
        if provider is not None:
            vec_index = VecIndex(cfg, store, provider)
        results = {"fts": [], "vector": [], "hybrid": []}
        for q in golden:
            query, expected = q["query"], set(q["expected"])
            with store.connect() as conn:
                fts_q = index_mod.build_fts_query(query)
                if not fts_q:  # 纯分隔符 query：空 MATCH 是 FTS5 语法错误，跳过 FTS 路
                    log.warning("eval query without searchable terms; skip fts",
                                query=query)
                fts = index_mod.search(conn, fts_q, limit=topk) if fts_q else []
            vec = []
            if vec_index is not None:
                qv = (await vec_index.provider.embed([query]))[0]
                vec = vec_index.search(qv, limit=topk)
            hybrid = rrf_merge(fts, vec)[:topk]
            rec: dict[str, float] = {}
            for mode, hits in (("fts", fts), ("vector", vec), ("hybrid", hybrid)):
                refs = {h["event_ref"] for h in hits}
                rec[mode] = len(refs & expected) / max(len(expected), 1)
                results[mode].append(rec[mode])
            log.info("eval query", query=query, topk=topk,
                     recall_fts=round(rec["fts"], 3),
                     recall_vec=round(rec["vector"], 3),
                     recall_hybrid=round(rec["hybrid"], 3))
        print(f"{'mode':<8}{'recall@' + str(topk):>10}")
        for mode, vals in results.items():
            avg = sum(vals) / len(vals) if vals else 0
            print(f"{mode:<8}{avg:>10.3f}")
        log.info("eval done", queries=len(golden), topk=topk,
                 recall_fts=round(sum(results["fts"]) / max(len(results["fts"]), 1), 3),
                 recall_vec=round(sum(results["vector"]) / max(len(results["vector"]), 1), 3),
                 recall_hybrid=round(sum(results["hybrid"]) / max(len(results["hybrid"]), 1), 3))

    asyncio.run(_main())


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    is_reset = args.command == "reset"
    try:
        cfg = load_config(args.config, validate_providers=not is_reset)
    except Exception as exc:
        if is_reset:
            if args.json_output:
                print(json.dumps(
                    {"error": {"code": "config_error", "message": str(exc)}},
                    ensure_ascii=False, indent=2))
            else:
                print(f"reset: config error: {exc}", file=sys.stderr)
            sys.exit(1)
        raise
    # 文件日志仅 serve（docs/server_logging.md §7）：CLI（reindex/eval/stage）行为零变更；
    # reset --json 的日志走 stderr，保证 stdout 是纯 JSON
    _setup_logging(
        cfg.server.log_level,
        file=cfg.server.log_file if args.command == "serve" else None,
        stream=sys.stderr if (is_reset and args.json_output) else None)
    log = structlog.get_logger("lvdata_mcp.main")
    log.info("lvdata-mcp v2 starting", **effective_summary(cfg))
    if args.command == "serve":
        _ensure_schema(cfg)
        log.info("schema ready")
        run_serve(cfg)
    elif args.command == "reset":
        from .reset import run_reset

        # plan/dry-run/未确认路径不得写库；schema 迁移由 run_reset 在实际执行前完成
        sys.exit(run_reset(cfg, date_spec=args.date, dry_run=args.dry_run,
                           yes=args.yes, as_json=args.json_output,
                           allow_running_server=args.allow_running_server))
    elif args.command == "reindex":
        run_reindex(cfg, with_vectors=args.vectors)
    elif args.command == "stage":
        run_stage(cfg, args.stage_name, args.force)
    else:
        run_eval(cfg, args.golden, args.topk)


if __name__ == "__main__":
    main()
