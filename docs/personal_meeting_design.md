# Personal Meeting 功能调研与设计（LiveKit）

> **状态**：调研 + 设计草案（未实施）
> **日期**：2026-11-09
> **基线**：`main` @ `f0f3182`（2026-11-09 工作树）
> **范围**：LivaChat 后端（`services/`）、iOS/Android App（`apps/ios`、`apps/android`）、Web 入会页（`apps/web`）、Agent 调度链路（`services/agent_relay/`）、部署（`infra/`、`configs/`）。
> **需求来源**：
> 1. 使用 LiveKit 作为 meeting server；
> 2. 通过 Agent 预约会议，产生入会的 Web 界面，并发送给 App；
> 3. App 支持两种入会模式：a. native 入会；b. web 入会；
> 4. 以当前 codebase 为基础，不猜测；需要其他技术文档时联网查询；
> 5. 分析与设计保存到本文档。
>
> **调研方法**：先直接读取仓库代码与设计文档（文中的 `file:line`/模块名均可在当前工作树核对），再查 LiveKit 官方文档/仓库确认 API、版本与部署约束。属于设计建议、尚未在代码中实现的内容，均以“建议/设计/本期”标注；不要把本文的实现示例当作现有能力。

---

## 目录

0. 结论摘要（TL;DR）
1. 需求拆解与术语
2. 现状调研（codebase 事实）
3. LiveKit 技术调研
4. 总体设计
5. 关键流程
6. 后端设计
7. Web 入会页设计
8. iOS 设计
9. Android 设计
10. 失败模式与边界矩阵
11. 测试计划
12. 分阶段实施与工作量
13. 风险与开放问题
14. 附录

---

## 0. 结论摘要（TL;DR）

### 0.1 推荐架构

```
┌────────────────────────────┐
│ iOS / Android App          │
│  ├─ 会话页（现有 WS 链路）  │
│  └─ Meeting Flow（新）      │
│       ├─ native: LiveKit SDK
│       └─ web: 系统浏览器     │
└──────────┬─────────────────┘
           │ HTTPS（会议 REST API，现有 token/device_id 鉴权）
           ▼
┌────────────────────────────┐        ┌──────────────────────────┐
│ LivaChat Server            │        │ LiveKit Server (SFU)     │
│  ├─ 现有 WS 对话链路        │──API──▶│  ├─ RoomService API      │
│  ├─ MeetingService（新）    │        │  ├─ Webhook → /api/...   │
│  ├─ MeetingStore（SQLite）  │        │  ├─ TURN / RTC           │
│  ├─ /api/meetings/*（新）   │        │  └─ LiveKit JWTs         │
│  └─ /meeting/ 入会页（新静态站）  │        └──────────┬───────────────┘
└──────────┬─────────────────┘                   │ WSS / WebRTC
           │                                     ▼
           │                          ┌──────────────────────────┐
           │                          │ Web 浏览器（LiveKit JS）  │
           │                          └──────────────────────────┘
           │
┌──────────┴─────────────────┐
│ Agent Relay（现有）         │
│  Hermes/OpenClaw            │
│   └─ meeting_request（新）  │
└────────────────────────────┘
```

### 0.2 关键结论

| # | 结论 |
|---|------|
| 1 | **LiveKit Server 自托管**，固定版本、独立子域名、可信 TLS 证书；单节点即可满足个人/小规模场景，后续可加 Redis 横向扩展。 |
| 2 | **会议元数据与会话数据同库**：新增 `meetings` / `meeting_invites` / `meeting_participants` 表到现有 `chat_server.db`（`ChatStore` schema 的同一 DB），不引入新的数据库进程。 |
| 3 | **不新建 wire 协议消息**：预约成功的邀请通过现有 `ai_text`（agent proactive 消息）下发，正文内嵌 `livachat_url://meeting/<invite_token>` 链接；App 渲染期沿用 webroot 的转换套路，把链接转成 `<server>/meeting/#t=<invite_token>`。 |
| 4 | **Agent 调度用新的 relay contract action `meeting_request`**（Hermes 优先，OpenClaw 可选对齐），由 relay 在服务端直接创建会议、生成邀请、返回确认文案；不让 Agent 自己拼页面或直接访问 LiveKit API secret。 |
| 5 | **Web 入会页是服务端托管的产品页**（`/meeting/`），不是 Agent 生成的静态 HTML。原因是 LiveKit JWT 必须由持有 API secret 的服务端签发；Agent 只负责“预约”和“把链接发给 App”。 |
| 6 | **邀请 token 放 URL fragment**：`https://<server>/meeting/#t=<invite_token>`。fragment 不会进入 HTTP 请求/访问日志；页面 JS 读取后通过 POST 换取 LiveKit JWT。 |
| 7 | **LiveKit JWT 短 TTL（默认 10 分钟）+ 自动刷新**；邀请 token 可配置 TTL/次数/可选口令；自托管 LiveKit **没有 Cloud 的 token revocation**，用短 TTL + 会议状态 + 删除房间来收敛风险。 |
| 8 | **App native 入会是主路径**；web 入会默认走系统浏览器（SFSafariViewController / Chrome Custom Tabs）。现有应用内 WebView 未实现媒体采集权限，若要求“应用内 web 入会”需额外改造（本文给出改法）。 |
| 9 | **不依赖推送**：会议邀请的实时消息是 best-effort；App 启动/前台/`session.ready` 时通过 `GET /api/meetings/upcoming` 拉取即将开始的会议，作为持久化兜底。APNs/FCM 不在本期范围。 |
| 10 | **无账号体系**：host 以 `device_id` 标识；受邀人通过分享链接入会。邀请 token 就是能力凭证；需要更强隔离时加口令/短 TTL。账号/通讯录是后续独立立项。 |

### 0.3 本期明确不做

- 账号 / 通讯录 / 日历系统对接 / 邮件或短信邀请；
- 会议改期 / 重复会议（recurring）：本期只支持单次会议；需要改期时取消后重新预约；
- APNs / FCM 推送与跨设备提醒；
- 会议录制（LiveKit Egress）与 AI 会议纪要；
- 端到端加密（E2EE）：默认 SFU 可见媒体；E2EE 需要额外的密钥分发与客户端支持，另行评估；
- LiveKit Agent（AI 参会者）与会议内 AI 对话；
- LiveKit Cloud 专属能力（如 token revocation、Cloud dashboard webhook）；
- 应用内 WebView 的 web 入会（列为可选增强，MVP 用系统浏览器）；
- 屏幕共享、会议锁定、等候室、主持人审批、踢人/禁言等高级会控。

---

## 1. 需求拆解与术语

### 1.1 用户故事

| # | 用户故事 | 验收标准 |
|---|----------|----------|
| US-1 | 用户在 LivaChat 对话里说“明天下午三点帮我约个会” | 后台 Agent 创建会议；App 收到一条包含会议标题/时间/入会链接的 Agent 消息 |
| US-2 | 用户点开邀请，选择“App 内加入” | App 通过 LiveKit native SDK 进入会议，能看到/听到其他参与者 |
| US-3 | 用户点开邀请，选择“网页加入” | 系统浏览器打开 `/meeting/#t=...`，入会后正常音视频 |
| US-4 | 用户把邀请链接发给没有安装 App 的人 | 对方在浏览器打开链接，输入名字即可入会 |
| US-5 | 用户想确认自己约了哪些会 | App 里有“即将开始”列表；至少包含 host 设备创建的会议 |
| US-6 | 会议取消/结束后，旧链接不能继续入会 | token 接口拒绝；LiveKit 房间被删除或不再接受新 token |

### 1.2 术语

| 术语 | 含义 |
|------|------|
| Meeting Server | LiveKit Server（SFU），负责房间、参与者、音视频转发 |
| MeetingService | LivaChat 后端新增的会议业务服务：创建会议、邀请、签发 LiveKit JWT、处理 webhook |
| MeetingStore | 会议元数据存储，复用 `chat_server.db` |
| Invite Token | 分享给参会人的高熵能力凭证，放在 URL fragment；换取 LiveKit JWT |
| LiveKit JWT | LiveKit access token，包含 `roomJoin`、`room`、`identity` 等 grants，由 MeetingService 用 API secret 签发 |
| 调度 Agent | LivaChat 现有后台 Agent（Hermes/OpenClaw），不是 LiveKit Agents |
| LiveKit Agent | 可选的“会议内 AI 参与者”，本期不做 |

### 1.3 关键约束（来自当前 codebase）

1. **没有账号体系**：`Authenticator.verify()` 在 dev 模式返回固定 `user_id="dev-user"`，只有 `device_id` 是稳定标识（`services/gateway/auth.py`；`docs/server_auth.md`）。
2. **没有推送**：`agent_relay` v2 明确把 APNs/FCM 列为非目标（`docs/fd_agent_relay_service_v2.md` §1.3）；App 只有前台服务通知，没有远程推送。
3. **Agent 的实时输出必须走现有契约**：Hermes/OpenClaw 的 nudge 回复只允许 `noop` / `message` / `job_request`（`services/agent_relay/prompts/hermes_monitor.md`；`services/agent_relay/contract.py`）。
4. **App 的会话音频由 App 独占**：iOS 使用单一 `AVAudioSession` / `AVAudioEngine`（`AudioSessionController`、`ConversationRuntimeFactory`）；Android 使用 `AudioRecord` / `AudioTrack` / `AudioRouteManager`。会议 native SDK 也会管理音频会话，二者不能同时占用。
5. **App 链接处理目前只特化 webroot**：`livachat_url://webroot/...` 在渲染期转成 `<serverBase>/webroot/...`；点击后进入应用内 WebView（`docs/webroot_feature.md`、`docs/ios_webroot_support.md`）。
6. **现有 WebView 没有媒体采集授权桥接**：Android `WebViewScreen` 没有 `WebChromeClient.onPermissionRequest`（默认拒绝）；iOS `WebPageView` 没有 `WKUIDelegate` 的媒体采集许可回调（行为依赖 WebKit 默认提示，无法做 origin 级授权/自动授权）。两者都不能作为稳定的 WebRTC 入会容器。
7. **服务端是单实例**：`infra/kubernetes/deployment.yaml` 明确 `replicas: 1`，进程内事件总线/push/会话归属均只在本进程有效；会议服务应沿用单实例假设。
8. **当前数据库是 SQLite**：`ChatStore` 是 `chat_server.db` 的唯一写者（`services/shared/persistence/chat_store.py`），但 `hermes_device_sessions` 等方法已有“低频直连 + `busy_timeout`”的先例。

---

## 2. 现状调研（codebase 事实）

> 本节只记录当前代码里能直接验证的事实；设计推导放在后续章节。

### 2.1 后端：单进程 FastAPI + WS 对话链路

| 事实 | 位置 |
|------|------|
| 单进程模块化单体，FastAPI + uvicorn，所有模块在 `services/` 下 | `services/app/application.py`、`services/app/main.py` |
| 对外实时入口只有 `GET /v1/ws/stream`（WebSocket），无 WebRTC/SFU | `services/gateway/endpoint.py:27` |
| `create_app()` 注册 `router`、可选 BFF、可选 `/webroot` 静态挂载 | `services/app/application.py:431-456` |
| 现有 `/webroot` 托管 agent 产出页面，`WebrootConfig(enabled=False)` 默认关 | `services/app/application.py:466-488`、`services/shared/config/models.py:290` |
| 服务端无任何 LiveKit / meeting 代码 | 全仓 `grep -i livekit` 为空；`meeting` 仅出现在 mock/ASR 文档等无关位置 |

**结论**：meeting 是一个全新领域，不能复用现有 WS 协议承载多人音视频。现有协议是“单条音频/视频上行 + 单条 TTS 下行”的 C2S 流，没有房间、参与者、track 订阅关系；多人会议需要 SFU 与 track 订阅模型，LiveKit 是新增的独立媒体面。

### 2.2 认证与身份

| 事实 | 位置 |
|------|------|
| dev 模式 token = `sha1_hex(secret + "LivaChat")`，明文 secret 不上网 | `docs/server_auth.md` §3.1；`services/gateway/auth.py` |
| WS 握手 query 为 `token` + `device_id`（UUID v4） | `services/gateway/endpoint.py:27-45` |
| REST 侧复用同一 `Authenticator`，BFF 通过 `get_principal()` 校验 token；`device_id` 仅作过滤 | `services/bff/common/deps.py:40-53` |
| 没有用户表/登录/刷新 token；`user_id` 固定 `dev-user` | `docs/server_design.md` §9；`docs/server_auth.md` |
| App 端 `device_id`：iOS Keychain（卸载重装保留）、Android SharedPreferences `livachat_device`（卸载清除） | `apps/ios/LivaChatApp/Core/Settings/CredentialStore.swift`；`apps/android/.../di/AppModule.kt` |

**结论**：会议 host 只能绑定 `device_id`；受邀人没有账号，邀请链接就是准入凭证。这是产品限制，不是实现细节。

### 2.3 Agent 链路：Hermes / OpenClaw

| 事实 | 位置 |
|------|------|
| Agent relay 与 chat server 同进程；事件经进程内 `InProcessEventBus` 扇出 | `services/app/application.py:130-142`、`:160-279` |
| 每个 device 一条 Hermes WS、一个 hermes session；会话跨 chat session 复用 | `docs/fd_agent_relay_service_v2.md` §4 |
| Chatlog watcher 在对话增长时 nudge 主 Agent；主 Agent 只允许三种响应 | `services/agent_relay/prompts/hermes_monitor.md`；`services/agent_relay/contract.py` |
| `job_request` 会创建独立 job session，结果经 `JobResultDeliverer` 投递；job/结果 TTL 48h | `services/agent_relay/adapters/hermes.py:2034`；`services/agent_relay/delivery.py`；`services/agent_relay/config.py:40` |
| 实时 Agent 消息经 `PushPublisher` 直接 enqueue 到 device 的 WS handler；离线返回 False（丢失） | `services/agent_relay/publisher.py:46-120`；`services/agent_relay/session_manager.py:388-435` |
| 实时 Agent 消息的 `message_id` 由 `_generate_message_id()` 随机生成（不是确定性幂等键） | `services/agent_relay/session_manager.py:47-51`、`:425` |
| App 侧 dialog/omni system prompt 会注入后台 Agent 能力声明 | `services/session/agent_job_ingest.py:29-37`；`services/app/application.py:279-289` |

**结论**：Agent 确实有“主动行动”的通道，但当前契约只有 `job_request/message/noop`；会议预约需要扩展契约或给 Agent 一个工具。本文推荐扩展 relay contract action。

### 2.4 App：iOS / Android

| 事实 | 位置 |
|------|------|
| iOS SwiftUI + MVVM + 手动 DI；`AppContainer` 是组合根；iOS 15 最低版本 | `docs/ios_app_arch.md` §1-§3；`docs/ios_dev_rules.md` |
| iOS 会话音频：`ConversationRuntimeFactory` 创建 `AudioSessionController` + `AudioCaptureController` + `AudioPlaybackController`；SDK 与 App 共享同一 engine | `apps/ios/LivaChatApp/Core/Conversation/ConversationRuntimeFactory.swift` |
| iOS 链接路由：`AppContainer.openWebLink` → webroot 走应用内 `WebPageView`，其他 http(s) 走 `SFSafariViewController` | `apps/ios/LivaChatApp/App/AppContainer.swift:214-260`；`Core/PlatformAdapters/WebLinkRouter.swift` |
| iOS webroot 转换：`LivachatURLConverter` 渲染前把 `livachat_url://webroot/<uuidv7>/...` 转成 `<serverBase>/webroot/...` | `apps/ios/LivaChatApp/Features/Conversation/LivachatURLConverter.swift` |
| Android Compose + Hilt + Navigation Compose；`ConversationCoordinator` 统一启停 SDK/采集/播放 | `docs/android_app_arch.md` §1-§3；`apps/android/.../domain/ConversationCoordinator.kt` |
| Android 音频：`AudioCaptureController`（AudioRecord 16k/20ms）、`AudioPlaybackController`（AudioTrack + JitterBuffer）、`AudioRouteManager`（音频焦点/MODE_IN_COMMUNICATION） | `apps/android/.../media/` |
| Android 链接路由：`MarkdownContent` 渲染前 `convertLivachatUrls`，`onOpenLink` 4 处接到 `WebViewScreen` | `apps/android/.../ui/components/MarkdownContent.kt:121-126`；`ui/navigation/NavGraph.kt` |
| Android 现有 WebView 无媒体采集权限回调 | `apps/android/.../ui/screens/webview/WebViewScreen.kt:139-223` |
| iOS 现有 WKWebView 无媒体采集权限回调 | `apps/ios/LivaChatApp/Features/WebView/WebPageView.swift:163-181`、`:325-339` |
| 无用户显示名/头像之外的 profile；无账号 | `apps/android/.../domain/model/UserPreferences.kt`；`apps/ios/LivaChatApp/Core/Settings/SettingsStore.swift` |
| 远程推送未实现；只有 Android 前台服务通知 | Android `MicCaptureService` / `ScreenCaptureService`；无 APNs/UNUserNotificationCenter 业务代码 |

**结论**：native 会议是 App 级新 Feature，不应塞进 `LivaChatSDK`（SDK 只负责现有 WS 协议/音视频管线）。会议需要与现有会话音频互斥。

### 2.5 部署现状

| 事实 | 位置 |
|------|------|
| dev：`livachat-server` 监听 `0.0.0.0:8080`，SSL 自签 | `configs/dev/server.yaml:5-18` |
| 数据根 `/opt/lvdata`，`chat_server.db` 在根下 | `configs/dev/server.yaml:242-264`；`docs/install_design.md` |
| Docker dev compose 只有 postgres/redis/server | `infra/docker/docker-compose.dev.yml` |
| K8s 单副本、Ingress 只暴露 8080、WebSocket 长连接 | `infra/kubernetes/deployment.yaml`、`infra/kubernetes/ingress.yaml` |
| 现有 webroot 静态托管模式可直接复用到会议 Web 页 | `services/app/application.py:466-488`；`docs/webroot_feature.md` §3.1 |

**结论**：LiveKit 是新的独立部署单元，不能塞进现有 8080 Ingress 的普通 HTTP 路径；需要独立子域名 + UDP 端口段。

### 2.6 差距清单

| # | 差距 | 影响 | 本文对应设计 |
|---|------|------|--------------|
| G1 | 无 meeting 数据模型/服务/API | 无法创建/查询/入会 | §6 |
| G2 | Agent 契约只有 3 种 action | Agent 无法预约会议 | §6.5 |
| G3 | 无 LiveKit JWT 签发 | 客户端无法入会 | §6.3 |
| G4 | 无入会 Web 页 | 外部用户无法入会 | §7 |
| G5 | App 无 meeting 路由/UI/SDK | 无法 native 入会 | §8/§9 |
| G6 | 现有 WebView 不支持 getUserMedia | 应用内 web 入会不可用 | §8.4/§9.4/§7.5 |
| G7 | 无账号/通讯录 | 只能分享链接，不能按人邀请 | §13 |
| G8 | 无远程推送 | 离线邀请不会主动到达 | §5.7/§13 |
| G9 | 会话音频与 LiveKit 音频冲突 | 同时开会话+会议会抢麦克风 | §8.5/§9.5 |
| G10 | LiveKit 需要可信 TLS 与 UDP 端口 | 现有自签/单端口部署不满足 | §3.2/§12 |

---

## 3. LiveKit 技术调研

> 以下信息来自 LiveKit 官方文档与仓库（2026-11-09 查询）。版本号会漂移，实施时需重新确认并固定。

### 3.1 组件与版本

| 组件 | 当前版本（2026-11-09） | 用途 | 许可 |
|------|------------------------|------|------|
| `livekit/livekit`（livekit-server） | `v1.13.11`（2026-10-25） | SFU / RoomService / Webhook / TURN | Apache-2.0 |
| `livekit/client-sdk-swift` | `2.19.0`（2026-10-27） | iOS/macOS native SDK | Apache-2.0 |
| `livekit/client-sdk-android` | `v2.30.0`（2026-11-02） | Android/Kotlin native SDK | Apache-2.0 |
| `livekit/client-sdk-js` | `v2.23.0`（2026-10-16） | Web 浏览器 SDK | Apache-2.0 |
| `@livekit/components-react` | `2.9.25` | Web React 组件 | Apache-2.0 |
| `livekit/agents` | `1.8.11` | 会议内 AI Agent（可选，本期不做） | Apache-2.0 |
| `livekit-api`（Python server SDK） | `1.2.2` | 后端签发 JWT / RoomService / Webhook 校验 | Apache-2.0 |

### 3.2 自托管部署要点

LiveKit 官方部署文档给出的关键要求：

| 项 | 要求 |
|----|------|
| 信令端口 | `7880/TCP`（RoomService + RTC signaling），生产建议放在 TLS 负载均衡/反向代理后 |
| RTC TCP | `7881/TCP`（UDP 不可用时的 ICE/TCP fallback），**不能**放在 7 层 LB/TLS 后 |
| RTC UDP | `50000-60000/UDP`（可配；必须对客户端开放） |
| TURN | 内置 TURN；`turn.tls_port` 默认 5349，若无 LB 必须设 443；需要独立域名和可信证书 |
| TLS | **必须是可信 CA 签发的证书**；官方明确 self-signed 不适用于生产部署 |
| 公网 IP | 云环境 `rtc.use_external_ip: true`；STUN 探测失败时可 `rtc.node_ip` 显式指定 |
| Redis | 多节点/生产推荐；单节点也可运行，但 LiveKit 自身需要 Redis 做分布式状态 |
| 容器 | Docker 化时建议 host networking，避免 UDP 端口映射问题 |
| 资源 | CPU/带宽受限；生产建议 10Gbps 网卡、计算优化型实例 |

**对 LivaChat 的含义**：

1. 不能把 LiveKit 挂在现有 `:8080` 的普通 HTTP 路径下；建议独立子域名 `<livekit-host>`。
2. 现有 dev 自签证书不能让 LiveKit 客户端信任；移动端/浏览器会拒绝。开发环境需要用可信本地 CA（如 mkcert，且设备信任该 CA）或公网域名 + Let's Encrypt。补充 codebase 约束：Android Manifest 是 `usesCleartextTraffic="false"`（`apps/android/LivaChatApp/src/main/AndroidManifest.xml`），iOS 有 ATS；`ws://` 在真机上基本不可用，必须 `wss://`。
3. `7881` 与 `50000-60000/UDP` 必须从客户端网络可达；这是与 LivaChat 现有单端口部署最大的差异。
4. 个人/小规模单节点即可；不要为了 meeting 引入多节点复杂度，除非并发明显增长。

**LiveKit Cloud 备选**：如果不想自建 TLS/UDP/TURN，可以直接用 LiveKit Cloud。服务端 SDK、JWT、RoomService、Webhook 的用法基本一致，区别是 URL/API key 来自 Cloud 项目、TURN 由 Cloud 维护、webhook 在 Cloud 控制台配置，且 Cloud 提供 token revocation 等能力。本文默认自托管；代码设计保持 `livekit_url` / `livekit_api_url` / `api_key` / `api_secret` 可配置，切换 Cloud 不需要改业务代码。

**最小自托管配置示例**（单节点）：

```yaml
# /etc/livekit/livekit.yaml
port: 7880

# 单节点可先不配；多节点/生产建议 Redis（独立实例，不要与 LivaChat 的 Redis DB 混用）
redis:
  address: 127.0.0.1:6379

rtc:
  tcp_port: 7881
  port_range_start: 50000
  port_range_end: 60000
  use_external_ip: true          # 云主机；若 STUN 探测失败，改用 node_ip

keys:
  "<LIVEKIT_API_KEY>": "<LIVEKIT_API_SECRET>"

room:
  empty_timeout: 300             # 创建后无人加入的保留时间
  departure_timeout: 300         # 最后一人离开后的保留时间
  max_participants: 20

webhook:
  api_key: "<LIVEKIT_API_KEY>"
  urls:
    - "https://<server-host>:8080/api/meetings/webhook"

turn:
  enabled: true
  domain: "<turn-host>"
  tls_port: 5349                 # 无 L4 LB 时设为 443
  cert_file: "/etc/letsencrypt/live/<turn-host>/fullchain.pem"
  key_file: "/etc/letsencrypt/live/<turn-host>/privkey.pem"
```

启动：

```bash
livekit-server --config /etc/livekit/livekit.yaml
# 或
docker run --rm --network host \
  -v /etc/livekit:/etc/livekit \
  livekit/livekit-server:v1.13.11 --config /etc/livekit/livekit.yaml
```

> 端口映射/防火墙需同时放行 `7880/TCP`、`7881/TCP`、`50000-60000/UDP`、`5349/TCP`（以及 TURN 的 UDP 端口，如启用）。如果 LiveKit 在反向代理后，必须保留 WebSocket upgrade 与长连接超时。
>
> 部署形态：现有 K8s 清单是单副本 + Ingress 只处理 8080/TCP；LiveKit 需要 UDP 端口段，在 K8s 里通常要 `hostNetwork` 或特殊 LoadBalancer 配置。个人/小规模部署建议先用 **Docker/systemd 单机** 跑 LiveKit，复用现有 K8s Ingress 只服务 `/meeting/` 和 `/api/meetings/*`，不要直接把 LiveKit 塞进现有 Ingress。

本地 PoC 可以先用 `livekit-server --dev`（内置 `devkey`/`secret`，仅本机 `ws://127.0.0.1:7880`）；它**不能**直接用于手机/局域网浏览器：移动端需要可信 WSS，浏览器从 HTTPS 页面连 `ws://` 也会被混合内容策略拦截。`infra/docker/docker-compose.dev.yml` 可以加一个 `livekit/livekit-server:v1.13.11` 服务做桌面浏览器 PoC，但真机联调仍建议独立主机 + 可信证书。

### 3.3 Access Token 与 Grants

LiveKit 用 JWT 做鉴权。解码后的 join token 关键字段：

```json
{
  "exp": 1621657263,
  "iss": "<api-key>",
  "sub": "<participant-identity>",
  "nbf": 1619065263,
  "video": {
    "room": "<room-name>",
    "roomJoin": true,
    "canPublish": true,
    "canSubscribe": true,
    "canPublishData": true,
    "roomAdmin": false
  },
  "metadata": "..."
}
```

关键 grants（`video` 字段）：

| 字段 | 含义 |
|------|------|
| `roomJoin` | 允许加入房间；必须与 `room` 一起设置 |
| `room` | 房间名；不能放 PII |
| `canPublish` | 允许发布音视频 |
| `canPublishSources` | 限制可发布的 track 来源：`camera` / `microphone` / `screen_share` / `screen_share_audio` |
| `canSubscribe` | 允许订阅他人 track |
| `canPublishData` | 允许发 data channel 消息 |
| `roomAdmin` | 房间管理权限 |
| `hidden` | 对其他参与者隐藏（适合 bot） |
| `kind` | 参与者类型：`standard` / `agent` / `egress` / `sip` 等 |

**安全要点**：

- **API secret 只能留在服务端**；浏览器/App 只能拿到短期 JWT。
- `identity`/`room` 会进入 LiveKit 日志与 trace，不应放姓名、电话、邮箱；用 `p_<uuid>` / `mtg_<uuid>` 这类 opaque ID。
- Token 过期只影响**首次连接**；连接期间 LiveKit Server 会主动刷新 token（刷新后的 token 有效期 = 10 分钟或原 token 剩余时间，取较长者），因此短 TTL 不会导致长会议中途掉线。
- **自托管没有 Cloud 的 token revocation**：移除参与者后，旧 token 仍可能在 TTL 内重连。必须用短 TTL + 会议状态校验 + 删除房间/轮换邀请来收敛。
- Token 的 `exp` 是必须的；LiveKit 的 `TokenVerifier` 会要求 `exp`。

### 3.4 Room 生命周期与管理 API

| 事实 | 说明 |
|------|------|
| 房间可以显式 `CreateRoom`，也可以在第一个参与者加入时自动创建 | 本期选择“自动创建”，避免为未来会议提前建空房 |
| 最后一个非 agent 参与者离开后，房间在 `departure_timeout` 后关闭 | 可配 |
| `empty_timeout` 是“房间创建后无人加入”的保留时间 | 只对首次创建有效 |
| `max_participants` 限制房间人数 | token 的 `RoomConfiguration` 可携带，但**只在房间首次创建时生效** |
| 删除房间会断开所有参与者 | `DeleteRoom` |
| `RoomConfiguration` 可在 token 中携带 `max_participants` / `empty_timeout` / `departure_timeout` / `metadata` / `agents` | 首次创建时应用 |

**本期策略**：

- 预约时不创建 LiveKit 房间；第一次有人入会时自动创建。
- 所有 token 使用同一套 `RoomConfiguration`（`max_participants` / `departure_timeout` / `metadata`），避免“谁先加入谁决定房间配置”的不一致。
- 会议取消/结束时：如果房间存在，调用 `DeleteRoom`；不存在则只更新 DB 状态。

### 3.5 Webhooks

LiveKit 自托管通过配置开启 webhook：

```yaml
webhook:
  api_key: <api-key>
  urls:
    - https://<server-host>/api/meetings/webhook
```

事件类型：

| 事件 | 用途 |
|------|------|
| `room_started` | 第一个参与者加入，会议从 `scheduled` → `active` |
| `room_finished` | 房间关闭，会议 → `ended` |
| `participant_joined` / `participant_left` | 参与者计数/审计 |
| `participant_connection_aborted` | 连接异常，诊断 |
| `track_published` / `track_unpublished` | 可选诊断 |
| `egress_*` / `ingress_*` | 本期不做录制/推流，忽略 |

**校验方式**（Python server SDK）：

```python
from livekit import api

receiver = api.WebhookReceiver(api.TokenVerifier(api_key, api_secret))
event = receiver.receive(raw_body_str, authorization_header)
```

`Authorization` 头是带 `sha256` claim 的 JWT；`receiver` 会校验签名与 body hash。必须使用**原始 body 字符串**，不能先 JSON 解析再序列化。LiveKit 发送的 `Content-Type` 是 `application/webhook+json`；FastAPI 路由应直接 `await request.body()`，不要声明 Pydantic body model（否则会被当普通 JSON 解析，body 字节可能不一致）。

**投递语义**：LiveKit 会重试，但不保证 exactly-once；webhook 处理器必须幂等（按 `event.id` 去重）。

### 3.6 客户端 SDK 事实

| 平台 | 安装 | 最低版本 | 备注 |
|------|------|----------|------|
| iOS | SPM `https://github.com/livekit/client-sdk-swift`，`upToNextMajor("2.19.0")` | iOS 13+（`Package.swift`）；Swift tools 6.1 / Xcode 16.3+ | `VideoView` 是 UIKit；SwiftUI 中需 `UIViewRepresentable` 包装 |
| Android | Maven `io.livekit:livekit-android:2.30.0` + JitPack | minSdk 21（LivaChat App minSdk 26） | Compose 用 `AndroidView` 包装 `SurfaceViewRenderer`/`TextureViewRenderer`；也可选 `livekit-android-compose-components` |
| Web | `livekit-client` + `@livekit/components-react` | 现代 Chrome/Firefox/Safari/Edge；Safari iOS 支持 | 需要 `getUserMedia`、WebRTC、WebSocket；浏览器必须允许麦克风/摄像头 |

**iOS SDK 注意事项**：

- LiveKit 默认自动管理 `AVAudioSession`（`.playback`；发布本地 track 时切 `.playAndRecord`）。如果 App 自己管理音频会话，可设置 `AudioManager.shared.audioSession.isAutomaticConfigurationEnabled = false`。
- `VideoView` 的读写必须在主线程；delegate 在 SDK 内部线程，需要切回 MainActor。
- 使用 weak 引用持有 `Participant` / `TrackPublication`，`Room` 断开后这些对象会失效。
- LiveKit 2.19 要求 Xcode 16.3+；实施前确认构建机/Xcode 版本。

**Android SDK 注意事项**：

- 需要 `RECORD_AUDIO` / `CAMERA` 运行时权限；LivaChat Manifest 已声明。
- LiveKit 自带 WebRTC native 库；AAR 体积较大，但 App Bundle 按 ABI 分发后可接受。LivaChat 已限制 `arm64-v8a` / `x86_64`。
- `Room` 要走生命周期管理；离开页面 `room.disconnect()`，并释放 renderer。
- LiveKit 会请求音频焦点/切换音频模式；与现有 `AudioRouteManager` 的 `MODE_IN_COMMUNICATION` 可能冲突，入会前必须停掉现有采集/播放。

### 3.7 Web 入会与 WebView 限制

- LiveKit Web SDK 官方支持 Safari（macOS/iOS）、Chrome、Firefox、Edge；`isBrowserSupported()` 可用于网关判断。
- 在**系统浏览器**中，`getUserMedia` 由浏览器弹出权限，用户体验最好。
- 在 **WebView** 中：
  - Android：必须实现 `WebChromeClient.onPermissionRequest`，对会议 origin 授予 `RESOURCE_AUDIO_CAPTURE` / `RESOURCE_VIDEO_CAPTURE`；**不实现时 WebView 默认拒绝**。
  - iOS：iOS 14.3+ WKWebView 支持 `getUserMedia`；iOS 15+ 可用 `WKUIDelegate.webView(_:requestMediaCapturePermissionFor:initiatedByFrame:type:decisionHandler:)` 显式授权。不实现时行为依赖 WebKit 默认提示（可能出现系统权限框），无法做 origin 级控制/自动授权，不能作为稳定集成方案。
  - 现有 LivaChat WebView：**Android 已于 2026-11-15 实现** `onPermissionRequest` 与受信任会议页媒体授权（见 `docs/android_webview_meeting_support.md`）；iOS 仍未接入，应用内 web 入会需继续使用 Safari/系统浏览器或专门改造。
- 自签证书：LiveKit 官方要求可信证书；WebView 现有“对当前 server 放行自签”的逻辑只覆盖 LivaChat server 的 host:port，不覆盖 LiveKit 子域名。**不要依赖 WebView 的自签放行来跑 WebRTC**。

**本文建议**：

- MVP 的 web 入会 = **系统浏览器**（iOS `SFSafariViewController` / Android Chrome Custom Tabs），零 WebView 改造、权限和 WebRTC 行为最接近真实浏览器。
- 应用内 WebView 入会作为 Phase 2 可选增强，按 §7.5 / §8.4 / §9.4 改造并限定 origin。

### 3.8 LiveKit Agents（可选，本期不做）

LiveKit Agents 是“会议内 AI 参与者”的框架：一个 Python worker 用 `AgentServer` + `AgentSession` 连接 LiveKit 房间，内置 STT/LLM/TTS/turn detection/tool use。可通过 token 的 `RoomConfiguration.agents` 在房间首次创建时 dispatch。

如果未来要做“AI 参加会议/记纪要”，有两条路：

1. **LiveKit Agents worker**：部署独立 worker，使用 LiveKit Inference 或自建 STT/LLM/TTS provider。与 LivaChat 现有 provider 体系是两套流水线。
2. **自建桥接服务**：用 `livekit` Python RTC SDK 加入房间，订阅/发布音频，复用 LivaChat 的 ASR/LLM/TTS。工作量大，但能复用现有 provider。

本期不做，避免把“预约会议”与“AI 参会”混在一起。本文的 `meeting_request` 调度 Agent 不是 LiveKit Agents。

### 3.9 与替代方案对比（为何选 LiveKit）

> 下表为基于公开资料的**定性比较**，不是本项目实测；需求已指定 LiveKit，列出仅用于记录选型上下文。

| 方案 | 自托管 | 客户端 SDK | Webhook/API | 运维复杂度 | 适配 LivaChat |
|------|--------|------------|-------------|------------|---------------|
| **LiveKit** | ✅ Apache-2.0 | iOS/Android/Web 官方 SDK | RoomService + Webhook + Egress + Agents | 中（UDP/TURN/TLS） | 高：token/API 清晰，SFU 独立 |
| Jitsi | ✅ | Web 为主，移动端集成较弱 | 有，但会议控制模型不同 | 中高（Jibri 录制重） | 中 |
| mediasoup | ✅ | 需自建客户端封装 | 需自建信令/房间管理 | 高 | 低：工作量大 |
| Janus | ✅ | 需自建封装 | 需自建 | 高 | 低 |
| Daily / Agora | ❌ SaaS | 好 | 好 | 低 | 中：成本/供应商锁定 |

需求已指定 LiveKit；上表只用于记录选型理由。LiveKit 的优势是“开源自托管 + 官方三端 SDK + 清晰的 JWT/RoomService/Webhook”，与 LivaChat 现有“服务端签发凭证、客户端只拿短期 token”的鉴权思路一致。

---

## 4. 总体设计

### 4.1 架构图

```
                         ┌──────────────────────────────────────────────┐
                         │              LivaChat Server                  │
                         │                                              │
  App ── HTTPS ─────────▶│  /api/meetings/*  ── MeetingService ──┐      │
  (token/device_id)      │        │                              │      │
                         │        ├─ MeetingStore (chat_server.db)      │
                         │        ├─ InviteToken (hash/expiry)          │
                         │        ├─ LiveKitAPI client ─────────┼──▶ LiveKit Server
                         │        └─ LiveKit JWT minter          │      │
                         │                                       │      │
  Browser ── HTTPS ─────▶│  /meeting/ 静态页（React + LiveKit JS）      │      │
  (invite token)         │        │                              │      │
                         │        └─ POST /api/meetings/join ────┘      │
                         │                                              │
  Agent Relay ──────────▶│  meeting_request ── MeetingService.create    │
  (Hermes/OpenClaw)      │        └─ ai_text 邀请消息 → App           │
                         └──────────────────────────────────────────────┘
                                              │
                                              │ WSS + WebRTC
                                              ▼
                                    ┌──────────────────┐
                                    │  LiveKit Server  │
                                    │  SFU / TURN      │
                                    └──────────────────┘
```

### 4.2 组件职责

| 组件 | 职责 | 不负责 |
|------|------|--------|
| `MeetingService` | 创建/查询/取消会议；邀请 token；签发 LiveKit JWT；调用 LiveKit RoomService；处理 webhook | 音视频转发（LiveKit）、UI |
| `MeetingStore` | `meetings` / `meeting_invites` / `meeting_participants` / `meeting_webhook_events` 的 SQLite 读写 | 房间状态实时同步（webhook 负责） |
| `MeetingRouter` | `/api/meetings/*` REST；App 鉴权 + 邀请 token 双通道 | 页面渲染 |
| `MeetingWeb` | `/meeting/` 静态 SPA + CSP/Referrer 头 | 会议业务逻辑 |
| Agent action | `meeting_request` 解析/校验/调用 MeetingService/返回确认文案 | 直接访问 LiveKit API secret |
| iOS `MeetingFlow` | 邀请页 + 原生会议页；LiveKit Swift SDK；音频/权限/生命周期 | 会议元数据持久化（走 API） |
| Android `MeetingFlow` | 邀请页 + 原生会议页；LiveKit Android SDK；音频/FGS/权限 | 同上 |
| Web Meeting SPA | 预览/入会/会议内 UI；LiveKit JS；媒体权限 | 签发 token（只调 LivaChat API） |

### 4.3 数据模型

新增表全部放在 `chat_server.db`（`ChatStore` 的同一 DB）。理由：会议与 device/session 关联紧密；单实例 SQLite 足够；避免再引入一个数据库进程。低频写走“直连 + `busy_timeout`”模式（与 `hermes_device_sessions` 一致），不进入 `ChatStore` 的高频消息队列。

```sql
-- 会议主表
CREATE TABLE IF NOT EXISTS meetings (
    meeting_id        TEXT PRIMARY KEY,          -- 'mtg_' + uuid7
    room_name         TEXT NOT NULL UNIQUE,      -- 同 meeting_id（LiveKit room name）
    host_device_uuid  TEXT NOT NULL,             -- host 设备（无账号体系）
    host_name         TEXT NOT NULL DEFAULT '',  -- 预约时的显示名（可空）
    title             TEXT NOT NULL,
    agenda            TEXT NOT NULL DEFAULT '',
    language          TEXT NOT NULL DEFAULT 'en',-- Agent 目标语言，用于 Web 页默认 UI
    start_at_ms       INTEGER NOT NULL,          -- UTC epoch ms
    duration_min      INTEGER NOT NULL,
    timezone          TEXT NOT NULL,             -- 规范形（IANA/固定偏移），用于展示
    status            TEXT NOT NULL,             -- scheduled|active|ended|cancelled
    max_participants  INTEGER NOT NULL DEFAULT 20,
    source            TEXT NOT NULL DEFAULT 'agent', -- agent|app|api
    created_at_ms     INTEGER NOT NULL,
    updated_at_ms     INTEGER NOT NULL,
    started_at_ms     INTEGER,
    ended_at_ms       INTEGER,
    metadata_json     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_meetings_host_time
    ON meetings(host_device_uuid, start_at_ms);
CREATE INDEX IF NOT EXISTS idx_meetings_status_time
    ON meetings(status, start_at_ms);

-- 邀请（一个会议可以有多条：host/guest/轮换）
CREATE TABLE IF NOT EXISTS meeting_invites (
    invite_id         TEXT PRIMARY KEY,          -- 'inv_' + uuid7
    meeting_id        TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    token_hash        TEXT NOT NULL UNIQUE,      -- sha256(hex) of raw token
    role              TEXT NOT NULL,             -- host|guest
    expires_at_ms     INTEGER NOT NULL,
    max_uses          INTEGER NOT NULL DEFAULT 0,-- 0 = 不限
    uses              INTEGER NOT NULL DEFAULT 0,
    passcode_hash     TEXT,                      -- 可选；PBKDF2
    created_at_ms     INTEGER NOT NULL,
    revoked_at_ms     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_invites_meeting
    ON meeting_invites(meeting_id);

-- 参与者（入会时创建；用于审计/显示 host）
CREATE TABLE IF NOT EXISTS meeting_participants (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id        TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    identity          TEXT NOT NULL,             -- 'p_' + uuid7（opaque）
    device_uuid       TEXT,                      -- App 入会时有值；Web 为 NULL
    display_name      TEXT NOT NULL,
    role              TEXT NOT NULL,             -- host|guest
    joined_at_ms      INTEGER NOT NULL,
    left_at_ms        INTEGER,
    UNIQUE(meeting_id, identity)
);
CREATE INDEX IF NOT EXISTS idx_participants_meeting
    ON meeting_participants(meeting_id, joined_at_ms);

-- Webhook 幂等
CREATE TABLE IF NOT EXISTS meeting_webhook_events (
    event_id          TEXT PRIMARY KEY,
    event_type        TEXT NOT NULL,
    meeting_id        TEXT,
    received_at_ms    INTEGER NOT NULL,
    payload_json      TEXT NOT NULL DEFAULT '{}'
);
```

**命名规则**：

- `meeting_id` / `room_name`：`mtg_<uuid7>`，不含 PII。
- `identity`：`p_<uuid7>`，每次入会可生成新的；LiveKit 日志里只看到这个。
- `token_hash`：`sha256(raw_token)` 的 hex；DB 泄露不能直接得到可用邀请。
- `timezone`：用 `canonicalize_timezone()` 归一（`services/shared/timeutil.py`），与现有 `devices.timezone` 语义一致。

**SQLite 外键**：`REFERENCES ... ON DELETE CASCADE` 只有在连接上 `PRAGMA foreign_keys=ON` 才生效；现有 `ChatStore` 未显式启用。MeetingStore 应在自己的写连接上启用该 pragma，或在删除会议时手动清理 `meeting_invites` / `meeting_participants`（二选一，保持一致）。本期不做物理删除，通常只更新状态，因此 FK 级联不是关键路径；但测试要覆盖。

### 4.4 会议状态机

```
                 create
                   │
                   ▼
              scheduled ──(第一个 participant join / room_started)──▶ active
                   │                                                   │
                   │ cancel                                            │ room_finished
                   ▼                                                   ▼
               cancelled                                            ended
                   ▲
                   └────────────── cancel（active 时也可）──────────────┘
```

- `scheduled`：已创建，未开始/无人入会。
- `active`：至少一次成功入会；以 `room_started` webhook 为准；无 webhook 时由首次签发 token 后置为 active（lazy）。
- `ended`：`room_finished` 或 `end_at_ms + join_grace_s` 过期；由 webhook 或 periodic sweeper 收敛。
- `cancelled`：host/agent 取消；删除 LiveKit room（若存在）、撤销邀请。
- 入会窗口：`[start_at_ms - join_early_s, end_at_ms + join_grace_s]`；窗口外只允许 host 提前进入（可配）。默认 `join_early_s=600`、`join_grace_s=3600`。
- `end_at_ms = start_at_ms + duration_min * 60_000`。

### 4.5 关键决策

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| D1 | 会议服务位置 | 放在现有 LivaChat Server 进程内（新模块 `services/meeting/`） | 单实例、与 device/agent/SQLite 同进程；避免新微服务 |
| D2 | 数据存储 | 复用 `chat_server.db`，新增表 | 不开新 DB；低频写有 `hermes_device_sessions` 先例 |
| D3 | 邀请传输 | `ai_text` + `livachat_url://meeting/<invite_token>` | 零 wire 协议改动；复用 webroot 的渲染期转换经验 |
| D4 | Agent 调度 | 新 relay contract action `meeting_request` | 不让 Agent 直接访问 LiveKit API secret；服务端可做校验/幂等/持久化 |
| D5 | Web 页归属 | LivaChat Server 托管 `/meeting/` 静态 SPA | 页面需要调用 LivaChat API 换 token；Agent 生成 HTML 无法安全持有 API secret |
| D6 | 邀请 token 位置 | URL fragment `#t=...` | fragment 不进 HTTP 请求/访问日志/Referer；页面 JS 读取后 POST 换 token |
| D7 | LiveKit JWT TTL | 默认 10 分钟 | 短 TTL 降低泄漏风险；LiveKit 连接期间自动刷新 |
| D8 | Web 入会容器 | MVP 用系统浏览器 | 现有 WebView 无媒体权限；系统浏览器 WebRTC/权限行为最可靠 |
| D9 | App 邀请入口 | 邀请 sheet 提供 native/web 两个按钮 | 明确满足“两种入会模式”；不强迫用户二选一 |
| D10 | 离线兜底 | `GET /api/meetings/upcoming` | 现有 push 是 best-effort；无 APNs/FCM；启动/前台拉取 |
| D11 | 房间创建时机 | 首次入会时自动创建 | 预约未来会议不应提前建空房；`RoomConfiguration` 统一 |
| D12 | Host 身份 | `host_device_uuid` + App 鉴权；受邀人用邀请 token | 无账号体系下最可行的隔离 |
| D13 | 会议内 AI | 本期不做 | 与“预约会议”解耦；避免引入 LiveKit Agents/桥接服务 |
| D14 | 录制 | 本期不做 | Egress 是独立服务 + 存储；先验证会议本身 |
| D15 | 推送/提醒 | 本期不做 | 现有架构明确非目标；本地提醒可作为 Phase 2 |

---

## 5. 关键流程

### 5.1 Agent 预约会议

```
用户语音：“明天下午三点帮我约个会”
  │
  ▼
Dialog LLM（现有流式对话）正常回复：“好，我让小黑安排”
  │
  ▼
chatlog 追加 user/assistant 行
  │
  ▼
ChatlogWatcher nudge Hermes（现有链路）
  │  载荷：chatlog_path / chat_session_id / timezone / new_lines / now_ms(建议新增)
  ▼
Hermes 主 turn 返回契约 JSON：
  {"action":"meeting_request",
   "title":"...",
   "start_at":"2026-11-10T15:00:00+08:00",
   "duration_min":30,
   "attendees":["..."],          // 仅用于文案，不做权限
   "note":"..."}
  │
  ▼
relay 校验 → MeetingService.create_from_agent()
  ├─ 解析/归一化时间、时区、时长
  ├─ 写 meetings 行（status=scheduled）
  ├─ 生成 guest invite token（hash 落库）
  └─ 返回确认 markdown（含 livachat_url://meeting/<token>）
  │
  ▼
relay 以 AgentResponseChunk 返回 → PushPublisher → s2c ai_text → App
```

**关键点**：

- Agent 只输出**语义字段**，不接触 LiveKit API key/secret，也不生成 HTML。
- `start_at` 必须是带时区偏移的 ISO 8601；relay 用 `datetime.fromisoformat()` 解析并校验未来时间/上限（如 ≤365 天）。
- `note` 映射到 `meetings.agenda`，展示在邀请页/会议页；`attendees` 只用于确认文案（当前没有通讯录/账号，不能按人授权）。
- `timezone` 来自 `AgentContext.timezone`（`session.started` 透传，见 `docs/agent_relay_prompts.md`）；`start_at` 的 offset 必须与之一致，否则以 `start_at` 的 offset 为准并记录告警。
- 建议在 nudge JSON 里增加 `now_ms`（服务器当前 UTC 毫秒），避免 Agent 用最后一条 chatlog 的时间猜“现在”。这是对现有 nudge 载荷的向后兼容新增字段。
- 如果 `meeting_service` 未装配，`meeting_request` 按 `noop` 处理并告警；prompt 里的 `$meeting_hint` 为空，Agent 不应主动输出该 action。

**幂等性**：

- Agent 可能对同一条 chatlog 重复 nudge；`meeting_request` 没有天然幂等键。建议在确认文案里使用确定性 `message_id = "msg_meeting_" + meeting_id`，由 `_publish_reply` 支持从 `chunk.metadata["message_id"]` 读取（当前是随机生成，见 `services/agent_relay/session_manager.py:47-51`）。
- 如果同一用户重复说“约个会”，应创建新会议（这是用户意图），不做内容去重。

### 5.2 邀请链接格式

**确认文案示例**（Agent 消息，实际按用户语言本地化）：

```markdown
已为你预约会议「周会」
时间：2026-11-10 15:00（Asia/Shanghai），预计 30 分钟

[在 App 中加入](livachat_url://meeting/<invite_token>)
```

如果配置了 `meeting.web_base_url`，再附一条 `[网页加入](https://<server-host>/meeting/#t=<invite_token>)`；否则 App 的邀请 sheet 从 `livachat_url` 推导分享链接。

**内部链接（写入 ai_text / 历史）**：

```
livachat_url://meeting/<invite_token>
```

- `invite_token` 是 32 字节随机数的 base64url（43 字符，`[A-Za-z0-9_-]`）。
- 该 scheme 只用于 App 渲染期转换；服务端不解析。

**对外链接（浏览器可直接打开）**：

```
https://<server-host>/meeting/#t=<invite_token>
```

- `/meeting/` 是服务端托管的 SPA；fragment 不发送给服务器。
- 如果配置了 `meeting.web_base_url`，Agent 的确认文案可以同时给出这个 https 链接；否则 App 端从 `livachat_url` 转换出来，并在邀请 sheet 里提供“分享网页链接”。

**App 渲染期转换**（扩展现有 webroot 逻辑）：

```
livachat_url://webroot/<uuidv7>[/…]  →  <serverBase>/webroot/<uuidv7>[/…]     （现有）
livachat_url://meeting/<token>       →  <serverBase>/meeting/#t=<token>             （新增）
```

- Android：`convertLivachatUrls`（`ui/navigation/ServerLinks.kt`）增加第二个前缀分支；token 校验 `[A-Za-z0-9_-]{43}`。
- iOS：`LivachatURLConverter.convert`（`Features/Conversation/LivachatURLConverter.swift`）同样增加分支。
- 存储/传输保持 raw；转换只发生在渲染前（与 webroot 的 D4' 决策一致）。

**为什么不让 Agent 现场生成入会页**：

1. 入会页需要调用 LivaChat API 换取 LiveKit JWT；页面本身不持有 API secret，但必须能通过服务端的邀请校验。Agent 现场生成的静态 HTML 没有可靠的鉴权/限流/状态校验链路。
2. 现有 `/webroot` 是**无鉴权公开静态目录**（`docs/webroot_feature.md` §3.5 已记录同源/无鉴权风险），把入会逻辑放进去会把“谁能入会”交给页面 JS，不可接受。
3. 会议页需要随会议状态（scheduled/active/ended/cancelled）、邀请过期、口令、人数限制变化；动态服务端页面/接口更合适。
4. 如果产品需要“每个会议一个定制外观”，可以让 Agent 用现有 webroot 生成一个**展示页**（议程/海报/背景），页面上的“入会”按钮仍指向 `/meeting/#t=...`；入会安全边界不变。

### 5.3 App 收到邀请

```
App 收到 ai_text（source=agent, proactive=true）
  → 气泡 Markdown 渲染
      → convertLivachatUrls 把 livachat_url://meeting/<token> 转成 https://<server>/meeting/#t=<token>
  → 用户点击链接
      → 链接分类器识别：同源 + path 以 /meeting/ 开头 + fragment 有 t
      → 打开 MeetingInviteSheet（不是 WebView）
```

**邀请 sheet 内容**：

- 打开前先调 `POST /api/meetings/preview {invite_token}` 获取会议摘要（标题/时间/状态/host/`requires_passcode`/`meeting_id`）；失败则显示“邀请无效/已过期”，不展示入会按钮。
- 会议标题、时间（设备本地时区 + 会议时区）、时长、host、agenda；
- 参与者/人数（可选）；
- 按钮：
  - **在 App 中加入**（native）→ 调 `POST /api/meetings/token`；
  - **网页加入**（web）→ 打开 `https://<server>/meeting/#t=<token>`（SFSafari / Custom Tabs）；
  - **分享链接**；
  - **取消会议**（仅 host，且需 App 鉴权匹配 `host_device_uuid`）。
- 如果会议已结束/取消：按钮置灰并显示原因。
- 如果 App 未配置服务器/凭证：native 按钮置灰，提示去设置；web 按钮仍可用。
- native 入会失败（SDK/证书/网络）时，保留“改用网页加入”入口，避免用户被卡死。
- **显示名**：App 设置新增本地 `display_name`（DataStore / UserDefaults，默认空）；邀请 sheet 可编辑并回写；Web 页每次输入。没有账号同步，换设备不继承。

**链接分类器**：

- Android：把现有 4 处 `onOpenLink = { url -> navController.navigate(Routes.webview(url)) }` 收敛到一个 `AppLinkRouter.route(url)`：
  - `isMeetingUrl(url, serverBase)` → `Routes.meetingInvite(...)`；
  - `serverBase.isWebrootURL(url)` → `Routes.webview(url)`；
  - 其他 http(s) → `Routes.webview(url)`（或系统浏览器，保持现状）。
- iOS：`AppContainer.presentWebLink` 增加 `serverBase.isMeetingURL(url)` 分支 → `presentedMeetingInvite`。
- 仅接受同源 `/meeting/`；不要接受任意 host 的 `/meeting/`，防止钓鱼。
- **外部链接直接进 App**（从微信/WhatsApp 等点 `https://<server>/meeting/#t=...`）需要 Universal Links（`apple-app-site-association`）或 Android App Links（`assetlinks.json` + 签名指纹）；本期不做，外部用户先在浏览器入会。App 内邀请消息的链接仍可 native 入会。

**为什么本期不新增 `meeting.invite` wire 消息**：

- 现有 `ai_text` 已支持 markdown、`message_id`、`proactive`、`source=agent`，足以承载“标题 + 时间 + 链接”；webroot 已经验证了“agent 输出链接 → App 渲染期转换 → 点击”的完整链路。
- 新增 S2C 消息需要改 `packages/api-schema`、重新生成 Swift/Kotlin/Python DTO、双端事件适配与测试；收益主要是富卡片 UI，不是功能可用性。
- 如果后续产品需要会议卡片（头像、时间块、Join 按钮），再新增 `meeting.invite` 消息；届时 App 的会议模块已经存在，增量较小。

### 5.4 Native 入会

```
MeetingInviteSheet “在 App 中加入”
  → POST /api/meetings/token?token=<wire>&device_id=<uuid>
      body: { invite_token, display_name }
  → LivaChat Server:
      ├─ 校验 App 鉴权（现有 Authenticator）
      ├─ 校验邀请 token（hash/expiry/uses/revoked）
      ├─ 校验会议状态/入会窗口
      ├─ 创建 meeting_participants 行（identity=p_<uuid7>）
      ├─ 签发 LiveKit JWT（roomJoin/room/canPublish/canSubscribe/...）
      └─ 返回 { livekit_url, token, meeting, role }
  → App 停止现有会话音频（§8.5/§9.5）
  → LiveKit SDK: room.connect(livekit_url, token)
  → 发布麦克风/摄像头（按用户选择）
  → 进入 native 会议 UI
```

**Host 识别**：

- 如果 `device_id == meetings.host_device_uuid`（DB 侧统一 lower-case 归一），role=host，JWT 加 `roomAdmin=true`；
- 否则必须有 `invite_token`，role=guest；
- 如果两者都不满足 → 403。
- **host 点击 guest 邀请链接时仍识别为 host**：服务端先按 `device_id` 判 host，再校验邀请；这样 host 不需要单独的主持人链接。
- Web 入会页没有 App 鉴权，默认只能以 guest 身份入会；host 的主持能力（取消、踢人等）以 App 为准。若未来需要网页主持，可另发 host invite（本期不做）。

**Token 端点**：

- App 鉴权沿用现有 `token` query + `device_id` query（与 BFF `/api/mylife/*` 一致）；
- 业务 payload 放 JSON body，避免邀请 token 进入 URL/日志；
- 返回 `livekit_url` 由服务端配置提供，客户端不硬编码。

### 5.5 Web 入会

```
用户打开 https://<server>/meeting/#t=<invite_token>
  → 加载 /meeting/ 静态 SPA
  → JS 读取 location.hash 的 t
  → POST /api/meetings/preview {invite_token}
      → 会议摘要（title/time/host/status/requires_passcode）
  → 用户输入 display_name（+ passcode，如需要）
  → POST /api/meetings/join {invite_token, display_name, passcode}
      → 校验邀请/状态/窗口
      → 创建 participant 行
      → 返回 { livekit_url, token, meeting }
  → livekit-client: room.connect(livekit_url, token)
  → 渲染会议 UI
```

**关键点**：

- Web 端点不需要 App 鉴权；邀请 token 就是凭证。
- `preview` 只返回非敏感摘要，不签发 token；用于入会前展示。
- `join` 校验通过后才签发 token；支持 `max_uses` / 口令 / 过期 / 撤销。
- 页面在 `beforeunload` / `pagehide` 时 `room.disconnect()`；离开后会议仍可由其他人继续。
- 如果会议已结束/取消：返回 410/409，页面显示“会议已结束/已取消”。

### 5.6 会议结束/取消

| 触发 | 行为 |
|------|------|
| 最后一人离开 + `departure_timeout` | LiveKit 发 `room_finished` → status=`ended`；邀请失效（token 端点按状态拒绝） |
| 到达 `end_at + join_grace` 仍无人 | periodic sweeper 把 `scheduled` → `ended`（或保持 scheduled 但拒绝入会；建议置 ended） |
| Host/Agent 取消 | status=`cancelled`；撤销所有 invite；若 LiveKit room 存在则 `DeleteRoom`（断开所有人） |
| LiveKit 房间被外部删除 | webhook `room_finished` → ended；客户端收到 `Disconnected(ROOM_DELETED)` |

**取消接口**：

- `POST /api/meetings/{meeting_id}/cancel`，App 鉴权 + host device 匹配；
- Agent 侧可选增加 `meeting_cancel` action（Phase 2），或让用户通过 App 取消；
- 取消后保留 DB 行用于历史/审计；不物理删除。

### 5.7 离线与补投

现有实时 Agent 消息是 best-effort：`PushPublisher` 在 device 不在线时返回 False（`services/agent_relay/publisher.py:52-59`），消息丢弃。会议邀请不能只依赖这条路径。

**兜底设计**：

1. `meetings` 行在创建时立即落库（SQLite，持久）。
2. App 在以下时机调用 `GET /api/meetings/upcoming`：
   - App 启动/进入会话页；
   - `session.ready` 后；
   - App 从后台回前台；
   - 用户打开“会议”入口。
3. 返回 host 设备创建的、未来 N 天内的会议；App 显示横幅/徽标/列表。
4. 如果 App 收到实时邀请消息，也刷新一次列表；两者按 `meeting_id` 去重。
5. 不新增 APNs/FCM；不做服务端重投队列。

**为什么不做“会议邀请 job”**：`job_request` 是为分钟级后台任务设计的；会议创建是秒级服务端动作。把邀请塞进 JobStore 会污染 job 语义、增加 48h 生命周期和状态机复杂度，收益不大。`meetings` 表 + upcoming API 已经是持久化兜底。

### 5.8 时区与时间显示

- Agent 的 `AgentContext.timezone` 来自 `session.start.config.timezone`，上游已 canonical（`services/session/manager.py`、`services/shared/timeutil.py`）。
- `meeting_request.start_at` 用带 offset 的 ISO 8601；服务端解析为 UTC epoch ms 落库，同时保留 `timezone` 用于展示。
- `meetings.start_at_ms` 是唯一排序/窗口判断依据；`timezone` 只影响文案。
- App/Web 展示：
  - 设备本地时间（主）；
  - 会议时区标签（如 `Asia/Shanghai`）与本地不同时显示“会议时间”副标题；
  - 24h/12h 遵循系统 locale。
- 夏令时：UTC ms + IANA timezone 可正确格式化；不要只存本地字符串。

---

## 6. 后端设计

### 6.1 配置

在 `services/shared/config/models.py` 新增：

```python
class MeetingConfig(BaseModel):
    """Personal meeting（LiveKit）配置。"""

    enabled: bool = False
    livekit_url: str = ""              # 客户端连接地址，如 wss://<livekit-host>
    livekit_api_url: str = ""          # 服务端 RoomService 地址，如 http://127.0.0.1:7880
    api_key: str = ""                  # LiveKit API key（env 注入）
    api_secret: str = ""               # LiveKit API secret（env 注入）
    web_base_url: str = ""             # 可选：对外基址；空 = 由客户端从 serverBase 推导
    token_ttl_s: int = 600             # LiveKit JWT TTL（秒）
    invite_ttl_s: int = 7 * 24 * 3600  # 邀请有效期
    join_early_s: int = 600            # 允许提前入会
    join_grace_s: int = 3600           # 会议结束后允许入会的宽限
    default_duration_min: int = 30
    max_duration_min: int = 480
    max_participants: int = 20
    empty_timeout_s: int = 300
    departure_timeout_s: int = 300
    webhook_enabled: bool = True       # dev 自签/不可达时可先关，靠 lazy/sweeper
    web_dist_dir: str = ""             # 空 = apps/web/meeting/dist（相对仓库根）
```

`AppConfig` 增加 `meeting: MeetingConfig = MeetingConfig()`。

**校验**（`shared/config/validation.py`）：

- `enabled=true` 时 `livekit_url` / `livekit_api_url` / `api_key` / `api_secret` 非空；
- `livekit_url` 必须是 `ws://` 或 `wss://`；生产必须 `wss://`；
- `livekit_api_url` 必须是 `http://` 或 `https://`；
- `token_ttl_s` / `invite_ttl_s` / durations 在合理范围；
- `web_dist_dir` 指向的 `index.html` 必须存在（fail-fast，与 webroot 的“承诺”语义一致）。

**配置示例**（`configs/dev/server.yaml`，默认关闭）：

```yaml
meeting:
  enabled: false
  livekit_url: "wss://<livekit-host>"
  livekit_api_url: "http://127.0.0.1:7880"
  api_key: "${LIVEKIT_API_KEY}"
  api_secret: "${LIVEKIT_API_SECRET}"
  web_base_url: ""                   # 空 = 客户端推导
```

生产用 `LIVACHAT__MEETING__API_SECRET` 等 env 注入，禁止把 secret 写进 yaml。

> `livekit_api_url` 只给服务端 RoomService 使用（如 `http://127.0.0.1:7880`）；**不要返回给客户端**。客户端只用 `livekit_url`（如 `wss://<livekit-host>`）。

### 6.2 MeetingStore

位置：`services/meeting/store.py`。

- 复用 `chat_server.db` 路径（`cfg.sqlite.chat_server_db_path`）；
- 启动时执行 `CREATE TABLE IF NOT EXISTS`（可放在 `ChatStore._init_db` 的 schema 里，或 `MeetingStore.ensure_schema()`；建议后者，避免 ChatStore 文件继续膨胀）；
- 所有写操作 `asyncio.to_thread` + `sqlite3.connect(timeout=10)` + `PRAGMA busy_timeout=5000`；
- 读操作可用只读连接 `file:...?mode=ro`；
- 不做长事务；单条 upsert。

核心方法：

```python
async def create_meeting(...) -> Meeting
async def get_meeting(meeting_id: str) -> Meeting | None
async def list_upcoming_by_host(device_uuid: str, from_ms: int, to_ms: int) -> list[Meeting]
async def update_status(meeting_id: str, status: str, *, started_at_ms=None, ended_at_ms=None) -> None
async def create_invite(meeting_id: str, role: str, ...) -> tuple[Invite, str]  # 返回 raw token 一次
async def find_invite_by_token(raw_token: str) -> Invite | None
async def consume_invite(invite_id: str) -> bool
async def revoke_invites(meeting_id: str) -> None
async def add_participant(...) -> Participant
async def mark_participant_left(...)
async def record_webhook_event(event_id, event_type, meeting_id, payload) -> bool  # False = duplicate
async def expire_stale_meetings(now_ms: int) -> int
```

**device_uuid 归一**：所有 `host_device_uuid` / `device_uuid` 的写入与查询统一 `lower()`。这是既有代码的硬约束：iOS `UUID().uuidString` 可能大写，而 `ChatStore` 的 recording/hermes 映射已按 lower 归一（`services/shared/persistence/chat_store.py` 的 `_get_or_create_recording_session_sync` 注释）；MeetingStore 若不做同样归一，host 匹配会 miss。

**邀请 token 生成**：

```python
raw = secrets.token_urlsafe(32)          # 43 chars, URL-safe
token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

- DB 只存 hash；raw 只在创建时返回一次（给 Agent 文案/给 App 分享）。
- 校验用 `hmac.compare_digest(token_hash, sha256(raw))`。
- `max_uses=0` 表示不限；`uses` 在成功签发 token 后 +1（原子 `UPDATE ... WHERE ...`）。
- `revoked_at_ms` 非空即拒绝。
- **过期时间**：`expires_at_ms = max(now + invite_ttl_s, meeting.end_at_ms + join_grace_s + 3600_000)`。不能简单用 `now + 7d`，否则 7 天后才召开的会议邀请会提前失效；也不能永不过期。

**口令**（可选）：

- 不上明文；用 PBKDF2-HMAC-SHA256（`hashlib.pbkdf2_hmac`，随机 salt，20 万次）存 `pbkdf2$iterations$salt$hash`；
- 校验用 `hmac.compare_digest`；
- 口令错误与邀请无效返回同样的 403，避免枚举。

### 6.3 MeetingService

位置：`services/meeting/service.py`。

```python
class MeetingService:
    def __init__(self, cfg: MeetingConfig, store: MeetingStore): ...

    async def create_from_agent(
        self, *, device_id: str, request: MeetingRequest, language: str = "en",
        now_ms: int | None = None,
    ) -> CreatedMeeting: ...

    async def create_from_app(
        self, *, device_id: str, request: MeetingRequest, host_name: str = "",
    ) -> CreatedMeeting: ...

    async def preview(self, invite_token: str) -> MeetingPreview: ...

    async def mint_app_token(
        self, *, device_id: str, meeting_id: str | None, invite_token: str | None,
        display_name: str,
    ) -> JoinCredentials: ...

    async def mint_web_token(
        self, *, invite_token: str, display_name: str, passcode: str | None,
    ) -> JoinCredentials: ...

    async def cancel(self, *, device_id: str, meeting_id: str) -> None: ...

    async def handle_webhook(self, raw_body: str, auth_header: str) -> None: ...

    async def aclose(self) -> None: ...  # 关闭 LiveKitAPI aiohttp session
```

`mint_app_token` 同时收到 `meeting_id` 与 `invite_token` 时，必须校验两者指向同一会议；否则 400，防止拿 A 会议的邀请去开 B 会议。

**创建会议**：

1. 校验参数：title 1-120 字符；`start_at` 可解析；`duration_min` 5-480；`start_at` 未来（允许 1 分钟容差）；不超过 365 天；`max_participants` 1-100。`host_name` 可选：Agent 通常不知道用户姓名，留空时 UI 显示“Host”；App 创建时可传本地显示名。
   - `display_name`：1-40 字符，去掉控制字符/首尾空白；空/非法 → “Guest”。不要把它拼进 HTML（React 自动转义），也不要放进日志。
2. `meeting_id = "mtg_" + uuid7()`；`room_name = meeting_id`。
3. 写 `meetings` 行。
4. 创建 guest invite（role=guest，TTL=invite_ttl_s），拿到 raw token。
5. 可选：创建 host invite（role=host），用于 App 分享/网页 host 入会；本期 host 用 App 鉴权即可，host invite 可不建。
6. 返回 `CreatedMeeting(meeting, invite_token, join_url, web_url, confirmation_markdown)`。

**重复 nudge 的防重**（建议）：`create_from_agent` 先按 `(host_device_uuid, title_normalized, start_at_ms)` 查最近 5 分钟内创建的 `scheduled|active` 会议；命中则复用该会议、**新建一条 guest invite**（raw token 不落库，无法复用旧链接），返回新的确认文案。这样同一 chatlog 被重复 nudge 时不会产生多个会议；用户明确要求“再约一个”时，标题/时间通常不同，仍会新建。

**防刷**：`create_from_agent` / `create_from_app` 增加 per-device 频率限制（如 10 次/小时）；Agent 异常循环/用户刷屏时返回错误并记 metric，不无限建会议。

**LiveKit JWT 签发**：

```python
from datetime import timedelta
from livekit import api
import json

def _mint_livekit_jwt(
    cfg: MeetingConfig,
    meeting: Meeting,
    *,
    identity: str,
    display_name: str,
    role: str,
) -> str:
    grants = api.VideoGrants(
        room_join=True,
        room=meeting.room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
        # 本期不做屏幕共享；host/guest 都只允许摄像头+麦克风。
        # 未来加屏幕共享时再给 host 增加 "screen_share"/"screen_share_audio"。
        can_publish_sources=["camera", "microphone"],
        room_admin=(role == "host"),
    )
    token = (
        api.AccessToken(cfg.api_key, cfg.api_secret)
        .with_identity(identity)
        .with_name(display_name)
        .with_ttl(timedelta(seconds=cfg.token_ttl_s))
        .with_grants(grants)
        .with_metadata(json.dumps({
            "meeting_id": meeting.meeting_id,
            "role": role,
        }, ensure_ascii=False))
        .with_room_config(api.RoomConfiguration(
            empty_timeout=cfg.empty_timeout_s,
            departure_timeout=cfg.departure_timeout_s,
            max_participants=meeting.max_participants,
            metadata=json.dumps({"meeting_id": meeting.meeting_id}),
        ))
        .to_jwt()
    )
    return token
```

> `api.RoomConfiguration` 来自 `livekit.protocol.room`，`livekit-api` 已在 `from livekit import api` 中 re-export。字段名是 snake_case（`empty_timeout`、`departure_timeout`、`max_participants`）。
>
> token 的 `metadata` 对同房间参与者可见；只放 `meeting_id` / `role` 等非敏感字段，不要放 token/邮箱/电话。

**入会校验顺序**：

1. 会议存在；
2. 状态不是 `cancelled` / `ended`；
3. 当前时间在 `[start_at_ms - join_early_s*1000, end_at_ms + join_grace_s*1000]`；host 可放宽；
4. 邀请有效（hash 命中、未过期、未撤销、未超次数）；
5. 口令正确（如有）；
6. 房间人数未满（可先依赖 LiveKit `max_participants`；如需友好错误，可 `ListParticipants` 查询，但会增加 API 调用；MVP 交给 LiveKit 拒绝）；
7. 创建 `meeting_participants` 行，签发 JWT。

**取消**：

- 校验 host device；
- `revoke_invites(meeting_id)`；
- `update_status(cancelled)`；
- `await lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))`（房间不存在/LiveKit 暂时不可用时吞掉并记日志）；
- 返回 204。

取消以 DB 状态为准：LiveKit `DeleteRoom` 是 best-effort，失败不阻断取消（否则 LiveKit 故障时用户无法取消会议）。旧 token 在 TTL 内可能仍能连上已存在的房间；下一次入会校验会因 `cancelled` 拒绝签发新 token。

### 6.4 HTTP API

建议新增 `services/meeting/router.py`，挂载到 `create_app`（仅在 `meeting.enabled` 时）：

```python
router = APIRouter(prefix="/api/meetings")
```

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/meetings` | App | App 主动创建会议（可选；Agent 主路径不需要） |
| GET | `/api/meetings/upcoming` | App | host 设备未来 N 天会议；`from`/`to`/`limit` |
| GET | `/api/meetings/{meeting_id}` | App host | 会议详情 |
| POST | `/api/meetings/token` | App | native 入会；body `{meeting_id?, invite_token?, display_name}`；host 传 `meeting_id` 即可，guest 传 `invite_token` |
| POST | `/api/meetings/{meeting_id}/cancel` | App host | 取消 |
| GET | `/api/meetings/{meeting_id}/invite` | App host | 获取/轮换分享链接（返回 `web_url`/`expires_at`） |
| POST | `/api/meetings/preview` | 邀请 token | Web 入会前预览 |
| POST | `/api/meetings/join` | 邀请 token | Web 入会换 LiveKit token |
| POST | `/api/meetings/webhook` | LiveKit JWT | LiveKit webhook |
| GET | `/meeting/` | 无 | 静态 SPA（静态挂载） |

**App 鉴权依赖**：

```python
async def get_meeting_principal(
    request: Request,
    token: str = Query(default=""),
    device_id: str | None = Query(default=None),
) -> Principal:
    authenticator = request.app.state.authenticator
    try:
        authenticator.verify(token, device_id or "")
    except AuthError:
        raise MeetingError("unauthorized", "authentication failed", 401)
    return Principal(user_id="dev-user", device_id=device_id)
```

这与 `services/bff/common/deps.py:get_principal` 语义一致；为避免 meeting 依赖 bff，建议复制到 `services/meeting/deps.py` 或抽到 `shared/auth`。**区别**：meeting 端点必须要求合法 `device_id`（UUID v4）——host 身份完全依赖它，不能像 BFF 一样允许 `None`；缺失/非法直接 401。

**错误信封**：沿用 BFF 风格：

```json
{"error": {"code": "invite_expired", "message": "invitation has expired"}}
```

状态码映射：

| code | HTTP |
|------|------|
| `bad_request` | 400 |
| `unauthorized` | 401 |
| `forbidden` | 403 |
| `not_found` | 404 |
| `meeting_ended` / `meeting_cancelled` | 410 |
| `invite_expired` / `invite_revoked` | 410 |
| `meeting_not_started` / `meeting_full` | 409 |
| `invite_exhausted` / `passcode_required` / `passcode_invalid` | 403 |
| `too_many_requests` | 429 |
| `livekit_unavailable` | 503 |

**示例：native 入会**

```http
POST /api/meetings/token?token=<wire>&device_id=<uuid>
Content-Type: application/json

{"invite_token":"<43-char>","display_name":"Alice"}
```

```json
{
  "livekit_url": "wss://<livekit-host>",
  "token": "<LiveKit JWT>",
  "role": "guest",
  "meeting": {
    "meeting_id": "mtg_019...",
    "title": "周会",
    "agenda": "...",
    "language": "zh-Hans",
    "start_at": "2026-11-10T15:00:00+08:00",
    "duration_min": 30,
    "timezone": "Asia/Shanghai",
    "status": "active",
    "host_name": "Alice",
    "requires_passcode": false
  }
}
```

host 调用同一端点时 body 可只传 `{"meeting_id":"mtg_..."}`；服务端按 `device_id` 识别 host，不需要邀请 token。

### 6.5 Agent contract 扩展

**Prompt 变更**（`services/agent_relay/prompts/hermes_monitor.md`）：

把“只允许三种响应”改为四种，新增：

```markdown
- 用户明确要求预约/安排会议（例如「明天下午三点和小王开会」）→
  `{"action": "meeting_request",
    "title": "会议标题",
    "start_at": "2026-11-10T15:00:00+08:00",
    "duration_min": 30,
    "attendees": ["小王"],
    "note": "可选备注"}`
  `start_at` 必须是带时区偏移的 ISO 8601；时间以本设备时区为准；
  无法确定时间/时长时不要猜，输出 `{"action":"noop"}` 或先向用户确认。
```

并在 mission 增加 `$meeting_hint`（仅 `meeting.enabled` 时非空）：

```markdown
# 会议预约

你可以为用户创建会议。用户明确要求预约/安排会议时，使用 `meeting_request` 契约；
relay 会创建会议、生成入会链接并发送给用户。不要自己生成网页或访问 LiveKit。
```

同时在“禁止”段明确：`meeting_request` 是 relay 执行的服务端动作，不属于“主 turn 长任务/高危工具”；不要因为该禁止条款而拒绝预约。

**Adapter 变更**（`services/agent_relay/adapters/hermes.py`）：

```python
elif action == "meeting_request":
    inc_nudge(self.name, "meeting_request")
    async for chunk in self._handle_meeting_request(conn, contract, context):
        yield chunk
```

`_handle_meeting_request`：

```python
async def _handle_meeting_request(self, conn, contract, context):
    if self.meeting_service is None:
        logger.warning("meeting_request but meeting service not configured")
        return
    try:
        created = await self.meeting_service.create_from_agent(
            device_id=conn.device_id,
            request=MeetingRequest.from_contract(contract),
            language=context.language,
        )
    except MeetingValidationError as exc:
        logger.info("meeting_request rejected", extra={"reason": str(exc)})
        return
    yield AgentResponseChunk(
        text=created.confirmation_markdown,
        is_final=True,
        metadata={"message_id": f"msg_meeting_{created.meeting.meeting_id}"},
    )
```

校验失败时当前设计按 `noop` 处理（不打扰用户）；如果产品希望有反馈，可改为 yield 一条本地化短提示（如“我没能确认会议时间，请再说一次”），但不要暴露内部校验细节。

**依赖注入**（`services/app/application.py`）：

- 启动时若 `meeting.enabled`，创建 `MeetingStore`、`MeetingService`、`LiveKitAPI`；**在 agent relay 装配之前**创建并注入，避免 adapter 先拿到 None；
- `hermes.meeting_service = meeting_service`；`openclaw.meeting_service = meeting_service`（若实现 OpenClaw 分支）；
- 关闭时 `await meeting_service.aclose()`。

**`AgentAdapter` 基类**：增加 `meeting_service: Any | None = None` 默认属性，保持向后兼容。

**`session_manager.py` 的 message_id 透传**：当前 `_on_adapter_push` 调用 `_publish_reply` 时不读取 `chunk.metadata`，`_publish_reply` 自己用 `_generate_message_id()` 随机生成（`services/agent_relay/session_manager.py:388-435`）。为了让会议邀请具备确定性幂等键，需要：

```python
# _on_adapter_push
message_id = (chunk.metadata or {}).get("message_id")
await self._publish_reply(..., message_id=message_id)

# _publish_reply
message_id: str | None = None
...
msg = PushMessage(..., message_id=message_id or _generate_message_id(), ...)
```

这是向后兼容的小改：现有 adapter 不传 metadata 时行为不变。

**OpenClaw**：

- 如果启用 OpenClaw，需在其 nudge 契约中解析 `meeting_request` 并调用同一 `MeetingService`；否则在 OpenClaw 的 prompt 中不注入 `$meeting_hint`，避免它输出未支持 action。
- 本期可先只支持 Hermes；OpenClaw 作为后续对齐项（见 §13 Q2）。

**Dialog awareness**：

- `services/session/agent_job_ingest.py` 的 `AGENT_AWARENESS_*_TEMPLATE` 能力清单增加“预约/创建会议”，让实时对话 LLM 知道可以转交给后台 Agent。
- 该改动是 prompt 文案，需同步 `docs/prompts_improvements.md` / `docs/agent_relay_prompts.md` 的规范副本（如有）。

### 6.6 LiveKit API 客户端

`services/meeting/livekit_client.py`：

```python
from livekit import api

class LiveKitClient:
    def __init__(self, cfg: MeetingConfig):
        self._api = api.LiveKitAPI(
            url=cfg.livekit_api_url,
            api_key=cfg.api_key,
            api_secret=cfg.api_secret,
        )
        self._receiver = api.WebhookReceiver(
            api.TokenVerifier(cfg.api_key, cfg.api_secret)
        )

    async def delete_room(self, room_name: str) -> None:
        await self._api.room.delete_room(api.DeleteRoomRequest(room=room_name))

    async def list_participants(self, room_name: str) -> list:
        resp = await self._api.room.list_participants(
            api.ListParticipantsRequest(room=room_name)
        )
        return list(resp.participants)

    def verify_webhook(self, raw_body: str, auth_header: str) -> api.WebhookEvent:
        return self._receiver.receive(raw_body, auth_header)

    async def aclose(self) -> None:
        await self._api.aclose()
```

依赖：`services/pyproject.toml` 增加 `livekit-api>=1.2.2`（会带入 `aiohttp`、`PyJWT` 等；现有 `PyJWT` 已是依赖）。`infra/docker/server.Dockerfile` 的 `pip wheel ... services/` 会解析该依赖；如果维护显式 wheel 列表，记得同步。

### 6.7 Webhook 处理

`POST /api/meetings/webhook`：

```python
@router.post("/webhook")
async def livekit_webhook(request: Request) -> Response:
    raw = await request.body()
    auth = request.headers.get("Authorization", "")
    try:
        event = request.app.state.meeting_service.verify_webhook(
            raw.decode("utf-8"), auth
        )
    except Exception:
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    await request.app.state.meeting_service.handle_webhook_event(event)
    return Response(status_code=200)
```

处理逻辑：

| event | 行为 |
|-------|------|
| `room_started` | `meeting_id` 从 `event.room.name` 解析；`update_status(active, started_at_ms=now)` |
| `room_finished` | `update_status(ended, ended_at_ms=now)`；标记所有 participant left |
| `participant_joined` | upsert participant（若 App 没来得及写）；更新计数 |
| `participant_left` | `mark_participant_left` |
| 其他 / 未知 meeting_id | 记录 `meeting_webhook_events` 后忽略；不要为未知房间创建会议行 |

**幂等**：先 `record_webhook_event(event.id, ...)`，重复则直接 200。

**PII 最小化**：`meeting_webhook_events.payload_json` 只存 `event_id/event_type/room_name/participant_identity/created_at` 等排查所需字段，不存 participant `name`/metadata；完整 payload 只用于当次处理，不落库。

**注意**：

- `event.room.name` 是 LiveKit room name（`mtg_...`）；`event.participant.identity` 是 `p_...`。
- 不要依赖 webhook 的到达顺序；状态更新用 `WHERE status IN (...)` 条件更新，允许乱序。
- webhook 不能阻塞超过 5 秒；SQLite 写很快，直接 `to_thread` 即可。若未来加外部调用，改为队列。
- **兜底 sweeper**：lifespan 启动一个 60s 周期的任务（或每个会议 API 请求先做 lazy check），把 `now > end_at_ms + join_grace_s` 且仍为 `scheduled|active` 的会议置 `ended`；与 webhook 的条件更新幂等。这样即使 webhook 丢失，旧链接也会失效。

### 6.8 安全与限流

| 面 | 措施 |
|----|------|
| API secret | 仅 env/Secret；日志脱敏；不返回客户端。`shared/observability/logging.py` 的敏感键清单加入 `api_secret` / `api_key` / `invite_token` / `passcode`。**LiveKit API key/secret 与 LivaChat `APP_AUTH_TOKEN` 必须独立**，不要复用 |
| Invite token | 32 字节随机；DB 存 hash；fragment 传输；TTL/次数/撤销；可选口令 |
| 暴力尝试 | `/api/meetings/preview` / `/join` 按 IP + invite hash 限流（如 20 次/分钟）；单实例内存滑窗 |
| App 鉴权 | 复用现有 `Authenticator`；host 操作校验 `device_id == host_device_uuid`。**dev-token 模式下 device_id 可伪造**：个人/受控部署接受；多用户/公网部署需切 JWT 或给 host 操作加独立 host secret |
| 日志 | 不记录 raw invite token / LiveKit JWT / passcode；URL 日志只记 path，不记 fragment（fragment 本就不发送） |
| Web 页 | CSP、`Referrer-Policy: no-referrer`、`X-Content-Type-Options: nosniff`、`Permissions-Policy: camera=(self), microphone=(self), display-capture=(self)` |
| Webhook | JWT 验签 + body hash；拒绝未签名请求 |
| PII | LiveKit identity/room 用 opaque ID；display_name 用户自填；LivaChat 日志不记录 display_name（或只记 hash）。注意 LiveKit token 的 `name` 便于 UI 显示，但可能进入 LiveKit 自身日志/trace；若合规要求更严格，可把显示名放 `metadata`、`name` 用 opaque，并自定义 UI 读取 metadata |
| 自托管 revocation 限制 | 短 TTL + 会议状态 + 取消时 `DeleteRoom`；移除参与者后旧 token 仍可能重连（自托管限制），文档明示 |
| 房间人数 | `max_participants` 在 token 的 `RoomConfiguration`；所有 token 一致 |
| 邀请泄露 | 邀请链接即能力；敏感会议建议口令 + 短 TTL；取消/轮换邀请 |

### 6.9 可观测性

新增 Prometheus 指标（复用 `shared.observability.metrics.get_registry()`）：

| 指标 | 类型 | 标签 |
|------|------|------|
| `livachat_meetings_created_total` | counter | `source=agent|app` |
| `livachat_meeting_join_tokens_total` | counter | `role=host|guest`, `result=ok|invalid|expired|ended` |
| `livachat_meeting_active` | gauge | 无 |
| `livachat_meeting_webhooks_total` | counter | `event`, `result=ok|duplicate|invalid` |
| `livachat_livekit_api_errors_total` | counter | `op=create|delete|list` |
| `livachat_meeting_join_latency_ms` | histogram | `path=app|web` |

**日志纪律**：

- 记录 `meeting_id`、`role`、`result`；不记录 `invite_token`、JWT、passcode、display_name。
- 不把 `device_id` 作为指标 label（无界基数），与现有 agent_relay 指标纪律一致。

**数据保留**：本期不自动清理会议元数据；建议后续增加按 `ended_at_ms` 的 90 天清理任务，`meeting_webhook_events` 保留 30 天。媒体不落库（LiveKit 不录制时），与现有“媒体不落库”原则一致。

**健康检查边界**：不要把 LiveKit 可达性作为 `/readyz` 的硬失败条件——LiveKit 故障不应导致现有 WS 对话服务被摘除。会议可用性用独立 metric/日志或 `/api/meetings/health` 观察；`/readyz` 仍只检查 chat store。

---

## 7. Web 入会页设计

### 7.1 实现位置与技术栈

Web 客户端的**唯一实现**是 `apps/web/meeting/`。其目录、技术栈、开发/构建方式、demo 形态以 [`docs/webmeeting_design.md`](webmeeting_design.md) 为准；本节不再维护第二套目录/依赖清单。

本文只定义生产入会页的**契约与页面行为**：

- 生产构建 `base` 固定 `/meeting/`（`npm run build`），服务端 `app.mount("/meeting", StaticFiles(directory=dist, html=True), name="meeting")`；与 demo 完全一致；
- demo 与生产使用同一份代码、同一个 `base=/meeting/`；demo/验证阶段允许客户端内置 key pair，生产必须切 `tokenMode=server`（见 `webmeeting_design.md` §3.13.4）；
- 生产页所需的邀请 token 入口、preview/join 调用与 `loading/invalid/preview/joining/inMeeting/ended/error` 七态，是在 demo 四态基础上扩展同一 app，**不得另起一个 Web app**；
- 不使用 CDN，所有 JS/CSS 打包进 `dist`，避免外部依赖与 CSP 放宽；**背景效果例外**：MediaPipe 的 wasm/模型必须自托管到 `dist/vendor/mediapipe/`（`assetPaths`，见 `webmeeting_design.md` §3.8.6），否则默认会走 jsDelivr/storage.googleapis.com，需相应放宽 CSP；不需要背景效果就设 `enableBackgroundEffects=false`。

### 7.2 URL 与 Token 交换

**页面 URL**：

```
https://<server-host>/meeting/#t=<invite_token>
```

**读取**：

```ts
const params = new URLSearchParams(location.hash.slice(1));
const inviteToken = params.get("t") ?? "";
if (!/^[A-Za-z0-9_-]{43}$/.test(inviteToken)) {
  // 显示“邀请链接无效”
}
```

**预览**：

```ts
const preview = await fetch("/api/meetings/preview", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ invite_token: inviteToken }),
}).then(r => r.json());
```

**入会**：

```ts
const creds = await fetch("/api/meetings/join", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    invite_token: inviteToken,
    display_name,
    passcode: passcode || undefined,
  }),
}).then(r => r.json());

const room = new Room();
await room.connect(creds.livekit_url, creds.token);
```

**安全**：

- 不把 invite token 写入 `localStorage`；只放内存（刷新后从 fragment 重新读取）。
- 不把 token 放在 query string；避免服务器/代理日志。
- `referrerPolicy: "no-referrer"`。
- 失败重试要有上限；429 显示“请稍后再试”。

### 7.3 页面状态与 UI

```
loading → invalid（无 token/格式错）
        → preview（会议摘要 + 名字输入 + 可选口令）
        → joining（按钮 loading）
        → inMeeting（RoomContext + VideoConference）
        → ended / error
```

**Lobby**（demo 阶段的 Home / PreJoin 扩展；同一 app，不是新页面）：

- 会议标题、时间（本地时区 + 会议时区）、host、agenda；
- 显示名输入（1-40 字符，默认 “Guest”）；
- 麦克风/摄像头开关（可选；入会后也可切换）；
- 口令输入（仅 `requires_passcode=true`）；
- “加入会议”按钮；
- 如果 `join` 返回 `meeting_not_started`：显示开始时间与倒计时（或“稍后再来”），不进入会议；
- “在 App 中打开”可选（如果检测到 LivaChat App scheme/universal link；本期可不做）。

**会议内**：

- 使用 `RoomContext` + `VideoConference`（与 demo 一致的 `MeetingRoom` 组件；不要另写一套 `LiveKitRoom` 实现）；
- 自定义顶部：会议标题、连接状态、离开按钮；
- 移动端：自适应网格；横屏提示；安全区；
- 连接质量指示（`ConnectionQualityIndicator`）；
- 错误：`ConnectionStateToast` + 自定义重试；
- 断线重连：LiveKit 会自动重连；如果进入 `Disconnected` 且会议仍 `active`，页面可重新调用 `/api/meetings/join` 拿新 token 再连接（不要复用过期 token）。

**主题**：

- 与 App 品牌色一致；使用 `data-lk-theme` CSS 变量覆盖；
- 暗色主题优先（App 固定暗色）。

**i18n**：

- MVP 至少 en / zh-Hans / zh-Hant；
- 优先使用 `meeting.language`（Agent 目标语言），否则 `navigator.language`，再 fallback en；
- 文案集中在一个 `i18n.ts` 字典；后续补齐 App 的 16 个语言。

> 以上 i18n 是**生产增量**，在同一个 `apps/web/meeting` 内实现（demo 阶段英文 UI，见 `webmeeting_design.md` §3.9）；不要为此另起页面或另一个 Web app。

### 7.4 媒体权限与浏览器兼容

- 入会前检查 `navigator.mediaDevices?.getUserMedia`；不支持则显示“浏览器不支持”并建议使用 Chrome/Safari。
- 麦克风权限被拒绝：允许 audio-only 入会？如果连麦克风都没有，LiveKit 仍可订阅他人，但无法发言；显示提示并提供重试。
- 摄像头权限被拒绝：可以仅音频入会；控制栏摄像头按钮置灰/提示。
- 自动播放策略：连接后由用户点击“加入”触发，音频播放通常被允许；若被阻止，显示“点击启用声音”。
- iOS Safari：需要用户手势才能播放音频/采集；Lobby 的“加入”按钮满足。
- 不要在页面加载时自动 `getUserMedia`；在用户点击加入后再请求，避免权限弹窗被拦截。

### 7.5 部署与静态托管

**挂载**（`services/app/application.py`）：

```python
if config.meeting.enabled:
    from meeting.router import router as meeting_router
    app.include_router(meeting_router)

    dist = Path(config.meeting.web_dist_dir or (
        Path(__file__).resolve().parents[2] / "apps/web/meeting/dist"
    ))
    if not (dist / "index.html").is_file():
        raise RuntimeError(f"meeting web dist missing: {dist}")
    app.mount("/meeting", StaticFiles(directory=str(dist), html=True), name="meeting")
```

- `web_dist_dir` 空 = 仓库根 `apps/web/meeting/dist`；
- `enabled=true` 且 dist 缺失 → **启动 fail-fast**（与 webroot 的“承诺”语义一致）；
- 构建步骤：`cd apps/web/meeting && npm ci && npm run build`；CI/部署前执行；生产构建的 `dist/config.json` 必须是 `tokenMode=server`，不得带 `apiKey/apiSecret`；
- 服务端不需要 Node。

**现有部署文件的缺口（必须改）**：`infra/docker/server.Dockerfile` 目前只 `COPY configs/` 并安装 Python 包，**没有** `apps/web/meeting/dist`；`infra/kubernetes/deployment.yaml` 也只挂载 config。`meeting.enabled=true` 后如果不改：

- Docker：增加 Node build stage（`node:20` 构建 `apps/web/meeting`）→ `COPY --from=web-build /build/apps/web/meeting/dist /app/apps/web/meeting/dist`；或把 dist 放到挂载卷，`meeting.web_dist_dir` 指向它。
- K8s：用 ConfigMap/emptyDir + initContainer 构建不现实（镜像里没有 Node）；推荐把 dist 打进镜像，或用对象存储/独立静态站点（但这样 `/meeting/` 与 API 不同源，需要 CORS/绝对 URL，且邀请 token 交换要跨域，安全性更复杂）。**本期推荐打进镜像，保持同源。**

**CSP/安全头**：

- 为 `/meeting/*` 响应加中间件：
  ```
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' wss://<livekit-host>; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
  Permissions-Policy: camera=(self), microphone=(self), display-capture=(self)
  ```
- `connect-src` 必须包含配置的 `livekit_url`；如果 LiveKit 用 TURN/TLS，WebRTC 的 ICE 不受 `connect-src` 限制，但信令 WSS 需要。
- 如果 `livekit_url` 可配置，CSP 头由服务端动态生成；不要写死。
- 页面与 API 同源（`/meeting/` 与 `/api/meetings/*` 同一 server），**不需要 CORS**；不要添加 `Access-Control-Allow-Origin: *`，避免把邀请兑换接口暴露给任意站点。
- 生产必须 HTTPS；`Secure` cookies 不用（无 cookie）。
- 如果生产页启用背景效果，CSP 必须按 `webmeeting_design.md` §3.12.4 放行 MediaPipe（`wasm-unsafe-eval` + `cdn.jsdelivr.net`/`storage.googleapis.com`，或自托管后收紧到 `'self'`）；否则背景效果会被 CSP 拦截。

**反向代理**：

- LivaChat 现有 Ingress 可继续服务 `/meeting/` 与 `/api/meetings/*`；
- LiveKit 用独立子域名，不经过 LivaChat Ingress；
- 如果必须同域，使用路径前缀可能不被 LiveKit SDK 支持（SDK 连接 URL 后使用 `/rtc` 路径），建议子域名。

### 7.6 Web 页安全

| 风险 | 缓解 |
|------|------|
| XSS | React 默认转义；不使用 `dangerouslySetInnerHTML`；CSP 禁 inline script（Vite 产物无 inline） |
| Token 泄漏 | fragment 不进请求；不写 localStorage；不发给第三方；Referrer no-referrer |
| 点击劫持 | `frame-ancestors 'none'` |
| 恶意邀请 | 高熵 token；限流；口令；过期/撤销 |
| 权限滥用 | 仅请求麦克风/摄像头；不请求屏幕共享（MVP） |
| 供应链 | 锁定依赖版本 + lockfile；不使用 CDN |

---

## 8. iOS 设计

### 8.1 依赖与模块

- 在 Xcode 工程添加 SPM 依赖：`https://github.com/livekit/client-sdk-swift`，`upToNextMajor("2.19.0")`。
- 仅 App target 依赖 LiveKit；`LivaChatSDK` 不依赖，保持 SDK 职责单一。
- 新增模块：
  ```
  apps/ios/LivaChatApp/
  ├── Features/Meeting/
  │   ├── MeetingFlowView.swift          # 单一 fullScreenCover：邀请/入会/会议内
  │   ├── MeetingInviteView.swift
  │   ├── MeetingRoomView.swift
  │   ├── MeetingViewModel.swift
  │   └── MeetingComponents.swift
  ├── Core/Meeting/
  │   ├── MeetingAPIClient.swift
  │   ├── MeetingModels.swift
  │   └── MeetingCoordinator.swift
  └── Core/PlatformAdapters/
      └── MeetingVideoView.swift          # UIViewRepresentable 包装 LiveKit.VideoView
  ```
- `MeetingVideoView` 是 UIKit 包装，放在 `PlatformAdapters`，符合 `docs/ios_dev_rules.md` §2/§4（第三方 SDK 只提供 UIKit 接口）。

### 8.2 路由与链接

**`ServerBase` 扩展**（`Core/Settings/ServerAddress.swift`）：

```swift
func isMeetingURL(_ url: URL) -> Bool {
    guard url.scheme?.lowercased() == scheme.rawValue,
          url.host?.lowercased() == host,
          effectivePort(of: url) == port else { return false }
    return url.path == "/meeting" || url.path.hasPrefix("/meeting/")
}
```

**`AppContainer`**：

- 新增 `@Published var presentedMeetingFlow: MeetingFlowRequest?`；
- `presentWebLink(_:)`：
  - 先判断 `serverBase.isMeetingURL(url)` → 解析 fragment `t` → `presentedMeetingFlow = ...`；
  - 再判断 `isWebrootURL` → `WebPage`；
  - 其他 → `WebLink`（Safari）。
- `AppRootView` 增加 `.fullScreenCover(item: $container.presentedMeetingFlow) { MeetingFlowView(...) }`。
- 注意 iOS 的“cover 上再 present cover”会互相 dismiss；用一个 `MeetingFlowView` 内部切换 invite/lobby/room，避免嵌套 cover。

**MeetingAPIClient**：复用 `ServerBase` + 当前 TLS policy（与 `ServerProbe` 一致：`allowsSelfSigned` 时只对当前 server host:port 放行自签），请求带 `token`/`device_id` query。不要把 LiveKit 连接用的信任策略和 LivaChat API 的信任策略混在一起——LiveKit SDK 仍要求可信证书。

**链接转换**：

- `LivachatURLConverter` 增加 `livachat_url://meeting/<token>` 分支：
  - token 校验 `[A-Za-z0-9_-]{43}`；
  - 输出 `serverBaseURL + "/meeting/#t=" + token`；
- 与 webroot 分支共用 `isTokenBoundary` / trailing punctuation 逻辑；
- 存储仍为 raw。

### 8.3 Native 会议 UI

**`MeetingFlowView`**：

```
enum MeetingStage {
    case invite(MeetingInvite)
    case joining
    case inRoom(MeetingJoinCredentials)
    case ended(reason)
    case error(MeetingError)
}
```

**邀请页**：

- 标题、时间、host、agenda、状态；
- 显示名输入（默认从设置读取；本期新增 `settings.display_name` 可选）；
- “在 App 中加入” / “网页加入” / “分享” / “取消”（host）；
- 入会前调用 `MeetingAPIClient.mintToken`。

**会议页**：

- `LiveKit.Room` + `RoomDelegate`；
- 网格：`LazyVGrid` + `MeetingVideoView`（每个 participant 的 video track）；
- 本地/远端音频由 LiveKit 自动播放；
- 控制栏：麦克风、摄像头、切换摄像头、扬声器/听筒、离开；
- 参与者列表、连接质量；
- `room.localParticipant.setMicrophone(enabled:)` / `setCamera(enabled:)`；
- `room.disconnect()` on leave/app termination；
- 错误：`DisconnectReason` 映射为 UI（房间删除/被移除/网络失败）。

**音频会话**：

- 入会前调用 `MeetingCoordinator.suspendConversation()`：
  - 停止现有 `ConversationRuntime`（`runtime.stop()`）；
  - 释放现有 `AVAudioSession` 的采集/播放；
- 让 LiveKit 自动管理 `AVAudioSession`（默认）；
- 如果遇到 `.playAndRecord` 配置冲突，设置 `AudioManager.shared.audioSession.isAutomaticConfigurationEnabled = false`，由 App 用 `.playAndRecord/.videoChat` 配置；
- 离会后重建 `ConversationRuntime`（`AppContainer.enterConversation()` / `startRuntime`），恢复原有音频会话。

**后台**：

- `UIBackgroundModes: audio` 已存在（`docs/ios_app_arch.md` §7.2）；
- LiveKit 可继续音频；视频在后台可能暂停；
- 如果产品要求后台会议，保持 audio 模式；否则后台可断开视频。

**权限**：

- `NSMicrophoneUsageDescription` / `NSCameraUsageDescription` 已存在；
- 入会前检查 `AVAudioSession.recordPermission` / `AVCaptureDevice.authorizationStatus`；
- 摄像头拒绝时允许音频入会。

### 8.4 Web 入会（iOS）

MVP：`SFSafariViewController` 打开 `https://<server>/meeting/#t=...`。

- 复用 `SafariLinkPresenter`（`Core/PlatformAdapters/WebLinkRouter.swift`）；
- 邀请 sheet 的“网页加入”直接调用 `SafariLinkPresenter.shared.present(url)`；
- 不需要 WebView 改造。

可选 Phase 2：应用内 WKWebView：

- 在 `WebPageView` 的 `WKUIDelegate` 实现：
  ```swift
  @available(iOS 15.0, *)
  func webView(_ webView: WKWebView,
               requestMediaCapturePermissionFor origin: WKSecurityOrigin,
               initiatedByFrame frame: WKFrameInfo,
               type: WKMediaCaptureType,
               decisionHandler: @escaping (WKPermissionDecision) -> Void) {
      guard isMeetingOrigin(origin) else { decisionHandler(.deny); return }
      decisionHandler(.grant)
  }
  ```
- `WKWebViewConfiguration` 设置 `allowsInlineMediaPlayback = true`、`mediaTypesRequiringUserActionForPlayback = []`；
- 仅对当前 server 的 `/meeting/` origin 授权；其他页面拒绝；
- App 自身必须已获得系统麦克风/摄像头权限，否则 WebView 的 grant 也会失败；
- 仍受 LiveKit 可信证书限制（自签不适用）。

### 8.5 与现有会话的互斥

**问题**：LivaChat 会话与 LiveKit 会议都要占用麦克风/音频会话。

**方案**：

1. 用户点“在 App 中加入” → `MeetingCoordinator.suspendConversation()`：
   - `runtime.stop()`（内部 `coordinator.stop()`：停止采集/播放/相机/打断，断开 SDK）；
   - 保留本地会话记录（`ConversationStore` 已落库）；
   - 释放音频会话。
2. 进入 LiveKit 会议。
3. 离开会议 → `MeetingCoordinator.resumeConversation()`：
   - `AppContainer.enterConversation()` 重建 runtime；
   - `ConversationCoordinator.start()` 重新连接 SDK / 采集 / 播放；
   - 服务端 session 会新建（`serverSessionID` 被清空），但本地历史仍在。
4. 如果产品要求“会议中仍保持 LivaChat WS 连接”，可以只停采集/播放、保留 SDK 连接；但实现更复杂，且服务端会把无音频的会话当作 idle。MVP 采用“暂停整个会话”。

**注意**：`ConversationRuntime.stop()` 会清空 `serverSessionID`；返回会话后是新服务端 session。这是现有生命周期语义，不需要改协议。

### 8.6 iOS 测试

| 层 | 用例 |
|----|------|
| 单元 | `LivachatURLConverter` 增加 meeting 分支：合法/非法 token、fragment、与 webroot 混合、大小写、幂等 |
| 单元 | `ServerBase.isMeetingURL`：同源/不同端口/不同 host/路径前缀 |
| 单元 | `MeetingAPIClient`：请求体、鉴权 query、错误映射、TLS policy |
| 单元 | `MeetingViewModel`：状态机（preview→joining→inRoom→ended/error）、取消、重试 |
| UI | 邀请 sheet 两个按钮；native 进入会议页；离开恢复会话；web 打开 SFSafari |
| 集成 | 两台模拟器/真机 + 一个浏览器入会；断言参与者、静音、摄像头、离开 |
| 回归 | 现有对话/历史/webroot 链接行为不变 |

---

## 9. Android 设计

### 9.1 依赖与模块

- `apps/android/settings.gradle.kts` 增加 JitPack（如果还没有）：
  ```kotlin
  dependencyResolutionManagement {
      repositories {
          google()
          mavenCentral()
          maven { url = uri("https://jitpack.io") }
      }
  }
  ```
- `apps/android/LivaChatApp/build.gradle.kts`：
  ```kotlin
  implementation("io.livekit:livekit-android:2.30.0")
  // 可选：implementation("io.livekit:livekit-android-compose-components:...")
  ```
- 仅 App 模块依赖 LiveKit；`LivaChatSDK` 不依赖。
- `MeetingApi` 复用现有 `TlsOkHttpClientFactory` / MyLife 的自签策略（只对当前 server host 放行）；LiveKit SDK 仍要求可信证书，两者不要混用同一个 trust-all 配置。
- 新增包：
  ```
  apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/
  ├── data/remote/meeting/
  │   ├── MeetingApi.kt
  │   └── dto/MeetingDtos.kt
  ├── data/repository/MeetingRepository.kt
  ├── media/MeetingRoomController.kt
  ├── di/MeetingModule.kt
  └── ui/screens/meeting/
      ├── MeetingInviteScreen.kt
      ├── MeetingScreen.kt
      ├── MeetingViewModel.kt
      ├── MeetingUiState.kt
      └── MeetingVideoView.kt        # AndroidView 包装 SurfaceViewRenderer
  ```

### 9.2 路由与链接

**`Routes.kt`**：

```kotlin
const val MEETING_INVITE = "meeting/invite?token={token}&webUrl={webUrl}"
const val MEETING_ROOM = "meeting/room?meetingId={meetingId}"
fun meetingInvite(token: String, webUrl: String) =
    "meeting/invite?token=${Uri.encode(token)}&webUrl=${Uri.encode(webUrl)}"
```

**`ServerLinks.kt`**：

- `convertLivachatUrls` 增加 meeting 分支；
- 新增：
  ```kotlin
  internal fun isMeetingUrl(url: String, serverBaseUrl: String): Boolean
  ```
  校验同源 + path `/meeting` 或 `/meeting/` + fragment `t`。

**链接分类器**：

- 新增 `AppLinkRouter`（或 `ServerLinks.kt` 中的纯函数）：
  ```kotlin
  sealed interface AppLink {
      data class Meeting(val inviteToken: String, val webUrl: String) : AppLink
      data class Web(val url: String) : AppLink
  }
  fun classify(url: String, serverBaseUrl: String): AppLink
  ```
- `ConversationScreen`、`HistoryDetailScreen`、`HotSlotScreen`、`EventListViewerScreen` 的 4 处 `onOpenLink` 改为：
  ```kotlin
  when (val link = classify(url, serverBaseUrl)) {
      is AppLink.Meeting -> navController.navigate(Routes.meetingInvite(link.inviteToken, link.webUrl))
      is AppLink.Web -> navController.navigate(Routes.webview(link.url))
  }
  ```
- 这样不会漏掉任何一个 Markdown 渲染入口。

**`NavGraph.kt`**：

- `MEETING_INVITE` → `MeetingInviteScreen`；
- `MEETING_ROOM` → `MeetingScreen`；
- 离开会议返回邀请/会话页。

### 9.3 Native 会议 UI

**`MeetingRoomController`**：

- 持有 `Room`（`LiveKit.create(context)`）；
- `connect(livekitUrl, token)`；
- 收集 `room.events`（`RoomEvent.TrackSubscribed/Unsubscribed/ParticipantConnected/Disconnected/ConnectionStateChanged`）；
- 暴露 `StateFlow<MeetingRoomState>`（participants、connection、local tracks）；
- `setMicrophoneEnabled` / `setCameraEnabled` / `switchCamera` / `disconnect`；
- 释放 renderer 和 `Room`。

**`MeetingScreen`**：

- Compose 全屏；网格布局 `LazyVerticalGrid`；
- 每个 participant 的 video track 用 `AndroidView { SurfaceViewRenderer(context) }`，在 `DisposableEffect` 中 `track.addRenderer` / `removeRenderer` + `release()`；
- 本地参与者优先显示（小窗）；
- 控制栏：麦克风、摄像头、切换摄像头、扬声器/听筒/蓝牙、离开；
- 参与者列表、连接质量；
- 状态：joining / connected / reconnecting / ended / error；
- 返回键：先确认“离开会议”。

**权限**：

- 入会前请求 `RECORD_AUDIO`（必需）和 `CAMERA`（可选）；Manifest 已声明；
- 拒绝麦克风：只允许“仅观看”或阻止入会（产品决策；建议仅观看 + 提示）；
- 拒绝摄像头：音频入会。

### 9.4 Web 入会（Android）

MVP：Chrome Custom Tabs / 系统浏览器打开 `https://<server>/meeting/#t=...`。

- 现有 `openInBrowser(context, url)`（`WebViewScreen.kt:64-69`）可用；
- 也可用 `androidx.browser:browser` Custom Tabs（依赖已在 `build.gradle.kts`）；
- 不需要 WebView 改造。

可选 Phase 2：应用内 WebView：

- `WebChromeClient.onPermissionRequest`：
  ```kotlin
  override fun onPermissionRequest(request: PermissionRequest) {
      val origin = request.origin.toString()
      if (!isMeetingOrigin(origin)) { request.deny(); return }
      val resources = request.resources.filter {
          it == PermissionRequest.RESOURCE_AUDIO_CAPTURE ||
          it == PermissionRequest.RESOURCE_VIDEO_CAPTURE
      }.toTypedArray()
      if (resources.isEmpty()) request.deny()
      else request.grant(resources)
  }
  ```
- `settings.mediaPlaybackRequiresUserGesture = false`（或由用户点击加入满足手势）；
- `settings.javaScriptEnabled = true`、`domStorageEnabled = true` 已开；
- 仅对当前 server 的 `/meeting/` origin 授权；其他页面拒绝；
- App 自身必须已获得 `RECORD_AUDIO` / `CAMERA` 权限，否则 grant 也会失败；
- 自签 LiveKit 证书不在 WebView 放行范围；仍需可信证书。

### 9.5 与现有会话的互斥

**方案**：

1. 进入 `MeetingInviteScreen` 前，`ConversationScreen` 发出 `ConversationEvent.OnScreenStop`（该事件已定义但当前未发出，见 `docs/android_app_arch.md` §7.2）；或由 `MeetingViewModel` 直接调用 `ConversationCoordinator.stop()`。
2. `ConversationCoordinator.stop()`：
   - `interruptCoordinator.stop()`；
   - `audioCapture.stop()` / `audioPlayback.stop()`；
   - `cameraController.unbind()`；
   - `sdk.disconnect()`；
   - 结束本地会话（`SessionUseCase.endSession`）。
3. 进入 `MeetingScreen`；LiveKit 接管音频焦点/麦克风/摄像头。
4. 离开会议：
   - `MeetingRoomController.disconnect()`；
   - 返回 `ConversationScreen`；`LaunchedEffect` 重新触发 `OnScreenStart`（或显式发 `OnScreenResume`），`ConversationCoordinator.start()` 重建。
5. 注意：Navigation Compose 会保留 conversation backstack entry 的 ViewModel，`onCleared()` 不会在导航到 meeting 时触发；必须显式 stop/start，不能依赖 `onCleared`。

**前台服务**：

- 会议麦克风在后台需要 FGS（Android 14 要求 `foregroundServiceType=microphone`）。现有 `MicCaptureService` 只服务 LivaChat 的 `AudioRecord`，不覆盖 LiveKit。
- MVP：会议仅前台；进入后台时提示/自动静音或断开。
- Phase 2：新增 `MeetingForegroundService`（`microphone` 类型）或复用现有 FGS 壳，确保 LiveKit 在后台继续音频。

### 9.6 Android 测试

| 层 | 用例 |
|----|------|
| 单元 | `convertLivachatUrls` meeting 分支；`isMeetingUrl`；`classify` |
| 单元 | `MeetingApi` 请求/鉴权/错误；`MeetingViewModel` 状态机 |
| UI | 邀请页 native/web 两个按钮；native 入会；离开恢复会话；web 打开 Custom Tabs |
| 仪器 | 两台设备/模拟器 + 浏览器入会；麦克风/摄像头开关；网络切换重连 |
| 回归 | 现有 WebView/webroot/MyLife 链接行为不变；会话页面退出/重进正常 |

---

## 10. 失败模式与边界矩阵

| 场景 | 行为 | 用户可见 |
|------|------|----------|
| LiveKit Server 不可用 | 创建会议仍可落库（不需要 LiveKit API）；入会 token 签发失败/连接失败 | “会议服务暂不可用，请稍后重试” |
| LivaChat Server 不可用 | 已有 LiveKit 会议不受影响；无法签发新 token/打开 `/meeting/` | 页面/接口 5xx |
| Agent relay 未启用 | 不能通过 Agent 预约；App 仍可手动创建（如实现） | 无邀请消息 |
| App 离线 | 实时邀请丢失；会议已落库 | 下次启动在“即将开始”看到 |
| 邀请过期 | `join`/`token` 返回 410 | “邀请已过期，请联系发起人” |
| 邀请被撤销/会议取消 | 410/409 | “会议已取消” |
| 口令错误 | 403 | “口令不正确” |
| 会议未开始（早于 join_early） | 403/409 | “会议尚未开始” |
| 会议已结束 | 410 | “会议已结束” |
| 房间人数满 | LiveKit 拒绝连接 | “会议人数已满” |
| Token 过期 | 客户端重新请求 token；若会议仍 active 则成功 | 无感（重连） |
| 重复 identity | LiveKit 断开旧连接（`DUPLICATE_IDENTITY`） | 旧设备提示；新设备入会 |
| 网络切换 | LiveKit 自动重连/ICE restart | “重连中”提示 |
| 麦克风权限拒绝 | 可仅观看（或阻止，按产品） | 权限提示 |
| 摄像头权限拒绝 | 音频入会 | 摄像头按钮置灰 |
| 现有会话正在运行 | native 入会前暂停会话；离会后恢复 | 会议期间 LivaChat 对话暂停 |
| WebView 不支持 getUserMedia | 应用内 web 入会失败；MVP 用系统浏览器 | 提示“请在浏览器打开” |
| 自签 LiveKit 证书 | 客户端拒绝连接 | “会议服务证书无效” |
| LiveKit Webhook 丢失 | 状态可能延迟；periodic sweeper/懒判断兜底 | 一般无感 |
| 自托管 token 无法撤销 | 被移除者 TTL 内可能重连 | 文档限制；取消会议删房间 |
| Android 卸载重装 | device_id 变化，host 看不到旧会议 | 需账号体系解决（后续） |

---

## 11. 测试计划

### 11.1 服务端单元测试

- `tests/unit/meeting/test_store.py`：
  - create/get/list/upcoming；status 条件更新；invite hash/expiry/uses/revoke；webhook 幂等；并发 consume。
- `tests/unit/meeting/test_tokens.py`：
  - `_mint_livekit_jwt` 用 `api.TokenVerifier` 解码；断言 `video.room`、`room_join`、`can_publish`、`room_admin`、`identity`、`exp`；
  - host/guest 权限差异；TTL。
- `tests/unit/meeting/test_service.py`：
  - 时间窗口、状态、口令、max_uses；
  - 取消：撤销邀请 + `DeleteRoom` 调用（mock）；
  - LiveKit API 失败降级。
- `tests/unit/meeting/test_api.py`：
  - App 鉴权 401；host 403；错误信封；429。
- `tests/unit/meeting/test_webhook.py`：
  - 构造签名 body（`AccessToken.with_sha256(base64(sha256(body)))`）；
  - 验证 `room_started`/`room_finished`；重复事件幂等；坏签名 401。

### 11.2 Agent 契约测试

- `tests/unit/agent_relay/test_hermes_contract.py`：
  - `$meeting_hint` 开/关两态；占位符不残留；
  - `meeting_request` 解析/校验/确认文案；
  - `meeting_service` 未配置 → noop；
  - `message_id` 确定性。
- integration：mock Hermes 返回 `meeting_request` → 会议落库 + `ai_text` 推送 + 链接格式正确。

### 11.3 Web E2E

- Playwright（仓库已有 `playwright` MCP/环境）：
  - 启动服务端 + LiveKit Server（Docker）；
  - 打开 `/meeting/#t=...`，预览、输入名字、加入；
  - 两个 browser context 入会，断言参与者、音视频 track、离开；
  - 过期/取消/口令错误页面；
  - `--use-fake-device-for-media-stream` 提供虚拟摄像头/麦克风。
- 注意：测试环境需要可信证书或 `ignoreHTTPSErrors`；LiveKit WebRTC 仍要求可信证书（可用 mkcert + 浏览器信任）。

### 11.4 移动端 E2E

- iOS 模拟器/真机 + Android 模拟器/真机；
- 组合：native + native、native + web、web + web；
- 验证：入会、麦克风、摄像头、切换摄像头、静音、离开、重连、host 取消；
- 验证现有对话在会议前后能恢复正常；
- 验证 webroot/普通链接不回归。

### 11.5 部署冒烟

```bash
# LiveKit Server
livekit-server --config /etc/livekit/livekit.yaml
lk room list

# LivaChat 会议 API
curl -k "https://<server-host>:8080/api/meetings/upcoming?token=<wire>&device_id=<uuid>"
```

- `GET /meeting/` 200，页面加载；
- 创建会议 → 入会 token 成功；
- webhook 从 LiveKit 到 LivaChat 成功（查日志/metric）；
- 取消会议 → 房间删除、旧链接 410。

---

## 12. 分阶段实施与工作量

| 阶段 | 内容 | 预估（1 人） | 验收 |
|------|------|--------------|------|
| S0 | LiveKit Server 部署 + 可信 TLS + UDP/TURN 打通；Python 脚本手工签发 JWT 并连入测试页 | 1-2d | 浏览器/命令行能入会 |
| S1 | MeetingStore/Service/API + 邀请 token + webhook + 单元测试 | 3-5d | API 测试全绿；手工创建/入会 |
| S2 | Agent `meeting_request` + prompt + 确认文案 + 注入 | 1-2d | mock Hermes 端到端；真机 Agent 验收 |
| S3 | Web 入会页（React + LiveKit 组件） + 部署挂载 + Playwright | 3-5d | 浏览器双人入会；移动 Safari 可用 |
| S4 | iOS native 会议（LiveKit SPM + 邀请/会议 UI + 音频互斥） | 4-6d | 真机 native 入会；与现有会话互斥/恢复 |
| S5 | Android native 会议（LiveKit Maven + 邀请/会议 UI + 音频互斥） | 4-6d | 同上 |
| S6 | 联调/安全/文档/部署收尾 | 2-4d | 全链路 E2E；安全清单；部署文档 |

**MVP 建议顺序**：S0 → S1 → S2 → S3（web 先可用）→ S4/S5 并行。
**总工期**：约 3-4 周（单人）；双端并行可压缩到 2.5-3 周。

**工作量风险**：

- LiveKit 可信 TLS/UDP/TURN 的网络打通可能比预期耗时（尤其企业网络/云安全组）。
- iOS LiveKit SPM 要求 Xcode 16.3+，现有构建机可能需升级。
- Android LiveKit AAR 体积/ProGuard/ABI 可能需要调整打包。
- 两端音频会话互斥的回归测试需要真机，模拟器不足以验证音频路由。

**实施时的文档同步清单**：

| 文档 | 同步内容 |
|------|----------|
| `docs/server_design.md` | 新增 `meeting` 模块/配置/API；说明与 WS 会话链路的关系 |
| `docs/agent_relay_prompts.md` / `docs/prompts_improvements.md` | `meeting_request` 契约、`$meeting_hint`、dialog awareness 能力清单 |
| `docs/ios_app_arch.md` / `docs/android_app_arch.md` | 新增 Meeting Feature/路由/音频互斥/链接分类 |
| `docs/install_design.md` | LiveKit Server 部署前置、TLS/UDP/TURN、`/meeting/` 构建产物 |
| `docs/ws-protocol-design.md` | **不修改**（本期零 wire 变更）；如需 `meeting.invite` 再另立设计 |
| `docs/server_auth.md` | **不修改**（复用现有 token/device_id 鉴权）；如未来改 header 鉴权再评估 |

---

## 13. 风险与开放问题

| # | 风险/问题 | 影响 | 建议/决策 |
|---|-----------|------|-----------|
| Q1 | 无账号体系，host 只能按 `device_id`；卸载重装/换机后失去 host 身份。且 dev-token 模式下所有设备共享同一 token，`device_id` 可伪造 | 无法管理旧会议；host 身份只在受控/个人部署下可信 | 接受；正式多用户前切 JWT/账号体系；邀请链接仍可入会 |
| Q2 | OpenClaw 是否同步支持 `meeting_request` | 只用 OpenClaw 的部署无法预约 | 本期先 Hermes；OpenClaw 作为后续对齐（同一 MeetingService） |
| Q3 | 应用内 WebView 入会 | 现有 WebView 不支持媒体采集；改造有安全面 | MVP 用系统浏览器；Phase 2 再评估 |
| Q4 | 无远程推送 | 离线邀请只能靠启动拉取 | 接受；后续加 APNs/FCM + device token 注册 |
| Q5 | 自托管 LiveKit 无 token revocation | 被移除者 TTL 内可重连 | 短 TTL + 取消删房间 + 轮换邀请；敏感会议加口令 |
| Q6 | LiveKit 需要可信 TLS/UDP/TURN | 现有 dev 自签/单端口部署不满足 | 独立子域名 + 公网 CA；开发用 mkcert 并信任 CA |
| Q7 | 音频会话冲突 | 同时开 LivaChat 会话和会议会抢麦克风 | 入会前暂停会话；离会后恢复；真机回归 |
| Q8 | Android 后台会议需要 FGS | 后台音频可能被系统限制 | MVP 前台；Phase 2 加 `MeetingForegroundService` |
| Q9 | iOS LiveKit 2.19 需要 Xcode 16.3+ | CI/构建机可能不满足 | 实施前升级/确认 |
| Q10 | 邀请 token 存在聊天历史/分享链接中 | 泄露即可入会 | 短 TTL、口令、取消/轮换；敏感会议不分享 |
| Q11 | 会议录制/Egress | 需要独立服务和存储 | 非目标；后续独立立项 |
| Q12 | AI 参会者/会议纪要 | 需要 LiveKit Agents 或桥接服务 | 非目标；后续独立设计 |
| Q13 | 日历集成/提醒 | 需 ICS/本地通知/推送 | 可先做 `.ics` + 本地通知；外部日历后续 |
| Q14 | 多实例部署 | 现有 server 单副本；meeting 依赖单实例 SQLite/内存限流 | 维持单实例；扩容需先解决 DB/事件总线 |
| Q15 | LiveKit 版本升级 | API/行为可能变化 | 固定版本；升级前跑 E2E；关注 changelog |
| Q16 | 同一 chatlog 重复 nudge 可能重复创建会议 | 用户看到重复会议 | `create_from_agent` 按 `(device, title, start_at)` 做 5 分钟去重（§6.3）；prompt 要求已处理过则 `noop` |
| Q17 | Host 在 Web 页没有主持能力 | 无法网页取消/踢人 | Web 默认 guest；主持用 App；需要时另发 host invite（后续） |
| Q18 | 一次 nudge 只能返回一个 action | “约会议 + 查资料”这类复合请求需要多轮 | 当前契约不支持复合 action；Agent 先处理一个，下一轮 nudge 再处理另一个（或由用户分开说） |

---

## 14. 附录

### 14.1 Codebase 锚点

| 主题 | 文件 |
|------|------|
| 后端入口/装配 | `services/app/application.py`、`services/app/main.py` |
| WS 端点/鉴权 | `services/gateway/endpoint.py`、`services/gateway/auth.py` |
| 会话/SQLite | `services/shared/persistence/chat_store.py`、`docs/server_design.md` §7 |
| BFF 鉴权 | `services/bff/common/deps.py` |
| Agent 事件/推送 | `services/agent_relay/models.py`、`publisher.py`、`session_manager.py`、`delivery.py` |
| Hermes 合约/prompt | `services/agent_relay/adapters/hermes.py`、`services/agent_relay/prompts/hermes_monitor.md`、`job_task.md` |
| Agent 认知声明 | `services/session/agent_job_ingest.py` |
| Webroot 设计 | `docs/webroot_feature.md`、`docs/ios_webroot_support.md` |
| 认证设计 | `docs/server_auth.md` |
| iOS App 架构 | `docs/ios_app_arch.md`、`docs/ios_dev_rules.md` |
| Android App 架构 | `docs/android_app_arch.md` |
| iOS 链接/WebView | `apps/ios/.../LivachatURLConverter.swift`、`Core/Settings/ServerAddress.swift`、`Core/PlatformAdapters/WebLinkRouter.swift`、`Features/WebView/WebPageView.swift` |
| Android 链接/WebView | `apps/android/.../ui/navigation/ServerLinks.kt`、`ui/navigation/NavGraph.kt`、`ui/screens/webview/WebViewScreen.kt` |
| 音频 | `apps/ios/.../Core/Audio/AudioSessionController.swift`、`Core/Conversation/ConversationRuntimeFactory.swift`；`apps/android/.../media/AudioCaptureController.kt`、`AudioPlaybackController.kt`、`AudioRouteManager.kt` |
| 部署 | `configs/dev/server.yaml`、`infra/docker/docker-compose.dev.yml`、`infra/kubernetes/*` |
| 配置模型 | `services/shared/config/models.py`、`shared/config/validation.py` |

### 14.2 LiveKit 参考

- LiveKit 自托管本地运行：<https://docs.livekit.io/transport/self-hosting/local/>
- LiveKit 生产部署：<https://docs.livekit.io/home/self-hosting/deployment/>
- Access tokens & grants：<https://docs.livekit.io/frontends/reference/tokens-grants/>
- Token 生成/刷新：<https://docs.livekit.io/home/server/generating-tokens/>、<https://docs.livekit.io/frontends/build/authentication/>
- Room 管理：<https://docs.livekit.io/home/server/managing-rooms/>
- Webhooks：<https://docs.livekit.io/home/server/webhooks/>
- 连接/重连：<https://docs.livekit.io/intro/basics/connect/>
- Swift quickstart：<https://docs.livekit.io/transport/sdk-platforms/swift/>
- Android quickstart：<https://docs.livekit.io/transport/sdk-platforms/android/>
- React quickstart：<https://docs.livekit.io/home/quickstarts/react/>
- LiveKit Agents：<https://github.com/livekit/agents>
- Python server SDK：<https://github.com/livekit/python-sdks>
- Server 配置样例：<https://github.com/livekit/livekit/blob/master/config-sample.yaml>

### 14.3 版本固定建议

```text
livekit-server          v1.13.11
client-sdk-swift        2.19.0
client-sdk-android      2.30.0
client-sdk-js           2.23.0
@livekit/components-react 2.9.25
livekit-api (Python)    1.2.2
```

> 以上为 2026-11-09 查询结果；实施时重新确认最新稳定版，并同步更新本文与锁文件。

---

*本文为设计草案，未实施。实施前需完成：LiveKit 网络/TLS 可行性验证、Xcode 16.3+ 构建验证、真机音频互斥验证、以及 Q1/Q2/Q3 的产品决策。*
