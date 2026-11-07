# LvData MCP Service 设计文档（定稿）

> 状态：v1.0 定稿（由 `lvdata_mcp_service-d4.md` Draft v1 与 `lvdata_mcp_service-k3.md` Design 1.0 合并）
> 目标读者：实现该服务的开发者 / Agent
> **2026-09-27 注**：本文为 v1 契约历史记录。工具集其后历经 v2 收敛——现为 **5 个只读 MCP tools**
> （`get_key_image` 已并入 `get_event`）+ 2 个 REST 运维端点；现状见
> [`lvdata_mcp_tools.md`](./lvdata_mcp_tools.md)。

## 1. 背景与目标

现有 LvData 工具链（`tools/data-analysis/`）已完成 数据盘点 → 音频转写 → 视频分析 → 多模态对齐 → 综合报告 的离线 pipeline（Phase 1-5），产物落在 SQLite（`lvdata_inventory.db`）与 `derived/` JSON 目录中。但整个链路是**手动触发的 CLI 脚本**：

- 没有常驻进程，新数据不会自动处理；
- 数据查询依赖手工 `sqlite3` / 读 JSON 文件，Agent 无法直接消费；
- 没有统一检索入口，跨 session 搜索、按事件/关键帧查询需要写临时脚本。

**Phase 6 服务化**：将上述管线包装为一个常驻 MCP 服务 `lvdata-mcp`——60s 轮询监控 `/opt/lvdata/`，发现新文件即触发增量分析；同时通过 **Streamable HTTP** 对外提供 `search` / `get_event` / `get_key_image` / `get_timeline` 等 MCP tools，供 Agent（Claude Code、Hermes、OpenClaw 等）直接查询 lvdata 的多模态会话数据。v1 关键词检索（FTS5），为后续 RAG（语义检索 / 问答）预留演进路径。

服务目标：

1. **常驻监控** `/opt/lvdata/`，每 60s 扫描一次；发现新文件自动调用现有 data tools 完成分析处理（幂等、增量）。
2. 以 **MCP (Model Context Protocol) Server** 对外暴露检索/查询 tools。
3. 传输层采用 **HTTP Streamable 模式**（不实现 stdio）。
4. 为**未来 RAG 检索**预留数据模型与扩展点（见 §9）。

## 2. 现状与数据流

### 2.1 原始数据布局

```
/opt/lvdata/
├── lvdata_inventory.db                     # SQLite 主库
├── derived/                                # 分析产物（JSON）
└── <device_uuid>/
    └── <session_id>/
        ├── session.json                    # 会话元数据（config/stats/时间戳）
        ├── chat/history.jsonl              # 用户↔助手对话（ts, role, text）
        ├── audio/*.ogg                     # 60s 分段音频（文件名 UUIDv7 = 时间戳）
        └── video/*.jpg                     # 桌面截图帧（文件名 UUIDv7 = 时间戳）
```

### 2.2 现有工具链（CLI，均已独立测试）

| 工具 | 作用 | 关键 CLI 参数 |
|------|------|--------------|
| `lvdata_inventory.py` | 扫描目录树 → 写入 `sessions/files/chat_messages/inventory_runs` 表；增量跳过（`should_skip_session` 按 mtime） | `--root-dir /opt/lvdata --db-path … --device-uuid … --full-scan` |
| `lvdata_analyze_audio.py` | 音频 → qwen omni 转写/事件/情绪 → `audio_analyses` 表 + derived JSON；内置 pending 检测与重试 | `--session-id … --concurrency N --max-attempts N --force --skip-convert` |
| `lvdata_analyze_video.py` | 截图 → 两阶段（filter 关键帧 → extract 详情）→ `video_analyses` 表 + derived JSON | `--session-id … --window-s 60 --pipeline two-step --max-frames-per-call N` |
| `lvdata_summary.py` | 合并 chat+audio+video → `timeline.json` → LLM 汇总 → 报告；stages: `align/summarize/report` | `--stage all\|align\|summarize\|report --session-id … --timezone Asia/Shanghai --force` |

### 2.3 关键产物（MCP tools 的数据来源）

**SQLite 表**（`/opt/lvdata/lvdata_inventory.db`，WAL 模式）：

- `sessions` — session_id(PK)、device_uuid、session_date、status、started_at、finalized_at、duration_ms、`analysis_status`(**pending → completed**)、`summary_status`、`video_status`、`video_summary_status`、`multi_summary_status`(pending/ok/failed)、`multi_summary_path`、各维度计数
- `files` — file_id、session_id(FK)、file_type(audio/video/chat/meta)、file_path(UNIQUE)、uuid_ts_ms（可作时间轴/水位）
- `chat_messages` — msg_id、session_id、ts_ms、role、text_content
- `audio_analyses` — session_id、segment_id、transcript、model、status、error
- `video_analyses` — session_id、frame_id、window_id、is_key_frame、key_rank、blur_level、extract_json(场景/OCR/apps/actions)、filter_status/extract_status
- `summary_runs` / `inventory_runs` — 运行历史

**derived 目录**（`/opt/lvdata/derived/<device_uuid>/<session_id>/`）：

- `audio/*.json` — 每段音频分析（ts_ms、transcript、summary、emotions）
- `video/windows/*.json` — 窗口级 caption、帧索引
- `video/frames/*.json` — 关键帧提取详情（scene/caption/apps/ocr/actions/people）
- `timeline.json` — **对齐产物**：`{session_id, sources, events: [{ts_ms, type: chat|audio|window|video, …}]}`，按 ts 排序，多模态事件合并
- `session_summary.json` / `session_summary.md` — 会话级 LLM 汇总
- `derived/_reports/report.json` — 跨 session 综合报告（`REPORT_DIR = "_reports"`）

## 3. 总体架构

### 3.1 定位与设计原则

`lvdata-mcp` 是一个**独立 Python 程序**（独立 venv / pyproject，位于 `tools/lvdata-mcp/`），做两件事：

| 平面 | 职责 |
|------|------|
| **Ingestion 平面** | 每 60s 轮询 `/opt/lvdata/`，发现新/变更 session → 按序调用现有 CLI 工具完成增量分析与索引更新 |
| **Query 平面** | 以 MCP Streamable HTTP 服务对外提供只读查询 tools，Agent 通过标准 MCP 客户端接入 |

设计原则：

- **不重写已有工具** — 四个 CLI 工具是唯一的数据处理实现，服务通过 **subprocess** 调用它们（进程隔离 = 依赖隔离 + 崩溃隔离 + 零侵入）；查询侧只读其产物（SQLite DB + `derived/` JSON），**绝不反向写工具的数据表**
- **只读查询面** — 全部 MCP tools 只读（`readOnlyHint`），数据变更只发生在 watcher 触发的 pipeline 中；`trigger_analysis` 是唯一例外，受 API key 保护且仅"入队"，不做数据变更
- **幂等与最终一致** — pipeline 每个阶段本身幂等可跳过；watcher 崩溃重启后从 DB 状态恢复，不丢 session；查询侧容忍"索引略滞后于文件"（暴露 freshness 状态而非强一致）
- **成本控制** — 音频/视频分析（按段/按窗，天然增量）随新数据即时跑；session 级 LLM 汇总（summarize / report）延迟到 session finalized 或静默超时后执行，避免活跃 session 反复花 tokens
- **单一事实来源不变** — 仍只有两类事实：源文件树 + `lvdata_inventory.db` + `derived/`。服务新增的 FTS/向量索引是**可重建的派生物**，随时可删了重建

### 3.2 进程内结构（单进程，asyncio）

```
┌──────────────────────────────── lvdata-mcp (1 process) ────────────────────────────────┐
│                                                                                        │
│  ┌─────────────────────┐        ┌──────────────────────────────────────────────────┐  │
│  │ Watcher (async task) │        │ MCP Server (uvicorn, streamable HTTP)            │  │
│  │  poll every 60s      │        │  ┌────────────┐ ┌───────────┐ ┌────────────────┐ │  │
│  │  ├─ run inventory    │        │  │ /mcp       │ │ /healthz  │ │ auth middleware│ │  │
│  │  ├─ detect dirty     │        │  │ (MCP tools)│ │ (+/readyz)│ │ (bearer token, │ │  │
│  │  │   sessions        │        │  │            │ │           │ │  optional)     │ │  │
│  │  ├─ per-session      │        │  └─────┬──────┘ └───────────┘ └────────────────┘ │  │
│  │  │   pipeline queue  │        │        │                                          │  │
│  │  └─ reindex FTS      │        │  ┌─────▼───────────────────────────────────────┐  │  │
│  └──────────┬───────────┘        │  │ Data Access Layer (store.py)                │  │  │
│             │ writes             │  │  ├─ SQLite (WAL) : sessions/files/chat/…    │  │  │
│             ▼                    │  │  ├─ derived JSON: summary/frames/timeline…  │  │  │
│  /opt/lvdata/lvdata_inventory.db │  │  ├─ FTS5 index : search_docs (v1 search)    │  │  │
│  /opt/lvdata/derived/**          │  │  └─ mtime cache: timeline / session_summary │  │  │
│  /opt/lvdata/<device>/<sess>/**  │  └─────────────────────────────────────────────┘  │  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

- **单事件循环**：watcher 是一个 `asyncio.Task`，MCP server 是 uvicorn；两者共享 `DataStore`（只读句柄 + 缓存）
- **SQLite WAL 模式**：watcher/pipeline 写、MCP 查询读并发安全（沿用各工具已采用的 SQLite 约定，服务连接显式 `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000`）
- **每 session 一把 asyncio.Lock**：防止同 session 的 pipeline 并发重入；全局信号量限制 pipeline 并发（默认 1，串行跑，省钱省心）
- **单写入者**：pipeline 任务全部经 watcher 的 asyncio.Queue 串行投递（`trigger_analysis` 也只入队），从根上避免两个分析进程同时写 SQLite

### 3.3 为什么不拆成两个进程

监控与查询共享同一份 DB/配置/日志，当前数据量级（十级 session/天）单进程足够；watcher 只做 subprocess 编排，不阻塞事件循环。未来若查询 QPS 或索引规模上来，可按 §3.2 的两平面拆分为 `lvdata-mcp-ingest` 与 `lvdata-mcp-serve` 两个 systemd unit，DB/derived 文件系统天然共享，无需改架构。

## 4. MCP Server 设计（Query 平面）

### 4.1 技术选型

| 项 | 选择 | 理由 |
|----|------|------|
| SDK | 官方 `mcp` Python SDK（`mcp.server.fastmcp.FastMCP`） | 生态标准；`FastMCP` 声明式注册 tool；内置 streamable HTTP |
| 传输 | **Streamable HTTP only**（`POST/GET /mcp`，单端点），不支持 stdio/SSE | 需求明确；SSE 已被 streamable HTTP 取代 |
| 会话模式 | `stateless_http=True` + `json_response=True` | 无状态水平扩展友好；Agent 客户端兼容性最好；如需服务端推送再改回有状态 |
| HTTP 框架 | Starlette + uvicorn（挂载 `mcp.streamable_http_app()` + 自建 `/healthz` `/readyz`） | 官方 SDK 原生支持；便于加 auth 中间件 |
| Python | ≥3.12（与 services/ 约定一致） | — |
| 配置 | pydantic-settings（env 前缀 `LVDATA_MCP_`）+ CLI flag 双通道 | 与 services/ 风格一致 |
| 日志 | structlog（JSON） | 与 services/ 一致 |

> 备选：`fastmcp` 2.x（自带 auth/中间件生态）。若后续需要 OAuth、tool 变换等高级特性可无痛迁移，tool 函数签名不变。

### 4.2 服务装配（示意）

```python
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount, Route

mcp = FastMCP("lvdata", stateless_http=True, json_response=True)

@mcp.tool(annotations={"readOnlyHint": True})
async def get_timeline(session_id: str, ...) -> dict:
    ...

app = Starlette(
    routes=[
        Route("/healthz", healthz),                       # liveness（§4.6）
        Route("/readyz", readyz),                         # readiness（§4.6）
        Mount("/", app=mcp.streamable_http_app()),        # MCP 端点 = /mcp
    ],
    middleware=[bearer_auth_if_configured],               # §4.5
)
```

### 4.3 Tools 清单（v1：8 个）

统一约定：

- 所有工具**只读**（`readOnlyHint`）；`trigger_analysis` 是唯一例外（只入队，不做数据变更，受 API key 保护）
- `session_id` 一律为规范化 UUID（小写短横线，与 `sessions.session_id` 一致）
- 时间参数用 **Unix ms**（与 DB `ts_ms` 对齐），显示/格式化交给 Agent（tools 内换算 `ts_local` 供展示）
- 返回 JSON 均带 `schema_version` 字段，便于演进

| Tool | 说明 | 主要参数 |
|------|------|---------|
| `list_sessions` | 发现 session（agent 的入口，否则拿不到 session_id） | `device_uuid?`, `date_from?`, `date_to?`, `status?`, `limit?`, `offset?` |
| `search` | 跨模态全文检索（chat 文本 / 音频转写 / 画面 caption+OCR / 摘要） | `query`, `type?`(chat/audio/video/window/summary), `session_id?`, `start_ms?`, `end_ms?`, `limit?` |
| `get_timeline` | 统一多模态时间线（timeline.json，缺则现算） | `session_id`, `start_ms?`, `end_ms?`, `types?`, `limit?`, `cursor?` |
| `get_event` | 单个事件完整详情（按 event_ref） | `event_ref` |
| `get_key_image` | 返回关键帧图片（MCP ImageContent） | `session_id`, `frame_id`, `max_width?` |
| `get_session_summary` | 会话 LLM 汇总报告直达 | `session_id` |
| `get_status` | 服务与数据新鲜度（watcher/pipeline/index 状态） | — |
| `trigger_analysis` | 手动触发 pipeline（幂等入队，唯一非只读例外） | `session_id?`, `stages?`, `force?` |

> 设计取舍：需求点名的 4 个之外，`list_sessions` / `get_status` 分别是 **discovery** 与 **可观测性** 的必需品；`get_session_summary` 提供摘要直达（避免绕 `search type=summary`）；`trigger_analysis` 让 agent 能主动催更（等下一轮 poll 是真实交互痛点）。全部纳入 v1。

### 4.4 Tool 详细规约

#### 4.4.1 `list_sessions`

```json
// 输入
{"device_uuid": "b53985c7-...", "date_from": "2026-07-01", "date_to": "2026-08-01",
 "status": "finalized", "limit": 50}
// 输出
{"sessions": [
  {"session_id": "019fabe0-...", "device_uuid": "b53985c7-...", "session_date": "2026-07-29",
   "status": "active", "started_at": "...", "duration_min": 45,
   "counts": {"chat": 8, "audio_segments": 13, "video_frames": 23},
   "derived": {"audio": "ok", "video": "ok", "summary": "pending"},
   "has_summary": true}
], "total": 3}
```

数据源：`sessions` 表 + 各状态列。`derived` 字段让 agent 知道哪些维度的数据可用——缺失如实标注，不编造。

#### 4.4.2 `search`

```json
// 输入
{"query": "响应慢", "type": "chat", "session_id": null,
 "start_ms": null, "end_ms": null, "limit": 20}
// 输出
{"hits": [
  {"event_ref": "019f92f2-...:chat:01J9...",
   "session_id": "019f92f2-...", "session_date": "2026-07-24",
   "type": "chat", "ts_ms": 1784876900000, "ts_local": "2026-07-24 15:08:20",
   "snippet": "……为什么这么<b>慢</b>……", "score": 1.83}
], "query": "响应慢", "searched_types": ["chat","audio","video","window","summary"]}
```

- v1 用 **SQLite FTS5**（`search_docs` 虚表，§6.2），`bm25` 排序；`unicode61` tokenizer 对中文效果差，采用 **CJK 文本预切分为空格分隔的 bigram**（索引与查询两侧同一函数处理，业界常用低成本方案；v2 由向量检索补足语义召回）
- `type` ∈ `chat | audio | video | window | summary`，缺省全类型
- snippet 由 FTS5 `snippet()` 生成，含 `<b>` 高亮标签——**agent 侧需剥离或提供纯文本开关**（`plain_text: true` 参数，v1 可选实现）
- `event_ref` 可直接喂给 `get_event` 取全文
- 空 query / 纯停用词 → 返回参数错误（MCP standard error），不返回全表

#### 4.4.3 `get_timeline`

```json
// 输入
{"session_id": "019fabe0-...", "start_ms": null, "end_ms": null,
 "types": ["chat","audio","video","window"], "limit": 200, "cursor": null}
// 输出
{"session_id": "019fabe0-...", "timezone": "Asia/Shanghai",
 "time_span": {"started_at": "...", "ended_at": "..."},
 "events": [ /* 与 timeline.json 的事件 schema 完全一致（见 lvdata_summary_analysis.md） */ ],
 "next_cursor": "eyJ0c19tcyI6MTc4NDg3NjkwMDAwMH0=",
 "source": {"built_at": "...", "stale": false}}
```

- 优先读 `derived/<device>/<session>/timeline.json`；**不存在或过期**（源数据 mtime 更新）时按 `lvdata_summary.py` 的对齐规则现算（复用其纯函数 `build_timeline(conn, session, root_dir, timezone)`，见 §7.2），现算结果**不回写** derived（写权限只归 pipeline）
- 分页：`limit`（默认 200，上限 1000）+ 不透明 cursor（编码最后一条事件的 `ts_ms`）；按 `(ts_ms, type优先级)` 稳定排序，与 align 规则一致
- `stale: true` 表示该 session 正有新数据在 pipeline 中，时间线稍后会更全

#### 4.4.4 `get_event`

```json
// 输入
{"event_ref": "019fabe0-3b0f-7947-8000-1e567def787d:video:019fabe5-b00e-7b8d-8000-8fc29ccdab9a"}
// 输出（video 示例；chat/audio/window 类似，字段=对应 derived JSON 全量）
{"event_ref": "...", "type": "video", "session_id": "...", "ts_ms": 1784877784590,
 "ts_local": "2026-07-29 11:23:04",
 "detail": {"scene": "browser", "caption": "...", "apps": [...], "ocr": [...], "actions": [...]},
 "related": {"window_id": "...", "image_available": true}}
```

- **event_ref = `{session_id}:{type}:{source_id}`**：`source_id` 分别是 `msg_id`(chat) / `segment_id`(audio) / `window_id`(window) / `frame_id`(video) / `session`(summary)。它是 search/get_timeline 输出中的稳定句柄，也是未来 RAG chunk 的主键（§9）
- 解析规则：chat → `chat_messages` 行；audio → `derived/.../audio/<seg>.json`；window/video → `derived/.../video/...`；summary → `session_summary.json`
- `image_available: true` 提示 agent 可继续调 `get_key_image` 取图（工具间自描述串联，agent 不用猜）
- `session_id` 不存在 → `not_found` + 相近候选（`similar` 字段，便于 agent 自我纠正）

#### 4.4.5 `get_key_image`

```json
// 输入
{"session_id": "019fabe0-...", "frame_id": "019fabe5-b00e-7b8d-8000-8fc29ccdab9a", "max_width": 1280}
// 输出 = MCP content blocks:
[
  {"type": "image", "data": "<base64 jpeg>", "mimeType": "image/jpeg"},
  {"type": "text",  "text": "{\"frame_id\":\"...\",\"caption\":\"...\",\"is_key_frame\":true,\"width\":1280,\"height\":800}"}
]
```

- 返回 **image block + text 元数据** 双块：多模态 agent 直接看图；纯文本 agent 也能拿到 caption 降级处理
- 仅服务**关键帧**（`derived/.../video/frames/<frame_id>.json` 存在即关键帧）；非关键帧请求返回明确错误并附该 window 的关键帧列表（引导 agent 换帧）
- `max_width`（可选，默认原图；当前帧 20-55KB 可直接回传）需要缩放时引入 Pillow（可选依赖）；缩放结果按 `(frame_id, max_width)` 做磁盘缓存（`<root>/derived/_cache/thumbs/`）
- 安全：路径校验必须落在 `<root_dir>/<device>/<session>/video/` 内（防 `../` 穿越；frame_id 形如 UUIDv7，先做格式校验）；text block **不含服务器绝对路径**（如需调试定位，仅可信网络部署可经配置开关暴露）

#### 4.4.6 `get_session_summary`

```json
// 输入
{"session_id": "019fabe0-..."}
// 输出
{"session_id": "...", "session_date": "...", "schema_version": 1,
 "summary": "...", "topics": [...], "notable_events": [...],
 "timeline_ref": "019fabe0-...:session:019fabe0-...",
 "status": "ok"}
// 未生成时
{"status": "not_ready", "pending_stages": ["summarize"],
 "hint": "调用 trigger_analysis(session_id=..., stages=[\"summary\"])"}
```

数据源：`derived/.../session_summary.json`（缺失 → `not_ready` + 指引，不编造）。

#### 4.4.7 `get_status`

```json
// 输出
{"service": {"uptime_s": 3600, "version": "0.1.0"},
 "watcher": {"last_poll_at": "...", "poll_interval_s": 60,
             "dirty_sessions": ["019fabe0-..."],
             "pipeline_running": {"session_id": "...", "stage": "video", "since": "..."}},
 "data": {"sessions_total": 3, "summaries_ok": 2,
          "index": {"docs": 512, "last_rebuild_at": "...", "lag_sessions": ["019fabe0-..."]}},
 "recent_runs": [{"stage": "audio", "session_id": "...", "status": "ok", "finished_at": "..."}],
 "errors": [{"session_id": "...", "stage": "summary", "error": "...", "retry_at": "..."}]}
```

agent 可用它判断"数据有多新"（`lag_sessions` 非空 → 提示结果可能滞后），运维可用它做告警采集。

#### 4.4.8 `trigger_analysis`

```json
// 输入
{"session_id": "019fabe0-...", "stages": ["audio", "video", "summary"], "force": false}
// 输出
{"accepted": true, "queued_sessions": ["019fabe0-..."], "reason": "enqueued"}
```

- **幂等入队**：只向 pipeline 队列投递（合并同 session 重复投递），与 watcher 的调度同源，不做任何数据变更
- `session_id` 缺省 = 全部 pending session；`stages` 缺省 = 自动（按 §5 触发条件）；`force: true` 透传 CLI `--force`（重置 skip 状态，慎用）
- 鉴权：绑定非回环地址时强制 bearer token（§4.5）；每 IP 限流与 pipeline 并发上限共用（§4.5）
- 执行结果统一落 `mcp_pipeline_runs`，`get_status.recent_runs` 可见

### 4.5 认证与网络安全

| 场景 | 配置 |
|------|------|
| 本机开发（默认） | bind `127.0.0.1:8090`，无 auth |
| 局域网/服务器 | bind `0.0.0.0` 时**强制** `LVDATA_MCP_TOKEN`（bearer），未配置则拒绝启动并报错 |
| 生产 | 前面挂 Caddy/nginx 终结 TLS；或后续接 fastmcp 2.x 的 OAuth |

- 中间件校验 `Authorization: Bearer <token>`，仅作用于 `/mcp`；`/healthz` `/readyz` 不鉴权（供探活）
- 全部 tools 只读，天然缩小攻击面；路径穿越在 `get_key_image` 单独防御（§4.4.5）
- MCP tools 参数只接受 `session_id`/`frame_id`/`event_ref` 等**标识符**，绝不接受文件路径；路径拼接统一在 `store.py` 内做规范化校验（`os.path.commonpath` 前缀检查）
- 简单限流：每 IP 并发请求数限制（默认 32），防止 agent 失控打爆 LLM 缓存重建路径

### 4.6 健康检查与生命周期

- `GET /healthz` → `{"status":"ok"}`（liveness）
- `GET /readyz` → watcher 首轮 poll 完成 + DB 可读后返回 200，否则 503（systemd/容器探针用）
- 优雅退出：`SIGTERM` → 停 watcher（等当前 subprocess 阶段结束或超时强杀）→ 关 uvicorn；子进程组独立 `process_group`，服务退出时子进程被收割，不留孤儿

### 4.7 配置

CLI flag 与 env 双通道（pydantic-settings，env 前缀 `LVDATA_MCP_`）：

| 配置 | 默认 | 说明 |
|------|------|------|
| `--root-dir` | `/opt/lvdata` | 数据根（与四个 CLI 工具一致） |
| `--db-path` | `/opt/lvdata/lvdata_inventory.db` | inventory DB（读 + 自有表写） |
| `--host` / `--port` | `127.0.0.1` / `8090` | MCP 服务监听 |
| `--token` | (none) | bearer token；绑非回环地址时必填 |
| `--poll-interval-s` | `60` | 监控轮询间隔 |
| `--summarize-quiet-s` | `600` | session 静默多久后触发 summarize |
| `--timezone` | `Asia/Shanghai` | 输出时间展示时区（与 summary 工具一致） |
| `--pipeline-concurrency` | `1` | 同时运行的 pipeline 数（LLM 成本控制） |
| `--tools-dir` | `tools/data-analysis`（仓库内自动定位） | 四个 CLI 所在目录 |
| `--max-ip-conn` | `32` | 每 IP 并发请求上限 |
| `--log-level` | `INFO` | — |

### 4.8 MCP Resources（可选，Phase 4）

资源适合暴露"稳定地址、可能带上下文"的内容，与 tools 互补：

- `lvdata://sessions/{session_id}/timeline` → timeline.json
- `lvdata://sessions/{session_id}/summary` → session_summary.json
- `lvdata://sessions/{session_id}/key-image/{index}` → 关键帧图
- `lvdata://stats/overview` → 全量统计

实现成本低（FastMCP 声明式 `@server.resource`），但 tools 已覆盖主要用例，作为可选项。

## 5. Watcher 设计（Ingestion 平面）

### 5.1 轮询主循环

```
every poll_interval_s (default 60s):
  1. run_pipeline_stage("inventory")            # 全量增量扫描，幂等，秒级完成
  2. dirty = detect_dirty_sessions()            # §5.2
  3. for session in dirty:
       enqueue(session, reason)                  # 去重入队，带 debounce（§5.3）
  4. worker 按序处理队列:
       run_pipeline_stage("audio",  session_id)  # 增量：跳过已分析 segment
       run_pipeline_stage("video",  session_id)  # 增量：跳过已分析 window/frame
       rebuild_session_index(session_id)         # §6.3：FTS 索引按 session 重建
       if session 静默超时 或 finalized:         # §5.3
         invalidate_summary(session_id)          # 重置 multi_summary_status='pending'
         run_pipeline_stage("summary", session_id)   # align + summarize
         rebuild_session_index(session_id)       # 把 session_summary 也纳入索引
       if 全部 session 汇总完成 且 距上次 report 有新数据:
         run_pipeline_stage("report")            # 跨 session 报告（低频）
```

- **pipeline 通过 subprocess 调用 CLI**：`sys.executable tools/data-analysis/lvdata_analyze_audio.py --session-id <id> ...`，继承 venv 与环境变量（`DASHSCOPE_API_KEY` 等）；每阶段独立超时（默认 30min，进程级兜底 kill），输出捕获进结构化日志（按 run_id 关联）
- **崩溃恢复**：watcher 不依赖内存态——脏判断全部来自 DB（`files` 行数/mtime 水位 + 各 `*_status` 列），重启后第一轮 poll 自然找回未完成工作（等价于 d4 的"启动补跑未完成队列"，无需额外代码）
- **失败退避**：单 session 单阶段失败 → 记录 `mcp_pipeline_runs`（§5.5），指数退避重试（1min/5min/15min），3 次后标 `failed` 并暂停该 session 该阶段（可在 `get_status` 中看到，`trigger_analysis(force=true)` 或人工 `--force` 恢复）

**增量与幂等要点**（现有工具自带，Watcher 只做编排不重复造轮子）：

- **inventory**：全量跑（无 `--session-id`），`should_skip_session` 按 mtime 跳过未变化 session，成本 ≈ 目录 walk；未来 session 量大时给 inventory 增加 `--session-id` 过滤（一行改动）
- **audio/video**：工具内部有 pending 检测（`analysis_status`/文件存在性），重复调用天然幂等；Watcher 只对 dirty session 定向调用（`--session-id`）
- **summary**：只在 `status == "finalized"` 或静默超时后跑；align 是本地合并（零成本），summarize 靠 `multi_summary_status='ok'` skip——有新数据时 watcher 先重置为 `'pending'` 再调工具（§5.4）

### 5.2 脏 session 检测

服务自有状态表（不动工具的表）：

```sql
CREATE TABLE IF NOT EXISTS mcp_watch_state (
    session_id        TEXT PRIMARY KEY,
    last_file_count   INTEGER NOT NULL DEFAULT 0,
    last_file_max_ms  INTEGER NOT NULL DEFAULT 0,   -- files.uuid_ts_ms 最大值水位
    last_data_change_at TEXT,                        -- 最近一次检测到新数据的时刻
    summarized_at     TEXT,                          -- 最近一次 summarize 完成时刻
    index_version     INTEGER NOT NULL DEFAULT 0     -- FTS 索引版本（重建用）
);
```

每轮 poll：

```sql
-- 与 files 表对账：文件数变多 / 出现了更大的 uuid_ts_ms → dirty
SELECT s.session_id, COUNT(f.file_id) AS n, MAX(f.uuid_ts_ms) AS max_ms
FROM sessions s LEFT JOIN files f ON f.session_id = s.session_id
GROUP BY s.session_id;
-- 与 mcp_watch_state 的水位比较，不同的即为 dirty（含新 session）
```

同时把以下情况也标 dirty（兜底，防止对账遗漏）：

- `sessions.analysis_status != 'completed'` 且有待分析音频/视频
- `multi_summary_status = 'failed'`（可被退避策略重试）
- 磁盘上出现 DB 中不存在的 session 目录（inventory 刚入库的新行）

> **边界声明**：水位对账检测"新增"不检测"删除"（文件数只增不减）。若未来出现"删除文件后需重分析"的场景，需补删除检测或文档化说明不支持（可用 `trigger_analysis(force=true)` 人工处理）。

### 5.3 Debounce（写入静默判定）

录音中的 session 会持续落文件（音频 ~60s/段，截图 ~1fps）。策略：

| 阶段 | 触发时机 | 理由 |
|------|---------|------|
| inventory | 每轮 poll 必跑 | 纯本地、幂等、秒级 |
| audio / video 分析 | session dirty 即入队 | 按段/按窗增量，新数据即时处理 → 派生数据近实时可查 |
| summarize / align | **静默超时**（`summarize_quiet_s`，默认 600s 无新文件）**或** `status='finalized'` | session 级 LLM 调用，避免活跃期反复重跑 |
| report | 所有选中 session 汇总完成且有新摘要 | 全局一次 LLM 调用，低频 |

即：活跃 session 的**段级/窗级分析是近实时的**（agent 查询能看到几秒前的转写与画面分析），而**摘要是最终一致的**（session 结束后 ~10 分钟内补齐）。

### 5.4 Summary 失效

`lvdata_summary.py` 按 `multi_summary_status='ok'` 跳过。当 session 在 summarize 之后又有新数据（`last_data_change_at > summarized_at`），watcher 先把该 session 的 `multi_summary_status` 重置为 `'pending'` 再调用工具——复用工具自身幂等逻辑，不引入 `--force` 的不可控重刷。

### 5.5 运行记录

```sql
CREATE TABLE IF NOT EXISTS mcp_pipeline_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,                    -- NULL = 全局（inventory / report）
    stage        TEXT NOT NULL,           -- inventory | audio | video | summary | report | reindex
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',  -- running | ok | failed
    exit_code    INTEGER,
    error        TEXT
);
```

`get_status` tool 直接读这张表 + `mcp_watch_state` 对外呈现。

## 6. 数据访问层（store.py / index.py）

### 6.1 数据源与读取方式

| 数据 | 来源 | 读取 |
|------|------|------|
| session 元数据/计数 | DB `sessions` | SQL |
| chat 消息 | DB `chat_messages` | SQL |
| 音频段分析 | `derived/<dev>/<sess>/audio/<seg>.json` | 文件 + mtime 缓存 |
| 视频窗/帧分析 | `derived/.../video/{windows,frames}/*.json` | 文件 + mtime 缓存 |
| 统一时间线 | `derived/.../timeline.json`，缺则现算 | 文件 / 纯函数 |
| session 摘要 | `derived/.../session_summary.json` | 文件 |
| 跨 session 报告 | `derived/_reports/report.json` | 文件 |
| 全文检索 | DB `search_docs`（FTS5，本服务自建） | SQL |

JSON 文件缓存：`{path: (mtime_ns, parsed)}`，命中即免解析；session 量级下内存占用可忽略。

### 6.2 FTS 索引表（本服务自有，可整体重建）

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS search_docs USING fts5(
    text,                                        -- 检索文本（CJK 已 bigram 预处理）
    event_ref UNINDEXED, session_id UNINDEXED,
    device_uuid UNINDEXED, type UNINDEXED,
    ts_ms UNINDEXED
);
```

每类文档的索引文本拼装：

| type | text 来源 |
|------|----------|
| `chat` | `text_content` |
| `audio` | `transcript` + `summary` + events/emotions 描述 |
| `video` | `caption` + `scene` + apps(name+title) + ocr.text + actions |
| `window` | `window_caption` |
| `summary` | `session_summary` + topics + notable_events（叙事字段） |

### 6.3 索引维护

- **按 session 重建**：`DELETE FROM search_docs WHERE session_id=?; INSERT ...` —— 幂等、与 watcher 的 session 粒度调度天然对齐（§5.1）
- 全量重建：`lvdata-mcp reindex` CLI 子命令（运维/升级 tokenizer 后用）
- 滞后可见：`mcp_watch_state.index_version` 与数据水位比较 → `get_status.data.index.lag_sessions`

## 7. 实现要点

### 7.1 目录结构

```
tools/lvdata-mcp/
  pyproject.toml                  # 独立包：lvdata-mcp（依赖见 §10）
  README.md                       # 运行手册（quickstart / systemd / 客户端配置示例）
  src/lvdata_mcp/
    __init__.py
    main.py                       # 入口：argparse → asyncio main（watcher task + uvicorn）
    config.py                     # pydantic-settings
    watcher.py                    # 轮询、脏检测、debounce、退避
    pipeline.py                   # subprocess 编排（4 个 CLI 工具 + reindex）
    store.py                      # DB/derived JSON 只读访问 + 缓存 + 路径规范化
    index.py                      # FTS5 建索引、CJK bigram、按 session 重建
    schemas.py                    # tool 输入输出 pydantic 模型
    tools.py                      # FastMCP tool 定义（8 个 tools）
    server.py                     # Starlette 装配：/mcp + /healthz + /readyz + auth
    timeutil.py                   # UUIDv7→ms、时区换算（与 tools 共享语义）
  tests/
    test_index.py                 # bigram、按 session 重建幂等
    test_tools.py                 # 8 个 tool 的输入输出（用测试夹具 session）
    test_watcher.py               # 脏检测、debounce、summary 失效逻辑
```

### 7.2 与 `tools/data-analysis/` 的代码关系

- **pipeline 一律 subprocess**（不重写、不 import 有副作用的 CLI 主流程）
- 仅**纯函数可 import 复用**：`lvdata_summary.py` 的对齐逻辑（`build_timeline(conn, session, root_dir, timezone)`，纯 DB+文件读取，供 `get_timeline` 现算路径）、UUIDv7 解析。注意该模块顶层 `import openai`（summary.py:46），import 会引入 openai 依赖——若 import 路径不稳（CLI 脚本非包），在 `lvdata_mcp/timeutil.py` 内做最小重实现（UUIDv7 解析仅 5 行）；对齐逻辑优先 import，失败降级为"只读 timeline.json，缺则返回 404 类错误并提示等待 pipeline"
- 服务自有表（`mcp_watch_state` / `mcp_pipeline_runs` / `search_docs`）沿用各工具的 `CREATE TABLE IF NOT EXISTS` 增量约定，不动既有表结构

### 7.3 测试策略

| 层 | 内容 |
|----|------|
| 单测 | 索引分词/重建、event_ref 解析、tool 参数校验、watcher 状态机（fake DB + tmpfs 数据树） |
| 集成 | 复制一份小样本 lvdata 到 tmp dir → 起服务 → 用 `mcp` SDK client 走 streamable HTTP 调 8 个 tools 端到端（夹具复用 `tools/data-analysis/tests/` 的样例数据） |
| 回归 | 真实 `/opt/lvdata` 只读挂载冒烟（CI 可选） |

### 7.4 客户端接入示例

```jsonc
// Claude Code / Hermes 等 agent 的 MCP 配置
{
  "mcpServers": {
    "lvdata": {
      "type": "http",
      "url": "http://127.0.0.1:8090/mcp",
      "headers": {"Authorization": "Bearer ${LVDATA_MCP_TOKEN}"}
    }
  }
}
```

Agent 典型工作流：`list_sessions` → `get_timeline`（或 `search` 定位关键词）→ `get_event` 取详情 → `get_key_image` 看图佐证。每个 tool 输出都带下一步可用的句柄（`event_ref` / `image_available` / `stale` / `not_ready`+hint），无需 agent 先验知识。

## 8. 错误处理

| 场景 | 行为 |
|------|------|
| DB 不存在 / 未初始化 | tools 返回可读错误 `database_not_initialized`，并提示运行 inventory（Watcher 也会自动建表） |
| session_id / event_ref 不存在 | `not_found` + 相近候选（`similar` 字段，便于 Agent 自我纠正） |
| 分析未完成 | `not_ready` + `{pending_stages}`，指引 `trigger_analysis` |
| timeline.json 缺失 | 实时回退合并（`build_timeline` 纯函数现算），不阻塞查询 |
| subprocess 失败 | `mcp_pipeline_runs` 记录；指数退避重试（1/5/15min）；3 次后标 `failed` 暂停 + 告警日志 |
| 工具参数非法 | MCP 层校验 + 描述里写清楚 |
| 空 query / 纯停用词 | 参数错误（MCP standard error），不返回全表 |
| 非关键帧请求 | 明确错误 + 附该 window 的关键帧列表 |

## 9. RAG 演进路线

v1 的关键词检索是**刻意保守的起点**——先让 agent "能查、能看、能追溯"，再逐层加语义能力。演进按收益/成本排序。

**设计原则（写入实现约定）**：

1. **检索结果永不脱离时间轴**——所有命中必须带 `ts_ms` 和 `session_id`。
2. **结构化描述先于原始媒体**——向量索引走 JSON 字段（caption/OCR/transcript），原始图像只在需要"看"时经 `get_key_image` 取。
3. **增量 > 全量**——所有索引/分析操作都按指纹增量执行。
4. **查询层与索引层解耦**——`store.py` 的查询接口签名固定（返回统一事件模型），未来把 FTS5 换向量库不改 tools。

### 9.1 R2：语义检索（`semantic_search`）

| 决策点 | 建议 | 理由 |
|--------|------|------|
| Chunk 粒度 | **1 event = 1 chunk**（audio 段 / video 帧 / window / chat 连续 3-5 轮合并为 1 chunk / session summary） | event_ref 已是稳定主键，检索结果可直接 `get_event` 追溯原文 + `get_key_image` 取证，引用链免费获得 |

chunk 单元明细：

| 单元 | 来源 | 粒度 | 元数据 |
|------|------|------|--------|
| chat 消息 | chat_messages | 1 条/段（超长按 500 字切） | session_id, ts_ms, role |
| 音频事件 | audio JSON | 1 段/条（~60s，转写天然成块） | session_id, ts_ms, segment_id |
| 关键帧 | video frames JSON | 1 帧/条（caption+OCR+actions 拼接） | session_id, ts_ms, frame_id, key_rank |
| 窗口事件 | video windows JSON | 1 窗口/条（caption） | session_id, ts_ms, window_id |
| 会话摘要 | session_summary.json | 1 条/会话 | session_id, date |

| Embedding 模型 | DashScope `text-embedding-v4`（多语言，1024 维，与现有百炼 Key 复用） | 中英混用数据；成本 ~¥0.0005/千 chunk，全量重建也忽略不计 |
| 向量存储 | **sqlite-vec**（独立 `lvdata_index.db`，重建时不锁主库） | 沿用"SQLite 优先"哲学：零新组件；十万 chunk 级暴力扫描毫秒级。数据量超百万再迁 Qdrant/pgvector（项目已有 PostgreSQL 基建） |
| 索引时机 | watcher 在 session pipeline 完成后增量 embed 脏 session（`DELETE+INSERT` 按 session） | 与 FTS 重建同一调度点，`mcp_watch_state.index_version` 统一追踪 |
| Hybrid 融合 | FTS bm25 + 向量 cosine，**RRF**（k=60）融合，无新依赖 | 关键词精查（OCR 文本、专有名词）与语义泛查互补 |
| Rerank（可选） | top-50 过 `qwen3-reranker` 重排取 top-10 | 百炼同 key；仅在 hybrid 结果噪声大时启用 |

```json
// semantic_search 输出与 search 同构，多一个 score 来源标注
{"hits": [{"event_ref": "...", "snippet": "...", "score": 0.83, "via": ["fts","vector"]}]}
```

### 9.2 R3：问答与证据链（`ask`）

- `ask(question, session_id?)`：检索 top-K chunk → 组装上下文（每条带 `event_ref` 与本地时间）→ LLM 生成答案 + **强制引用 event_ref** → 返回 `{answer, citations: [{event_ref, quote}]}`
- 关键约束：**答案每个事实必须挂引用**，无引用不下结论（沿用 report 工具"无证据不下结论"的 prompt 纪律）；agent 可对每个 citation 调 `get_event` / `get_key_image` 给用户出示原文与截图——这是 lvdata 场景相比普通文档 RAG 的独特优势：**多模态证据链闭环**
- 多模态 embedding（`multimodal-embedding-v1` 直接 embed 截图）作为 video chunk 的补充信号，解决"caption 没写到但图里有"的召回盲区；v3 再评估，v2 不阻塞

### 9.3 R4：记忆与用户画像（与 LivaChat 主产品的交汇点）

- lvdata 本质是**用户长期行为的沉淀**：在 report.json 之上蒸馏 per-device/per-user 的 `memory.md`（偏好、常用应用、活跃时段、反复出现的问题），作为新 tool `get_user_profile` 暴露
- 这与 `services/` 规划中的 `memory/`、`user-profile/` 服务天然衔接——lvdata-mcp 可作为那两个服务的数据供给方，或反向被其消费；建议届时以 MCP tool 契约为准对接，避免直接读库耦合
- 主动式场景：结合 watcher 的实时性，"session 刚结束就自动产出摘要 + 更新记忆"，agent 在下次对话开场即拥有昨日上下文

### 9.4 工程配套（与模型选型同等重要）

- **评估先于调优**：建 20-50 条 golden query（"用户哪天抱怨过响应慢？""7/29 下午在用什么应用？"），回归比较 FTS / vector / hybrid / rerank 各档的 recall@10——没有评估集的 RAG 改动都是玄学
- **新鲜度契约**：embedding 与 FTS 共用 `index_version`，agent 经 `get_status` 可感知索引滞后；不要出现"文件已分析但向量没跟上"的静默不一致
- **成本护栏**：embedding/ask 全部走百炼按量计费，`mcp_pipeline_runs` 记录 token 用量（沿用各工具 `usage_*` 字段约定），月度成本可审计
- **隐私红线**：embedding/问答会把转写与画面描述上传云端，与 Phase 2/3 同一隐私约定；如需全离线，v2 可换本地 bge-m3 + 本地向量库，`--offline` 开关在架构上预留（embedding provider 抽象层）

### 9.5 路线图汇总

| 版本 | 能力 | 新组件 | 触发方式 |
|------|------|--------|---------|
| v1（本设计） | FTS 关键词检索 + 8 tools + watcher | 无（SQLite FTS5） | — |
| v2 | hybrid 语义检索 `semantic_search` | sqlite-vec + text-embedding-v4 | watcher 增量 embed |
| v3 | 问答 `ask` + 证据链 + rerank | qwen3-reranker（可选） | tool 调用时 |
| v4 | 用户记忆 `get_user_profile` | 与 memory/user-profile 服务对接 | summarize 后蒸馏 |

## 10. 分阶段实施计划

> 阶段划分遵循 d4 的交付排序：**先 Query 平面、后 Ingestion 平面**——查询侧可用现有 CLI 已产出的数据先行验证，Watcher 是 Phase 2 才有的自动化。

### Phase 1 — Server 骨架 + 查询 tools（核心交付）

- [x] `tools/lvdata-mcp/` 骨架、pyproject、config、store 只读层
- [x] FastMCP `transport="http"` 跑通：`list_sessions` / `get_timeline` / `get_event` / `get_key_image` / `get_session_summary` / `get_status` 全绿
- [x] `search` v1（FTS5 + CJK bigram + BM25 + snippet）
- [x] 测试：FastMCP `ClientSession` 集成测试（复用 `tools/data-analysis/tests/` 样例数据）
- [x] `/healthz` `/readyz` + auth 中间件

### Phase 2 — Watcher + Pipeline 编排

- [x] `watcher.py` 60s 轮询 + `mcp_watch_state` 持久化 + 水位对账脏检测
- [x] `pipeline.py` subprocess 编排（inventory→audio→video→summary）+ `mcp_pipeline_runs` + 退避重试
- [x] `trigger_analysis` / debounce / summary 失效 / 崩溃恢复（首轮 poll 补跑）
- [x] systemd unit / Dockerfile（`python:3.12-slim`，注意 ffmpeg/opus 依赖；或复用宿主机路径）

### Phase 3 — RAG 向量检索

- [x] sqlite-vec 集成、事件级 chunk、增量索引（独立 `lvdata_index.db`）
- [x] embedding 接入（text-embedding-v4 / bge-m3）、`search` 混合检索 + RRF
- [x] golden query 评估集（20-50 条）+ 各档 recall@10 回归
- [x] 时间上下文扩展（命中 → 邻域事件 ±2min）

### Phase 4 — 增强（按需）

- [ ] `ask` 问答 + 证据链、LLM rerank、多模态检索（图像向量）
- [ ] MCP Resources 补齐、鉴权强化（mTLS/OAuth）
- [ ] 指标：检索耗时、索引规模、pipeline 成功率（prometheus 与 services/ 一致）

## 11. 决策记录与风险

| 决策 | 理由 | 风险/备选 |
|------|------|----------|
| subprocess 编排而非 import 工具 | 隔离依赖、复用成熟 CLI、工具独立演进 | 进程开销可忽略（低频任务）；未来若重构为库可平滑切换 |
| 只读 tools + 写入仅限 pipeline | 避免锁竞争、保证查询稳定 | `trigger_analysis` 是唯一例外（只入队，受 API key 保护） |
| SQLite FTS5 起步、向量后置 | 零依赖快速落地 80% 需求；时间轴先于语义 | 语义检索缺失期间 Agent 可用 `get_timeline`+`get_event` 兜底 |
| 事件为最小检索单元 | 保持时间语义、天然 chunk 边界 | 事件过长（单段转写）需切块，在 chunk 字段留 `offset` |
| 单 worker 串行 pipeline | 当前数据量小，正确性优先 | 数据量增长后按 session 并行（Queue + per-session Lock 已预留） |
| CJK bigram 预切分 | unicode61 对中文检索无效，低成本显著提升 | 大词表膨胀；v2 向量检索兜底语义 |
| stateless HTTP + json_response | 无状态水平扩展友好、客户端兼容性最好 | 服务端推送/通知需改回有状态（§12 Q3） |
| 鉴权绑定非回环时强制 | 安全默认，且不牺牲本机开发体验 | — |
| 水位对账检测新增、不检测删除 | 实现简单、覆盖主路径 | 删除重分析场景需显式处理（§5.2 边界声明） |

**开放问题**（实现前确认）：

1. `/opt/lvdata` 的部署形态：本机目录？挂载卷？写入方是谁（recording 服务？）——影响 Watcher 的监听方式（轮询 vs inotify）。
2. 服务进程归属：与后端同机部署还是独立主机？影响端口/鉴权策略。
3. `get_key_image` 的图片体积预算：MCP 传输帧大小限制（当前规划 ≤1280px JPEG ≤300KB）。
4. 多 root / 多设备合并查询 — v1 按 device 过滤但单 root；多 root（多机器数据汇集）是否需要、`root_dir` 是否升级为列表。
5. `sessions.status`（active→finalized）的实际写入方与时机——影响 summarize 触发语义（当前有静默超时兜底，但语义需与 recording 侧对齐）。
6. active session 的实时推送 — streamable HTTP 支持 server→client 通知；是否需要在新数据分析完成时推送，取决于 agent 侧使用模式（当前假设 agent 主动查询 + `stale` 标记足够）。
7. 鉴权升级路径 — bearer token 够用到什么规模？接入企业 SSO/OAuth 的时机（倾向：暴露到非可信网络时直接迁 fastmcp 2.x auth）。
8. report 触发频率 — 跨 session 报告随 summarize 完成低频触发；是否需要 `get_report` tool 直接暴露（v1 可用 `search type=summary` 间接覆盖，暂不单列）。
9. 向量索引与 DB 同文件 vs 分文件 — sqlite-vec 写放大对 inventory DB 的影响未测；默认倾向独立 `lvdata_index.db`。

## 12. Dependencies

```toml
# tools/lvdata-mcp/pyproject.toml（核心）
dependencies = [
  "mcp>=1.12",            # 官方 SDK：FastMCP + streamable HTTP server/client
  "uvicorn>=0.30",
  "starlette>=0.40",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "structlog>=24",
]
# 可选：
#   Pillow>=10        — get_key_image 缩放（max_width）
#   sqlite-vec>=0.1   — v2 向量检索
#   openai>=1.52      — v2 embedding / v3 ask（百炼 OpenAI 兼容端点）
# Python >= 3.12；FTS5 为 SQLite 内建，无需扩展
```

## 13. 调研参考

- 数据与 pipeline 现状：`docs/data/lvdata_inventory.md` / `lvdata_audio_analysis.md` / `lvdata_video_analysis.md` / `lvdata_summary_analysis.md`
- 分析计划（Phase 6 工具化出处）：`docs/data/lvdata-analysis-plan.md`
- MCP 协议与传输：<https://modelcontextprotocol.io/specification>（Streamable HTTP）
- 官方 Python SDK：<https://github.com/modelcontextprotocol/python-sdk>（`FastMCP`、`streamable_http_app`）
- sqlite-vec：<https://github.com/asg017/sqlite-vec>（v2 候选）

*Design version: 1.0（合并定稿）| Created: 2026-08-05 | 合并自 lvdata_mcp_service-d4.md (Draft v1) + lvdata_mcp_service-k3.md (Design 1.0)*
