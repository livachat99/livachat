# LivaChat Agent 会议功能分析与设计

> **2026-11-15 更新**：Android 应用内 WebView 已实现会议页布局/视口与受信任会议页的
> 摄像头/麦克风权限支持，详见 `docs/android_webview_meeting_support.md`；本文 §8.3
> 等“Android WebView 无媒体权限”描述为历史限制。屏幕共享仍建议使用系统浏览器。
>
> 状态：设计 v3（2026-11-15）。v2（会议基础功能）已实现（§18）；v3 增强
> （URL 携带会议号、自动填充、姓名持久化）已实施（§19.12）。
>
> 需求来源：用户 2026-11-14 的 6 点需求 + 后续 7 项决策（§0.3）。
>
> 关联文档：
> - `docs/personal_meeting_design.md`：LiveKit 选型/部署/旧版个人会议设计。本文复用其 LiveKit 调研与 JWT 事实，但业务设计不同（§2.7）。
> - `docs/webroot_feature.md`：`livachat_url://webroot/...` 的渲染期转换。**本功能不再使用 `livachat_url://` 会议链接**，App 端本期不改。
> - `docs/webmeeting_design.md`：Web 会议 SPA 的既有设计。

## 0. 结论摘要

### 0.1 需求到设计的映射

| # | 需求 | 设计落点 | 归属 |
|---|---|---|---|
| 1 | 用户通过自然对话创建/修改会议；创建必须提供**会议时间**和**会议主题**；Prompt 明确提示缺项 | Monitor Prompt 增加会议段：缺时间/主题时用 `message` 追问；齐全时派生 `job_request`；后台任务调用会议工具 | `services/agent_relay/prompts/*`、`prompt_hints.py` |
| 2 | lvdata_mcp 新增 `create_meeting`、`remove_meeting`、`get_meetings`；修改 = 删除旧会议 + 创建新会议 | `tools.py` 注册 3 个工具；`MeetingStore` 读写；`schemas.py` 参数模型 | `tools/lvdata-mcp/` |
| 3 | lvdata DB 新建 `meetings` 表；有开始/截止时间；结束后 4 小时过期；提前入会不超过 30 分钟；**硬删除** | `MEETINGS_SQL` 加入 `V2_DDL`；`start_at_ms`/`end_at_ms`；BFF 签发 token 时执行窗口策略 | `ddl.py`、`services/bff/meeting/` |
| 4 | lvdata REST 按会议号查会议；LivaChat server 提供 BFF `join_meeting`；Web 调用后拿到 LiveKit URL + token | 新增 `GET /api/meetings/{meeting_number}`（Bearer）；新增 `POST /api/mylife/join_meeting`；BFF 调 lvdata REST，窗口校验后签发 JWT | `server.py`、`bff/meeting/` |
| 5 | Agent 创建后通过现有 `ai_text` 返回 Markdown：主题、时间、会议号、入会 URL；**URL 是 `https://<external_server_addr>/meeting/<meeting_number>`**；本期不改 Android/iOS App | 后台任务最终文本 → `JobDelivery` → `PushMessage` → `ai_text`；URL 由 Prompt hint 从 `meeting.external_server_addr` 注入并用工具返回会议号拼接；Web 自动预填、仍可手输 | `services/agent_relay/`、`apps/web/meeting/` |
| 6 | 以 codebase 为基础，不猜测 | §2 先列已验证事实，§3 之后才是设计；决策/限制集中在 §15 | 本文 |

### 0.2 关键结论

1. **会议数据归 lvdata-mcp**：`meetings` 表放在 `lvdata_inventory.db`；lvdata-mcp 是唯一写入方。BFF 不直接读表，只通过 lvdata REST 查询（与现有 `events/remove` 的“BFF → lvdata REST”模式一致）。
2. **BFF 是 token 的唯一签发方**：`livekit_key`/`livekit_secret` 只存在于 LivaChat server 配置；lvdata-mcp 和 Web 都不接触 secret。会议是否存在、是否在入会窗口内，由 BFF 在签发前判定。
3. **`join_meeting` 是公开端点（不要求 App token）**：Web 会议页在系统浏览器/Safari 中打开，拿不到 App 的 `token`/`device_id`；如果复用现有 `get_principal`，Web 会直接 401。**会议号本身就是能力凭证**，配合 8 位随机、30 分钟/4 小时窗口、每 IP 限流和短 TTL 收敛风险（§6.6）。
4. **会议号同时是 LiveKit room 名**：BFF 用 8 位会议号作为 `VideoGrants.room`，不额外创建/管理 LiveKit 房间；第一个持有效 token 的参与者加入时由 LiveKit 自动建房。不需要 RoomService，也不需要 `livekit_api_url`。
5. **Web 端复用现有 `tokenMode=server`**：`HttpTokenProvider` 已支持从服务端拿 `serverUrl` + `participantToken`；需要改的是请求/响应字段、`livekitUrl` 在 server 模式可选、错误体解析。**Home 不发随机 room；链接携带 8 位会议号（路径 `/meeting/<meeting_number>`），Web 启动时自动预填，用户仍可手输；不做 `#m=`/query 预填**（§19）。
6. **App 端本期不改**：Agent 直接输出 `https://<external_server_addr>/meeting/<meeting_number>`，现有 App 的 Markdown 链接渲染即可点击。iOS 非 webroot http(s) 已走 `SFSafariViewController`；Android 现有链接统一进 `WebViewScreen`，而该 WebView 没有实现 `onPermissionRequest`，**Android 应用内打开可能无法使用摄像头/麦克风**（已知限制，§8.3）。后续 App 版本再把会议链接改为 Custom Tabs/系统浏览器。
7. **Agent 侧不新增 wire 协议**：Monitor 的 `message` 和后台任务最终文本都走现有 `ai_text`；会议通知就是普通 job result，沿用 `msg_agent_job_<job_key>` 幂等键（§9.4）。
8. **窗口策略只在一处执行**：`join_early_s=1800`、`expire_after_end_s=14400` 只作为 BFF 配置；lvdata 只存绝对时间，不做“能不能入会”的判断。Agent 的提示文案从同一份 BFF 配置生成，避免 Prompt 与实现漂移。
9. **会议号采用 8 位数字**（用户已确认）：存储/校验为 `^\d{8}$`，不再分组加连字符；约 26.4 bit 熵，必须保留限流与窗口控制（§6.6、§11）。
10. **删除是硬删除**（用户已确认）：`remove_meeting` 直接 `DELETE` 行；删除后 BFF 只能得到 404。没有 `status=cancelled` 状态，也没有“已取消但保留记录”的语义。
11. **本期不做**：账号/通讯录、主持人/嘉宾角色、邀请 token、口令、LiveKit Webhook、RoomService、录制、参与人列表、重复会议、日历同步、最大人数硬限制、多设备归属隔离、Android/iOS App 修改。

### 0.3 用户已确认决策

| # | 决策 | 影响 |
|---|---|---|
| U1 | Web Home **不需要随机产生 room，必须输入** | 删除现有 `Generate a random meeting number` 按钮/`randomRoomId` 用法；首页会议号输入必填 |
| U2 | **本期不修改 Android/iOS App** | 不新增 `livachat_url://meeting` 转换；直接输出 https 链接；Android WebView 媒体权限是已知限制 |
| U3 | Web 会议基址是 `https://<external_server_addr>/meeting/`；**2026-11-15 增强：链接在基址后追加 8 位会议号**（`/meeting/<meeting_number>`）；`external_server_addr` 在 LivaChat server 的 `meeting` section 配置 | Agent 通知不再用 `livachat_url://meeting`；Prompt hint 注入基址并用工具返回的会议号拼接（§19） |
| U4 | `meeting` section 字段：`external_server_addr`、`livekit_key`、`livekit_secret`、`livekit_server` | 配置模型/校验/BFF token 按这些名字实现 |
| U5 | **链接携带 8 位会议号（路径段）；不带号或非法时忽略，仍允许手输** | Web 启动读取 `/meeting/<number>` 预填；通知正文仍写会议号；旧 `/meeting/` 兼容（§19） |
| U6 | **会议号码：8 位数字** | 所有正则/生成/示例改为 `^\d{8}$`；不分组加连字符 |
| U7 | **删除使用硬删除** | 无 `status`/`cancelled`；删除后 404 |
| U8 | **多设备目前不需要会议归属隔离** | 会议表无 owner；服务器级共享；`get_meetings` 返回该 server 的全部会议 |
| U9 | 其他 follow 推荐 | 公开 join、默认结束 +60min、`get_meetings` 默认未结束、token TTL 600s、不做最大人数硬限制、通知含会议号 |

### 0.4 与旧版 `personal_meeting_design.md` 的关系

旧文档的 LiveKit 调研（版本、JWT claims、部署、浏览器/WebView 限制）仍然有效；但其业务设计（`chat_server.db`、`MeetingService`、邀请 token、host/guest、webhook、RoomService、`join_early_s=600`、`join_grace_s=3600`）与本需求不同。本文不修改旧文档，只复用其**基础设施结论**，并在 §2.7 列出差异。

---

## 1. 需求与术语

### 1.1 需求拆解

| # | 需求要点 | 必须满足 | 验收方式 |
|---|---|---|---|
| 1 | 用户通过自然对话创建/修改会议；创建/预约必须提供会议时间和会议主题；Prompt 明确提示缺项 | 缺任一项时 Agent 不得创建，必须追问；两项齐全才创建 | 对话测试：只说“帮我约个会” → 追问；说“明天下午3点产品评审” → 创建 |
| 2 | lvdata_mcp 新增 `create_meeting`、`remove_meeting`、`get_meetings`；修改 = remove old + create new | 三个工具可被 Agent 调用；错误结构化；修改后旧会议号失效、新会议号生效 | MCP 工具测试；对话修改测试 |
| 3 | `meetings` 表存会议；开始/截止时间；结束后 4 小时过期；提前入会不超过 30 分钟；硬删除 | 会议持久化；窗口外 BFF 拒绝签发；删除后 404 | DB 测试；BFF 时间边界测试 |
| 4 | lvdata REST 按会议号查会议；LivaChat server 提供 BFF `join_meeting`；Web 调 BFF 拿 LiveKit URL + token | REST 存在且鉴权；BFF 校验后签发 token；Web 能入会 | 集成测试 + 真机 Web 入会 |
| 5 | Agent 创建后通过 `ai_text` 返回 Markdown：主题、时间、会议号、入会 URL；URL 是 `https://<external_server_addr>/meeting/<meeting_number>`；不改 App | 通知到达 App；https 链接可点击；Web 打开后自动预填会议号，用户可手输 | 端到端测试 |
| 6 | 以 codebase 为基础，不猜测 | 设计引用现有文件/行为；不确定项显式列出 | 本文 §2、§15 |

### 1.2 术语

| 术语 | 含义 |
|---|---|
| 会议主题（topic） | 用户给会议起的名字，1–100 字符，必填 |
| 会议时间 | 开始 `start_at_ms` + 结束 `end_at_ms`；用户只需提供“会议时间”，结束时间可缺省为开始 + 60 分钟 |
| 会议号（meeting number） | **8 位数字**（如 `12345678`）；同时作为 LiveKit room 名；是入会能力凭证 |
| 入会窗口 | `[start_at_ms - 30min, end_at_ms + 4h]`（含边界）；窗口外 BFF 不签发 token |
| meeting token | LiveKit JWT，包含 `roomJoin`、`room`、`identity`、`name`、`exp` 等 claims；短 TTL |
| LiveKit server URL | Web 客户端连接的 `wss://` 地址；由 BFF 从 `meeting.livekit_server` 返回 |
| external_server_addr | LivaChat server 的公网基址（scheme + host + port）；Agent 通知里的入会 URL = `{external_server_addr}/meeting/<meeting_number>`（不带号的基址仅作兼容入口） |
| BFF | LivaChat server 的 `/api/mylife/*` 查询/执行面；本文新增 `join_meeting` |
| `ai_text` | Agent relay → gateway → App 的文本推送帧；Agent 回复和后台任务结果都走它 |

### 1.3 关键约束

- 只允许两个必填字段：会议时间、会议主题。结束时间不是必填；Agent 可以按用户说的“到几点”传入，否则默认 60 分钟。
- 会议结束后 4 小时不能再入会；提前入会不能超过 30 分钟。这是**签发 token 时**的判定，不是 LiveKit 房间生命周期。
- 修改会议 = `remove_meeting` 旧会议 + `create_meeting` 新会议；会议号会变化，旧链接/旧会议号失效。
- Web 会议页由 LivaChat server 托管在 `/meeting/`；Web 从 BFF 拿 `livekit_server` 和 token。
- **链接携带会议号**：Agent 通知的 URL 是 `{external_server_addr}/meeting/<meeting_number>`；Web 启动时预填，用户仍可手输。会议号缺失/非法时忽略，不阻断手工输入（§19）。
- **App 端本期不改**：通知里的 URL 是普通 https 链接，由现有 App 的链接渲染/打开逻辑处理。
- 文档/代码不得写入个人隐私信息；服务器地址、LiveKit 主机、密钥一律用占位符或环境变量。

---

## 2. Codebase 现状（已验证事实）

> 本节只写已在代码中确认的事实；设计提案从 §3 开始。
>
> 快照说明：§2 记录的是 2026-11-14 实现前的 codebase 状态；基础功能已实现（§18），
> 2026-11-15 的 URL 增强见 §19。涉及“链接不带会议号”“无 `/meeting` 挂载”
> “5 个工具”等旧事实时，以 §18/§19 为准。

### 2.1 lvdata-mcp

| 事实 | 证据 |
|---|---|
| Starlette + FastMCP（Streamable HTTP，`stateless_http=True`）；路由含 `/healthz`、`/readyz`、`/api/status`、`/api/analysis/trigger`、`/api/events/remove` 和 MCP 路由 | `tools/lvdata-mcp/src/lvdata_mcp/server.py:36-206` |
| `/mcp` 与 `/api` 共用 Bearer token 中间件；`/healthz`、`/readyz` 不鉴权；每 IP 并发限流 | `server.py:208-260` |
| 当前对外 MCP 工具恰为 5 个，全部只读：`search`、`semantic_search`、`get_timeline`、`get_event`、`get_recent_events` | `tools.py:40-...`；`tests/test_tools.py:76-82` 锁定集合 |
| MCP `initialize` instructions 明写 `"LivaChat's recorded multimodal lifelog data. Read-only."`，测试按字面量锁定 | `server.py:36-44`；`tests/test_tools.py:455-484` |
| 工具注册模式：`@mcp.tool(annotations={"readOnlyHint": True}, description=...)`；工具函数为 `async`，内部同步调 `DataStore` | `tools.py:43-...` |
| 参数模型集中在 `schemas.py`（pydantic `BaseModel` + `Field` + `field_validator`） | `schemas.py` |
| DDL 由 `ddl.ensure_schema(conn, default_timezone, timezones)` 幂等执行；`V2_DDL` 是 DDL 元组 | `ddl.py:240-242`、`ddl.py:343-377` |
| `serve`/`reindex`/`eval` 启动前都会调 `_ensure_schema`，用可写连接 + WAL + `busy_timeout=5000` | `main.py:197-208`、`main.py:412-415` |
| 数据库默认 `<root_dir>/lvdata_inventory.db`，WAL | `config.py:148-158`、`configs/dev/lvdata_mcp.yaml` |
| `DataStore` 是只读 facade（`mode=ro`）；写操作（如 `EventRemover`）另开可写 sqlite 连接 | `store.py`、`remove.py:32-42` |
| REST 错误统一 `{"error": {"code", "message"}}`；`error_result` 支持额外字段 | `tools.py:26-28`、`server.py` |
| 现有时间工具：`now_iso`、`ms_to_local_or_none`、`date_window_ms`、`store.effective_tz` | `timeutil.py`、`store.py`、`tools.py` |

### 2.2 LivaChat server / BFF

| 事实 | 证据 |
|---|---|
| BFF 路由前缀 `/api/mylife`，注册 timeline/graph/search/media/remove/agent_jobs 子路由 | `services/bff/router.py:14-20` |
| `BffError` 统一错误信封；`_STATUS_BY_CODE` 映射 400/401/404/409/416/502/503 | `services/bff/router.py:22-36` |
| `get_principal` 从 query 取 `token`/`device_id`，用 `gateway.auth.Authenticator` 校验；失败 401 | `services/bff/common/deps.py:57-70` |
| BFF → lvdata-mcp 的现有客户端：httpx + `Authorization: Bearer`；URL 未配置 → 503；连接失败/非 200 → `upstream_unavailable` 502；上游 400 → `bad_request` | `services/bff/common/lvdata_api.py:15-45` |
| `BffConfig` 已有 `lvdata_api_url`、`lvdata_api_token`、`lvdata_api_timeout_s`；`AppConfig` 已组合 `bff`、`webroot`、`agent_relay` 等 | `services/shared/config/models.py:270-329` |
| 配置校验集中在 `services/shared/config/validation.py::validate_config`（`ConfigError` fail-fast） | `validation.py:17-28` |
| `/webroot` 是唯一静态挂载点；**没有 `/meeting` 挂载**；没有 meeting/LiveKit 业务代码 | `services/app/application.py:428-488`；`rg -i livekit services` 无业务代码 |
| `services/pyproject.toml` **未声明** `livekit-api`（已有 `PyJWT`、`httpx` 等） | `services/pyproject.toml` |
| server 配置中 BFF 已指向本地 lvdata-mcp；webroot 已可启用 | `configs/dev/server.yaml:273-284` |

### 2.3 Agent relay / `ai_text`

| 事实 | 证据 |
|---|---|
| Prompt 模板在 `services/agent_relay/prompts/`；Hermes/OpenClaw 各有 monitor 与 background task 模板 | `prompts/hermes_monitor.md`、`prompts/openclaw_monitor.md`、`prompts/job_task.md`、`prompts/openclaw_background_task.md` |
| Monitor 契约只允许三种响应：`noop`、`job_request`、`message`；需要工具/长任务时派生 `job_request`，不在主 turn 调 MCP | `hermes_monitor.md:18-30`、`openclaw_monitor.md:19-31` |
| Monitor/任务 prompt 用 `string.Template.safe_substitute` 注入 `$timezone_hint`、`$agent_name_hint`、`$webroot_hint` 等 | `adapters/hermes.py:2599-2635`、`adapters/openclaw.py:1475-1515` |
| `prompt_hints.py` 是共享提示文案（`timezone_hint`、`agent_name_hint`、`webroot_hint`），Hermes/OpenClaw 同文 | `prompt_hints.py:46-...` |
| 后台任务模板明确列出 `lvdata` MCP tools（当前 5 个），服务地址 `127.0.0.1:8090/mcp` | `job_task.md:12-20`、`openclaw_background_task.md:12-20` |
| 后台任务完成 → `JobDelivery.deliver` 构造 `PushMessage(text=result_text, proactive=True, job_key=...)` | `delivery.py:70-110` |
| `PushPublisher.publish` 把 `PushMessage` 构造成 `ai_text` 帧 | `publisher.py:46-153` |
| Monitor 的 `message` 回复经 `SessionManager._publish_reply` 同样走 `PushPublisher` | `session_manager.py:407-436` |
| 现有 prompt 契约测试会断言 `$timezone_hint`/`$webroot_hint` 在禁用时不出现在渲染结果中 | `services/tests/unit/agent_relay/test_hermes_contract.py:147-229` |

### 2.4 Web meeting（`apps/web/meeting`）

| 事实 | 证据 |
|---|---|
| `MeetingConfig` 分 `client`/`server` 两种 token 模式；`ServerTokenConfig` 含 `tokenEndpoint` | `apps/web/meeting/src/config.ts:14-35` |
| **`livekitUrl` 当前是 common 必填字段**，server 模式也要求 | `config.ts:14`、`config.ts:257-258` |
| `HttpTokenProvider` POST `tokenEndpoint`，body 是 `{roomName, participantName}`；`credentials:'same-origin'`；响应接受 `serverUrl|livekit_url`、`participantToken|token` | `src/credentials/httpTokenProvider.ts:44-60` |
| 响应校验会拒绝 `roomName`/`participantName` 与请求不一致的返回 | `httpTokenProvider.ts:24-41` |
| `App.tsx` 初始 stage 是 `{kind:'home'}`；**没有读取 URL hash/query 的预填逻辑**（v3 将增加路径预填，§19） | `src/App.tsx:46-53` |
| `Home` 支持 `defaultRoomName`/`defaultParticipantName` props，但只在 App 内部回填（Back/Leave）时使用 | `src/components/Home.tsx:19-70`、`App.tsx:117-140` |
| `Home` 有 `Generate a random meeting number` 按钮：`handleGenerate` 调 `randomRoomId()` 生成 `xxxx-xxxx` | `Home.tsx:60-64`、`validation.ts:1-22`、`messages.ts:13` |
| 会议号校验是 `^[a-z0-9][a-z0-9_-]{0,63}$`；首页占位符 `123-456-789` | `src/validation.ts:1-4`、`messages.ts:4-8` |
| `PreJoinStage` 在用户点击加入时调用 `provider.getConnectionDetails({roomName, participantName})` | `src/components/PreJoinStage.tsx:39-43` |
| `HttpTokenProvider` 目前不解析错误响应体，只抛 `HTTP <status>` | `httpTokenProvider.ts:56-58` |
| Vite `base: '/meeting/'`；dev 端口 5174；构建有 client secret 防护；CSS target 兼容 Chromium 99 | `vite.config.ts` |
| `public/config.json` 当前是 client 模式 demo（不应作为生产配置） | `apps/web/meeting/public/config.json` |

### 2.5 App 端现状（本期不改）

| 事实 | 证据 |
|---|---|
| Android Markdown 链接：只放行 http/https，`onOpenLink` 统一导航到 `WebViewScreen` | `MarkdownContent.kt:76`、`NavGraph.kt:63,85,166,208` |
| Android `WebViewScreen` 的 `WebChromeClient` 只实现进度/标题回调，**没有 `onPermissionRequest`**；WebView 默认拒绝摄像头/麦克风 | `WebViewScreen.kt:212-219`；旧文档 §3.7 |
| Android 已有 `CustomTabsUrlOpener`（Custom Tabs，失败回退 `ACTION_VIEW`），但本期不改链接路由 | `ui/legal/UrlOpener.kt:13-22` |
| iOS `presentWebLink`：webroot URL → 应用内 `WebPage`；其他 http(s) → `SafariLinkPresenter`（`SFSafariViewController`） | `AppContainer.swift:225-260` |
| iOS `ServerAddress.isWebrootURL` 只匹配 `/webroot/`；`/meeting/` 会走 Safari | `Core/Settings/ServerAddress.swift:111-118` |
| Android/iOS 只识别 `livachat_url://webroot/`；没有 `livachat_url://meeting` 转换 | `ServerLinks.kt:19`、`LivachatURLConverter.swift:3` |

### 2.6 静态托管

- `services/app/application.py:466-488` 只挂载 `/webroot`，没有 `/meeting`。
- Web 会议构建产物是 Vite SPA，`base='/meeting/'`；仓库没有把 `apps/web/meeting/dist` 挂到 LivaChat server 的代码/配置。
- 因此 `https://<external_server_addr>/meeting/` 目前会 404；必须新增静态挂载。

### 2.7 与旧版 `personal_meeting_design.md` 的差异

| 维度 | 旧版 | 本需求/本文 |
|---|---|---|
| 存储 | `chat_server.db` 的 4 张表 | `lvdata_inventory.db` 的单张 `meetings` 表 |
| 写入方 | LivaChat server `MeetingService` | lvdata-mcp 的 3 个 MCP tools |
| 入会凭证 | 邀请 token | 8 位会议号；公开 BFF join |
| 身份 | host/guest，`host_device_uuid` | 无角色/无归属；服务器级共享 |
| 时间策略 | `join_early_s=600`、`join_grace_s=3600` | `join_early_s=1800`、`expire_after_end_s=14400` |
| 删除 | 状态机 `ended/cancelled` | **硬删除**；删除后 404 |
| LiveKit 房间 | RoomService 创建/删除 + Webhook | 不调用 RoomService；首个 token 入会时自动建房 |
| Web 入会 | `#t=<invite_token>` | 路径 `/meeting/<meeting_number>`；Web 自动预填，仍允许手输 |
| App 链接 | `livachat_url://meeting/<invite_token>` + 渲染期转换 | **直接 https**：`https://<external_server_addr>/meeting/<meeting_number>`；本期不改 App |
| 可复用 | LiveKit 版本/部署/JWT claims/客户端 SDK/WebView 限制/`livekit-api` 事实 | 同左；业务层重新设计 |

### 2.8 差距清单

| 差距 | 需要新增/修改 | 关键文件 |
|---|---|---|
| 没有 `meetings` 表 | 新增 `MEETINGS_SQL` 并加入 `V2_DDL` | `tools/lvdata-mcp/src/lvdata_mcp/ddl.py` |
| 没有会议读写 | 新增 `meetings.py`（`MeetingStore`，硬删除） | 新文件 |
| 没有会议 MCP tools | 注册 3 个工具；更新 `INSTRUCTIONS`/测试 | `tools.py`、`schemas.py`、`tests/test_tools.py` |
| 没有会议 REST | 新增 `GET /api/meetings/{meeting_number}` | `server.py` |
| 没有 BFF join | 新增 `bff/meeting/` router/service；配置；错误码 | `services/bff/`、`services/shared/config/` |
| 没有 LiveKit 签发依赖 | 增加 `livekit-api>=1.2.2` | `services/pyproject.toml` |
| 没有 `/meeting` 托管 | 新增 `_mount_meeting`；配置 `meeting.web_dir` | `services/app/application.py` |
| Web 有随机按钮/无 server 完整支持 | 删除随机按钮；server 模式校验；错误解析；`livekitUrl` 可选 | `apps/web/meeting/src/` |
| Prompt 不提会议 | 增加 `$meeting_hint` 与模板段；注入 `external_server_addr` | `prompt_hints.py`、`prompts/*.md`、adapters |
| Android WebView 媒体权限不足（链接本身是普通 https，可点击） | **本期不改**；已知限制见 §8.3 | — |

---

## 3. 总体架构

### 3.1 组件图

```
┌────────────────────────────── App (iOS / Android, 本期不改) ─────────────────────┐
│  对话/Agent 消息渲染 (Markdown)                                                  │
│  Agent 输出普通 https 链接: https://<external_server_addr>/meeting/<number>      │
│    iOS: 非 webroot http(s) → SFSafariViewController                              │
│    Android: 现有 onOpenLink → WebViewScreen（媒体权限限制，见 §8.3）              │
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │ HTTPS
                                    ▼
┌────────────────────────────── LivaChat server ──────────────────────────────────┐
│  /meeting/            静态 SPA（apps/web/meeting/dist）                          │
│  POST /api/mylife/join_meeting   公开 BFF（会议号 + 用户名）                      │
│    ├─ 校验 meeting.enabled / 入会窗口 / 会议存在                                  │
│    ├─ GET <lvdata_api_url>/api/meetings/{number}  (Bearer)                       │
│    └─ livekit-api 签发 JWT；返回 livekit_server + token                          │
│  /webroot/            既有 agent 页面托管（不变）                                 │
│  Agent relay           Monitor/后台任务 → ai_text；meeting_hint 注入 external_server_addr │
└───────────────┬───────────────────────────────────────┬─────────────────────────┘
                │ Bearer REST                            │ WSS (WebRTC)
                ▼                                        ▼
┌────────────────────────── lvdata-mcp ──────────┐  ┌──────────────────────────────┐
│  MCP tools: create/remove/get_meetings         │  │ LiveKit server (SFU)          │
│  REST: GET /api/meetings/{number}              │  │  - 校验 JWT                   │
│  SQLite: lvdata_inventory.db → meetings        │  │  - 首个 token 自动建房        │
│  Agent (Hermes/OpenClaw) ── /mcp ──────────────┘  │  - 音视频转发                 │
└────────────────────────────────────────────────┘  └──────────────────────────────┘
```

### 3.2 创建流程（对话）

```
用户: “明天下午3点，产品评审”
  → App 对话链路（ASR/LLM）→ chatlog
  → Agent monitor 读取 chatlog
      ├─ 缺时间/主题 → {"action":"message","text":"还需要…"} → ai_text
      └─ 齐全 → {"action":"job_request", ...}
  → Agent relay 创建后台任务
  → 后台任务调用 lvdata MCP create_meeting(topic, start_time, end_time?, timezone)
  → lvdata-mcp 生成 8 位会议号、写 meetings 表、返回会议详情
  → 后台任务按 meeting_hint 模板生成 Markdown（含会议号 + https://<external_server_addr>/meeting/<number>）
  → JobDelivery → PushMessage → PushPublisher → ai_text
  → App 渲染：主题/时间/会议号/链接
```

### 3.3 入会流程

```
用户点击通知里的 https://<external_server_addr>/meeting/<number>
  → iOS Safari VC / Android WebView（本期不改 App）
  → /meeting/<number> 由 SPA fallback 返回 index.html；Home 读取路径中的 8 位会议号并预填
  → 用户确认/手输会议号 + 姓名（姓名从 localStorage 预填，可改）
  → POST /api/mylife/join_meeting {"meeting_number":"12345678","participant_name":"..."}
  → BFF：
      1. 校验 meeting.enabled、格式、限流
      2. GET lvdata REST /api/meetings/12345678
      3. 校验当前时间在 [start-30m, end+4h]
      4. livekit-api 签发 JWT（room=8 位会议号，identity=随机，name=用户名）
      5. 返回 {serverUrl: livekit_server, roomName, participantName, participantToken}
  → 浏览器 livekit-client.connect(serverUrl, token)
  → LiveKit 校验 JWT；首个参与者自动建房；音视频开始
```

### 3.4 信任边界

| 边界 | 谁能做什么 | 保护 |
|---|---|---|
| App ↔ LivaChat server | App 有 `token`/`device_id`；普通 BFF 接口用 `get_principal` | 现有 `Authenticator` |
| Web ↔ BFF `join_meeting` | 公开；只凭会议号 | 8 位随机、入会窗口、每 IP 限流、短 TTL token |
| BFF ↔ lvdata REST | 内网；Bearer token | `bff.lvdata_api_token` = lvdata `server.token`；lvdata 绑定回环/防火墙 |
| Agent ↔ lvdata MCP | 内网；Bearer token；Agent 可信 | `server.token`；无设备/用户身份（用户已确认不做隔离） |
| Web ↔ LiveKit | 只拿短期 JWT；不接触 `livekit_secret` | JWT `room` 限定会议号；短 TTL |
| lvdata DB | 会议 + 事件数据同库 | 文件权限；备份；不含 LiveKit secret |

### 3.5 关键设计决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 会议记录放 lvdata DB，由 lvdata-mcp 写入 | 需求明确；Agent 通过 MCP 直接管理；BFF 只走 REST |
| D2 | BFF 签发 LiveKit token | secret 只能留在 server；lvdata/Web 都不应持有 |
| D3 | `join_meeting` 公开、凭会议号 | Web 在系统浏览器中打开，拿不到 App token；见 §6.6 |
| D4 | 8 位会议号 = LiveKit room 名 | 少一次 RoomService；首个入会者自动建房 |
| D5 | 窗口策略只在 BFF | 单一执行点；lvdata 只存绝对时间 |
| D6 | Agent 输出 `https://<external_server_addr>/meeting/<meeting_number>` | 本期不改 App；现有 App 能点击 https 链接；链接自带会议号，打开即可预填（§19） |
| D7 | 链接携带会议号（路径段）；正文仍写会议号；缺失/非法时忽略 | 2026-11-15 用户确认（取代 v2 的“链接不带会议号”）；仍允许手输（§19） |
| D8 | 删除硬删除 | 用户已确认；无状态机/软删除 |
| D9 | 不改 Android/iOS | 用户已确认；Android WebView 媒体限制作为已知风险 |
| D10 | 通知走现有 `ai_text` | 零 wire 协议改动；复用 job result 投递/补投 |

---

## 4. 数据模型

### 4.1 `meetings` DDL

加入 `tools/lvdata-mcp/src/lvdata_mcp/ddl.py` 的 `V2_DDL`（幂等）：

```sql
CREATE TABLE IF NOT EXISTS meetings (
    meeting_number TEXT PRIMARY KEY,              -- 8 位数字，如 12345678
    topic          TEXT NOT NULL,                 -- 1..100 字符，无控制字符
    start_at_ms    INTEGER NOT NULL,              -- epoch ms（UTC 绝对时间）
    end_at_ms      INTEGER NOT NULL,              -- epoch ms；必须 > start_at_ms
    timezone       TEXT NOT NULL DEFAULT '',      -- 创建时设备/请求时区，仅用于展示回退
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_meetings_start ON meetings(start_at_ms);
```

设计说明：

- **无 `status`/`cancelled_at`**：删除是硬删除；没有“已取消但保留”的状态（U7）。
- **无 `updated_at`**：会议只创建/删除，不更新；修改 = 删旧建新。
- **无参与人/邀请表**：本需求没有邀请 token、参与人审计、主持人角色。
- **无 `owner_device_id`**：用户已确认多设备不需要归属隔离（U8）；会议是该 LivaChat server 级共享。
- **`timezone` 只用于展示回退**：窗口判断用 `start_at_ms`/`end_at_ms`，与设备时区无关。
- **过期不落库**：`now > end_at_ms + 4h` 由 BFF 计算；表里的记录保留，直到 `remove_meeting` 硬删除或可选清理（§4.6）。

### 4.2 会议号

用户已确认：**8 位数字**（U6）。

- 规范形式：`^\d{8}$`，例如 `12345678`；**不存连字符**。
- 输入/查询规范化：去掉空格、连字符、全角数字转半角；校验 8 位数字；存储/比较用纯 8 位。
- 生成：`secrets.randbelow(90_000_000) + 10_000_000`（首位非 0）；碰撞时重试（最多 5 次）。
- 同时作为 LiveKit `room`；不含姓名/电话/邮箱等 PII。
- 约 26.4 bit 熵。比 9 位低，**必须保留**每 IP 限流、30 分钟/4 小时窗口和短 TTL；若未来安全评审要求更高，可改为 12 位/字母数字（需同步 Web/BFF/lvdata 正则）。

### 4.3 时间与入会窗口

- 存储：`start_at_ms`、`end_at_ms`，UTC epoch 毫秒；不存本地时间。
- 创建时：Agent 传 ISO 8601。带偏移（`+08:00`/`Z`）直接解析；不带偏移时必须同时给 `timezone`（IANA），否则报 `invalid_param`。
- 默认结束时间：`start + 60min`（U9）。
- 最大时长：24h（推荐；超出报 `invalid_param`）。
- 创建时要求 `end_at_ms > now`（已经结束的会议没有入会价值）；`start_at_ms` 可以在过去（会议已开始）。
- 入会窗口（BFF 执行）：

```
join_open_ms  = start_at_ms - join_early_s * 1000       # 默认 1800s = 30min
join_close_ms = end_at_ms   + expire_after_end_s * 1000 # 默认 14400s = 4h
允许 ⇔ join_open_ms <= now_ms <= join_close_ms
```

边界含等号：正好提前 30 分钟允许；正好结束后 4 小时允许；超过才拒绝。

### 4.4 硬删除

| 操作 | 行为 |
|---|---|
| `create_meeting` | `INSERT` 新行；返回会议详情 |
| `remove_meeting` | 在一个事务里 `SELECT` 行 + `DELETE`；返回被删除的会议详情；不存在 → `not_found` |
| `get_meetings` | 只查存在的行；无 `status`/`include_cancelled` |
| `join_meeting` | 查不到 → 404 `meeting_not_found`；找到再看窗口 |

- 重复 `remove_meeting`：第二次 `not_found`；Agent 应把 `not_found` 解释为“会议不存在或已删除”。
- 删除后旧 token 在 TTL 内仍可能首次连接（自托管 LiveKit 没有 Cloud 的 token revocation）；BFF 不再签发新 token。若需要立即踢人，后续加 RoomService（本期不做，§15）。
- 删除后旧会议号可以被后续随机生成复用；概率极低；token TTL 短，风险可接受。

### 4.5 多设备/归属

- 用户已确认：**目前不需要会议归属隔离**（U8）。
- `meetings` 无 owner；`get_meetings` 返回该 LivaChat server 的全部会议；`remove_meeting` 可删除任一存在的会议。
- 这是单用户/受信部署的假设。若未来多用户，需要加 `owner_device_id`、工具入参传 device_id、查询/删除过滤；届时再改表/工具/Prompt。
- BFF `join_meeting` 仍是公开的“凭会议号入会”，与归属无关。

### 4.6 保留与清理

- 会议记录量很小；MVP 不做自动清理。
- 过期会议（`end_at_ms + 4h < now`）仍留在表中；`get_meetings` 默认只返回未结束的会议（`end_at_ms >= now`），要查看历史/刚结束的会议需 `include_past=true` 或日期窗；可选后台/运维脚本删除 `end_at_ms < now - 30d` 的行。
- 备份：`lvdata_inventory.db` 的既有备份流程自动包含 `meetings`。

---

## 5. lvdata-mcp 设计

### 5.1 新模块 `meetings.py`

新增 `tools/lvdata-mcp/src/lvdata_mcp/meetings.py`：

- 会议号生成/规范化（8 位）；
- `meetings` 表读写（可写连接，WAL + `busy_timeout`，模式对齐 `remove.py`）；
- 时间解析/格式化（复用 `timeutil`）；
- 供 MCP tools 和 REST handler 调用。

建议接口：

```python
class MeetingError(Exception):
    def __init__(self, code: str, message: str): ...

@dataclass
class Meeting:
    meeting_number: str
    topic: str
    start_at_ms: int
    end_at_ms: int
    timezone: str
    created_at: str

class MeetingStore:
    def __init__(self, cfg: LvdataMcpConfig): ...
    def create(self, *, topic: str, start_at_ms: int, end_at_ms: int,
               timezone: str) -> Meeting: ...
    def delete(self, meeting_number: str) -> Meeting | None: ...  # 返回被删行
    def get(self, meeting_number: str) -> Meeting | None: ...
    def list(self, *, start_ms: int | None, end_ms: int | None,
             include_past: bool, limit: int) -> list[Meeting]: ...
```

写连接模式（对齐 `EventRemover._conn`）：

```python
conn = sqlite3.connect(self.db_path, timeout=10)
conn.execute("PRAGMA journal_mode = WAL")
conn.execute("PRAGMA busy_timeout = 5000")
conn.row_factory = sqlite3.Row
```

`MeetingStore` 不放进 `DataStore`，避免把只读 facade 改成可写。`DataStore` 仍只读。接线：`server.create_app` 实例化 `MeetingStore(settings)`，`register_tools(mcp, store, vec_index, meeting_store)` 多传一个参数；`api_get_meeting` 闭包持有同一个实例。

### 5.2 MCP tools

在 `tools.py::register_tools` 中注册；参数模型放 `schemas.py`。工具描述用英文（与现有工具一致）。

#### 5.2.1 `create_meeting`

```python
@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    description=(
        "Create a meeting. Requires a topic and a start time (ISO 8601 with "
        "timezone offset, or a naive time plus timezone). end_time is optional "
        "and defaults to start+60 minutes. Returns an 8-digit meeting_number "
        "and local start/end times."
    ),
)
async def create_meeting(
    topic: str,
    start_time: str,
    end_time: str | None = None,
    timezone: str | None = None,
) -> dict:
```

参数：

| 参数 | 类型 | 必填 | 校验 |
|---|---|---|---|
| `topic` | `str` | 是 | trim 后 1–100 字符；拒绝控制字符/换行 |
| `start_time` | `str` | 是 | ISO 8601；带偏移或配合 `timezone`；2000–2100 |
| `end_time` | `str \| None` | 否 | 缺省 `start+60min`；`> start`；时长 ≤ 24h；`end > now` |
| `timezone` | `str \| None` | 条件 | 无偏移时必填；有偏移时可省略；省略时用 `cfg.watcher.timezone` 仅作展示回退 |

成功返回：

```json
{
  "meeting": {
    "meeting_number": "12345678",
    "topic": "产品评审",
    "start_time": "2026-11-15T15:00:00+08:00",
    "end_time": "2026-11-15T16:00:00+08:00",
    "timezone": "Asia/Shanghai"
  }
}
```

注意：**不返回 `join_url`**。入会 URL 由 LivaChat server 的 `meeting_hint` 注入（§9.5/§19）：基址 `https://<external_server_addr>/meeting/` + 工具返回的 8 位 `meeting_number`；lvdata 不知道 `external_server_addr`。

错误：

| code | 场景 |
|---|---|
| `invalid_param` | topic 为空/过长/含控制字符；时间不可解析；end ≤ start；时长超限；会议已结束；时区非法 |
| `internal` | 生成会议号连续碰撞/写库异常 |
| `database_not_initialized` | `meetings` 表不存在（未执行 `ensure_schema`） |

#### 5.2.2 `remove_meeting`

```python
@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    description=(
        "Delete a meeting by meeting number. Hard delete: the row is removed "
        "and the meeting can no longer be joined. Returns the deleted meeting "
        "details. A second delete returns not_found."
    ),
)
async def remove_meeting(meeting_number: str) -> dict:
```

成功返回（带被删除的会议详情，Agent 无需再查一次即可生成删除通知）：

```json
{
  "removed": true,
  "meeting": {
    "meeting_number": "12345678",
    "topic": "产品评审",
    "start_time": "2026-11-15T15:00:00+08:00",
    "end_time": "2026-11-15T16:00:00+08:00",
    "timezone": "Asia/Shanghai"
  }
}
```

错误：`invalid_param`（会议号格式非法）、`not_found`（不存在/已删除）、`database_not_initialized`、`internal`。

#### 5.2.3 `get_meetings`

```python
@mcp.tool(
    annotations={"readOnlyHint": True},
    description=(
        "List meetings. Optional start_date/end_date are local dates "
        "(YYYY-MM-DD, inclusive) interpreted in timezone. Defaults to "
        "meetings that have not ended; include_past=true also returns past "
        "meetings. Returns 8-digit meeting numbers and local start/end times."
    ),
)
async def get_meetings(
    start_date: str | None = None,
    end_date: str | None = None,
    timezone: str | None = None,
    include_past: bool = False,
    limit: int = 50,
) -> dict:
```

语义：

- `start_date`/`end_date` 用现有 `date_window_ms` 换算为本地日界；过滤 `start_at_ms` 落在窗口内的会议（跨日会议按开始日归属）。
- 无日期窗时：
  - `include_past=false`（默认）：`end_at_ms >= now` 的会议，按 `start_at_ms` 升序；刚结束但仍在入会窗口内的会议会落在 `include_past=true` 里，Agent 查“正在进行的会议”时应显式使用 `include_past=true` 或日期窗；
  - `include_past=true`：全部会议，按 `start_at_ms` 降序（最近的在前）。
- 没有 `status`/`include_cancelled`：硬删除后行不存在；不存在就没有“已取消”记录。
- `limit` 1–100，默认 50。
- 返回：

```json
{
  "timezone": "Asia/Shanghai",
  "meetings": [
    {
      "meeting_number": "12345678",
      "topic": "产品评审",
      "start_time": "2026-11-15T15:00:00+08:00",
      "end_time": "2026-11-15T16:00:00+08:00",
      "timezone": "Asia/Shanghai"
    }
  ],
  "total": 1
}
```

错误：`invalid_param`（日期格式非法/逆序/limit 越界）、`database_not_initialized`、`internal`。`timezone` 无效或为空时按现有工具语义回退到服务配置时区（`store.effective_tz`），不报错；响应里的 `timezone` 是实际生效值。

#### 5.2.4 工具集合与 annotations

新增后对外工具集为 8 个：

```
search, semantic_search, get_timeline, get_event, get_recent_events,
create_meeting, remove_meeting, get_meetings
```

必须更新 `tests/test_tools.py::test_tool_set`。`create_meeting`/`remove_meeting` 的 `readOnlyHint=False`；`remove_meeting` 的 `destructiveHint=True`、`idempotentHint=False`；`get_meetings` 的 `readOnlyHint=True`。

### 5.3 REST API

新增 `GET /api/meetings/{meeting_number}`：

- 鉴权：沿用 `bearer_auth_middleware`（`/api` 路径自动覆盖）；BFF 用 `bff.lvdata_api_token`。
- 路径参数规范化：接受 `12345678`、`1234 5678`、`1234-5678`（去空格/连字符）；非法 → 400 `invalid_param`。
- 查询：`MeetingStore.get`。
- 不存在/已被硬删除 → 404 `not_found`。
- 存在 → 200：

```json
{
  "meeting": {
    "meeting_number": "12345678",
    "topic": "产品评审",
    "start_at_ms": 1794726000000,
    "end_at_ms": 1794729600000,
    "timezone": "Asia/Shanghai"
  }
}
```

- 响应头 `Cache-Control: no-store`。
- 不返回 `joinable`/`expired`：窗口策略由 BFF 执行（D5）。REST 只返回原始时间。
- `meetings` 表不存在（未执行 `ensure_schema`）→ 503 `database_not_initialized`。
- 读写用 `asyncio.to_thread` 卸载（与 `api_remove` 同模式），避免阻塞事件循环。

路由注册在 `server.py` 的 routes 列表中，位于 `*mcp_app.routes` 之前：

```python
Route("/api/meetings/{meeting_number}", api_get_meeting),
```

### 5.4 `INSTRUCTIONS` 与文档

当前 `INSTRUCTIONS` 首句是 `"LivaChat's recorded multimodal lifelog data. Read-only."`，加入写工具后不再准确。建议改为：

```text
LivaChat's recorded multimodal lifelog data and meeting scheduler.
Lifelog tools are read-only. Manage meetings with create_meeting /
remove_meeting / get_meetings.
Locate with get_recent_events / get_timeline; search with search ...
Conventions: ...; meeting times are ISO 8601 with timezone offset;
failures return error{code,message} (do not rely only on isError).
```

需要同步：

- `tools/lvdata-mcp/tests/test_tools.py:455-484` 的 `INSTRUCTIONS` 字面量；
- `docs/data/lvdata_mcp_tools.md` 的工具集/§14.2；
- `VERSION`（可选，但建议 bump）。

### 5.5 常量与配置

把会议策略常量放在 `meetings.py`：

```python
MEETING_NUMBER_DIGITS = 8
DEFAULT_DURATION_S = 3600
MAX_DURATION_S = 24 * 3600
MAX_TOPIC_CHARS = 100
```

8 位会议号生成：

```python
def generate_meeting_number() -> str:
    return str(secrets.randbelow(90_000_000) + 10_000_000)
```

碰撞用主键 `IntegrityError` 捕获 + 重试；不要先 `SELECT` 再 `INSERT`（竞态）。

`meeting.enabled` 是 **LivaChat server 侧**的产品开关（BFF/Prompt/Web 挂载）；lvdata MCP 的三个工具仍会注册。若部署要求“硬关闭”（连 MCP 工具都不可见），需要在 lvdata 配置增加同名开关并条件注册工具；本期不做，靠 Prompt 门控 + BFF 503 即可。

### 5.6 并发与一致性

- SQLite WAL 允许 watcher 读与会议写并发；`meetings` 表独立，不参与 events 的 FTS/向量/日汇总。
- 写操作是毫秒级；MCP tool 内部用 `asyncio.to_thread` 包 `MeetingStore` 调用，避免 `busy_timeout` 阻塞事件循环。
- 硬删除在同一个连接/事务里 `SELECT` + `DELETE`，返回被删行；不要先删再查（查不到详情）。
- `create_meeting` 的 Agent 重试可能产生重复会议（新会议号）。MVP 不引入 idempotency key；Prompt 要求“工具超时/失败时先 `get_meetings` 确认是否已创建，再决定是否重试”（§9）。

### 5.7 测试

| 测试 | 内容 |
|---|---|
| DDL | `ensure_schema` 两次幂等；新库/旧库都能建 `meetings` |
| create | 正常创建；默认结束时间；带偏移/naive+timezone；非法 topic/时间/时长/已结束 |
| number | 8 位生成；规范化空格/连字符；非法格式；碰撞重试（mock `secrets`） |
| remove | 正常硬删除；返回详情；第二次 `not_found`；不存在 `not_found` |
| get_meetings | 默认 upcoming；`include_past`；日期窗/时区；limit |
| MCP | 工具集合 8 个；annotations；`INSTRUCTIONS` 字面量 |
| REST | Bearer 缺失 401；非法会议号 400；不存在 404；存在 200 + `Cache-Control`；表缺失 503 |
| 并发 | 两个 create 并发不产生重复主键；WAL 下读不阻塞；delete 返回被删行 |

---

## 6. LivaChat server / BFF 设计

### 6.1 配置

用户已确认 `meeting` section 的四个字段（U4）：

```yaml
meeting:
  external_server_addr: "https://<server-host>:<port>"   # Agent 通知里的 web 基址
  livekit_key: "${LIVEKIT_API_KEY}"                       # LiveKit API key（env）
  livekit_secret: "${LIVEKIT_API_SECRET}"                 # LiveKit API secret（env，绝不提交）
  livekit_server: "wss://<livekit-host>"                  # Web 客户端连接的 LiveKit 地址
```

设计上还需要这些字段（同 section）：

```python
class MeetingConfig(BaseModel):
    """Agent-created meetings + LiveKit token issuance."""

    enabled: bool = False
    external_server_addr: str = ""       # scheme+host+port；无尾斜杠；join 基址 = {addr}/meeting/
    livekit_key: str = ""
    livekit_secret: str = ""
    livekit_server: str = ""             # wss://<livekit-host>
    web_dir: str = ""                    # 构建后的 apps/web/meeting/dist
    token_ttl_s: int = 600               # LiveKit JWT TTL
    join_early_s: int = 1800             # 提前 30 分钟可入会
    expire_after_end_s: int = 14400      # 结束后 4 小时过期
    join_rate_limit_per_min: int = 100    # 每 IP 每分钟 join 尝试上限
```

`AppConfig` 增加：

```python
meeting: MeetingConfig = MeetingConfig()
```

在 `services/shared/config/validation.py::validate_config` 增加 `_validate_meeting(cfg)`：

- `enabled=true` 时：
  - `bff.enabled` 必须为 true；
  - `external_server_addr` 非空，`http://`/`https://`，不带 path/trailing slash；**非 localhost 必须 `https://`**（localhost 允许 http 仅用于本机 dev）；
  - `livekit_key`、`livekit_secret` 非空；
  - `livekit_server` 非空且**必须以 `wss://` 开头**；`ws://` 一律拒绝（包括 localhost/dev——否则 WebRTC 不能正常 work）；
  - **跨字段**：`external_server_addr` 可以是 `http://localhost`（localhost 是安全上下文），但 `livekit_server` 仍必须是 `wss://`；不要因为页面是 localhost/HTTP 就放宽为 `ws://`；
  - `web_dir` 非空且 `<web_dir>/index.html` 存在（fail-fast，语义同 webroot 的“承诺”原则）；
  - `token_ttl_s` 60–3600；`join_early_s >= 0`；`expire_after_end_s >= 0`；`join_rate_limit_per_min > 0`。
- `enabled=false` 时不做这些检查；BFF join 返回 503 `meeting_disabled`，`/meeting` 不挂载，Prompt 不含会议段。

`configs/dev/server.yaml`（tracked 模板默认 disabled；**禁止把 LiveKit 凭据写入 YAML/提交**）：

```yaml
meeting:
  enabled: false
  external_server_addr: ""              # 本地用 LIVACHAT__MEETING__EXTERNAL_SERVER_ADDR 覆盖
  livekit_key: ""                       # 本地用 LIVACHAT__MEETING__LIVEKIT_KEY 覆盖
  livekit_secret: ""                    # 本地用 LIVACHAT__MEETING__LIVEKIT_SECRET 覆盖
  livekit_server: ""                    # 本地用 LIVACHAT__MEETING__LIVEKIT_SERVER 覆盖
  web_dir: "apps/web/meeting/dist"      # 相对仓库根，由 resolve_path 解析
  token_ttl_s: 600
  join_early_s: 1800
  expire_after_end_s: 14400
  join_rate_limit_per_min: 100
```

本地 dev/test 在 gitignored 仓库根 `.env` 中启用（先构建 `dist`）：

```bash
LIVACHAT__MEETING__ENABLED=true
LIVACHAT__MEETING__EXTERNAL_SERVER_ADDR=https://<server-host>[:port]
LIVACHAT__MEETING__LIVEKIT_KEY=<dev-key>
LIVACHAT__MEETING__LIVEKIT_SECRET=<dev-secret>
LIVACHAT__MEETING__LIVEKIT_SERVER=wss://<livekit-host>
```

生产也可用 `${LIVEKIT_API_KEY}` 占位符或同名 `LIVACHAT__` 覆盖；secret 只从环境/`.env` 注入，绝不提交。模板默认 disabled 是为了让未构建 SPA / 未配置 LiveKit 的 fresh checkout 仍能启动；启用时 `web_dir/index.html` 必须存在。

**Agent 通知 URL**：

```python
join_base = f"{cfg.meeting.external_server_addr.strip().rstrip('/')}/meeting/"
# 最终链接：join_base + <meeting_number>（工具返回值，8 位数字）
```

`external_server_addr` 只用于 LivaChat server 渲染 `meeting_hint`（Agent 通知）和部署文档；BFF `join_meeting` 本身不需要它（Web 已经在 `/meeting/` 同源页面）。

依赖：`services/pyproject.toml` 增加 `livekit-api>=1.2.2`（旧设计 §3.1 已核实版本；`with_identity/with_name/with_ttl/with_grants/to_jwt` API 见旧文档 §6.3）。

### 6.2 `join_meeting` 契约

**路由**：`POST /api/mylife/join_meeting`（BFF 前缀下；公开，不依赖 `get_principal`）。

**请求**：

```json
{
  "meeting_number": "12345678",
  "participant_name": "Alice"
}
```

| 字段 | 类型 | 必填 | 校验 |
|---|---|---|---|
| `meeting_number` | `str` | 是 | 规范化后 `^\d{8}$`；也接受空格/连字符（去空格/连字符） |
| `participant_name` | `str` | 是 | trim 后 1–40 code points；拒绝控制字符；不改变大小写/空格（只 trim） |

**成功响应**（200，`Cache-Control: no-store`）：

```json
{
  "serverUrl": "wss://<livekit-host>",
  "roomName": "12345678",
  "participantName": "Alice",
  "participantToken": "<livekit-jwt>"
}
```

- `serverUrl` = `cfg.meeting.livekit_server`。
- `roomName` = 规范化后的 8 位会议号，与 Web 请求中的 `roomName` 一致。
- `participantName` = trim 后的值。
- 字段名与现有 `HttpTokenProvider.parseConnectionDetails` 对齐（同时兼容 `livekit_url`/`token` 别名）。
- 成功响应不返回主题/时间；join 只需 serverUrl/token。

**实现位置**：

```
services/bff/meeting/
  __init__.py
  router.py       # POST /join_meeting；手动解析 JSON，BffError
  service.py      # 校验/窗口/签发 token
  ratelimit.py    # 每 IP 限流
  schemas.py      # JoinMeetingRequest
```

`services/bff/router.py`：

```python
from bff.meeting.router import router as meeting_router
router.include_router(meeting_router)
```

`_STATUS_BY_CODE` 增加：

```python
"meeting_not_found": 404,
"meeting_not_started": 403,
"meeting_expired": 410,
"meeting_disabled": 503,
"rate_limited": 429,
```

`BffError` 建议扩展可选 details（向后兼容），用于返回 `opens_at`/`expires_at`：

```python
class BffError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
```

错误处理器合并 details：

```python
body = {"error": {"code": exc.code, "message": exc.message, **exc.details}}
```

### 6.3 校验顺序与窗口

`service.py` 的顺序（先便宜后昂贵，先防滥用后查库）：

1. `cfg.meeting.enabled`；false → `meeting_disabled` 503。
2. 手动解析 JSON（空体/非对象 → `bad_request` 400）；pydantic `JoinMeetingRequest`。
3. 规范化会议号；非法 → `bad_request` 400。
4. trim/校验 `participant_name`；非法 → `bad_request` 400。
5. 每 IP 限流；超限 → `rate_limited` 429。
6. 调 lvdata REST `GET /api/meetings/{number}`：
   - 404（不存在/已硬删除）→ `meeting_not_found` 404；
   - 400 → `bad_request` 400；
   - 连接失败/超时/非 200 → `upstream_unavailable` 502。
7. 窗口判断（`now_ms = int(time.time()*1000)`）：
   - `now_ms < start_at_ms - join_early_s*1000` → `meeting_not_started` 403，details `opens_at_ms = start_at_ms - join_early_s*1000`；
   - `now_ms > end_at_ms + expire_after_end_s*1000` → `meeting_expired` 410，details `expires_at_ms = end_at_ms + expire_after_end_s*1000`；
   - 否则允许。
8. 签发 token（§6.4）。
9. 返回；不记录 token。

**边界**：`join_open_ms <= now <= join_close_ms` 允许（含等号）。

### 6.4 LiveKit token 签发

```python
import secrets
from datetime import timedelta
from livekit import api

remaining_ms = (end_at_ms + expire_after_end_s * 1000) - now_ms
# 至少 60s，避免窗口末尾 token 立即过期；最多 config TTL
ttl_s = min(token_ttl_s, max(60, int(remaining_ms / 1000) + 30))

identity = f"guest_{secrets.token_hex(8)}"
grants = api.VideoGrants(
    room_join=True,
    room=meeting_number,              # 8 位数字
    can_publish=True,
    can_subscribe=True,
    can_publish_data=True,
    can_publish_sources=["camera", "microphone", "screen_share", "screen_share_audio"],
)
token = (
    api.AccessToken(livekit_key, livekit_secret)
    .with_identity(identity)
    .with_name(participant_name)
    .with_ttl(timedelta(seconds=ttl_s))
    .with_grants(grants)
    .to_jwt()
)
```

要点：

- **只给会议号对应的 room 签发**：`room` 必须来自 lvdata 查询结果，绝不能直接用客户端传的任意 `roomName`，否则任何人都能让 BFF 为任意房间签 token。
- `identity` 随机，不含 PII；`name` 是显示名，会进入 LiveKit UI/日志，接受用户输入但限制长度/控制字符。
- `can_publish_sources` 必须含 `screen_share`（桌面 Chrome/Safari 屏幕共享）；`screen_share_audio` 为系统音频预留。缺 `screen_share` 时 LiveKit 控制栏不显示屏幕共享按钮（2026-11-15 修复）。
- 不设 `room_admin`；不设 `RoomConfiguration`（MVP 不做 max participants/empty timeout）。
- `exp` 由 `with_ttl` 设置；LiveKit 要求 `exp`。
- 自托管 LiveKit 没有 Cloud 的 token revocation：已签发的 token 在 TTL 内仍可能首次连接；硬删除只能阻止**新**签发（§15）。
- 旧文档已核实：token 过期只影响首次连接；连接期间 LiveKit Server 会主动刷新 token（旧文档 §3.3）。因此 600s TTL 不会导致长会议中途掉线。
- **600s 不是会议时长**：它只约束“BFF 签发 → 浏览器首次 `Room.connect`”这一步。Web 在用户点击加入时才调用 `join_meeting`（`PreJoinStage`/`enterMeeting`），正常远小于 600s；1 小时会议连接建立后由 LiveKit 刷新 token 维持，不会因为初始 token 过期而在会中超时。
- 断线重连使用 LiveKit 刷新后的 token；如果刷新尚未发生且原 token 已过期（极端时序），可能需要重新 `join_meeting` 换 token。正常网络/正常会议时长下不是问题。
- 窗口上界签发的 token 有最小 60s TTL，可能晚于窗口最多 60s 首次连接；如需严格，BFF 把窗口上界提前 60s（或把 ttl 最小值降到 1s）。

### 6.5 错误码与客户端文案

| HTTP | code | 场景 | Web 文案（建议） |
|---|---|---|---|
| 400 | `bad_request` | JSON/字段/会议号格式非法 | 会议号或姓名格式不正确 |
| 403 | `meeting_not_started` | 早于开始前 30 分钟 | 会议尚未开放 |
| 404 | `meeting_not_found` | 会议号不存在/已删除 | 会议不存在，请检查会议号 |
| 410 | `meeting_expired` | 晚于结束后 4 小时 | 会议已结束 |
| 429 | `rate_limited` | 每 IP 限流 | 尝试过于频繁，请稍后再试 |
| 502 | `upstream_unavailable` | lvdata 不可达/异常 | 会议服务暂时不可用 |
| 503 | `meeting_disabled` | 功能未启用/配置缺失 | 会议功能未启用 |

没有 `meeting_cancelled`：硬删除后就是 `meeting_not_found`。`meeting_not_started`/`meeting_expired` 的 details 仍由 BFF 返回（epoch ms），供调试/兼容；当前 Web UI 只显示简短文案，不再展示 `opens_at_ms`/`expires_at_ms`（2026-11-15）。

### 6.6 公开端点的安全设计

**为什么不能复用 `get_principal`**：

- Web 会议页由系统浏览器/Safari/Custom Tabs 打开，不是 App 的 WebView 会话；没有 `token` query。
- 如果把 App 的 `token`/`device_id` 拼进链接，会进入浏览器历史、Referer、截图、分享；等于把设备凭证泄露给任何拿到链接的人；且 `token` 是长期凭证，不能这样用。
- 旧设计的邀请 token 是另一种解法，但需求明确用“会议号 + 用户名”调用 BFF；本期不引入邀请 token。

**因此**：

- `join_meeting` 公开，只凭会议号。
- 会议号是能力凭证：拿到会议号的人可以入会；这与“把会议链接发给别人”的产品语义一致。
- 收敛措施：
  1. 8 位随机会议号（约 26.4 bit）；
  2. 仅在 `[start-30m, end+4h]` 窗口内可换 token；
  3. 每 IP 每分钟 `join_rate_limit_per_min`（默认 100）；
  4. token TTL 600s，且被窗口末尾截断；
  5. token 只允许 `room=会议号`，不能进其他房间；
  6. 成功响应不返回主题/时间；错误响应只在必要时应返回 `opens_at_ms`/`expires_at_ms`；
  7. 不记录 token；日志只记录会议号/结果/耗时/IP。
- 8 位比 9 位少约 3.3 bit；**不要降低**限流/窗口/TTL。若安全评审认为 8 位不够，优先改为 12 位/字母数字（会改变用户输入体验）。
- 限流实现：进程内 `collections.deque` 滑动窗口；key 为 `request.client.host`。注意：若部署在反向代理后，`request.client.host` 可能是代理 IP；要么在可信代理层配置 `X-Forwarded-For` 解析，要么接受“全局限流”。不要无条件信任客户端传入的 XFF。
- 多 worker：当前 server 是单进程（SQLite/进程内 push）；内存限流足够。若未来多 worker，改用 Redis/网关限流。

### 6.7 lvdata REST 客户端

在 `services/bff/common/lvdata_api.py` 增加：

```python
async def get_meeting(base_url: str, token: str, timeout_s: float,
                      meeting_number: str, *,
                      transport: httpx.AsyncBaseTransport | None = None) -> dict:
    if not base_url:
        raise BffError("meeting_disabled", "bff.lvdata_api_url 未配置")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/api/meetings/{meeting_number}",
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise BffError("upstream_unavailable", f"lvdata-mcp 请求失败: {exc}") from exc
    if resp.status_code == 404:
        raise BffError("meeting_not_found", "meeting not found")
    if resp.status_code == 400:
        raise BffError("bad_request", _error_message(resp))
    if resp.status_code != 200:
        raise BffError("upstream_unavailable", f"lvdata-mcp HTTP {resp.status_code}")
    return resp.json()["meeting"]
```

复用 `bff.lvdata_api_url`/`bff.lvdata_api_token`/`bff.lvdata_api_timeout_s`；不新增超时字段。`meeting_number` 是 8 位数字，无路径注入风险。

### 6.8 `/meeting` 静态托管

在 `services/app/application.py` 增加 `_mount_meeting(app, config)`，在 `_mount_webroot` 之后调用：

```python
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


def _is_spa_navigation(path: str, scope) -> bool:
    if scope["method"] not in ("GET", "HEAD"):
        return False
    if Path(path).suffix or path.startswith(("assets/", "vendor/")):
        return False
    accept = dict(scope.get("headers") or []).get(b"accept", b"").decode("latin-1")
    return "text/html" in accept


class SpaStaticFiles(StaticFiles):
    """v3：/meeting/<path> 的导航请求回退 index.html；静态资源仍按文件返回/404。"""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404 and _is_spa_navigation(path, scope):
                return await super().get_response("index.html", scope)
            raise


def _mount_meeting(app: FastAPI, config: AppConfig) -> None:
    if not config.meeting.enabled:
        return

    root = Path(config.meeting.web_dir)
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(
            f"meeting web_dir has no index.html: {root}; "
            "build apps/web/meeting and point meeting.web_dir at dist"
        )
    app.mount("/meeting", SpaStaticFiles(directory=str(root), html=True), name="meeting")
```

`_is_spa_navigation(path, scope)` 规则：

- 只处理 `GET`/`HEAD`；
- `Accept` 包含 `text/html`（浏览器导航；fetch/asset 请求不命中）；
- `path` 无文件扩展名（`Path(path).suffix == ""`），且不以 `assets/`、`vendor/` 开头。

要点：

- `StaticFiles(html=True)` 让 `/meeting/` 返回 `index.html`；`/meeting/assets/...` 返回构建资源；`/meeting/config.json` 返回运行时配置。
- **v3 增强**：`/meeting/34479999`（以及 `/meeting/abc` 等非法路径）的导航请求由 SPA fallback 返回 `index.html`（200）；Web 读取路径中的会议号，合法则预填，非法则忽略。缺失的 JS/CSS 仍 404，不返回 HTML。
- Vite dev server 已验证对 `/meeting/<任意段>` 自动回退 index.html，dev 无需额外配置；生产 `StaticFiles` 需要上述子类。
- Vite `base='/meeting/'` 与挂载路径一致。
- `index.html` 缺失即启动失败（与 webroot 的 fail-fast 语义一致）；不要静默 404。
- `web_dir` 推荐生产用绝对路径（如 `/opt/livachat/meeting`）；dev 用相对仓库根路径。
- 静态资源缓存：`index.html`/`config.json` 不应长缓存；带 hash 的 assets 可长缓存。Starlette `StaticFiles` 默认不设缓存头；如需要，在反向代理或自定义 `StaticFiles` 子类处理。MVP 可先不处理。
- `external_server_addr` 只决定 Agent 通知里的 URL；静态挂载路径固定 `/meeting/`。反向代理必须把 **`/meeting/` 前缀**（含 `/meeting/<meeting_number>`）和 `/api/mylife/join_meeting` 路由到 LivaChat server。

### 6.9 测试

| 测试 | 内容 |
|---|---|
| config | enabled 缺 `external_server_addr`/key/secret/server/web_dir/index.html → `ConfigError`；disabled 不校验；`livekit_server=ws://...`（包括 localhost）→ `ConfigError` |
| join 成功 | mock lvdata 200 + 窗口内；解码 JWT 校验 `iss/sub/name/video.room/video.roomJoin/exp` |
| 窗口 | `start-30m-1ms` → 403；`start-30m` → 200；`end+4h` → 200；`end+4h+1ms` → 410 |
| 删除 | lvdata 404 → `meeting_not_found` 404；lvdata 500 → 502；lvdata 400 → 400 |
| 公开性 | 不带 token/device_id 也能调用；带不带都不影响 |
| 限流 | 超过 `join_rate_limit_per_min` → 429 |
| 安全 | 客户端传任意 `roomName` 不会进入 token；token `room` 永远等于查询到的 8 位会议号 |
| 响应 | `Cache-Control: no-store`；不含 secret/topic |
| 长会议 | 连接后跨过初始 token TTL（可用较短 TTL + 等待，或 1 小时会议实测），确认 LiveKit 刷新 token、连接不掉线；断线重连使用刷新后的 token |
| lvdata client | 404/400/连接失败映射；`transport` 注入 `httpx.MockTransport` |

---

## 7. Web meeting 设计

### 7.1 配置变化

当前 `MeetingConfigCommon` 把 `livekitUrl` 作为 common 必填（`config.ts:14`）。server 模式下 URL 由 BFF 返回，Web 不应再要求一份。建议：

```ts
type MeetingConfigCommon = {
  appTitle: string
  tokenTtlSeconds: number
  videoCodec: MeetingVideoCodec
  showPreJoin: boolean
  enableSettingsMenu: boolean
  enableBackgroundEffects: boolean
  backgroundEffectsPreload: BackgroundEffectsPreload
  backgroundImages: BackgroundImageConfig[]
  mediapipeAssetBase: string
  showDebugPanel: boolean
}

type ClientTokenConfig = MeetingConfigCommon & {
  tokenMode: 'client'
  livekitUrl: string
  apiKey: string
  apiSecret: string
}

type ServerTokenConfig = MeetingConfigCommon & {
  tokenMode: 'server'
  tokenEndpoint: string
  livekitUrl?: string   // 可选：BFF 未返回时的兜底
}
```

`loadConfig`：

- client：`livekitUrl` 仍必填，且**只接受 `wss://`**（包括 localhost/demo；`ws://` 不行）；
- server：`livekitUrl` 可选；若提供也必须是 `wss://`；不提供也能启动；
- `createCredentialProvider` 给 `HttpTokenProvider` 传 `fallbackServerUrl = config.livekitUrl ?? ''`。
- 现有 `config.ts::validateLivekitUrl` 允许 `ws://`/`wss://`，本期改为只允许 `wss://`；相应测试（如 `ws://localhost:7880`）改为期望报错。

生产 `config.json` 示例：

```json
{
  "appTitle": "LivaChat Meeting",
  "tokenMode": "server",
  "tokenEndpoint": "/api/mylife/join_meeting",
  "tokenTtlSeconds": 600,
  "videoCodec": "vp8",
  "showPreJoin": false,
  "enableSettingsMenu": true,
  "enableBackgroundEffects": true,
  "mediapipeAssetBase": "/meeting/vendor/mediapipe/",
  "showDebugPanel": false
}
```

不再包含 `apiKey`/`apiSecret`；`livekitUrl` 可省略。BFF 返回的 `serverUrl` 来自 `meeting.livekit_server`。

### 7.2 `HttpTokenProvider` 变化

请求体从内部 `JoinRequest` 映射为 BFF 契约：

```ts
body: JSON.stringify({
  meeting_number: request.roomName,        // 8 位数字（调用方已 normalize）
  participant_name: request.participantName.trim(),
})
```

- 发送前对 `participant_name` 做 `trim()`；`parseConnectionDetails` 的 name 一致性比较也按 `trim()` 后比较。BFF 会 trim 并在响应里返回 trim 后的 `participantName`；如果请求带首尾空格而客户端用原串比较，会误判“mismatched participantName”。

响应解析：

- `parseConnectionDetails` 增加 `fallbackServerUrl` 参数：
  - `serverUrl = validateServerUrl(raw.serverUrl ?? raw.livekit_url ?? fallbackServerUrl)`
  - `validateServerUrl` 改为**只接受 `wss://`**（现有实现允许 `ws:`/`wss:`）；`ws://` 返回 `wss://` 错误，不允许降级。
  - 其余逻辑不变（`participantToken`/`token` 别名、room/name 一致性校验）。
- 错误解析：

```ts
export class TokenEndpointError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly details: Record<string, unknown> = {},
  ) { super(message) }
}
```

`getConnectionDetails` 在 `!res.ok` 时尝试 `res.json()`，读取 `error.code/message` 与额外字段（`opens_at_ms`/`expires_at_ms`），抛 `TokenEndpointError`；非 JSON 时回退 `HTTP <status>`。

### 7.3 Home 页：必须输入，无随机生成

用户已确认：**不需要随机产生 room，必须输入**（U1）。

- **删除 `Generate a random meeting number` 按钮**（`Home.tsx` 的 `handleGenerate`、`nameInputRef` 的生成焦点逻辑、`messages.generateRoom`）。
- **删除 `randomRoomId()` 用法**；`validation.ts` 的 `ROOM_ALPHABET`/`randomRoomId` 若无其他调用可一并删除（避免死代码）。
- `Home` 的会议号输入仍必填；server 模式按 8 位数字校验：

```ts
const MEETING_NUMBER_RE = /^\d{8}$/

export function normalizeMeetingNumber(input: string): string {
  return input
    .replace(/\s|-/g, '')
    .replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0))
}

export function isValidMeetingNumber(input: string): boolean {
  return MEETING_NUMBER_RE.test(input)
}
```

- `Home` 在 `config.tokenMode === 'server'` 时：
  - 提交前 `normalizeMeetingNumber`；非法显示 `messages.meetingNumberFormat`（“Enter an 8-digit meeting number.”）；
  - `maxLength={12}`（允许粘贴带空格/连字符；提交时归一化并校验 8 位）、`inputMode="numeric"`；
  - **输入框为空时不显示 placeholder**（会议号/用户名都不显示提示文字）；删除对应 message 常量。
- **v3 URL 预填（§19）**：
  - 链接格式 `https://<external_server_addr>/meeting/<8位会议号>`；
  - 新增 `meetingNumberFromUrl(pathname, BASE_URL)`：仅接受单个 8 位 ASCII 数字段（可带尾部 `/`），缺失/非法/多级路径 → `null`；
  - `App` 初始 stage 用 URL 值预填 `defaultRoomName`；用户可随时改写/清空；
  - 不做 hash/query 预填；不在手工输入后回写 URL（v1）。
- **v3 姓名持久化（§19）**：
  - 复用现有 key `livachat-meeting:display-name`；
  - 读取时 `trim` + 1–40 code point + 拒绝控制字符；非法/空/存储异常 → 视为未保存；
  - 在 Home 提交校验通过后、PreJoin 提交时、以及连接成功后保存；失败不影响入会。
- **焦点**：粗指针（移动端）**不 autofocus**，避免弹出键盘；桌面细指针聚焦第一个空字段（URL 已预填则聚焦用户名，否则会议号）。
- `defaultRoomName` prop 继续用于 Back/Leave 回填；URL 预填与它共用同一状态。
- client/demo 模式仍可手输任意 room 名（现有 `isValidRoomName`）；生产 server 模式不走随机/自由建房。

### 7.4 错误 UI

`App.tsx` 的 `enterMeeting` catch 分支根据 `TokenEndpointError.code` 映射：

| code | 文案 |
|---|---|
| `meeting_not_found` | 会议不存在，请检查会议号 |
| `meeting_not_started` | 会议尚未开放；不展示 `opens_at_ms` |
| `meeting_expired` | 会议已结束 |
| `rate_limited` | 尝试过于频繁，请稍后重试 |
| `meeting_disabled` | 会议功能未启用 |
| 其他 | `error.message` 或通用失败文案 |

没有 `meeting_cancelled`：删除后是 `meeting_not_found`。`messages.ts` 增加对应常量；时间格式化用 `Intl.DateTimeFormat(undefined, {...})`，跟随浏览器时区。

### 7.5 开发/构建/部署

- **dev**：Vite 5174 与 LivaChat server 不同源。server 模式需要在 `vite.config.ts`（或未跟踪的 `vite.config.local.ts`）加：

```ts
server: {
  host: '127.0.0.1',                 // 与 external_server_addr 对齐
  port: 5174,
  proxy: { '/api': { target: 'https://localhost:8080', secure: false } },
}
```

  `tokenEndpoint: "/api/mylife/join_meeting"` 会经 Vite 代理到 server；同源，无 CORS。
- **生产**：`npm run build` → `dist/`；由 LivaChat server 的 `_mount_meeting` 托管在 `/meeting/`。`config.json` 放在 `dist/`，运行时读取。
- **vendor**：`public/vendor/`（MediaPipe WASM/模型）必须包含在构建产物中；`mediapipeAssetBase` 用 `/meeting/vendor/mediapipe/`。
- **HTTPS/WSS**：浏览器 `getUserMedia` 要求安全上下文；`/meeting/` 必须是 HTTPS（localhost 除外）。**`livekit_server` 必须始终是 `wss://`，localhost/dev 也不例外**；`ws://` 下 WebRTC 不能正常 work，不要使用 `livekit-server --dev` 默认的 `ws://127.0.0.1:7880`，dev 也要给 LiveKit 配 TLS/WSS（或本地 TLS 代理）。
- **external_server_addr**：反向代理必须把 `/meeting/` **前缀**（含 `/meeting/<meeting_number>`）和 `/api/mylife/join_meeting` 路由到 LivaChat server；Agent 通知使用 `{external_server_addr}/meeting/<meeting_number>`。
- **自签证书**：Custom Tabs/Safari 不享受 App WebView 的放行；真机需安装根 CA 或使用可信证书；LiveKit 同理。Android 现有 WebView 的自签放行只覆盖 LivaChat server 的 host:port，不覆盖 LiveKit。
- **反向代理**：如果代理设置 CSP，`connect-src` 必须包含 `wss://<livekit-host>`（以及同源 `/api`）；不要设置 `Permissions-Policy: camera=(), microphone=()` 之类的禁用策略，否则浏览器会拒绝 `getUserMedia`。屏幕共享还需要允许 `display-capture=(self)`（或不设置该指令）。

### 7.6 测试

| 测试 | 内容 |
|---|---|
| config | server 模式无 `livekitUrl` 可加载；client 模式缺 `livekitUrl` 报错；server+apiKey/apiSecret 报错（现有逻辑）；`livekitUrl=ws://...`（包括 localhost）报错 |
| provider | 请求体是 `{meeting_number, participant_name}`；响应别名；缺 serverUrl 用 fallback；错误体解析为 `TokenEndpointError` |
| validation | 8 位规范化（空格/连字符/全角）；非法格式 |
| URL 预填 | `/meeting/34479999`、尾斜杠 → 预填；缺失/7/9 位/字母/多级/非 base 前缀 → null |
| 姓名存储 | 合法值读取；空/超长/控制字符/`localStorage` 异常 → 空；保存时 trim |
| Home | 无随机生成按钮；空/非 8 位不能提交；server 模式输入 8 位后可提交；空输入无 placeholder |
| 焦点 | 粗指针设备不 autofocus；桌面聚焦第一个空字段 |
| UI | 错误码到简短文案映射；不展示 `opens_at_ms`/`expires_at_ms` |
| 构建 | `ALLOW_CLIENT_TOKEN` 防护仍生效；CSS target 不变 |

---

## 8. App 端（本期不改）

用户已确认：**目前不修改 Android/iOS App**（U2）。因此：

### 8.1 链接是普通 https

Agent 通知里的入会 URL 是：

```
https://<external_server_addr>/meeting/<meeting_number>
例：https://<server-host>:<port>/meeting/34479999
```

不是 `livachat_url://meeting`。现有 App 的 Markdown 链接渲染只放行 http/https，因此这个链接可以直接点击；不需要新增 scheme 转换，也不需要 `#m=`。Web 打开后从路径预填会议号（§19）。

### 8.2 iOS 行为

- `AppContainer.presentWebLink` 对非 webroot 的 http(s) 走 `SafariLinkPresenter`（`SFSafariViewController`）。
- `/meeting/`（含 `/meeting/<meeting_number>`）不是 `/webroot/`，`ServerAddress.isWebrootURL` 返回 false，因此会议页会在 Safari VC 中打开。
- Safari VC 的媒体权限行为接近系统浏览器；这是当前 iOS 的可行路径。

### 8.3 Android 已知限制

- 现有 Android 链接统一导航到 `WebViewScreen`（`NavGraph.kt:63,85,166,208`）。
- `WebViewScreen` 的 `WebChromeClient` 没有实现 `onPermissionRequest`（`WebViewScreen.kt:212-219`）；Android WebView 默认拒绝 `RESOURCE_AUDIO_CAPTURE`/`RESOURCE_VIDEO_CAPTURE`。旧设计 §3.7 已指出这一点。
- 因此 **Android 应用内打开 `/meeting/` 可能无法使用摄像头/麦克风**。本期不改 App，所以这是已知限制，不是实现遗漏。
- 过渡方案（不改 App）：
  - 用户在 Android 系统浏览器（Chrome/华为浏览器等）中打开 `https://<external_server_addr>/meeting/<meeting_number>`（自动预填）或 `/meeting/` 手输会议号；
  - 或在后续 App 版本把 `/meeting/` 链接改为 Custom Tabs/系统浏览器（`CustomTabsUrlOpener` 已存在，但需要改链接路由）。
- 该限制应写入发布说明和用户提示（Agent 的会议消息可以附一句“如应用内无法使用摄像头，请在系统浏览器打开链接”）。

### 8.4 后续（不在本期）

- Android 会议链接走 Custom Tabs；
- iOS 如需应用内 WebView，实现 `WKUIDelegate` 媒体捕获授权和自签证书处理；
- 或恢复 `livachat_url://meeting` 渲染期转换（如果未来需要隐藏实际服务器地址）。

---

## 9. Agent Prompt 与通知

### 9.1 现有 Prompt 架构

- Monitor（`hermes_monitor.md`/`openclaw_monitor.md`）：只做决策，输出 `noop`/`job_request`/`message`；需要工具/长任务时派生 job。
- Background task（`job_task.md`/`openclaw_background_task.md`）：执行具体任务，可调 `lvdata` MCP；最终 `text` 是给用户的成品文案，`NOOP` 表示不打扰。
- 共享提示由 `prompt_hints.py` 生成，`string.Template` 注入 `$timezone_hint`/`$agent_name_hint`/`$webroot_hint`；Hermes/OpenClaw 同文。
- 任务结果经 `JobDelivery` → `PushMessage` → `Publisher` → `ai_text`。

### 9.2 必填字段与缺项行为

Monitor Prompt 增加会议段（通过 `$meeting_hint`）：

- 用户表达“创建/预约/安排/修改/取消/查看会议”时：
  - **必须**拿到 `会议时间` 和 `会议主题`；
  - 缺任一项：用 `message` 追问，明确缺什么，不要创建；
  - 两项齐全：派生 `job_request`，在 `spec.task` 里写清主题、开始/结束时间（带时区）、用户时区，并要求后台任务调用对应工具；
  - 不要编造时间/主题；相对时间（“明天下午3点”）按设备时区和当前对话时间解析；无法确定就问。
- 修改会议：派生任务，要求 `get_meetings` 找到旧会议 → `remove_meeting` → `create_meeting`；新会议号会变化，通知里必须给新的会议号，链接用新会议号拼接（`.../meeting/<new_number>`）。如果目标会议已结束/刚结束，`get_meetings` 需 `include_past=true` 或日期窗，否则默认查不到。
- 取消会议：`get_meetings` 找到会议号 → `remove_meeting`；多个候选时先问用户。已结束/刚结束的会议同样需要 `include_past=true` 或日期窗。
- 查看会议：`get_meetings`，按用户语言汇总；用户问“正在进行的会议”时传 `include_past=true` 或日期窗，否则默认只返回未结束的会议。
- 会议消息里的入会 URL 用 `meeting_hint` 注入的基址 + 工具返回的 8 位会议号：`https://<external_server_addr>/meeting/<meeting_number>`；不要用 `livachat_url://`，不要编造服务器地址/会议号。

示例（缺项）：

```json
{"action":"message","text":"可以，请告诉我会议时间和会议主题。例如：明天下午3点，产品评审。"}
```

示例（齐全）：

```json
{"action":"job_request","summary":"正在创建会议","spec":{"task":"调用 lvdata MCP create_meeting 创建会议：topic=产品评审，start_time=2026-11-15T15:00:00+08:00，end_time=2026-11-15T16:00:00+08:00，timezone=Asia/Shanghai。成功后按 meeting_hint 的模板输出最终 Markdown：会议号使用工具返回值，链接为 https://<external_server_addr>/meeting/<meeting_number>；失败则说明原因。"}}
```

Background Prompt 增加：

- 工具列表加 `create_meeting / remove_meeting / get_meetings`；
- 如果任务缺少主题/时间，不要调用 `create_meeting`；直接输出请用户补充的文案；
- `create_meeting` 返回的 `meeting_number`/`start_time`/`end_time` 是权威值，照抄，不要自行换算/拼接；
- `create_meeting` **不返回 join_url**；入会 URL = `meeting_hint` 的基址 `https://<external_server_addr>/meeting/` + 工具返回的 8 位 `meeting_number`；
- 修改按 remove old → create new；创建失败时不要谎报成功——此时旧会议已被硬删除，必须如实告知用户“旧会议已删除，新会议未创建”；
- `remove_meeting` 返回 `not_found` 时停止修改流程，先告知用户“旧会议不存在/已删除”，不要自行创建新会议（除非用户明确要求新建）；
- 创建/修改/删除成功后必须输出最终文案，不要因为“无需打扰”而输出 `NOOP`（用户明确发起了操作，需要结果确认）；只有纯巡检任务才允许 `NOOP`；
- 最终文本用用户语言；链接格式固定 `https://<external_server_addr>/meeting/<meeting_number>`，不新增 query/fragment；会议号必须来自工具返回，不要自行编造。

### 9.3 输出模板

创建成功：

```markdown
会议已创建。

**会议主题**：产品评审
**会议时间**：2026-11-15 15:00–16:00（Asia/Shanghai）
**会议号**：12345678
**入会链接**：[加入会议](https://<server-host>:<port>/meeting/12345678)

打开链接会自动填入会议号（也可手动输入）。提前 30 分钟可入会；会议结束后 4 小时内仍可加入。
```

修改成功：

```markdown
会议已修改（会议号已更新，请使用新的会议号）。

**会议主题**：产品评审（改期）
**会议时间**：2026-11-16 10:00–11:00（Asia/Shanghai）
**会议号**：87654321
**入会链接**：[加入会议](https://<server-host>:<port>/meeting/87654321)

旧会议号已失效。
```

删除成功（硬删除）：

```markdown
会议已删除。

**会议主题**：产品评审
**会议时间**：2026-11-15 15:00–16:00（Asia/Shanghai）
**会议号**：12345678

该会议号已失效，无法再入会。
```

查看列表：

```markdown
你最近的会议：

1. **产品评审** — 2026-11-15 15:00–16:00（Asia/Shanghai）
   会议号：12345678 · [加入会议](https://<server-host>:<port>/meeting/12345678)
2. ...
```

标签（“会议主题”等）可按用户语言本地化；URL 格式固定为 `.../meeting/<meeting_number>`（会议号来自工具返回）。主题是用户输入，Agent 应作为纯文本展示，避免把主题里的 Markdown/链接当作指令。

### 9.4 `ai_text` 投递路径

已验证的现有路径：

```
后台任务完成
  → JobDelivery.deliver (delivery.py:70-110)
  → PushMessage(text=result_text, proactive=True, job_key=..., message_id="msg_agent_job_<job_key>")
  → PushPublisher.publish (publisher.py:46-...)
  → _build_ai_text_frame (publisher.py:125-153)
  → gateway push channel → App ai_text
```

Monitor 的 `message` 回复走 `SessionManager._publish_reply` → `PushPublisher`，同样是 `ai_text`。会议通知不需要新 wire 消息、不需要 App 新协议。

### 9.5 Prompt hint 接线

在 `services/agent_relay/prompt_hints.py` 增加：

```python
def meeting_hint(enabled: bool, *, external_server_addr: str,
                 join_early_s: int, expire_after_end_s: int,
                 verbose: bool) -> str:
    if not enabled or not external_server_addr:
        return ""
    join_base = f"{external_server_addr.strip().rstrip('/')}/meeting/"
    example_url = f"{join_base}34479999"   # 只作格式示例；实际用工具返回的会议号
    # verbose=True: monitor 版——必填字段、缺项追问、job_request 约束
    # verbose=False: background 版——工具列表、输出模板、修改/删除流程
```

建议文案要点（最终逐字稿在实现时评审）：

- Monitor：
  - “创建/预约/修改会议必须拿到会议时间和会议主题；缺哪个就用 message 追问，不要编造。”
  - “两项齐全 → job_request；不要在主 turn 调 MCP。”
  - “修改 = 先删旧再建新；新会议号会变，必须给新的会议号。”
  - “入会 URL 格式为 `{join_base}<8位会议号>`（示例 `{example_url}`）；后台任务必须用工具返回的 `meeting_number` 拼接，正文也要写会议号。”
  - “提前 `{join_early_s//60}` 分钟可入会，结束后 `{expire_after_end_s//3600}` 小时失效。”
- Background：
  - “工具：`create_meeting(topic, start_time, end_time?, timezone?)`、`remove_meeting(meeting_number)`、`get_meetings(...)`。”
  - “start_time/end_time 用 ISO 8601；相对时间按设备时区解析；缺主题/时间不要创建。”
  - “以工具返回的 `meeting_number`/`start_time`/`end_time` 为准，照抄。”
  - “入会 URL = `{join_base}` + 工具返回的 8 位 `meeting_number`（示例 `{example_url}`）；工具不返回 join_url；不要用 livachat_url://。”
  - “最终输出模板（§9.3）；链接携带会议号；查看列表逐条拼接；取消/删除不生成入会链接。”
  - “如果用户可能在 Android 应用内打开且媒体不可用，提示用系统浏览器打开。”

接线：

- `prompts/hermes_monitor.md`、`prompts/openclaw_monitor.md`、`prompts/job_task.md`、`prompts/openclaw_background_task.md` 增加 `$meeting_hint`。
- `adapters/hermes.py::_render_mission/_render_job_task`、`adapters/openclaw.py::_render_nudge/_render_background_task` 的 `safe_substitute` 增加 `meeting_hint=...`。
- `services/app/application.py` 给 Hermes/OpenClaw adapter 设置 `meeting_enabled`/`external_server_addr`/窗口值（或直接传渲染好的 hint 字符串），来源是 `config.meeting`。
- `meeting.enabled=false` 时 hint 为空，模板中的 `$meeting_hint` 渲染为空；现有契约测试风格应断言禁用时内容不出现。

### 9.6 测试

| 测试 | 内容 |
|---|---|
| hint | enabled/disabled；monitor/background 两版文案；`external_server_addr` 尾斜杠归一；窗口分钟/小时换算 |
| 模板 | `$meeting_hint` 在 4 个模板中都被替换；禁用时不出现 |
| 契约 | monitor 仍只允许三种 action；缺项示例是 `message`；齐全示例是 `job_request` |
| 输出 | 模板包含主题/时间/8 位会议号/`https://<external_server_addr>/meeting/<meeting_number>`；不含 `livachat_url://` |
| 兼容 | 现有 `$timezone_hint`/`$webroot_hint` 测试不回归 |

---

## 10. 端到端时序

### 10.1 创建

```
User → App(LLM/chat) → chatlog
Agent monitor → job_request
Agent relay → background job
background job → lvdata MCP create_meeting
lvdata MeetingStore → INSERT meetings（8 位会议号）
lvdata → tool result (meeting_number, start/end, timezone)
background job → result_text (Markdown, 会议号 + https://<external_server_addr>/meeting/<number>)
JobDelivery → ai_text → App
App 渲染链接；用户点击
```

### 10.2 入会

```
App click → https://<external_server_addr>/meeting/<number>
  → iOS Safari VC / Android WebView
Web → GET /meeting/<number> (SPA fallback → index.html)
Home 从路径预填 8 位会议号；姓名从 localStorage 预填；用户可改
Web → POST /api/mylife/join_meeting
BFF → GET lvdata /api/meetings/<number>
BFF → window check
BFF → livekit-api JWT（livekit_key/livekit_secret）
BFF → {serverUrl: livekit_server, roomName, participantName, participantToken}
Web → livekit-client.connect(serverUrl, token)
LiveKit → validate JWT → create/join room
```

### 10.3 修改/删除

```
User: “把产品评审改到明天上午10点”
Agent monitor → job_request
background job → get_meetings(start_date/end_date, include_past) → 按 topic 筛选 meeting_number
background job → remove_meeting(old)       # 硬删除
background job → create_meeting(same topic, new time)
background job → Markdown (new number + .../meeting/<new_number>)
ai_text → App
```

删除：`get_meetings` → `remove_meeting`；通知含会议号，不生成入会链接（或明确说明链接已失效）。

### 10.4 失败矩阵

| 场景 | lvdata | BFF | Web 表现 |
|---|---|---|---|
| 会议不存在/已硬删除 | REST 404 | `meeting_not_found` 404 | “会议不存在” |
| 早于开始-30m | REST 200 | `meeting_not_started` 403 | “会议尚未开放” |
| 晚于结束+4h | REST 200 | `meeting_expired` 410 | “会议已结束” |
| lvdata 不可达 | 连接失败 | `upstream_unavailable` 502 | “会议服务暂不可用” |
| 会议功能关闭 | — | `meeting_disabled` 503 | “会议功能未启用” |
| 限流 | — | `rate_limited` 429 | “尝试过于频繁” |
| LiveKit 不可达 | — | 仍签发 token | Web 连接失败；检查 `wss://`/证书 |
| 浏览器非安全上下文 | — | — | Web 现有 “Secure connection required” |
| Android WebView 媒体被拒 | — | — | 摄像头/麦克风不可用；用系统浏览器（§8.3） |

---

## 11. 安全与隐私

### 11.1 威胁与收敛

| 威胁 | 影响 | 收敛 |
|---|---|---|
| 会议号被猜到/枚举 | 未授权入会 | 8 位随机（约 26.4 bit）+ 窗口 + 每 IP 限流 + 短 TTL；不要降低限流 |
| 消息/链接被转发 | 拿到会议号的人可入会 | 链接本身不含会议号，单独转发链接不足以入会；但转发整条消息会同时暴露会议号——这是“分享会议”的产品语义；如需更强，后续加邀请 token/口令（本期不做） |
| BFF 被当作任意房间 token 工厂 | 可进任意 LiveKit 房间 | token 的 `room` 只来自 lvdata 查询结果；绝不接受客户端 `roomName` 直接签发 |
| `livekit_secret` 泄露 | 可伪造任意 token | secret 只读环境变量；不写 yaml/日志/响应；Web 永不接触 |
| Token 泄露 | TTL 内可入会 | 600s TTL；窗口末尾截断；日志不记录 token |
| 硬删除后旧 token 仍可用 | 删除后短暂可入会 | TTL 短；BFF 不再签发；如需严格，调用 RoomService 删除房间（§15） |
| 会议结束后已连接用户不退出 | 长时间占用 | 本需求只限制“入会”；如需到时踢人，加 RoomService/定时任务（不做） |
| 多设备误删他人会议 | 无归属隔离 | 用户已确认不需要隔离；Server 级共享；Agent 仅操作已知会议号 |
| Prompt 注入（主题含指令/Markdown） | Agent 行为/渲染异常 | 主题 1–100 字符、无控制字符；Agent 把主题当数据；App 链接只允许 http(s) |
| 会议号进入日志 | 内部日志可见 | 会议号是能力凭证；限制日志访问；不记录 token/姓名全量 |
| Android WebView 媒体权限 | 无法入会 | 本期不改 App；用系统浏览器；后续 App 改 Custom Tabs（§8.3） |

### 11.2 隐私

- 文档/配置/代码不得包含真实姓名、邮箱、电话、住址、设备 ID、Apple Team ID、账号、个人服务器 IP/域名、密钥/token；本文用 `<server-host>`、`<livekit-host>`、`${LIVEKIT_API_KEY}` 等占位符。
- `meetings.topic` 是用户内容，可能包含个人信息；DB 访问权限与备份策略沿用 lvdata 数据面。
- LiveKit `identity` 用随机 `guest_<hex>`；`room` 用 8 位会议号；不要把姓名/设备 ID 放进 `identity`/`room`（旧文档 §3.3 的安全要点）。
- `name` 是显示名，会进入 LiveKit UI/日志；限制 1–40 code points，拒绝控制字符。
- BFF 日志：会议号、结果码、耗时、IP；不记录 token、不记录完整姓名（可记录长度/哈希）。
- lvdata 日志：会议号、操作、结果；不记录完整 topic（可记录长度/哈希）。
- `external_server_addr` 是部署地址；文档/示例用占位符，不写真实个人服务器 IP/域名。

### 11.3 传输与部署

- `/meeting/` 必须 HTTPS；**LiveKit 必须始终 `wss://`，包括 localhost/dev**。`ws://` 一律不行：WebRTC 不能正常 work；HTTP 页面还会额外触发 `getUserMedia` 安全上下文限制。
- 自签证书：Custom Tabs/Safari 不享受 App WebView 的放行；真机需安装根 CA 或使用可信证书；LiveKit 同理。Android 现有 WebView 的自签放行只覆盖 LivaChat server 的 host:port，不覆盖 LiveKit。
- lvdata-mcp 绑定回环或内网；Bearer token 必须与 BFF 配置一致；不要把 `/mcp` 暴露公网。
- BFF 反向代理：限流 key 依赖 `request.client.host`；如代理不透明，需在可信代理层处理 XFF，不能盲目信任客户端头。

---

## 12. 可观测性

### 12.1 日志

| 组件 | 记录 | 不记录 |
|---|---|---|
| lvdata MCP | 工具名、会议号、操作结果、耗时、错误码 | topic 全文、token、姓名 |
| lvdata REST | 路径、状态码、耗时、IP | 响应体中的 topic |
| BFF join | 会议号、结果码、耗时、IP、`identity` | token、姓名全量 |
| Web | 现有 console/error；不打印 token | token |

### 12.2 指标（建议，非 MVP 阻塞）

- `meeting_create_total{result}`
- `meeting_remove_total{result}`
- `meeting_join_total{result}`（`ok/not_found/not_started/expired/rate_limited/upstream_error`）
- `meeting_join_latency_ms`
- `meeting_active_numbers`（可选，DB count）

现有 `services/agent_relay/metrics.py`/app metrics 可扩展；若成本高，先日志。

---

## 13. 兼容、迁移与发布

### 13.1 兼容性

- `meetings` 表是新增表，幂等 DDL；不影响 events/watcher/FTS/向量/日汇总。
- lvdata MCP 工具集从 5 → 8；旧 Agent 客户端不受影响。
- BFF 新增路由；现有 `/api/mylife/*` 行为不变；`BffError` 的 details 是向后兼容扩展。
- Web 默认仍是 client 模式；server 模式是配置切换；删除随机按钮不影响 client demo 的手输。
- App **不改**：Agent 输出普通 https 链接，现有 App 能渲染/点击；iOS Safari VC 可用；Android WebView 媒体限制见 §8.3。
- Prompt 新增 `$meeting_hint`；`enabled=false` 时模板渲染为空，行为与现在一致。

### 13.2 发布顺序

1. **lvdata-mcp**：升级代码/依赖；启动时 `ensure_schema` 建 `meetings`；重启服务。验证 MCP 工具集、REST。
2. **LivaChat server**：加 `livekit-api` 依赖、`MeetingConfig`（`external_server_addr`/`livekit_key`/`livekit_secret`/`livekit_server` 等）、BFF join、`/meeting` 挂载；重启 server。验证 `join_meeting`、JWT、`/meeting/`。
3. **Web**：构建 `apps/web/meeting`（server 模式 `config.json`，无随机按钮）；把 `dist` 部署到 `meeting.web_dir`；验证首页手输 8 位会议号、入会。
4. **Agent Prompt**：更新 4 个模板 + `prompt_hints` + adapters；重启 relay；验证创建/修改/删除/查看。
5. **监控**：观察 join 结果码、限流、lvdata 错误、Android 用户反馈。

**无需 App 发布**。但 Android 应用内媒体限制需要在发布说明/用户提示中说明；如需 Android 应用内完整体验，后续单独发布 App 版本（§8.4）。

### 13.3 回滚

- 关闭 `meeting.enabled`：BFF join 返回 503、`/meeting` 不挂载、Prompt 不含会议段；已创建的会议记录保留。
- 回滚 server 版本：移除路由/挂载；lvdata 表保留（前向兼容）。
- 回滚 lvdata：旧版本不认识 `meetings` 表，但不会删除；新工具不可用。
- Web 回滚到 client 模式：需要 `livekitUrl` + apiKey/apiSecret；`livekitUrl` 仍必须是 `wss://`（localhost/demo 也不例外）。仅 demo/受控环境使用，生产不建议。

---

## 14. 测试计划

### 14.1 单元

- lvdata：§5.7。
- BFF：§6.9。
- Web：§7.6。
- Prompt：§9.6。

### 14.2 集成

- lvdata REST + BFF `join_meeting`（真实 SQLite fixture，httpx `MockTransport`/本地 server）。
- JWT 解码：`iss`/`sub`/`name`/`video.room`/`roomJoin`/`canPublish`/`canSubscribe`/`exp`。
- 时间边界：用 freezegun/参数化在 `start-30m`、`end+4h` 前后 1ms 测试。
- 限流：连续 N+1 次请求。
- 硬删除：删除后 lvdata 404、BFF 404、Web 提示不存在。

### 14.3 端到端（手工/自动化）

1. Agent 对话创建会议（时间+主题齐全）→ App 收到 ai_text → 通知含 8 位会议号 + `https://<external_server_addr>/meeting/<meeting_number>`。
2. 缺时间/缺主题 → Agent 追问，不创建。
3. 点击链接 → iOS Safari VC / Android WebView 打开 `/meeting/<meeting_number>`；会议号自动预填（记录 Android 媒体限制）。
4. URL 预填或手输 8 位会议号 → 输入姓名 → 入会成功（iOS/Safari/Chrome）；姓名下次打开自动预填。
   - 打开 `/meeting/abc`（非法）→ 页面正常加载、输入框为空、无错误；仍可手输。
5. 早于开始 30 分钟 → Web 显示未开放。
6. 结束后 4 小时内 → 可入会；超过 → 拒绝。
7. 修改会议 → 旧会议号失效、新会议号可入会。
8. 删除会议 → 旧会议号 404；通知说明已删除。
9. 两个参与者同时入会（不同 identity）→ 不互相踢。
10. 无效会议号/格式 → Web 提示。
11. 关闭 `meeting.enabled` → 503/未挂载/Prompt 无会议能力。
12. Android 应用内打开 → 记录摄像头/麦克风是否可用；不可用时用系统浏览器验证可用。

### 14.4 兼容性

- Chrome/Edge/Firefox/Safari/iOS Safari/Android Chrome/华为浏览器（Chromium 99）——现有 Web 会议支持矩阵；会议页复用同一 SPA，不新增浏览器要求。
- iOS SFSafariViewController；Android WebView（已知媒体限制）/系统浏览器。
- 无 App 新版本要求；Android 完整体验需要后续 App 版本（§8.4）。

---

## 15. 已确认决策与已知限制

### 15.1 已确认决策

| # | 决策 | 状态 |
|---|---|---|
| 1 | Web Home 不随机生成 room，必须输入 | 已确认（U1） |
| 2 | 本期不修改 Android/iOS App | 已确认（U2） |
| 3 | Web URL 基址是 `https://<external_server_addr>/meeting/`；**v3 起链接追加 8 位会议号**（`/meeting/<meeting_number>`）；`meeting` section 配置四个字段 | 已确认（U3/U4；v3 2026-11-15） |
| 4 | 链接携带会议号；缺失/非法忽略，仍允许手输；Web 自动预填 | 已确认（U5；v3 2026-11-15，取代旧“链接不带会议号”） |
| 5 | 会议号码 8 位数字 | 已确认（U6） |
| 6 | 删除使用硬删除 | 已确认（U7） |
| 7 | 多设备不需要会议归属隔离 | 已确认（U8） |
| 8 | 其他 follow 推荐：公开 join、默认结束 +60min、`get_meetings` 默认未结束、token TTL 600s、不做最大人数硬限制、通知含会议号 | 已确认（U9） |

### 15.2 实现细节（按推荐，不再作为产品决策）

| # | 细节 | 推荐 |
|---|---|---|
| 1 | 8 位会议号显示 | 连续 8 位数字 `12345678`；不分组/不加连字符；输入可容忍空格/连字符并归一化 |
| 2 | 默认结束时间/最大时长 | +60min / 24h |
| 3 | `get_meetings` 默认范围 | `end_at_ms >= now`；`include_past=true` 看历史/刚结束 |
| 4 | 限流 | 每 IP 每分钟 100 次（可配） |
| 5 | token TTL | 600s；窗口末尾截断，最小 60s 余量（边界签发的 token 可能晚于窗口最多 60s 首次连接；如需严格，把窗口上界提前 60s） |
| 6 | `remove_meeting` 重复删除 | 第二次 `not_found`；Agent 解释为“不存在或已删除” |
| 7 | `external_server_addr` 格式 | scheme+host+port，无 path/尾斜杠；生产 `https://` |
| 8 | `livekit_server` | **必须始终 `wss://`，localhost/dev 也不例外**；`ws://` 一律拒绝（WebRTC 不能正常 work） |
| 9 | `web_dir` | 构建产物 `dist` 路径；`index.html` 必须存在（fail-fast） |
| 10 | `meeting.enabled` | 安全开关；false 时 BFF 503、`/meeting` 不挂载、Prompt 无会议段 |

### 15.3 已知限制（非待确认）

| # | 限制 | 影响/后续 |
|---|---|---|
| 1 | Android 现有 WebView 没有 `onPermissionRequest` | 应用内打开 `/meeting/` 可能无法使用摄像头/麦克风；本期不改 App；用系统浏览器；后续 App 改 Custom Tabs（§8.3） |
| 2 | 硬删除后已签发 token 在 TTL 内仍可能首次连接 | 自托管 LiveKit 无 token revocation；BFF 不再签发；如需严格，后续加 RoomService 删除房间 |
| 3 | 会议结束后已连接用户不会被踢 | 本需求只限制“入会”；如需到时踢人，后续加 RoomService/定时任务 |
| 4 | 多设备无归属隔离 | 用户已确认不需要；server 级共享；未来多用户需加 owner |
| 5 | 8 位会议号约 26.4 bit | 必须保留限流/窗口/短 TTL；若安全评审要求更高，改 12 位/字母数字 |
| 6 | App 不改，通知链接是普通 https | 现有 App 可点击；iOS Safari VC 可用；Android 见第 1 条 |

---

## 16. 实施任务拆分（建议 PR）

| 阶段 | 内容 | 依赖 | 验收 |
|---|---|---|---|
| P1 | lvdata：`MEETINGS_SQL` + `MeetingStore`（硬删除）+ 3 MCP tools + REST + tests | 无 | MCP 工具集 8 个；REST 200/404/401；DB 幂等 |
| P2 | server：`MeetingConfig`（四个用户字段 + 设计字段）+ `livekit-api` + BFF join + 限流 + `/meeting` 挂载 + tests | P1 | join 成功/窗口/错误码/JWT claims |
| P3 | Web：删除随机按钮；server 模式 config/provider/错误 UI；8 位输入；build | P2 | 首页手输入会；边界错误提示 |
| P4 | Prompt：`meeting_hint`（`external_server_addr`/窗口）+ 4 模板 + adapters + tests | P2 | 对话创建/修改/删除/查看；通知含 8 位会议号 + 直接 URL |
| P5 | 端到端/真机/文档更新（`docs/data/lvdata_mcp_tools.md`、`docs/agent_relay_prompts.md`） | P1–P4 | §14.3 全过 |
| P6 | **v3 增强（§19）**：`SpaStaticFiles`、URL 预填、姓名持久化、去 placeholder、移动端不 autofocus、prompt 链接带会议号 | P2/P4 | §19.9 |

P1/P2 可先行；P3/P4 依赖 P2；无需 App 阶段。P6 为 2026-11-15 新增，基础功能已实现（§18），P6 已实施（§19.12）。

---

## 17. 附录：已验证的关键代码位置

| 主题 | 位置 |
|---|---|
| lvdata 工具注册/错误 | `tools/lvdata-mcp/src/lvdata_mcp/tools.py` |
| lvdata DDL/幂等 | `tools/lvdata-mcp/src/lvdata_mcp/ddl.py:240,343` |
| lvdata REST/auth | `tools/lvdata-mcp/src/lvdata_mcp/server.py:36-260` |
| lvdata 写连接模式 | `tools/lvdata-mcp/src/lvdata_mcp/remove.py:32-42` |
| BFF 路由/错误 | `services/bff/router.py` |
| BFF 鉴权 | `services/bff/common/deps.py:57-70` |
| BFF → lvdata | `services/bff/common/lvdata_api.py` |
| 配置模型/校验 | `services/shared/config/models.py:270-329`、`validation.py` |
| 静态挂载 | `services/app/application.py:428-488` |
| Agent 投递 | `services/agent_relay/delivery.py:70-110`、`publisher.py:46-153` |
| Prompt hints | `services/agent_relay/prompt_hints.py`、`adapters/hermes.py:2599-2635`、`adapters/openclaw.py:1475-1515` |
| Web 配置/provider | `apps/web/meeting/src/config.ts`、`credentials/httpTokenProvider.ts` |
| Web 首页/随机按钮 | `apps/web/meeting/src/components/Home.tsx`、`validation.ts`、`messages.ts` |
| Android 现状（不改） | `MarkdownContent.kt:76`、`NavGraph.kt:63,85,166,208`、`WebViewScreen.kt:212-219` |
| iOS 现状（不改） | `AppContainer.swift:225-260`、`ServerAddress.swift:111` |
| 旧版 LiveKit 调研 | `docs/personal_meeting_design.md` §3、§6.3 |

---

### 附：最小可用配置清单（部署前检查）

- [ ] lvdata-mcp 已升级并重启，`meetings` 表存在，MCP 工具集为 8 个。
- [ ] `bff.lvdata_api_url`/`bff.lvdata_api_token` 与 lvdata 一致。
- [ ] `meeting.enabled=true`。
- [ ] `meeting.external_server_addr` 是公开可达的 `https://<server-host>:<port>`，无尾斜杠。
- [ ] `meeting.livekit_key`/`meeting.livekit_secret` 从环境注入；`meeting.livekit_server` 是 `wss://<livekit-host>`。
- [ ] `meeting.web_dir` 指向已构建的 `dist`，且 `index.html` 存在。
- [ ] `https://<external_server_addr>/meeting/` 和 `/meeting/<8位会议号>` 均可访问（后者由 SPA fallback 返回 index）；`config.json` 是 server 模式，`tokenEndpoint=/api/mylife/join_meeting`，无随机按钮。
- [ ] LiveKit server 已启动，WSS 证书受设备信任。
- [ ] Agent Prompt 已包含 `$meeting_hint`，通知含 8 位会议号，链接为 `.../meeting/<meeting_number>`；`meeting.enabled=false` 时无会议段。
- [ ] Android 用户提示：应用内媒体不可用时用系统浏览器打开。
- [ ] 监控 join 结果码/限流；日志不含 token/完整姓名。

---

## 18. 实施记录（2026-11-14）

按本设计完成了 P1–P5：

- **P1 lvdata-mcp**：`meetings` 表（`V2_DDL`）、`MeetingStore`（8 位号/硬删除/读只读连接）、
  `create_meeting` / `remove_meeting` / `get_meetings`、`GET /api/meetings/{number}`、
  `INSTRUCTIONS`/README/工具文档同步。
- **P2 LivaChat server/BFF**：`MeetingConfig` + `_validate_meeting`（wss-only/跨字段/`web_dir`
  fail-fast）、`BffError.details`、`get_meeting` 客户端、公开 `POST /api/mylife/join_meeting`
  （限流/窗口/JWT/`Cache-Control: no-store`）、`/meeting` 静态托管、`livekit-api` 依赖、
  dev `meeting` section 默认关闭。
- **P3 Web**：`livekitUrl` 在 server 模式可选；`validateLivekitUrl`/`validateServerUrl`
  **wss-only**；`HttpTokenProvider` 发 `{meeting_number, participant_name}`、trim 姓名、
  解析 `TokenEndpointError`；Home 删除随机生成、8 位校验/`inputMode=numeric`/`maxLength=12`；
  App/PreJoin 错误码文案；Vite `/api` 代理；`config.example.json` 改为 server 模式。
- **P4 Agent Prompt**：`meeting_hint`（monitor/background 两版）、四个模板 `$meeting_hint`、
  Hermes/OpenClaw 四路 render、`application.py` 四属性注入；默认 disabled 不回归。
- **P5 验证**：真实 lvdata REST + BFF `join_meeting` 跨进程 E2E 通过（JWT `video.room`/
  `name`/`roomJoin` 校验）；Web 真机/LiveKit 音视频与 Agent 对话 E2E 仍需在具备
  `wss://` LiveKit 与真实设备的环境手工验收。

测试结果：lvdata 全量（排除两个依赖 services/recording 的既有 rebuild 测试）通过；
services unit **1709 passed, 1 skipped**（排除一个与本功能无关的既有
`test_updated_at_monotonic_across_transitions` 毫秒级 flaky 测试）；Web `npm run check`、
`npm test`（51）与 client/server 两种构建通过。最终审查补齐了：

- `external_server_addr` / `livekit_server` 的非法 URL/端口校验（`urlsplit` + `.port`），
  `meeting_hint` 对地址做 `strip()`/尾斜杠归一；
- Python 会议号正则改为 `[0-9]{8}`（`\d` 会匹配 Unicode 十进制数字，如阿拉伯-印度数字）；
  Web 同步用 `NFKC` 归一；
- `join_meeting` 每 IP 限流状态有上限（`max_ips`），避免公开端点状态无界增长；
- Vite dev `/api` 代理改为 `https://localhost:8080` + `secure:false`，与 dev server
  默认 SSL 及 `apps/web/mylife` 的约定一致。

> 2026-11-15：v3 增强（URL 携带会议号、自动填充、姓名持久化）已实施（§19.12）；
> 上表为基础功能（P1–P5）的测试结果。

---

## 19. v3 增强：URL 携带会议号 + 自动填充 + 姓名持久化（2026-11-15，已确认，已实施）

> 状态：设计已确认。**本节取代全文旧口径“链接不带会议号”**（§0.2-5/6、§0.3 U3/U5、
> §2.7、§3.1–3.3、§3.5 D6/D7、§5.2.1、§6.3/6.8、§7.3/7.5、§9.2/9.3/9.5、
> §10、§15.1 决策 3/4、§16 P6）；旧段落保留作演进记录。
> 范围：Web、`/meeting` 静态托管、Agent prompt；不改 BFF/lvdata/Android/iOS App。

### 19.1 目标

1. Agent 通知的入会 URL 携带 8 位会议号：`https://<external_server_addr>/meeting/<meeting_number>`。
2. Web App 加载后读取 URL 路径中的会议号并自动填入；缺失或非法格式时忽略，不报错。
3. **永远允许用户手工输入/修改会议号**。
4. 用户名可通过 `localStorage` 持久化；下次打开能读到就预填，读不到/非法就让用户输入。
5. 会议号、用户名输入框为空时不显示提示文字（placeholder）。
6. 移动端不自动聚焦，避免弹出软键盘。

### 19.2 URL 契约

- 新格式：`https://<external_server_addr>/meeting/<8位ASCII数字>`，例 `.../meeting/34479999`。
- 兼容旧格式：`.../meeting/`（无号）仍可打开，输入框留空。
- URL 中会议号只用于**预填**，不是授权凭证；BFF 仍校验会议存在、入会窗口、限流并签发 token。
- URL 解析严格：单个段、`^[0-9]{8}$`；允许尾部 `/`；query/hash 忽略；多级路径、全角/连字符/字母一律忽略（不归一化 URL）。
- 页面不自动改写 URL；手工输入号码不回写地址栏（v1）。

### 19.3 服务端 SPA fallback（生产）

`StaticFiles(html=True)` 对 `/meeting/34479999` 默认 404；新增 `SpaStaticFiles`（实现位置 `services/app/application.py`）：

- 先按静态文件查找；命中文件直接返回。
- 404 时只对“导航请求”回退 `index.html`：
  - `GET`/`HEAD`；
  - `Accept` 含 `text/html`；
  - 路径无文件扩展名（`Path(path).suffix == ""`）；
  - 不以 `assets/`、`vendor/` 开头。
- 其他 404 保持原样，避免缺失 JS/CSS 返回 HTML（MIME 错误）。
- `/meeting/config.json`、`/meeting/favicon.svg`、`/meeting/assets/*`、`/meeting/vendor/*` 行为不变。
- `/meeting` 仍 307 到 `/meeting/`；`/meeting/` 仍返回 index。
- Vite dev server 已验证对 `/meeting/<任意段>` 自动回退 index.html，dev 无需额外配置。
- 反向代理必须路由 `/meeting/` **前缀**（含 `/meeting/<number>`）到 LivaChat server。

伪代码：

```python
class SpaStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404 and _is_spa_navigation(path, scope):
                return await super().get_response("index.html", scope)
            raise
```

### 19.4 Web：URL 预填

新增纯函数（建议 `src/meetingUrl.ts`，Vitest 可测）：

```ts
export function meetingNumberFromUrl(
  pathname: string,
  baseUrl: string = import.meta.env.BASE_URL,
): string | null
```

规则：去掉 `baseUrl` 前缀与尾部 `/` 后，剩余部分必须是单个 `[0-9]{8}` 段；否则 `null`。

`App` 初始化 stage 时读取一次：

```ts
const [stage, setStage] = React.useState<Stage>(() => {
  const roomName = meetingNumberFromUrl(window.location.pathname)
  return roomName ? { kind: 'home', roomName } : { kind: 'home' }
})
```

- 复用 `Home.defaultRoomName`；用户可随时改写/清空。
- 非法/缺失 → 空输入框，不显示错误。
- 从 PreJoin/Error 返回时沿用 `stage.roomName`，不重新读 URL。
- 不做 hash/query 预填，不监听 popstate；不改 URL。

### 19.5 Web：姓名持久化

- 复用现有 key：`livachat-meeting:display-name`（兼容已存数据）。
- 抽出共享 helper（建议 `src/storage.ts`）：
  - `readStoredParticipantName(): string | null`
  - `saveParticipantName(name: string): void`
- 读取：`trim()` 非空、1–40 code point、拒绝控制字符；非法/空/`localStorage` 异常 → `null`（视为未保存）。
- 保存：
  - Home 表单提交且客户端校验通过后；
  - PreJoin 提交时（该阶段可改名）；
  - App 连接成功后的最终值（服务端 trim 结果）。
  - 全部 try/catch；存储不可用不影响入会。
- 下次打开 `/meeting/` 或 `/meeting/<number>`：能读到且合法就预填；读不到就留空。
- 不存会议号、不跨设备同步；同源 agent webroot 页面理论上可读该 key（姓名敏感度低）。

### 19.6 Web：输入框与焦点

- 删除会议号、用户名两个 `placeholder` 属性；删除 `messages.meetingNumberPlaceholder` / `participantNamePlaceholder`。
- 标签、`aria-*` 保持不变。
- **移动端不自动聚焦**：粗指针（`matchMedia('(pointer: coarse)').matches`）或无法判断时，不设置 autofocus。
- 桌面细指针：聚焦第一个空字段；URL 已预填会议号则聚焦用户名，否则聚焦会议号。
- React `autoFocus` 仅在挂载时生效；用户后续清空不重新聚焦。

### 19.7 Agent Prompt / 通知

- `meeting_hint` 使用基址 `join_base = {external_server_addr}/meeting/`，不再给出固定完整 URL。
- Monitor 版：
  - 创建/预约/修改/查看派生 `job_request`；
  - 要求后台任务用工具返回的 8 位 `meeting_number` 拼接 `{join_base}<meeting_number>`；
  - 正文仍写会议号；不得编造会议号。
- Background 版：
  - `create_meeting` 不返回 join_url；成功后用 `meeting_number` 拼接；
  - `get_meetings` 列表逐条拼接各自会议号；
  - `remove_meeting`/取消：不生成入会链接（会议已删除）；
  - 输出模板示例见 §9.3（更新为带号链接）。
- 旧行为 **“链接不带会议号” 废止**；旧通知仍可用，只是不预填。

### 19.8 兼容性与安全

- 旧 `/meeting/` 链接、已发送通知不受影响。
- 会议号出现在浏览器历史、Referer、访问日志中；本需求接受该 trade-off。会议号不是密钥，BFF 仍执行窗口/限流/token 校验。
- 如部署方希望减少 Referer 泄漏，可在反代/静态层加 `Referrer-Policy: same-origin`（可选，不在本期）。
- `localStorage` 可能被隐私模式/禁用存储阻塞：读取失败即留空，写入失败忽略。
- 不新增 API/DB 字段；`join_meeting` 请求体不变。
- Vite dev 与生产静态托管行为一致（均有 SPA 回退）；生产不回退 asset 路径。

### 19.9 测试

Web（Vitest，node env）：
- `meetingUrl`：`/meeting/34479999`、尾斜杠 → 预填；缺失、7/9 位、字母、多级、非 base 前缀、query/hash → `null`。
- `storage`：合法值读取；空/超长/控制字符/异常 → `null`；保存 trim；写入抛错不崩溃。
- 现有 `npm run check`、`npm test` 不回归。

Services（pytest）：
- `/meeting/34479999` + `Accept: text/html` → 200 index；
- `/meeting/abc` + `Accept: text/html` → 200 index；
- `/meeting/config.json` → JSON；`/meeting/assets/missing.js` → 404；`/meeting/vendor/...` 缺失 → 404；
- `/meeting/` → 200；`/meeting` → 307。
- Prompt：hint 含 `.../meeting/` 基址与 `.../meeting/34479999` 示例；Background 含用 `meeting_number` 拼接规则；不再断言“链接不带会议号”。

手工：
- `http://127.0.0.1:5174/meeting/83883884`：会议号预填；姓名从 localStorage 预填；可手改。
- `/meeting/abc`：页面加载、输入框为空、无错误；手工输入可入会。
- 输入姓名并提交 → 刷新 → 姓名保留；清 localStorage → 空。
- 空输入框无 placeholder；移动端打开不弹键盘。
- `https://localhost:8080/meeting/83883884`（静态 dist）同样生效。

### 19.10 变更文件（预计）

- `apps/web/meeting/src/meetingUrl.ts`（新增）+ 测试
- `apps/web/meeting/src/storage.ts`（新增）+ 测试
- `apps/web/meeting/src/App.tsx`（URL 初始 stage、共享存储 helper）
- `apps/web/meeting/src/components/Home.tsx`（去 placeholder、读存储、提交保存、焦点）
- `apps/web/meeting/src/components/PreJoinStage.tsx`（提交保存姓名）
- `apps/web/meeting/src/messages.ts`（删除 placeholder 常量）
- `services/app/application.py`（`SpaStaticFiles`）
- `services/agent_relay/prompt_hints.py`（链接格式）
- 测试：`test_meeting_mount.py`、`test_prompt_hints.py`、`test_hermes_contract.py`、`test_openclaw.py`
- 文档：本节 + `apps/web/meeting/README.md`

### 19.11 已确认决策（2026-11-15）

| # | 决策 | 结论 |
|---|---|---|
| V1 | 非法 URL 路径 | SPA fallback 加载 App，客户端忽略非法号码（推荐） |
| V2 | 手工输入是否回写 URL | v1 不回写 |
| V3 | 姓名保存时机 | 校验通过即保存 + 连接成功再保存 |
| V4 | 焦点 | **移动端不 autofocus**（用户 2026-11-15 确认）；桌面聚焦第一个空字段 |

### 19.12 实施记录（2026-11-15）

- **Web**：新增 `src/meetingUrl.ts`（严格 8 位路径解析）与 `src/storage.ts`
  （姓名读写/校验/异常回退）；`App.tsx` 用 URL 初始化 home stage；`Home.tsx`
  去 placeholder、读存储、提交保存、按粗/细指针决定 autofocus；`PreJoinStage.tsx`
  提交保存姓名；`messages.ts` 删除 placeholder 常量。
- **Server**：`application.py` 新增 `SpaStaticFiles` + `_is_spa_navigation`；
  `/meeting/<path>` 导航回退 index.html，`assets/`、`vendor/`、`config.json`
  及非导航请求仍按原逻辑（缺失资源 404）。
- **Prompt**：`meeting_hint` 改为 `join_base` + 工具返回的 `meeting_number`；
  monitor/background 都要求拼接；列表逐条拼接；取消/删除不生成入会链接。
- **文档**：本节、`docs/agent_relay_prompts.md` §10、`apps/web/meeting/README.md`。
- **未改**：BFF/lvdata/配置模型/Android/iOS App。
- **2026-11-15 追加修复**：BFF token 的 `can_publish_sources` 增加
  `screen_share` / `screen_share_audio`；此前只有 camera/microphone，导致 server
  模式下 LiveKit 控制栏隐藏屏幕共享按钮。
- **2026-11-15 review 强化 prompt**：修改会议必须先具备新主题/新时间且
  `get_meetings` 命中唯一旧会议，信息不全不得先 `remove_meeting`；0 个候选停止、
  多个候选让用户选择；修改后的链接/正文必须使用 `create_meeting` 返回的新会议号；
  用户主题/时间按纯文本处理，防止 Markdown/指令注入。
- **2026-11-15 review 安全/可靠性**：
  - tracked dev config 不再含 LiveKit key/secret/域名；dev 模板默认 `enabled: false`，
    本地通过 gitignored `.env` 的 `LIVACHAT__MEETING__*` 覆盖启用（需先构建 dist）；
    避免 fresh checkout 启动失败和凭据入库。
  - `join_meeting` 先限流再读 body，并限制 16 KiB；malformed/oversized 请求也计入
    限流，避免公开端点绕过限流/无界缓冲。
  - `external_server_addr` / `livekit_server` 拒绝反斜杠、空白/控制字符和 userinfo，
    避免 `urlsplit` 与浏览器解析差异。
  - lvdata：`try_resolve_timezone` 捕获 `OverflowError`；`MeetingStore` 连接用
    `contextlib.closing` 关闭；topic 拒绝不可 UTF-8 编码的代理字符。

验证结果：

- Web：`npm run check` 通过；11 个测试文件 / **58 passed**；`npm run build` 通过。
- Services unit：**1720 passed, 1 deselected**（既有毫秒级 flaky 除外）；ruff 通过。
  lvdata 全量通过（排除两个依赖 services/recording 的既有 rebuild 测试）。
- Playwright 冒烟：`/meeting/34479999` 会议号预填；`/meeting/abc` 页面正常、输入框空、
  无错误；两个输入框均无 placeholder；姓名可从 localStorage 读取，提交后写入；
  桌面聚焦第一个空字段；粗指针（移动端）不 autofocus 由 `matchMedia('(pointer: coarse)')` 控制。
- 服务端 SPA fallback 单测覆盖：`/meeting/34479999`、`/meeting/abcdefgh`、尾斜杠、
  额外段 → 200 index；`config.json` → JSON；`assets/missing.js`、`vendor/missing` → 404；
  `Accept: application/json` → 404；HEAD → 200。
