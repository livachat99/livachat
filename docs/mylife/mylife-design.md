# MyLife — Phase 1 Feature Design（合并终稿）

> **Status**: Final（合并稿，实施契约）
> **Date**: 2026-08-19
> **Supersedes**: `mylife-k3.md` + `mylife-d4.md`。本稿 = k3 的部署形态 / Replay 设计 / 前端栈
> + d4 的 Timeline/Graph API 形态 / 风险清单 / 分阶段验收，并经评审逐条核对代码与
> `/opt/lvdata` 真实数据（核对锚点见 §12）。实现时若与本文档偏离，需同步更新本文档。
> **Scope**: Phase 1 — Timeline / Graph / Replay 三个只读视图 + BFF 查询面 + Web App（移动优先）
> **更新（2026-10-13）**：Web app 已不再使用（决策见 `android_change_v3.md`）；`/mylife` 静态托管与
> `bff.web_dist` 配置已从服务端装配摘除。本文 §3.1 D4/D8、§3.2 Web App 行、§5.2 配置示例为历史设计记录，不再代表现状。

---

## 1. 背景与目标

### 1.1 产品愿景

用户不断上传生活碎片 → AI 理解这些碎片 → 自动形成"人生事件 / 人物 / 地点 / 关系 /
情绪 / 主题" → 最终以一个可探索的 **Life World** 展示。底层是数据，上层是"人生"。

```
                 MY LIFE
                    │
        ┌───────────┼───────────┐
        ↓           ↓           ↓
      Time        People      Places, objects, topics...
        │           │           │
        └───────────┼───────────┘
                    ↓
                  Events
                    ↓
              Raw Memories
                    ↓
        image / audio / chat / video
```

### 1.2 Phase 1 范围

只做**展示层**：三个视图 + 一个 BFF 模块 + 一个 Web App。数据生产（录音/上传）与
AI 理解（分析管线）**已经存在**（见 §2），Phase 1 不改动它们。

| View | 用户的问题 | 核心交互 |
|------|-----------|---------|
| **Timeline** | 我什么时候做了什么？ | Scroll / Explore |
| **Graph** | 我的生活之间有什么联系？ | Explore / Zoom / Connect |
| **Replay** | 重新经历这段时间，用动画方式回放 | Play / Pause / Scrub |

### 1.3 非目标（Phase 1 不做）

- 不做 token 鉴权（URL `?token=xxx` 解析但**不校验**，为 Phase 2 冻结契约，见 §5.5）
- 不做数据上传入口（数据来自既有 App 录音链路）
- 不做新的 AI 分析能力（只消费既有分析管线产物；place/person 等缺失实体见 §4.4）
- 不做多用户隔离（单用户 dev 场景，`device_id` 可选过滤）
- 不做 iOS / Android 原生 App（仅 Web App：先移动 web，后桌面 web）
- 不做跨 session 连续回放（Replay 为单 session 粒度）
- 不做全文/语义搜索（lvdata FTS 已具备，接入归 Phase 2）
- 不使用 Next.js（React + Vite，保持简单快速）

---

## 2. 现状调研（代码事实，2026-08-19 核对）

Phase 1 设计完全基于以下**已存在**的代码与数据。与草案核对中发现的事实错误已在
本稿全部修正；关键 schema 均经 `/opt/lvdata` 真实数据抽样验证。

### 2.1 数据生产层（已存在，不改）

**Recording 服务**（`services/recording/`）：每设备每 UTC 自然日一个 recording session，
落盘到 `<base_dir>/<device_uuid>/<recording_session_id>/`（dev 环境 `base_dir = /opt/lvdata`）：

```
/opt/lvdata/<device_uuid>/<recording_session_id>/
  session.json            # 元数据 + stats + analysis_status
  audio/<uuid7>.ogg       # 用户语音，60s OGG Opus 分段，文件名 uuid7 内含毫秒时间戳
  video/<uuid7>.jpg       # 屏幕/相机 JPEG 帧
  image/                  # 预留（当前无数据）
  chat/history.jsonl      # user↔assistant 对话，含 ts_ms
```

### 2.2 AI 理解层（已存在，不改）

**LvData 分析管线**（CLI：`tools/data-analysis/`；常驻 watcher + MCP 查询面：
`tools/lvdata-mcp/`）消费原始数据，产出衍生数据：

```
/opt/lvdata/derived/<device_uuid>/<recording_session_id>/
  audio/<segment_id>.json      # 每音频段：transcript / summary / language /
                               #   emotions[{emotion,confidence,evidence}] / objects[] / people[]
  summary.json                 # 音频聚合：emotions_dist / people / objects / 全量 transcript
  video/frames/<frame_id>.json # 每关键帧：scene / caption / apps[] / objects[] / ocr[] / people / actions[]
  video/windows/<win>.json     # 窗口级筛选结果
  video_summary.json           # 视频聚合：scene_timeline / apps_used / people / objects
  timeline.json                # ★ 多模态对齐统一时间线
  session_summary.json         # ★ session 摘要：stats / topics / notable_events /
                               #   user_behavior_timeline / screen_activity
/opt/lvdata/derived/_reports/report.json   # 跨 session 洞察
```

关键 schema 事实（真实数据验证）：

- `timeline.json` 事件类型共 **5 类**：`chat`（role/text）、`audio`（segment_id/transcript/
  summary/duration_s/emotions/empty）、`window`（window_id/caption/static）、`video`
  （frame_id/scene/caption/apps/ocr/actions）、**`agent_job`**（job_key/status/summary/
  fail_reason/has_result，来自 SQLite 镜像表）。排序键 `(ts_ms, EVENT_PRIORITY)`，
  `EVENT_PRIORITY = {chat:0, window:1, video:2, audio:3, agent_job:4}`
  （`lvdata_summary.py:115`）。每事件带 `ts_ms` + `ts_utc`；头部带 `session_id` /
  `device_uuid` / `session_date` / `timezone` / `started_at` / `ended_at` / `time_span_ms`
- **emotion 枚举（英文）**：`happy / sad / angry / surprised / frustrated / neutral / other`
  （`lvdata_analyze_audio.py:173`）
- **scene 枚举（小写）**：`desktop / browser / ide / terminal / chat / document / video / other`
  （`lvdata_analyze_video.py:127`）
- **people 字段真实形态（重要）**：video 侧 `people[] = {count, description}`——**无 name
  字段**；audio 聚合侧 `people[] = {role, gender, description}`——name 基本为 null。
  description 是自由文本（如"一名戴眼镜的男子"）。**无法产出可用的 person 实体节点**
  （见 §4.4 Gap）
- `session_summary.json` 的 `stats` 含 `duration_min / chat_messages / audio_segments /
  video_key_frames / silent_periods[]`（本地计算，可信；Replay 沉默期可直接消费）
- `timeline.json` 的 audio/video 事件不含 people/objects 明细——Graph 读聚合文件（§6.2）

### 2.3 SQLite 清单层（已存在，只读）

`/opt/lvdata/lvdata_inventory.db`（WAL 模式）。`sessions` 表
（`lvdata_inventory.py:56-76`）：`session_id / device_uuid / session_date / timezone /
status / started_at / duration_ms / analysis_status / root_path / ...`，另有
`chat_messages / audio_analyses / video_analyses / agent_jobs`（镜像）等表。

**`analysis_status` 的真相（两草案均半错，此处定死）**：

- 磁盘上的 `session.json` 的 `analysis_status` 是 recording 侧 seam，**固定 'pending'，
  管线从不回写该文件**——实测已完整分析的 session 磁盘文件仍为 `pending`，**不可用作
  分析完成信号**
- SQLite `sessions.analysis_status` 会被音频分析更新为 `completed`
  （`lvdata_analyze_audio.py:302`），但它只代表**音频**分析完成；空模态 session 永远
  不会完成（`tools/lvdata-mcp/src/lvdata_mcp/watcher.py:163` 明确因此不用状态列做脏检测）
- **结论**：面向用户的"分析状态"必须按 **derived 文件存在性**推导（§8.3）

### 2.4 后端服务层（已存在）

- **Phase 1 单体**：单一 FastAPI 进程（`services/app/application.py`），`docs/mono-repo.md:185`
  明确 "Phase 1: 单一服务，不同模块"。新路由经 `services/app/routes.py` 挂载
- **包布局**：`services/` 下顶层 Python package，`pyproject.toml`
  `[tool.setuptools.packages.find] where=["."]`；新增顶层 package 后需重跑 `make server-install`
- **配置**：`shared/config/models.py` 的 `AppConfig`（pydantic-settings，env 前缀
  `LIVACHAT__`，嵌套分隔符 `__`）+ `configs/dev/server.yaml`
- **运行**：`make server-dev` → uvicorn（reload）；dev 环境 **SSL 开启**（自签证书），
  端口 8080 → dev 后端是 **https://localhost:8080**
- **Auth 现状**：`gateway/auth.py` dev 模式比对 `auth.dev_token`，jwt 预留；
  只保护 WS 握手，HTTP 路由目前无鉴权
- **依赖**：`fastapi>=0.115`（starlette ≥0.40；`FileResponse` 自 starlette 0.39 起
  **原生支持 HTTP Range**，§8.1）

### 2.5 前端层（现状）

`apps/web/` 为空目录（仅 `.gitkeep`）——全新绿地。仓库目前无 node 工具链，
`.gitignore` 需补 node 条目。

### 2.6 结论

数据源足够支撑三个视图。Phase 1 新增只有两块：`services/bff/`（只读查询面 +
静态托管）和 `apps/web/mylife/`（展示）。**BFF 只读，不写，不调 LLM。**

---

## 3. 总体架构

```
┌──────────────┐   WS 录音    ┌──────────────────────────┐
│ LivaChat App │ ───────────→ │ services/ (FastAPI 单体)  │
└──────────────┘              │  recording → /opt/lvdata  │
                              └──────────┬───────────────┘
                                         │ (既有)
                              ┌──────────↓───────────────┐
                              │ lvdata 分析管线 (既有)     │
                              │  → SQLite + derived/     │
                              └──────────┬───────────────┘
                                         │ 只读
        ┌────────────────────────────────┼────────────────────────┐
        │                 services/bff/（新增，同进程模块）           │
        │  timeline/   graph/   replay/   common/                   │
        │     │           │         │        └── store/media/deps   │
        │     └───────────┴─────────┘                               │
        │            /api/mylife/*   +   /mylife/* (SPA 静态资源)     │
        └────────────────────────────┬─────────────────────────────┘
                                     │ HTTPS（同源，无 CORS）
                              ┌──────↓───────┐
                              │ apps/web/    │
                              │  mylife/     │  React + Vite SPA
                              │  Timeline / Graph / Replay
                              └──────────────┘
```

### 3.1 核心决策

| # | 决策 | 理由 |
|---|------|------|
| D1 | **BFF 是单体进程内的新模块，不是独立服务** | `docs/mono-repo.md:185` "Phase 1: 单一服务，不同模块"；少一个进程/端口/ingress 规则。模块边界保持干净（只依赖 `shared/` + 文件系统/SQLite，不 import gateway/session 内部），未来拆分只需把 router 挂到新 app。**拆分触发条件**：BFF 重 IO 查询影响实时 WS 链路延迟时 |
| D2 | **BFF 只读 lvdata（SQLite 清单 + derived JSON + 文件树），不读 PG，不调 lvdata-mcp** | 会话清单/元数据以 `sessions` 表为权威（免扫文件树）；内容数据（转写/画面/主题/情绪/对齐时间线）全在 derived 文件；agent_jobs 已镜像进 SQLite 并嵌入 timeline.json。沿用"查询侧只读 SQLite + derived JSON"架构约定（`lvdata_mcp_service.md` §3.1）。PG 只有原始表，引入无收益 |
| D3 | **BFF 内零 LLM** | 全部语义产物由离线管线预计算，BFF 只做确定性聚合。API 延迟可预期（读文件 + JSON 映射），无运行时 LLM 成本与故障面。代价：新数据需等管线（watcher 60s 轮询 + summarize 静默窗口 600s）——用分析状态标记消化（§8.3） |
| D4 | **URL 形状 `…/mylife/?token=xxx`，token 解析但不校验** | Phase 2 接鉴权时 URL、前端、书签、分享链接零改动；校验逻辑收敛在 `deps.py` 单点（§5.5）。**2026-10-13：/mylife 托管已摘除，本决策退役** |
| D5 | **Graph Phase 1 确定性提取，6 类实体节点** | event/topic/emotion/object/app/scene 均有结构化产物支撑；person/place 无可靠数据源，kind 保留但不出节点（§4.3/§4.4）。零新增 LLM 调用 |
| D6 | **Replay 由后端编排 cue 序列，前端纯播放器** | 回放节奏策略（沉默期压缩、帧停留）后端可调而不发前端版；沉默期直接消费 `session_summary.stats.silent_periods` |
| D7 | **媒体经 BFF 代理 + `FileResponse` 原生 Range + immutable 缓存** | `/opt/lvdata` 非 Web 静态目录；starlette ≥0.39 原生 Range（音频 seek 必需）；uuid7 文件名内容不可变 → `Cache-Control: immutable` 长效缓存 |
| D8 | **SPA 由同一 FastAPI 进程托管 + 前端 HashRouter** | 生产 URL 与 API 同源无 CORS；`#/timeline` 等 hash 路由刷新永远命中同一 `index.html`，免 SPA fallback 配置（KISS） |

### 3.2 URL 与端口规划

| 用途 | URL |
|------|-----|
| API（生产/开发同源） | `https://server/api/mylife/...` |
| Web App（生产） | ~~`https://server/mylife/?token=xxx`~~（2026-10-13 摘除） |
| Web App（开发） | `http://localhost:5173/mylife/?token=xxx`（Vite dev server，proxy `/api` → `https://localhost:8080`，`secure:false` 因 dev 后端自签 HTTPS） |

---

## 4. 领域模型

### 4.1 分层模型（对齐愿景图）

```
Life World（前端三视图）
   ▲
   ├─ Timeline: EventStream（跨 session 合并、按本地日分组、游标分页）
   ├─ Graph:    EntityGraph（节点=实体，边=会话级共现）
   └─ Replay:   SessionPlayback（单 session cue 序列 + 媒体）
   │
Events ── timeline.json 事件（chat/audio/window/video/agent_job）
   │
Raw Memories ── recording session 文件（audio/video/chat）
```

### 4.2 事件模型（5 类，DTO 直通映射）

Timeline/Replay 事件 = `timeline.json` 事件字段 + BFF 注入的
`session_id / device_uuid / media_url`（前端无需拼路径）。五类事件全部映射，
**不得按类型白名单丢弃**（k3 草案 4 类映射会静默丢 agent_job，已修正）。

### 4.3 实体模型（Graph）

- 实体 ID：`"{kind}:{归一化 label}"`，如 `topic:旅游与天气`、`app:google chrome`
  （app 小写归一，topic 保留原文）。节点 id 稳定，前端可跨请求保持状态
- 节点属性：`{id, kind, label, weight, first_seen_ms, last_seen_ms}`；
  `event` 节点附加 `session_id`（支撑 Graph → Timeline/Replay 跳转的 Connect 交互）
- 边：`{source, target, weight, kind}`；Phase 1 只有 `kind=session_cooccur`
  （同 session 共现，weight=共现 session 数）；`event_cooccur`（事件级共现）
  字段预留，归 Phase 2 细化

| node.kind | 数据来源 | Phase 1 |
|-----------|---------|---------|
| `event` | 每个已分析 session 一个节点（label = 日期 + 首要 topic） | ✅ |
| `topic` | `session_summary.json → topics[]` | ✅ |
| `emotion` | `summary.json → emotions_dist`（英文枚举值即节点） | ✅ |
| `object` | `summary.json` / `video_summary.json → objects[].name` | ✅ |
| `app` | `video_summary.json → apps_used[].name` | ✅ |
| `scene` | `video_summary.json → scene_timeline[].scene`（小写枚举） | ✅ |
| `person` | **无可靠数据源**（§2.2：video 无 name，audio name 多为 null，description 自由文本） | ❌ kind 保留 |
| `place` | **无结构化提取**（仅存于 transcript/caption 自由文本） | ❌ kind 保留 |

### 4.4 Gap 清单（显式声明，不用假数据伪装）

1. **person 不可用**：见上表。Phase 2 在分析管线增加人物抽取/归一后，BFF 聚合逻辑
   按 kind 通用，零改动生效
2. **place 无提取**：同上，Phase 2 管线增加地点抽取
3. **跨 session 实体归一**：同名实体按 label 字符串归并，未做实体消歧——单用户
   小规模可接受；Phase 2 引入实体表时升级
4. **关系（relationship）**：无任何数据源，Phase 1 完全不做
5. **事件级共现边**：需逐段/逐帧读取，Phase 1 只做会话级（边 schema 已预留 kind）

---

## 5. BFF 设计（`services/bff/`）

### 5.1 模块结构

```
services/bff/
├── __init__.py
├── router.py               # 聚合 router，prefix="/api/mylife"，由 app/routes.py 挂载
├── common/
│   ├── __init__.py
│   ├── deps.py             # FastAPI 依赖：token 解析(bypass) / device 过滤 / 分页
│   ├── store.py            # LvdataStore：只读 SQLite + derived JSON + 文件定位
│   ├── media.py            # 媒体路径校验 + FileResponse（原生 Range）+ 转码缓存
│   └── schemas.py          # Pydantic DTO（API 契约，对应 §6）
├── timeline/
│   ├── __init__.py
│   ├── router.py           # GET /overview, GET /timeline
│   └── service.py          # 跨 session 事件合并、本地日分组、游标分页
├── graph/
│   ├── __init__.py
│   ├── router.py           # GET /graph, GET /graph/entity/{id}
│   └── service.py          # 六类实体聚合、共现建边、ego 子图（纯本地计算，无 LLM）
└── replay/
    ├── __init__.py
    ├── router.py           # GET /replay/{session_id}
    └── service.py          # timeline → cue 序列（含沉默期压缩）
```

依赖方向：`bff/*` → `bff/common` → `shared/`（config/utils）。禁止反向依赖。

### 5.2 配置与挂载

**`shared/config/models.py` 新增：**

```python
class BffConfig(BaseModel):
    enabled: bool = True
    data_root: str = ""                  # 默认回退 recording.base_dir（/opt/lvdata）
    lvdata_db_path: str = ""             # 默认 <data_root>/lvdata_inventory.db
    web_dist: str = "apps/web/mylife/dist"   # SPA 构建产物目录（生产）——2026-10-13 已随 /mylife 托管摘除
    media_cache_dir: str = ""            # 音频转码缓存，默认 <data_root>/_cache
    device_uuids: list[str] = []         # 数据范围白名单（服务端 scope）；空 = 全部
    timezone: str = "Asia/Shanghai"      # 本地日分组默认时区

class AppConfig(BaseSettings):
    ...
    bff: BffConfig = BffConfig()
```

**`services/app/routes.py` 挂载（仿照 gateway_router 模式）：**

```python
from bff.router import router as bff_router

router.include_router(bff_router)          # /api/mylife/*
# SPA 静态托管（2026-10-13 已摘除）原在 application.py create_app() 中按 cfg.bff 条件挂载:
#   app.mount("/mylife", StaticFiles(directory=cfg.bff.web_dist, html=True))
```

新增顶层 package 后需重跑 `make server-install`。

### 5.3 common 层要点

**store.py（LvdataStore，只读）**：

- SQLite 每次请求短连接 `sqlite3.connect("file:...?mode=ro", uri=True)`，
  `PRAGMA busy_timeout=5000`（WAL 并发读安全）
- `list_sessions(filters)` → `sessions` 表（**以表为准，不扫文件树**）
- `read_timeline(session_id)` / `read_derived(session_id, kind)` → derived JSON
  读取；缺失/解析失败返回带 `missing` 标记的结构，**不触发 align**（那是
  lvdata-mcp watcher 的职责，BFF 不越权）
- `resolve_media(device, session, kind, filename)` → 路径解析 + 防穿越校验
- 派生文件 schema 演进容错：单行/单文件解析失败跳过，不整段失败

**media.py**：

- 路径校验：`device`/`session` 必须 UUID 形态；`filename` 必须匹配
  `^[0-9a-f-]+\.(ogg|jpg|m4a)$`；`resolve()` 后必须位于
  `<data_root>/<device>/<session>/` 内——任一失败返回 400/404
- 返回 `FileResponse`（starlette 原生 Range，音频 seek 必需；**钉
  `starlette>=0.49.1`**，含 Range 解析安全修复 GHSA-7f5h-v6xp-fcq8）
- `Cache-Control: public, max-age=31536000, immutable`——uuid7 文件名内容不可变
- `?format=m4a`：ffmpeg 懒转码 AAC/M4A 并缓存到 `media_cache_dir`（ffmpeg 已是
  分析管线工具链一环）；默认返回原始 OGG。按需触发，不做全量预转

**deps.py**：见 §5.5。

### 5.4 错误与响应约定

- 成功响应带 `"schema_version": 1`（沿用 lvdata 派生文件既有约定）
- 时间一律 Unix epoch ms（`ts_ms`），ISO 字符串仅作附字段
- 错误统一：`{"error": {"code": "...", "message": "..."}}`，code ∈
  `not_found / not_analyzed / bad_request / range_invalid / internal`

### 5.5 认证（Phase 1：Bypass）

> **2026-10-13 已实施（Phase 2）**：`get_principal` 已接入 `gateway.auth.Authenticator`
> 校验（dev 模式 `== auth.dev_token`，失败 401 `unauthorized` envelope）；media 强制条款
> 同步兑现（同经 `get_principal`，错 token 一律 401）。下方代码块为 Phase 1 原文（历史记录）。

```python
async def get_principal(token: str = Query(default=""),
                        device_id: str | None = Query(default=None)) -> Principal:
    # Phase 1: 解析但不校验。保留 token 形参以冻结 URL 契约
    # （?token=xxx 前端、书签、分享链接形态不变），
    # Phase 2 在此接入 gateway.auth.Authenticator 的 dev/jwt 校验，调用方零改动。
    return Principal(user_id="dev-user", device_id=device_id)
```

- `device_id` 是唯一实际生效的客户端过滤参数；`bff.device_uuids` 是服务端 scope
  （白名单非空时，请求设备必须在白名单内）。两者叠加
- **Phase 2 强制条款：media 端点必须纳入同一鉴权**——否则 token 形同虚设
  （裸媒体 URL 可直接访问原始录音/截图）
- Phase 2 迁移路径：复用 `AuthConfig`（dev_token / jwt），只改 `deps.py` 一处（**2026-10-13 已按此实施**；jwt 仍为预留）

---

## 6. API 规约

统一前缀 `/api/mylife`；所有端点接受 `token`（bypass）与 `device_id` 参数，下文从略。

### 6.1 Timeline

#### `GET /api/mylife/overview` — Timeline 着陆页 + Replay 会话选择器（共用）

按本地日倒序的"我有哪些天"。数据源：`sessions` 表 + 各 session 的
`session_summary.json`（缺失时降级为仅有元数据）。

```jsonc
// Response 200
{
  "schema_version": 1,
  "timezone": "Asia/Shanghai",
  "days": [
    {
      "date": "2026-08-17",                    // 本地日（按 session 事件 ts 归日，§8.2）
      "counts": {"chat": 57, "audio": 263, "video": 6, "window": 2, "agent_job": 0},
      "sessions": [
        {
          "session_id": "01a00d9f-1049-75aa-8000-438ebedb149c",
          "device_uuid": "6e8f80d3-…",
          "status": "finalized",
          "started_at": "2026-08-17T07:06:20Z",
          "stats": {"duration_min": 311, "chat_messages": 57,
                    "audio_segments": 263, "video_key_frames": 6},
          "summary": "用户与 AI 进行了问候与故事创作对话…",   // 可空
          "topics": [{"topic": "旅游与天气", "mentions": 6}],
          "analysis": {"timeline": "ok", "summary": "ok"}     // ok|pending|missing，§8.3
        }
      ]
    }
  ],
  "total_sessions": 5
}
```

#### `GET /api/mylife/timeline` — 跨 session 合并事件流（Timeline 主数据接口）

| 参数 | 类型 | 说明 |
|------|------|------|
| `date_from` / `date_to` | YYYY-MM-DD | 本地日范围（缺省 = 全量） |
| `session_id` | str | 可选，聚焦单会话 |
| `types` | csv | 事件类型过滤（chat,audio,window,video,agent_job） |
| `limit` | int | 默认 200，最大 500 |
| `cursor` | str | 不透明游标（`"{date}\|{index}"`），首页省略 |
| `timezone` | str | 按日分组时区，默认配置值 |

```jsonc
// Response 200
{
  "schema_version": 1,
  "timezone": "Asia/Shanghai",
  "days": [
    {
      "date": "2026-08-17",
      "events": [
        {"ts_ms": 1786935043413, "ts_utc": "…", "type": "chat", "role": "user",
         "text": "…", "session_id": "…", "device_uuid": "…"},
        {"ts_ms": 1786935050000, "ts_utc": "…", "type": "audio", "segment_id": "…",
         "transcript": "…", "summary": "…", "duration_s": 60.0, "empty": false,
         "emotions": [{"emotion": "neutral", "confidence": 0.7}],
         "media_url": "/api/mylife/media/<dev>/<sess>/audio/<segment_id>.ogg",
         "session_id": "…", "device_uuid": "…"},
        {"ts_ms": 1786935060000, "ts_utc": "…", "type": "video", "frame_id": "…",
         "scene": "browser", "caption": "…", "apps": [], "ocr": [], "actions": [],
         "media_url": "/api/mylife/media/<dev>/<sess>/video/<frame_id>.jpg",
         "session_id": "…", "device_uuid": "…"},
        {"ts_ms": 1786935070000, "ts_utc": "…", "type": "agent_job", "job_key": "…",
         "status": "done", "summary": "…", "session_id": "…", "device_uuid": "…"}
      ]
    }
  ],
  "next_cursor": null,
  "total": 338
}
```

- 事件源：`timeline.json` 直通映射（不重新对齐——对齐是分析管线的职责），
  按 `(ts_ms, EVENT_PRIORITY)` 全局排序后按本地日分组
- 某 session `timeline.json` 缺失 → 该 session 不出现在事件中，其天在 overview 中
  以 `analysis.timeline=pending/missing` 呈现"分析中"态；**不整请求报错**

### 6.2 Graph

#### `GET /api/mylife/graph` — 生活关系图

| 参数 | 说明 |
|------|------|
| `date_from` / `date_to` | 本地日范围（缺省全量） |
| `types` | 实体类型过滤（event,topic,emotion,object,app,scene） |
| `focus` | 实体 ID（ego 子图中心），缺省 = 全图 |
| `depth` | ego 半径（默认 1） |
| `min_weight` | 节点/边权重下限（降噪，默认 1） |

```jsonc
// Response 200
{
  "schema_version": 1,
  "nodes": [
    {"id": "evt:01a00d9f", "kind": "event", "label": "8月17日 · 旅游与天气",
     "session_id": "01a00d9f-…", "weight": 12,
     "first_seen_ms": 1786935043413, "last_seen_ms": 1786953843413},
    {"id": "topic:旅游与天气", "kind": "topic", "label": "旅游与天气", "weight": 6, "…": "…"},
    {"id": "emotion:happy", "kind": "emotion", "label": "happy", "weight": 3, "…": "…"},
    {"id": "app:google chrome", "kind": "app", "label": "Google Chrome", "weight": 9, "…": "…"}
  ],
  "edges": [
    {"source": "evt:01a00d9f", "target": "topic:旅游与天气", "weight": 6,
     "kind": "session_cooccur"},
    {"source": "topic:旅游与天气", "target": "emotion:happy", "weight": 2,
     "kind": "session_cooccur"}
  ],
  "stats": {"sessions": 5, "date_range": ["2026-08-13", "2026-08-18"]}
}
```

建边规则：`event` ↔ 该 session 提取出的全部实体；实体 ↔ 实体同 session 共现即连边
（weight = 共现 session 数）。数据源映射见 §4.3。

#### `GET /api/mylife/graph/entity/{id}` — 实体详情

```jsonc
{
  "schema_version": 1,
  "id": "topic:旅游与天气", "kind": "topic", "label": "旅游与天气", "weight": 6,
  "first_seen_ms": 1786935043413, "last_seen_ms": 1787200000000,
  "appearances": [                       // session 粒度，上限 50，时间倒序
    {"session_id": "01a00d9f-…", "session_date": "2026-08-17",
     "snippet": "用户与 AI 进行了问候与故事创作对话…"}
  ]
}
```

点击 appearance → 前端跳 Timeline 对应日期；点击 `event` 节点 → 跳 Replay 该 session。

### 6.3 Replay

#### `GET /api/mylife/replay/{session_id}` — 回放脚本（cue 序列）

回放编排逻辑收敛在后端，前端只做"播放器"：

```jsonc
// Response 200
{
  "schema_version": 1,
  "session": {"session_id": "…", "device_uuid": "…", "session_date": "2026-08-17",
              "started_at": "…", "ended_at": "…", "time_span_ms": 18800000},
  "duration_ms": 4065000,                // 压缩后的回放总时长
  "cues": [
    {"t_ms": 0,     "type": "chat",  "role": "user", "text": "…"},
    {"t_ms": 5087,  "type": "audio", "text": "…", "duration_ms": 60000,
     "emotions": [{"emotion": "neutral", "confidence": 0.7}],
     "media_url": "/api/mylife/media/…/audio/<seg>.ogg"},
    {"t_ms": 12000, "type": "frame", "caption": "GitHub 仓库页面…", "hold_ms": 8000,
     "media_url": "/api/mylife/media/…/video/<frame>.jpg"},
    {"t_ms": 45000, "type": "pause", "duration_ms": 45000, "hint": "沉默期"}
  ]
}
```

- `t_ms` = 相对回放开始的偏移（压缩后时间轴）；原始绝对时刻随 cue 附 `ts_utc` 供时钟显示
- cue 类型 = `chat / audio / frame / pause`，另加 **`job`**（agent_job 事件映射，§4.2
  五类事件全部映射的延伸；status/summary/fail_reason 直通）——实现偏离：初稿未列 job
- `pause` 由 `session_summary.stats.silent_periods` 生成（缺失时回退：`empty` 音频段 /
  大时间空隙探测）；压缩倍率默认 8×，可配置
- `timeline.json` 缺失 → `404 {"error": {"code": "not_analyzed"}}`
- 变速（1x/2x/4x）与"跳过静默段"是前端播放器行为，不进 API（前端播放器段边界
  以后端 `t_ms` 为绝对时间轴，Scrub 直接映射压缩位置）

### 6.4 Media

#### `GET /api/mylife/media/{device}/{session}/{kind}/{filename}`

- `kind ∈ {audio, video, image}`；校验与缓存策略见 §5.3
- audio 支持 `?format=m4a`（Safari 旧版兜底，§8.1）
- 大图缩略（`?max_width=`）归 Phase 2

---

## 7. 前端设计（`apps/web/mylife/`）

### 7.1 技术选型

| 项 | 选择 | 理由 |
|----|------|------|
| 框架 | **React 19 + TypeScript + Vite 8** | 需求指定；不用 Next.js |
| 路由 | `react-router` **HashRouter**，3 个路由 | 静态托管零 fallback；支持深链接（`#/replay?session=<id>` 可收藏/分享） |
| 数据获取 | `@tanstack/react-query` | 缓存/轮询（analysis pending 时 60s 刷新） |
| 样式 | Tailwind CSS + CSS 变量主题 | 移动优先、暗色"人生世界"基调 |
| Graph 渲染 | **d3-force 自绘 canvas**（~~react-force-graph~~） | 移动端性能（节点上百时 SVG DOM 是陷阱）；canvas 目标一致。实现偏离：`react-force-graph` 的传递依赖含 GitHub 直拉包，npm 12 拒绝安装 → 改用 `d3-force` + `d3-zoom` 自绘（缩放/拖拽/连边/捏合均手写） |
| Replay 动画 | `framer-motion` | cue 驱动的进出场动画（Ken Burns / 打字机） |
| 音频 | 原生 `<audio>` + 自定义进度条 | Range 由 BFF `FileResponse` 原生支持 |

所有依赖落地时钉 minor 版本。UI/UX 目标：**cool, attractive**——深色星空/粒子基调；
Timeline 是"河流"，Graph 是"星图"，Replay 是"时光机"。移动优先（触控滑动、底部
tab bar、安全区适配、触控目标 ≥44px），≥768px 进入桌面布局（顶部导航 + 大屏星图）。

> **2026-09-22 现状更正（Android-only，见 docs/android_timeline_hotslot.md §14）**：Android 侧 Graph 与 Replay **已整栈删除**、
> MyLife 首页**收敛为单视图**（无 tab/底栏）且标题改**「时光机」**——本段的"三视图 + 底部 tab bar"比喻**仅对 Web 版成立**
>（`apps/web/mylife` 三 tab 并存，header 仍「✦ MyLife」）；Web 语境下"时光机"仍指 Replay。

### 7.2 目录结构

```
apps/web/mylife/
├── index.html
├── package.json
├── vite.config.ts            # base: '/mylife/'，dev proxy /api → https://localhost:8080 (secure:false)
├── tailwind.config.ts
├── public/
└── src/
    ├── main.tsx
    ├── App.tsx               # HashRouter + 底部 tab（Timeline/Graph/Replay）
    ├── api/
    │   ├── client.ts         # fetch 封装：baseURL=/api/mylife，自动附带 ?token=
    │   ├── hooks.ts          # useOverview / useTimeline / useGraph / useEntity / useReplay
    │   └── types.ts          # 与 bff/common/schemas.py 对齐的 TS 类型
    ├── token.ts              # 从 location.search 读 token，存入 sessionStorage
    ├── views/
    │   ├── TimelineView.tsx  # #/timeline
    │   ├── GraphView.tsx     # #/graph
    │   └── ReplayView.tsx    # #/replay?session=<id>
    ├── components/
    │   ├── HeatStrip.tsx     # Timeline：每日密度条（点击定位日期）
    │   ├── DayGroup.tsx      # Timeline：本地日分组 + 粘性日期头 + 年月分隔
    │   ├── EventCard.tsx     # 五类事件卡片
    │   ├── LifeGraph.tsx     # Graph：force 星图 + 节点详情抽屉
    │   ├── Player.tsx        # Replay：播放控制（Play/Pause/Scrub/变速/跳静默）
    │   └── Stage.tsx         # Replay：动画舞台（帧画面 + 字幕 + 音频）
    └── theme.css
```

### 7.3 三个视图的交互设计

**Timeline（Scroll / Explore）**

- 首屏 `GET /overview` → HeatStrip 密度条 + 按本地日倒序的"河流"章节（日期粘性头 +
  一句话 summary + stats 徽章：N 段语音 / N 张画面 / N 条对话）
- 事件流 `GET /timeline` 游标分页无限滚动；月/年跨度为大字分隔符
- 卡片：chat 气泡；audio 卡片（摘要 + 情绪 chip + 内联播放）；frame 卡片（关键帧缩略图
  + caption）；window 卡片；agent_job 状态徽章卡（容忍内容可变，§11.6）
- 顶部类型过滤 chips + 日期跳转
- `analysis.pending` 的天显示"分析中…"骨架卡，react-query 60s 轮询
- 点击事件卡片 → 跳 Replay 对应 session 并定位该时刻

**Graph（Explore / Zoom / Connect）**

- `GET /graph` → 力导向星图：节点颜色按 kind（topic=金、emotion=粉、app=蓝、
  event=白、object=紫、scene=青），节点大小 ∝ weight
- 交互：捏合/滚轮 Zoom；拖拽 Explore；点击节点 → **聚焦 ego 子图**（邻居高亮、
  其余淡出）+ 底部抽屉显示 appearances（Connect）→ 点条目跳 Timeline 日期 /
  Replay session
- 顶部 kind 图例开关 + 日期范围筛选（`?date_from=&date_to=`）+ `min_weight` 降噪
- 移动端提供"实体 Top 列表"副视图（列表 ↔ 图切换）

**Replay（Play / Pause / Scrub）**

- 入口：Replay Tab 会话列表（数据同 overview），或从 Timeline/Graph 事件跳入
- `GET /replay/{session_id}` → cue 序列进入播放器状态机
- Stage：frame cue 以 Ken Burns 效果展示关键帧 + caption 字幕；audio cue 播放对应
  音频段并同步打出转写字幕（`<audio>` 进度与主进度同步）；chat cue 气泡逐条浮现；
  pause cue 显示"沉默期"过场（时钟快走动画）
- 底部控制条：Play/Pause、进度条 Scrub（按 `t_ms` seek，音频按 Range 定位）、
  变速 1x/2x/4x、"跳过静默段"开关、当前时刻时钟（HH:MM:SS，显示 `ts_utc` 本地时）

### 7.4 开发工作流

```bash
# 后端（既有，https://localhost:8080）
env $(grep -v '^#' services/.env | grep -v '^$' | xargs) make server-dev

# 前端（新增，http://localhost:5173/mylife/?token=dev）
cd apps/web/mylife && npm install && npm run dev
```

`vite.config.ts` 关键配置：

```ts
export default defineConfig({
  base: '/mylife/',
  server: {
    port: 5173,
    proxy: { '/api': { target: 'https://localhost:8080', secure: false } },
    // secure:false — dev 后端 SSL 使用自签证书（configs/dev/cert.pem）
  },
  build: { outDir: 'dist' },   // 生产由 FastAPI 托管 cfg.bff.web_dist（2026-10-13 已摘除）
})
```

落地时同步更新：根 `REPO.md`（常用命令）、`docs/mono-repo.md`（`apps/web` 与
`services/bff` 条目）、`.gitignore`（`node_modules/`、`dist/`）、`Makefile`
（`mylife-install` / `mylife-dev` / `mylife-build` 目标）。

---

## 8. 关键设计决策与权衡

### 8.1 音频格式：OGG Opus 与 Safari

录制格式 OGG Opus（recording 服务确定，不改）。浏览器现状（2026-08）：
Chrome/Edge/Firefox 原生可播；**Safari 18.4+（iOS 18.4+，2025-03 起）已原生支持
Ogg Opus**；iOS < 18.4 设备及部分 Safari 版本存在兼容问题。决策：

- 默认返回原始 OGG；BFF media 端点支持 `?format=m4a` 懒转码兜底（§5.3）
- 前端按 `canPlayType('audio/ogg; codecs=opus')` 自动选择地址
- 真机验收必须覆盖 iOS Safari（§10）

### 8.2 日期语义：本地日分组

recording session 按 **UTC 自然日**切分，`timeline.json` 带 `timezone` 展示时区。
若按 UTC 日展示，用户"一天"会被劈成两天——生活日志产品不可接受。决策：

- BFF 按事件 `ts_ms` 落入**请求时区**（默认 `bff.timezone`）的本地日分组
- 跨 UTC 日的两个 session 在同一本地日内自然合流（Timeline 是跨 session 合并流，
  天然支持）；session 粒度仅 Replay 选择器可见
- `session_date`（UTC 日）仅作为存储/过滤键，不作为展示分组键

### 8.3 "分析状态"推导规则（以 derived 文件存在性为准）

| 状态 | 条件 |
|------|------|
| `ok` | `timeline.json`（或对应产物）存在且可解析 |
| `pending` | session 存在但 derived 产物尚未生成（管线排队中） |
| `missing` | derived 目录存在但目标产物缺失/损坏 |

**禁止**使用磁盘 `session.json` 的 `analysis_status`（固定 `pending`，永不回写，
§2.3）；SQLite `sessions.analysis_status` 仅反映音频分析，可作参考不作判据。
前端对 `pending` 天展示"分析中"并轮询——消化管线的 60s+600s 延迟（D3）。

### 8.4 部署形态：同进程模块 vs 独立进程

采同进程（D1）。已识别的对立论据（独立进程可隔离重 IO 查询与实时 WS 链路）在
单用户 dev 场景不成立；若未来 BFF 查询压力影响 WS 延迟，按模块边界拆为独立
uvicorn 进程（router 直接挂到新 app，配置项不变）。

---

## 9. 实施计划（分阶段，每阶段独立可交付）

| 阶段 | 内容 | 交付物 | 验收标准 |
|------|------|--------|----------|
| **P1 Timeline** | `bff/common`（BffConfig/LvdataStore/deps/media 校验）+ timeline API + Web 脚手架 + TimelineView | store/聚合单测（fixture 迷你 lvdata：SQLite + 2 个 session 的 derived 树）+ Vite/TS/Tailwind/HashRouter/token/proxy 空壳 + HeatStrip/日分组/无限滚动/五类卡片 | 本地日分组与游标翻页正确；类型过滤生效；缺失 timeline.json 的 session 返回 `pending` 标记不报错；`tsc --noEmit` + pytest 通过 |
| **P2 Graph** | graph API（六类实体聚合 + 共现建边 + ego 子图 + 实体详情）+ GraphView | graph router/service + 单测（节点权重、边、focus 子图、appearances）+ canvas 星图 + 图例 + 详情抽屉 | 点击实体聚焦子图正确；kind/日期过滤生效；appearance 可跳 Timeline 日期、event 节点可跳 Replay |
| **P3 Replay** | replay API（cue 生成 + pause 压缩）+ media 端点 + ReplayView + Safari 适配 + 桌面打磨 | cue 单测（t_ms 单调、pause 来自 silent_periods）+ 媒体路径穿越用例（`..`/绝对路径/非 UUID 全 400/404）+ Range 请求用例 + Player/Stage/变速/跳静默 + `?format=m4a` | 回放按 cue 有序呈现；音频可 seek；iOS Safari 真机可播；桌面响应式 ≥768px 正常 |

每阶段含：`tsc --noEmit` + pytest 单测 + 真实 `/opt/lvdata` 数据手动冒烟。
收尾：README / mono-repo.md / .gitignore / Makefile 更新；依赖钉版
（含 `starlette>=0.49.1`）。

---

## 10. 测试计划

- **BFF 单测**（`services/tests/bff/`，pytest，沿用仓库配置）：
  - `LvdataStore`：fixture 目录树（迷你 SQLite + derived/）→ overview/timeline/
    graph/replay DTO 正确性
  - 本地日分组：跨 UTC 日的两个 session 合流于同一本地日
  - media 路径校验：路径穿越用例全部 400/404；Range 请求 206 契约
  - graph 聚合：六类节点与边权重正确性；ego 子图裁剪
  - replay cue：`t_ms` 单调递增；pause 与 silent_periods 对齐
- **API 冒烟**：FastAPI TestClient 挂载 bff router，200/404/错误信封契约断言
- **前端**：`vitest` 跑 api/types 与播放器状态机单测（Phase 1 轻量，不追覆盖率）
- **手工验收**：dev 环境真实数据走完三视图 + **iOS Safari 音频播放**（OGG 直放与
  m4a 兜底各验证一次）

---

## 11. 风险与开放问题

1. **BFF 与 lvdata 同机部署前提**：BFF（同进程于主 app）直读 `/opt/lvdata` 文件系统
   与 SQLite。跨机部署时需共享挂载，或改为走 lvdata-mcp 查询面（本设计不覆盖后者）
2. **Phase 2 媒体鉴权**：media 端点必须纳入同一鉴权（§5.5 强制条款），否则 token
   形同虚设
3. **timeline.json 缺失/过期**：BFF 只读现成产物，缺失时返回 `pending/missing`
   标记；触发重算是 watcher 的职责，BFF 不越权
4. **派生文件 schema 演进**：`extract_json` / 聚合 JSON 是自由格式，解析须容错
   （单行失败跳过），并随管线 prompt 演进保持 fixture 兼容测试
5. **OCR 不做实体**：OCR 文本仅作事件属性展示（噪声控制）；未来 place/object 提取
   可作候选源（需 LLM 过滤）
6. **agent_job 事件可变性**：镜像表是"单行最新状态"，同一 job 事件内容随 align
   重跑更新——前端 Timeline/Replay 需容忍事件内容可变（不影响事件数与排序）
7. **无用户态的 device 过滤**：Phase 1 用 `device_id` 参数 + `device_uuids` 白名单；
   auth 落地后迁移为 "user → devices → 数据" 授权模型（PG users/devices 表已具备，
   届时 BFF 才引入 PG 只读连接）
8. **Replay 沉默期压缩倍率**（8×）与 frame `hold_ms` 默认值，待真实体验调优
9. **Phase 2 token 方案**：复用 `auth.dev_token` 比对，还是为 web 单独发 JWT？
   （影响 `deps.py` 单点，已隔离）
10. **Graph 服务端裁剪**：`min_weight` 之外的过滤维度（按 device？）视真实数据
    规模再定，Phase 1 前端裁剪即可
11. ~~`derived/webroot/` 空目录用途~~ → 已决策：Phase 1 用 `apps/web/mylife/dist`
    （代码与产物同仓）

---

## 12. 事实基线锚点（2026-08-19 核对）

| 事实 | 锚点 |
|------|------|
| timeline.json 五类事件 + EVENT_PRIORITY | `tools/data-analysis/lvdata_summary.py:115`、`:366-471`（build_timeline） |
| emotion 英文枚举 | `tools/data-analysis/lvdata_analyze_audio.py:173` |
| scene 小写枚举 | `tools/data-analysis/lvdata_analyze_video.py:127` |
| video people 无 name（count+description） | `lvdata_analyze_video.py:134,151` |
| audio 聚合 people（role+gender+description） | 真实数据 `/opt/lvdata/derived/…/summary.json` 抽样 |
| session_summary.stats.silent_periods | 真实数据 `…/session_summary.json` 抽样 |
| sessions 表结构 | `tools/data-analysis/lvdata_inventory.py:56-76` |
| SQLite analysis_status 仅音频分析更新 | `lvdata_analyze_audio.py:294-302` |
| 状态列不可作完备判据 | `tools/lvdata-mcp/src/lvdata_mcp/watcher.py:163` |
| 磁盘 session.json analysis_status 固定 pending | 真实数据 `…/session.json` 抽样（derived 完整仍为 pending） |
| 查询侧只读 SQLite + derived JSON 约定 | `docs/data/lvdata_mcp_service.md` §3.1 |
| "Phase 1: 单一服务，不同模块" | `docs/mono-repo.md:185` |
| 新顶层 package 需重跑 server-install | `services/pyproject.toml` packages.find + `Makefile` |
| gateway auth dev_token 模式 | `services/gateway/auth.py:18-27`、`services/shared/config/models.py:34` |
| watcher 60s 轮询 / summarize 600s 静默 | `tools/lvdata-mcp/README.md` |
| FileResponse 原生 Range（starlette 0.39+）；0.49.1 Range 安全修复 | starlette release notes（GHSA-7f5h-v6xp-fcq8） |
| Safari 18.4+ 原生 Ogg Opus | WebKit Features in Safari 18.4 / Apple Safari 18.4 Release Notes |
| apps/web 为绿地 | `apps/web/`（仅 .gitkeep） |
| derived/webroot 空目录存在 | `/opt/lvdata/derived/webroot/`（空） |

---

*Design version: 2.0（合并终稿）| Created: 2026-08-19 | 合并自 mylife-k3.md + mylife-d4.md，经代码与真实数据逐条核对*
