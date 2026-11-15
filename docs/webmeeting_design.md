# Web Meeting App 调研与设计（React + Vite + LiveKit）

> **状态**：设计已确认（2026-11-11），二次评审后修订；P0/P1 已实施（`apps/web/meeting`），P2 待定
> **已确认范围**：`apps/web/meeting` 是唯一 Web 实现（demo/验证阶段允许浏览器内置 key pair；未来切服务端签发）；demo server 已确认（livekit-server `v1.13.11`；host/port 只写本地 `public/config.json`），key 是专用 demo key；WSS 已用本地 meet.git 在 Chrome/Safari 验证可正常入会；仅内网测试；Krisp 降噪本期不做；其余按本文推荐默认（默认跳过 PreJoin、不做 E2EE/录制、英文 UI、base 固定 `/meeting/`（demo/生产一致）、运行期 `public/config.json`）。
> **日期**：2026-11-11
> **需求来源**：用户口述 6 条（见 §1.1）
> **调研对象**：`~/github/meet.git`，`main @ a1ae54a`（2026-11-11 直读源码与 `.env.local` 变量名；后者 key/secret 真实值不落入本文）
> **代码基线**：`livachat_meeting`，`dev/meeting @ f0f3182`
> **Scope**：新增 `apps/web/meeting/`（React + Vite SPA）；本文负责调研与设计，实施/测试进度见 [`docs/webmeeting_impl_plan.md`](webmeeting_impl_plan.md)。不改后端、iOS、Android、WS 协议。
> **方法**：先直读 `~/github/meet.git` 与当前仓库代码，再给出设计。文中标 **事实** 的内容均可在对应文件核对；标 **设计/建议** 的内容为尚未实施的决策，不应当作现有能力。

---

## 目录

0. 结论摘要
1. 需求拆解与术语
2. `meet.git` 调研（事实）
3. 新 app 设计
4. 附录（文件映射、代码骨架、版本来源、参考）

---

## 0. 结论摘要

### 0.1 目标架构

```
┌───────────────────────────────────────────────────────────────┐
│ apps/web/meeting（静态 SPA，base=/meeting/）                   │
│                                                               │
│  Home（会议号 + 用户名）                                       │
│    │                                                          │
│    ▼                                                          │
│  PreJoin（复用 @livekit/components-react 的 PreJoin）          │
│    │  onSubmit                                                │
│    ▼                                                          │
│  CredentialProvider ──► 本期：浏览器内用 jose 签发 LiveKit JWT │
│    │                    （API secret 来自 config.json）       │
│    ▼                                                          │
│  Room（livekit-client）                                       │
│    └─ RoomContext.Provider                                    │
│         ├─ VideoConference（网格/控制栏/聊天/屏幕共享/参与者） │
│         ├─ SettingsMenu（摄像头/麦克风/扬声器）                │
│         ├─ KeyboardShortcuts / DebugMode / RecordingIndicator  │
│         └─ useLowCPUOptimizer                                 │
└───────────────────────────┬───────────────────────────────────┘
                            │ WSS 信令 + WebRTC 媒体
                            ▼
                 ┌─────────────────────────┐
                 │ LiveKit Server（自托管） │
                 │  Room / SFU / TURN      │
                 └─────────────────────────┘
```

### 0.2 关键决策

| # | 决策 | 理由 / 来源 |
|---|------|-------------|
| D1 | 新 app 用 **React 18.3.1 + Vite 8 + TypeScript**，不引入 Next.js | 需求明确不要 Next；`meet.git` 的会中代码与 Next 解耦，可整体迁移；Vite 脚手架对齐 `apps/web/mylife` |
| D2 | **静态 SPA**，本期无后端；demo/验证阶段 LiveKit JWT 在浏览器用 `jose` 生成，**允许内置 key pair** | 需求方已确认：当前只做 demo/验证，允许客户端内置 key pair；`meet.git` 的 API route 需要 Next server，改成纯前端后无需部署 Node。未来切服务端签发（§3.13.4），本阶段不实现 |
| D3 | 首页只负责 **meeting number（= LiveKit room name）+ 用户名** | 需求原文；不保留 meet 的 Demo/Custom 双 tab 与「server URL + token」表单 |
| D4 | **默认跳过 PreJoin**，Home 直接进入 Meeting；需要会前设备预览/选择时设 `showPreJoin=true` | 2026-11-13 需求方改为默认关闭；`PreJoin` 组件保留为可选步骤，用户名仍由首页带入 |
| D5 | 会中 UI/UX **基本复刻 meet**：`RoomContext + VideoConference`，外加 SettingsMenu、KeyboardShortcuts、DebugMode、RecordingIndicator、LowCPUOptimizer | 需求原文；这些代码与 Next 无关，迁移成本低 |
| D6 | 运行期读取 **`public/config.json`**（gitignore），提交 app 根目录的 `config.example.json` | 需求允许 JSON 配置；运行期读取可换环境/轮换而不重新构建。硬编码作为备选 |
| D7 | identity = **opaque 随机 ID**（`u_<random>`）；`name` claim = 用户显示名 | 只要求唯一性；显示名只放 `name` claim，避免姓名进服务端日志；不再复刻 meet 的 `name__postfix`，也不需要 sessionStorage |
| D8 | Token TTL 默认 **10 分钟**，范围 **120..3600s**，`nbf` 回拨 30s | meet 用 5m；LiveKit 连接期间会自动刷新 token，短 TTL 不影响长会议；TTL 只能限制泄露 JWT 的重放，不能限制泄露 secret 后重新签发 |
| D9 | MVP **不做** E2EE、录制启停、Krisp 降噪、Datadog、i18n、路由/分享链接 | 无后端/超出首页需求；保留 RecordingIndicator 与 SettingsMenu 的媒体部分，后续可增量加回 |
| D10 | 本 app 是 **demo/验证定位**：API secret 在前端可见 | 需求方已确认本阶段允许内置 key pair，且 `meet.git/.env.local` 是专用 demo key；必须不公网部署、UI/文档显著标注。未来公网前切服务端签发 |
| D11 | `apps/web/meeting` 是**唯一 Web 实现**，同一套代码支撑 demo 与未来生产页 | 避免与 `personal_meeting_design.md` §7 出现第二套实现；demo 与生产都使用 base `/meeting/`；差异只在 `CredentialProvider`、入口态与 `tokenMode` |
| D12 | 默认视频编码 **vp8 + simulcast**；`vp9/av1` 只在显式配置时启用（走 SVC，无 simulcast） | `livekit-client` 明确 SVC codec 下 `videoSimulcastLayers` 无效；demo 要覆盖 Chrome/Safari/Firefox，vp8 兼容性最好 |

### 0.3 与 `meet.git` 的功能差异

| 能力 | `meet.git` | 新 app（MVP） |
|------|-----------|---------------|
| 首页 | Demo / Custom 双 tab | 单表单：会议号 + 用户名 |
| 房间来源 | 随机生成或 URL path `/rooms/<name>` | 用户输入 meeting number |
| Token | `/api/connection-details` 服务端签发 | demo/验证阶段浏览器内 `jose` 签发（允许内置 key）；未来 `tokenMode=server` |
| 会前 | `PreJoin` | `PreJoin`（用户名预填） |
| 会中 | `VideoConference` + 自定义设置/调试/录制指示 | 同左，去掉 Datadog 与录制启停 |
| 摄像头背景 | `@livekit/track-processors` 模糊/图片 | 保留，改用新版 `BackgroundProcessor` API |
| 降噪 | Krisp（设置菜单开启时） | 本期不做；仅保留麦克风开关与设备选择 |
| E2EE | URL hash 口令 + worker | 不做（后续可加） |
| 录制 | Egress + S3 API route | 不做；仅显示「正在录制」指示 |
| 区域固定 | `getLiveKitURL` 解析 LiveKit Cloud region | 不做；config 直接给最终 URL |
| 调试面板 | Shift+D + 可选 Datadog | 保留面板，去掉 Datadog |

### 0.4 本期明确不做

- 服务端 token 端点、邀请链接、会议注册表、鉴权、等候室、会议口令。
- E2EE、录制启停/Egress/S3、Krisp 降噪、AI 参会者、会议纪要。
- i18n（先英文 UI）、PWA、分享链接/深链、多 LiveKit region。
- 移动端原生集成、与 LivaChat 现有 WS 会话的联动。

---

## 1. 需求拆解与术语

### 1.1 需求原文（语义拆解）

| # | 需求 | 本文对应 |
|---|------|----------|
| N1 | 调研 `~/github/meet.git`（Next.js），`.env.local` 有可用 key pair 与 LiveKit server | §2、§3.4、§3.5 |
| N2 | 新 Web meeting app 不用 Next.js，基本 React + Vite，类似 `apps/web/mylife` | §3.2、§3.3 |
| N3 | 首页：输入 meeting number（即 LiveKit room name）与用户名；key pair/server 放 JSON 或 hardcoding | §3.4、§3.6 |
| N4 | 入会后的功能与 UI/UX 基本 follow `meet.git` | §2.6、§3.8 |
| N5 | 代码放 `apps/web/meeting` | §3.3 |
| N6 | 以 codebase 为基础，不猜测；分析设计落本文 | 全文 |

> **Key 归属（已确认）**：`meet.git/.env.local` 中的 key 是**专用 demo key**，本 app 直接复用，不需要新生成；不要用于生产。由于 LiveKit key 没有 room scope，secret 暴露的资产仍是该 demo LiveKit server（不只某个房间；host/port 只写本地 `public/config.json`），轮换/泄露处理按 §3.13.3。

### 1.2 术语

| 术语 | 含义 |
|------|------|
| Meeting number | 本设计中与 **LiveKit room name** 1:1 等同；输入什么字符串，就加入哪个房间 |
| API key / secret | LiveKit 服务端鉴权用的 key pair；key 不敏感，secret 敏感 |
| LiveKit JWT | 由 key/secret 以 HS256 签发的短期 access token；包含 `roomJoin`、`room`、`canPublish` 等 grants |
| identity | LiveKit 参与者唯一 ID（JWT `sub`），会进入服务端日志；本设计用 opaque `u_<random>`，不含显示名 |
| display name | 其他参与者看到的名称（JWT 顶层 `name` claim）；可为任意 Unicode |
| PreJoin | `@livekit/components-react` 的会前组件：摄像头预览、麦克风/摄像头开关、设备选择、用户名输入 |
| VideoConference | `@livekit/components-react` 的会中预置组件：网格/焦点布局、控制栏、聊天、屏幕共享、参与者列表 |

### 1.3 关键约束（来自当前 codebase）

- `apps/web/` 当前只有 `mylife`（Vite + React 19 + Tailwind 4），且 `Makefile` 标注 **Reference only: no longer developed or maintained**。新 app 复用其 Vite/TS 脚手架模式，不复用其运行时/托管链路。
- 后端 `services/app/application.py` 只挂载 `/webroot`；`/mylife` 托管已摘除（`docs/server_auth.md`、`docs/webroot_feature.md` §2.1）。本 app 不依赖后端。
- 仓库根 `.gitignore` 已忽略 `node_modules/`、`dist/`、`*.tsbuildinfo`；**未**忽略 `apps/web/meeting/public/config.json`，需新增。
- `docs/personal_meeting_design.md` §7 已有一版「`/meeting/` 入会页 + 邀请 token + 服务端签发 JWT」设计。该节现在只定义**生产入会契约与页面行为**；**Web 实现以本文为准**（`apps/web/meeting` 是唯一实现），不再维护第二套目录/技术栈，两者关系见 §3.14。

---

## 2. `meet.git` 调研（事实）

### 2.1 技术栈与版本

来源：`~/github/meet.git/package.json`（pnpm 10.18.2，Node `>=18`）。

| 依赖 | 版本 | 用途 |
|------|------|------|
| `next` | 15.5.21 | App Router / API routes / SSR |
| `react` / `react-dom` | 18.3.1 | UI |
| `typescript` | 5.9.3 | 类型 |
| `livekit-client` | 2.23.0 | Room / Track / WebRTC |
| `@livekit/components-react` | 2.9.24 | `PreJoin` / `VideoConference` / hooks |
| `@livekit/components-styles` | 1.2.0 | LiveKit 组件 CSS（含 `prefabs`） |
| `@livekit/krisp-noise-filter` | 0.4.5 | 浏览器端麦克风降噪 |
| `@livekit/track-processors` | `^0.8.0`（实测安装 0.8.1） | 背景模糊/虚拟背景（MediaPipe） |
| `livekit-server-sdk` | 2.19.1 | 服务端签发 JWT、Egress |
| `react-hot-toast` | `^2.5.2` | Toast（录制指示/错误） |
| `tinykeys` | `^3.0.0` | Shift+D 调试面板 |
| `@datadog/browser-logs` | `^5.23.3` | 可选日志上报 |

`@livekit/components-react@2.9.24` 的 peerDependencies：`react >=18`、`react-dom >=18`、`livekit-client ^2.20.1`、`tslib ^2.6.2`、`@livekit/krisp-noise-filter ^0.2.12 || ^0.3.0 || ^0.4.0`。其中 Krisp 是 optional peer；本 app 不装、不 import。React 19 也在 peer 允许范围内，但 meet 固定 18.3.1 且实测可用。

`~/github/meet.git/.env.local` 的未注释 `LIVEKIT_URL` 已确认是 demo server（自有域名，不是 `*.livekit.cloud`；实际 host/port 只写本地 `public/config.json`），对端 livekit-server 版本为 `v1.13.11`。已用本地 meet.git 连接该 server，Chrome 与 Safari 均能正常入会，WSS/TLS 与基本媒体路径已验证。因此本设计把 LiveKit 视为自托管，不做 `getLiveKitURL` 的 region 固定；**真实 secret 不写入本文**。

### 2.2 目录与职责

```
app/
├── api/
│   ├── connection-details/route.ts   # 服务端签发 LiveKit JWT
│   └── record/
│       ├── start/route.ts            # Egress + S3 开始录制
│       └── stop/route.ts             # 停止录制
├── custom/
│   ├── page.tsx                      # 自定义 server URL + token 入会
│   └── VideoConferenceClientImpl.tsx # 直接连接，无 PreJoin
├── rooms/[roomName]/
│   ├── page.tsx                      # Server Component：解析 env/query
│   └── PageClientImpl.tsx            # PreJoin -> 取 token -> VideoConference
├── layout.tsx                        # LiveKit styles / Toaster / metadata
└── page.tsx                          # 首页：Demo / Custom 双 tab
lib/
├── CameraSettings.tsx                # 摄像头预览/设备/背景效果
├── MicrophoneSettings.tsx            # 麦克风/设备/Krisp
├── SettingsMenu.tsx                  # 设置面板（媒体 + 可选录制）
├── KeyboardShortcuts.tsx             # Ctrl/Cmd+A/V
├── Debug.tsx                         # Shift+D 调试面板 + 可选 Datadog
├── RecordingIndicator.tsx            # 正在录制：toast + 红框
├── useSetupE2EE.ts                   # URL hash 口令 + e2ee worker
├── usePerfomanceOptimiser.ts         # 低 CPU 降质（文件名拼写沿用）
├── client-utils.ts                   # randomString / generateRoomId / encodePassphrase
├── getLiveKitURL.ts                  # LiveKit Cloud region 固定
└── types.ts                          # ConnectionDetails / isVideoCodec
styles/
├── globals.css
├── Home.module.css
├── SettingsMenu.module.css
└── Debug.module.css
```

### 2.3 页面、路由与入会流程

**事实**：

- `/`（`app/page.tsx`）
  - `Demo` tab：`generateRoomId()` = `${randomString(4)}-${randomString(4)}`；可选 E2EE，口令随机 64 字符；跳转 `/rooms/<roomId>`，E2EE 时附带 `#<encodePassphrase(passphrase)>`。
  - `Custom` tab：输入 LiveKit server URL 与现成 token；跳转 `/custom/?liveKitUrl=...&token=...`，E2EE 时同样带 hash。
  - tab 通过 `?tab=custom` 区分（`useSearchParams` + `router.push`）。
- `/rooms/[roomName]`（Server Component）
  - 从 `process.env.LIVEKIT_URL` 取 server URL；`region` 经 `getLiveKitURL` 固定到 LiveKit Cloud region；`codec` 默认 `vp9`（只接受 `videoCodecs` 中的值）；`hq` 默认 false；`singlePC` 默认 true（只有 `singlePC=false` 才关闭）。
  - 把 `roomName/serverUrl/region/hq/codec/singlePeerConnection` 传给 `PageClientImpl`。
- `/custom`
  - 校验 `liveKitUrl`、`token`、可选的 `codec`/`singlePC`；直接渲染 `VideoConferenceClientImpl`，无 PreJoin，连接后立即 `enableCameraAndMicrophone()`。

**入会时序（`/rooms/[roomName]`）**：

```
浏览器
  │ 1. 打开 /rooms/<roomName>
  │ 2. PageClientImpl 渲染 PreJoin（本地预览，不连服务器）
  │ 3. 用户填用户名、选设备、开关摄像头/麦克风，点 Join
  ▼
GET /api/connection-details?roomName=..&participantName=..
  │ 4. 服务端用 API key/secret 签发 5 分钟 JWT
  ▼
{ serverUrl, roomName, participantName, participantToken }
  │ 5. new Room(roomOptions)
  │ 6. room.connect(serverUrl, token, { autoSubscribe: true })
  │ 7. 按 PreJoin 选择 setCameraEnabled / setMicrophoneEnabled
  ▼
<RoomContext.Provider> + <VideoConference>
```

### 2.4 连接凭证签发（`app/api/connection-details/route.ts`）

**事实**：

- 输入：`roomName`、`participantName`、可选 `metadata`、可选 `region`。
- `LIVEKIT_URL` 必须存在；`region` 经 `getLiveKitURL` 固定（仅对 `*.livekit.cloud` 生效）。
- 参与者后缀来自 cookie `random-participant-postfix`；不存在则 `randomString(4)`；cookie 属性 `HttpOnly; Path=/; SameSite=Strict; Secure`，有效期 120 分钟。
- `identity = "${participantName}__${postfix}"`，`name = participantName`，`metadata` 可选。
- `AccessToken` TTL 设为 `5m`；grant 为：
  ```ts
  {
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canPublishData: true,
    canSubscribe: true,
  }
  ```
- 返回：
  ```ts
  type ConnectionDetails = {
    serverUrl: string
    roomName: string
    participantName: string
    participantToken: string
  }
  ```
- 错误：缺 `roomName` / `participantName` 返回 400；其他异常返回 500。

**从 `livekit-server-sdk@2.19.1` 源码核对到的 JWT 形状**（`dist/AccessToken.js`）：

- `new AccessToken(apiKey, apiSecret, { identity, name, metadata })`
- `addGrant({...})` 把 grants 放进 `video` 字段。
- `toJwt()` 使用 `jose.SignJWT`，`alg: HS256`，`iss = apiKey`，`sub = identity`，`nbf = now`，`exp = now + ttl`；`name/metadata` 为顶层 claim。
- 默认 TTL 为 `6h`；meet 显式覆盖为 `5m`。
- 源码里有一句明确的浏览器警告：
  > "You should not include your API secret in your web client bundle."

**设计含义**：meet 之所以把签发放在 Next API route，是因为 API secret 不能进浏览器。新 app 按需求在 **demo/验证阶段**把 secret 放前端，属于已知例外，必须在 UI/文档中显式标注（§3.13）。

### 2.5 会前 PreJoin（`PageClientImpl.tsx`）

**事实**：

- `preJoinDefaults = { username: '', videoEnabled: true, audioEnabled: true }`。
- `PreJoin` 的 `onSubmit` 收到 `LocalUserChoices`：
  ```ts
  type LocalUserChoices = {
    videoEnabled: boolean
    audioEnabled: boolean
    videoDeviceId: string
    audioDeviceId: string
    username: string
  }
  ```
- 提交后先 `setPreJoinChoices(values)`，再 `fetch('/api/connection-details?...')`，把响应存入 `connectionDetails`。
- 在 `connectionDetails` 与 `preJoinChoices` 都存在前，页面只显示 `PreJoin`。
- `PreJoin` 默认 `persistUserChoices` 为 true；`loadUserChoices(defaults)` 的合并顺序是 **localStorage 已存值覆盖 defaults**（components-core `persistent-storage/user-choices.ts`）。这意味着如果先在家里输入用户名、再进入 PreJoin，PreJoin 里可能显示上次保存的旧名字，而不是首页刚输入的名字。新设计必须显式处理（§3.7）。

### 2.6 会中 UI/UX（`VideoConferenceComponent`）

**事实**：

- 挂载时用 `new Room().prepareConnection(serverUrl)` 预热 DNS/TLS（独立 Room，不连接）。
- `Room` options（非 HQ、非 E2EE 的默认路径）：
  ```ts
  {
    videoCaptureDefaults: {
      deviceId: choices.videoDeviceId || undefined,
      resolution: VideoPresets.h720,
    },
    publishDefaults: {
      dtx: false,
      videoSimulcastLayers: [VideoPresets.h540, VideoPresets.h216],
      red: true,
      videoCodec: 'vp9',
    },
    audioCaptureDefaults: { deviceId: choices.audioDeviceId || undefined },
    adaptiveStream: true,
    dynacast: true,
    singlePeerConnection: true,
  }
  ```
  若 `hq=true`：采集 `h2160`，simulcast `[h1080, h720]`。若启用 E2EE 且 codec 是 `av1`/`vp9`：codec 置空，`red=false`。
- `connectOptions = { autoSubscribe: true }`。
- 连接后按 `userChoices.videoEnabled` / `audioEnabled` 调用 `localParticipant.setCameraEnabled(true)` / `setMicrophoneEnabled(true)`。
- 监听 `RoomEvent.Disconnected` -> `router.push('/')`；`RoomEvent.EncryptionError` / `MediaDevicesError` -> `alert` + console。
- `useLowCPUOptimizer(room)`：CPU 受限时 `track.prioritizePerformance()`、可选停止视频处理器、把远端 video quality 降到 LOW；低功耗模式开启后，新订阅的 track 也降到 LOW。
- 会中组合：
  ```tsx
  <div className="lk-room-container">
    <RoomContext.Provider value={room}>
      <KeyboardShortcuts />
      <VideoConference
        chatMessageFormatter={formatChatMessageLinks}
        SettingsComponent={SHOW_SETTINGS_MENU ? SettingsMenu : undefined}
      />
      <DebugMode />
      <RecordingIndicator />
    </RoomContext.Provider>
  </div>
  ```
- `VideoConference` 官方能力（类型声明原文）：聚焦单个参与者、分页网格、基础非持久聊天、屏幕共享等。
- `KeyboardShortcuts`：实际代码在 `Ctrl/Cmd+A` 切麦克风、`Ctrl/Cmd+V` 切摄像头；注释写的是 `Cmd/Ctrl-Shift-A/V`，但代码没有检查 `shiftKey`。这会与聊天输入框的「全选/粘贴」冲突（§3.8.7 会修正）。
- `DebugMode`：`Shift+D` 开关；显示 Room SID、本地/远端参与者、track、bitrate、permissions；`setLogLevel`；可选 Datadog；赋值 `window.__lk_room`。
- `RecordingIndicator`：`useIsRecording()` 为 true 时弹 3 秒 toast，并在整个房间容器内嵌红色 3px 边框。
- `SettingsMenu`：
  - tabs：`media`（camera/microphone/speaker）与可选 `recording`（仅 `NEXT_PUBLIC_LK_RECORD_ENDPOINT` 存在时）。
  - Camera：本地预览、`TrackToggle`、`MediaDeviceMenu kind="videoinput"`、背景效果（meet 原实现为 None / Blur / 两张 Unsplash 图片；当前实现见 §3.8.6）。
  - Microphone：`TrackToggle`、`MediaDeviceMenu kind="audioinput"`、Krisp 降噪开关；非低功耗设备默认开启。
  - Speaker：`MediaDeviceMenu kind="audiooutput"`。
  - Recording：检查 `room.isE2EEEnabled`，E2EE 时拒绝；`fetch(endpoint + '/start|stop?roomName=...')`。
  - Close：通过 `useMaybeLayoutContext()` 派发 `toggle_settings`。
- E2EE：从 `location.hash` 读口令；`ExternalE2EEKeyProvider` + `new Worker(new URL('livekit-client/e2ee-worker', import.meta.url))`；`room.setE2EEEnabled(true)`。

### 2.7 样式与主题

**事实**：

- `app/layout.tsx` 导入顺序：`styles/globals.css` → `@livekit/components-styles` → `@livekit/components-styles/prefabs`；`<body data-lk-theme="default">`；`<Toaster />`。
- `globals.css`：`color-scheme: dark`、`background-color: #111`、`html/body` 100% 高、`body` flex column、`overflow: hidden`；footer 深色。
- `Home.module.css`：居中 main、max-width 500px、tab 选择器、卡片式 tabContent（1px 半透明边框 + 0.5rem 圆角）。
- `SettingsMenu.module.css` / `Debug.module.css`：设置 tab 下划线、调试浮层。
- 没有 Tailwind；组件样式以 LiveKit CSS 变量 + CSS Modules 为主。

### 2.8 Next.js 耦合点与迁移影响

| `meet.git` 中的 Next 耦合 | 作用 | 新 app 的替代 |
|---------------------------|------|---------------|
| `app/api/connection-details/route.ts` | 服务端签发 JWT | 浏览器内 `jose` 签发（demo/验证）；未来 `tokenMode=server` |
| `app/api/record/*` | Egress 录制启停 | MVP 去掉；仅保留 RecordingIndicator |
| `app/rooms/[roomName]/page.tsx` | Server Component 读 env/query | 纯客户端 state machine，无路由 |
| `app/layout.tsx` metadata/viewport | title/OG/theme-color | `index.html` |
| `'use client'` | Next RSC 边界 | 删除 |
| `next/navigation` `useRouter` | 页面跳转 | 本地状态切换；离开回到 Home |
| `next/server` `NextRequest/NextResponse` | API route | 不适用 |
| `process.env.LIVEKIT_*` / `NEXT_PUBLIC_*` | 构建/运行配置 | `public/config.json` |
| `next.config.js` headers（COOP/COEP） | 跨源隔离/E2EE | 默认不加；启用 E2EE 时在静态服务器配置 |
| `source-map-loader` + `.mjs` 规则 | Next webpack 兼容 | Vite 原生 ESM，不需要 |
| `next build` / `next start` | 构建/服务 | `vite build` / 任意静态服务器 |
| `pnpm` | 包管理 | 对齐 `mylife` 用 npm（也可 pnpm，见 §3.2） |

### 2.9 可复用 vs 需替换

| 模块 | 结论 | 说明 |
|------|------|------|
| `VideoConference` + `RoomContext` | **直接复用** | 与 Next 无关 |
| `SettingsMenu` / `CameraSettings` / `MicrophoneSettings` | **基本复用** | 去掉 recording tab；背景处理器改新 API；去掉 Krisp（本期不做） |
| `KeyboardShortcuts` | **复用并修正** | 加 Shift、忽略输入框 |
| `DebugMode` | **复用并裁剪** | 去掉 Datadog |
| `RecordingIndicator` | **直接复用** | 无录制启停也能显示服务端录制状态 |
| `useLowCPUOptimizer` | **直接复用** | 与 Next 无关 |
| `client-utils` 的 `randomString/generateRoomId` | 可参考 | 新 app 用 `crypto.getRandomValues` |
| `getLiveKitURL` | **不复用** | config 直接给最终 URL |
| `ConnectionDetails` 类型 | **复用** | 保持未来服务端端点兼容 |
| `Home.module.css` | **改写** | 首页从双 tab 变单表单 |
| E2EE hook | **不迁移** | MVP 不做 |
| recording API routes | **不迁移** | MVP 不做 |

---

## 3. 新 app 设计

### 3.1 目标与非目标

**目标**：

1. 在 `apps/web/meeting/` 提供一个可独立构建/部署的 React + Vite SPA。
2. 首页输入 meeting number + 用户名，即可加入对应 LiveKit 房间。
3. 会中交互尽量与 `meet.git` 一致：PreJoin、网格/焦点、麦克风/摄像头/屏幕共享、聊天、参与者、设置、快捷键、调试、录制指示、低 CPU 优化。
4. key pair / LiveKit URL 通过 JSON 配置或硬编码提供（本期）；为未来「服务端签发」留出接口。

**非目标**：

- 生产级鉴权/邀请/会议管理；E2EE；录制启停；Krisp 降噪；i18n；PWA；分享链接；多 region。
- 修改 LivaChat 后端、App、WS 协议、LiveKit 服务端。

### 3.2 技术选型与版本

#### 3.2.1 版本矩阵（建议锁定）

| 包 | 版本 | 来源 / 理由 |
|----|------|-------------|
| `react` / `react-dom` | `18.3.1` | `meet.git` 实测组合；`components-react` peer 允许 >=18。React 19 虽在 peer 范围，但不在本设计的已验证组合内 |
| `livekit-client` | `2.23.0` | `meet.git` 固定版本 |
| `@livekit/components-react` | `2.9.24` | 同上 |
| `@livekit/components-styles` | `1.2.0` | 同上 |
| `@livekit/track-processors` | `0.8.1` | `meet.git` 的 `^0.8.0` 实际安装版本 |
| `jose` | `6.1.3` | `components-react` 的传递依赖已有 `jose ^6.0.12`；直接依赖它来签 JWT，避免引入 Node 版 server SDK |
| `react-hot-toast` | `2.5.2` | 复用 meet 的 toast |
| `tinykeys` | `3.0.0` | Debug 面板 Shift+D |
| `vite` | `8.2.1` | 对齐 `apps/web/mylife` |
| `@vitejs/plugin-react` | `6.0.5` | 对齐 mylife；peer 要求 `vite ^8` |
| `typescript` | `5.9.3` | 对齐 meet；mylife 的 TS 7 不是本 app 的已验证组合 |
| `vitest` | `4.1.11` | 对齐 mylife；纯 Node 单测 |
| `tslib` | `2.8.1` | `components-react` peer 显式声明；npm 会自动装，但显式列出更稳 |
| Node | `^20.19.0 \|\| ^22.12.0 \|\| >=24.0.0` | Vite 8 与 Vitest 4 engines 的交集（Vitest 4 不支持 Node 21/23） |

#### 3.2.2 为什么是 React 18 而不是 mylife 的 React 19

- `@livekit/components-react@2.9.24` 的 peer 是 `react >=18`，React 19 理论可用。
- 但 `meet.git` 是 LiveKit 官方维护的参考实现，固定 React 18.3.1 + livekit-client 2.23.0 + components-react 2.9.24，属于已验证组合。
- 本 app 的会中代码大量复用 meet 的写法（Room options、hooks、settings），沿用 18.3.1 风险最低。
- 升级 React 19 应作为独立变更，配 E2E 验证，不与本次迁移混在一起。

#### 3.2.3 为什么用 Vite 8（而不是 meet 的 Next/webpack）

- 需求明确不要 Next。
- mylife 已用 Vite 8.2.1 + `@vitejs/plugin-react@6.0.5`，脚手架/构建命令可直接对齐。
- Vite 原生支持 ESM、CSS Modules、`import.meta.env`、`new URL(..., import.meta.url)`；meet 的 `source-map-loader` 等 webpack 兼容配置不再需要。
- 注意 Vite 8 的 Node 要求（^20.19 或 >=22.12），当前环境 Node 24.21.0 满足。

#### 3.2.4 不用 Tailwind

- LiveKit 组件自带一套基于 CSS 变量的样式（`@livekit/components-styles`）。Tailwind 4 的 preflight reset 可能与 LiveKit 的 `lk-*` 样式冲突。
- 本 app 的页面很少：Home 一张卡片 + LiveKit 会中界面；用 CSS Modules/普通 CSS 更贴近 meet，也更容易做像素级对照。
- 如果后续要引入 Tailwind，建议关闭 preflight 并做回归；本期不引入。

#### 3.2.5 不用 react-router

- 只有 Home / PreJoin / Meeting / Error 四个阶段，用 React state machine 即可。
- 避免刷新/深链的 token 生命周期问题（刷新后内存 token 丢失，本来就要回首页）。
- 未来若要分享会议链接，再加 hash 路由（`#room=...`）即可。

#### 3.2.6 不用 React StrictMode

- `meet.git` 在 `next.config.js` 中显式 `reactStrictMode: false`。
- LiveKit 的 Room 创建/连接是副作用；StrictMode 在 dev 下会双调用 effect，容易产生重复连接/重复采集。
- 本 app 的 `main.tsx` 不包 `<StrictMode>`。Home/PreJoin 的纯 UI 不受影响。

#### 3.2.7 包管理器

- 对齐 mylife 使用 **npm**，提交 `package-lock.json`；`Makefile` 目标也用 npm。
- 如果团队更偏好 pnpm，可改用 pnpm，但不要混用锁文件。

### 3.3 目录结构

```
apps/web/meeting/
├── index.html
├── package.json
├── package-lock.json
├── README.md                     # 运行/配置/部署/dev 警告
├── tsconfig.json
├── vite.config.ts
├── vitest.config.ts
├── config.example.json            # 提交；占位符（不要放 public/，否则会被发布到 dist）
├── public/
│   ├── favicon.svg
│   └── config.json                # 不提交；本地/部署时填入真实 key pair
└── src/
    ├── main.tsx
    ├── App.tsx                    # 阶段状态机 + document.title
    ├── config.ts                  # 加载 + 校验 public/config.json
    ├── credentials/
    │   ├── types.ts               # CredentialProvider / ConnectionDetails
    │   ├── clientTokenProvider.ts # 本期：jose 浏览器签发
    │   ├── httpTokenProvider.ts   # 预留：未来服务端端点
    │   └── index.ts               # 按 tokenMode 选择 provider
    ├── identity.ts                # opaque identity（u_<random>）
    ├── token.ts                   # LiveKit JWT（jose）
    ├── messages.ts                # 英文文案 / 错误信息（后续 i18n）
    ├── assets/
    │   └── backgrounds/           # 虚拟背景图（Vite 打包，不用 public/）
    ├── components/
    │   ├── Home.tsx
    │   ├── PreJoinStage.tsx
    │   ├── MeetingRoom.tsx
    │   ├── ErrorView.tsx
    │   ├── ConfigError.tsx        # 配置加载/校验失败（含可操作提示）
    │   ├── Loading.tsx
    │   ├── SettingsMenu.tsx
    │   ├── CameraSettings.tsx
    │   ├── MicrophoneSettings.tsx
    │   ├── KeyboardShortcuts.tsx
    │   ├── DebugMode.tsx
    │   └── RecordingIndicator.tsx
    ├── hooks/
    │   └── useLowCPUOptimizer.ts  # 从 meet 迁移（文件名修正拼写）
    └── styles/
        ├── globals.css
        └── home.module.css
```

> 说明：`CameraSettings` 的虚拟背景图片建议放 `src/assets/backgrounds/` 并由 Vite 打包（不要从 `public/` 里 `import`，Vite 对 `public` 的 JS import 语义与 webpack 不同）。

### 3.4 配置设计

#### 3.4.1 为什么用运行期 `public/config.json`

| 方案 | 优点 | 缺点 |
|------|------|------|
| `src/config.ts` 硬编码 | 最简单，类型直接 | 换环境要重新构建；secret 进源码/包 |
| `src/config.json` 静态 import | 类型/构建期校验 | 会被打进 JS bundle；换环境要重新构建 |
| **`public/config.json` 运行期 fetch（推荐）** | 构建一次可多环境部署；轮换 key 只需替换文件；不把 secret 写进源码 | 仍然对所有访问者可见；需要处理 404/缓存 |

**重要**：运行期 JSON 并不比打包进 JS 更安全——它同样是公开资源。它解决的是「可替换/不重新构建」，不是「保密」。真正的保密只能靠服务端签发（§3.13）。

> 示例文件放 app 根目录 `config.example.json`，**不要**放 `public/`：`public/` 下的一切都会在 `vite build` 时复制到 `dist/` 并被发布。运行期只放 `public/config.json`（gitignore）。`vite build` 还会带一个构建闸门（§3.11.2）：检测到 client 模式且内置真实 secret 时默认失败，demo 构建需显式 `ALLOW_CLIENT_TOKEN=1`。

#### 3.4.2 Schema

```ts
export type MeetingVideoCodec = 'vp8' | 'h264' | 'vp9' | 'av1'

export type BackgroundImageConfig = {
  label: string                  // filter 卡片文案
  image: string                  // http(s)://、data:image/... 或相对路径
  thumbnail?: string             // 可选；缺省使用 image
}

export type MeetingConfigCommon = {
  appTitle: string               // UI 标题 / document.title，默认 "LivaChat Meeting"
  livekitUrl: string             // wss://<livekit-host>:<port>（最终 URL，不再做 region 解析）
  tokenTtlSeconds: number        // 默认 600；范围 120..3600
  videoCodec: MeetingVideoCodec  // 默认 'vp8'；vp9/av1 走 SVC，vp8/h264 走 simulcast
  showPreJoin: boolean           // 默认 false；true 则 Home -> PreJoin -> Meeting
  enableSettingsMenu: boolean    // 默认 true
  enableBackgroundEffects: boolean // 默认 true；关闭则不显示背景效果
  backgroundEffectsPreload: 'auto' | 'off' // 默认 'auto'；入会后延迟预取 MediaPipe 资源，不 attach processor
  backgroundImages: BackgroundImageConfig[] // 默认 []；追加到内置 Desk/Nature/Lake/Flower 之后
  mediapipeAssetBase?: string    // 空/缺省 = MediaPipe CDN；生产建议 `${BASE_URL}vendor/mediapipe/`
  showDebugPanel: boolean        // 默认 import.meta.env.DEV
}

// 本期使用：浏览器内签发 token，因此需要 key pair
export type ClientTokenConfig = MeetingConfigCommon & {
  tokenMode: 'client'
  apiKey: string                 // LIVEKIT_API_KEY
  apiSecret: string              // LIVEKIT_API_SECRET（敏感）
}

// 未来使用：服务端签发 token，客户端不携带 secret
export type ServerTokenConfig = MeetingConfigCommon & {
  tokenMode: 'server'
  tokenEndpoint: string          // POST 换取 ConnectionDetails
  apiKey?: never
  apiSecret?: never
}

export type MeetingConfig = ClientTokenConfig | ServerTokenConfig
```

> 重要：`tokenMode=server` 时客户端配置中**不应出现** `apiKey/apiSecret`。用 discriminated union 而不是「都可选」，避免实现时又把 secret 塞回前端。

#### 3.4.3 `config.example.json`（提交，放 app 根目录）

```json
{
  "appTitle": "LivaChat Meeting",
  "livekitUrl": "wss://<livekit-host>:<port>",
  "tokenMode": "client",
  "apiKey": "<LIVEKIT_API_KEY>",
  "apiSecret": "<LIVEKIT_API_SECRET>",
  "tokenTtlSeconds": 600,
  "videoCodec": "vp8",
  "showPreJoin": false,
  "enableSettingsMenu": true,
  "enableBackgroundEffects": true,
  "backgroundEffectsPreload": "auto",
  "backgroundImages": [],
  "mediapipeAssetBase": "",
  "showDebugPanel": false
}
```

> 上面是 `tokenMode=client` 示例。未来 `tokenMode=server` 时：删除 `apiKey` / `apiSecret`，改为提供 `tokenEndpoint`（见 §3.4.2 的 union 类型）。
>
> `showDebugPanel` 在示例里是 `false`：调试面板默认只在 `import.meta.env.DEV` 打开，生产/内网部署不应因为复制示例而开启。
>
> **demo 环境（已确认）**：`livekitUrl` 为 `wss://<livekit-host>:<port>`（实际值只写本地 `public/config.json`），livekit-server `v1.13.11`；`apiKey` / `apiSecret` 用 `meet.git/.env.local` 里的**专用 demo key**。真实 secret 只写本地 `public/config.json`，不写入本文、不提交仓库。

本地开发时：

```bash
cd apps/web/meeting
cp config.example.json public/config.json
# livekitUrl = wss://<livekit-host>:<port>（实际值只写本地 public/config.json）
# apiKey / apiSecret 用 meet.git/.env.local 的专用 demo key（已确认可复用）
# 填入 public/config.json（不要把真实值提交）
```

#### 3.4.4 加载与校验

```ts
export type LoadConfigDeps = {
  fetchImpl?: typeof fetch
  isHttps?: boolean
}

export async function loadConfig(deps: LoadConfigDeps = {}): Promise<MeetingConfig> {
  const fetchImpl = deps.fetchImpl ?? fetch
  const url = `${import.meta.env.BASE_URL}config.json`
  const res = await fetchImpl(url, {
    cache: 'no-store',
    credentials: 'omit',
    referrerPolicy: 'no-referrer',
  })
  if (!res.ok) {
    throw new Error(
      `Failed to load ${url} (HTTP ${res.status}). Copy config.example.json to public/config.json.`,
    )
  }
  const contentType = res.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    // 常见于静态服务器把 404 回退成 index.html，此时 res.json() 会抛难懂的 SyntaxError
    throw new Error(
      `${url} did not return JSON (Content-Type: ${contentType}). Confirm public/config.json exists.`,
    )
  }
  const isHttps =
    deps.isHttps ?? (typeof location !== 'undefined' && location.protocol === 'https:')
  return validateConfig(await res.json(), { isHttps })
}
```

校验规则：

- `livekitUrl`：非空；`new URL()` 合法；协议必须是 `ws:` 或 `wss:`；如果 `isHttps` 而 URL 是 `ws:`，直接报错（混合内容会被浏览器拦截）。`isHttps` 由调用方注入，单测不依赖全局 `location`。
- `tokenMode=client`：`apiKey` / `apiSecret` 必须为非空字符串，`trim()`；不要把 secret 写进错误信息。
- `tokenMode=server`：`tokenEndpoint` 必须为非空字符串；**必须拒绝** `apiKey` / `apiSecret`（运行时 JSON 不受 TS union 约束，误配会把 secret 带回前端）。
- `tokenTtlSeconds`：整数，`120..3600`；缺省 600。
- `videoCodec`：`vp8 | h264 | vp9 | av1`；缺省 `vp8`。
- `mediapipeAssetBase`：可选字符串；非空时必须以 `/` 或 `http(s)://` 开头，并建议以 `/` 结尾（代码按 `${base}wasm`、`${base}selfie_segmenter.tflite` 拼接）。空串/缺省表示使用 MediaPipe 默认 CDN。
- 布尔项：缺省值见 §3.4.2。
- 说明：MVP 只验收 client 模式；server 的 `HttpTokenProvider` 骨架已给出，但未纳入本次内网测试验收。
- 失败时显示可操作的错误页（文件路径、HTTP 状态、复制示例的命令），不要白屏。文案用英文，与英文 UI 一致（§3.9）。

#### 3.4.5 安全上下文检查

启动时检查：

```ts
if (!window.isSecureContext) {
  // 显示错误：需要 HTTPS 或 localhost
}
```

原因：`crypto.subtle`（jose 签 JWT）和 `navigator.mediaDevices.getUserMedia` 都只在安全上下文可用。`http://localhost` / `http://127.0.0.1` 是安全上下文；`http://192.168.x.x` 不是。

#### 3.4.6 `.gitignore`

在仓库根 `.gitignore` 增加：

```gitignore
apps/web/meeting/public/config.json
```

`config.example.json` 放在 app 根目录并保留在版本库；`public/config.json` 不提交。不要把 `~/github/meet.git/.env.local` 复制进仓库，也不要把带真实 secret 的 `dist/` 提交/上传到公共位置。

#### 3.4.7 未来机制（预留）

配置里的 `tokenMode` / `tokenEndpoint` 用来平滑切换到服务端签发：

```ts
export interface CredentialProvider {
  getConnectionDetails(req: {
    roomName: string
    participantName: string
  }): Promise<ConnectionDetails>
}
```

- `ClientTokenProvider`：本期实现，用 `jose` + config secret。
- `HttpTokenProvider`：未来实现，POST `{ roomName, participantName }` 到 `tokenEndpoint`，返回同样的 `ConnectionDetails`；此时客户端配置**不含** `apiKey/apiSecret`。
- UI 与 `MeetingRoom` 只依赖 `CredentialProvider`，切换时改 `tokenMode`/配置并选择 provider。

未来端点契约：优先与 `personal_meeting_design.md` §7.2 的生产契约对齐（`POST /api/meetings/join` → `{ livekit_url, token }`）；`HttpTokenProvider` 负责把它适配成 `ConnectionDetails`。如果选择与 meet 的 `/api/connection-details` 对齐，则使用下面的 camelCase 形状。**两者只能选一个**，并在实现时同步更新 `personal_meeting_design.md`。

生产契约（`personal_meeting_design.md` §7.2）：

```
POST <tokenEndpoint>
Content-Type: application/json
{ "invite_token": "...", "display_name": "Alice", "passcode": "..." }

200
{
  "livekit_url": "wss://<livekit-host>",
  "token": "<jwt>",
  "meeting": { "...": "..." },
  "role": "participant"
}
```

`HttpTokenProvider` 的适配规则：

- `livekit_url` → `serverUrl`；
- `token` → `participantToken`；
- `roomName` / `participantName` 用请求值补全（服务端应回显或校验）；
- 必须校验 `serverUrl` 是 `ws`/`wss`、`token` 非空，不能直接 `as ConnectionDetails`（§4.2.3）。

> 本阶段不实现 server 模式；这里只固定未来接口，避免未来再起一个 Web app。

### 3.5 Token 签发设计

#### 3.5.1 为什么用 `jose` 而不是 `livekit-server-sdk`

- `livekit-server-sdk` 是 Node 服务端 SDK；在浏览器里会主动打印「不要把 API secret 放进 web bundle」的警告，且 barrel import 容易把 Node-only 模块带进 bundle。
- `jose` 是浏览器可用的纯 WebCrypto JWT 库，且已经是 `@livekit/components-react` 的依赖。
- 我们只需要复刻 `AccessToken.toJwt()` 的 20 行逻辑，不值得为它引入 Node SDK + polyfill。

#### 3.5.2 参考实现（`src/token.ts`）

```ts
import { SignJWT } from 'jose'

export type TokenInput = {
  apiKey: string
  apiSecret: string
  roomName: string
  identity: string
  displayName: string
  ttlSeconds: number
}

export async function createParticipantToken(input: TokenInput): Promise<string> {
  const now = Math.floor(Date.now() / 1000)
  const key = new TextEncoder().encode(input.apiSecret)

  return new SignJWT({
    name: input.displayName,
    video: {
      room: input.roomName,
      roomJoin: true,
      canPublish: true,
      canSubscribe: true,
      canPublishData: true,
    },
  })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuer(input.apiKey)
    .setSubject(input.identity)
    .setNotBefore(now - 30)          // 容忍浏览器时钟小幅落后
    .setExpirationTime(now + input.ttlSeconds)
    .sign(key)
}
```

#### 3.5.3 Claims 对照（与 meet 的服务端 token 一致）

| Claim | 值 | 说明 |
|-------|----|------|
| `iss` | API key | LiveKit 用 `iss` 找到对应 secret 验签 |
| `sub` | identity | 参与者唯一 ID；进入服务端日志，不要用 PII（生产场景） |
| `nbf` | now - 30s | 容忍时钟偏差 |
| `exp` | now + ttl | 必须存在；LiveKit `TokenVerifier` 要求 `exp` |
| `name` | 显示名 | 其他参与者看到的名称 |
| `video.room` | roomName | 房间名 |
| `video.roomJoin` | true | 允许入会 |
| `video.canPublish` | true | 允许发布音视频/屏幕共享 |
| `video.canSubscribe` | true | 允许订阅他人 |
| `video.canPublishData` | true | 允许 data channel（聊天） |
| `video.roomAdmin` | 不设置 | 不授予管理权限 |

不设置 `metadata`、`attributes`、`kind`、`hidden`、`roomConfig`；房间参数走 LiveKit server 默认配置。

#### 3.5.4 Identity 策略

```ts
const ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'

function randomId(length = 16): string {
  const bytes = crypto.getRandomValues(new Uint8Array(length))
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join('')
}

/** Opaque identity: no display name, no PII, no storage. */
export function makeIdentity(): string {
  return `u_${randomId()}`
}
```

- `identity` 只用于唯一性，**不携带显示名**；显示名只放 `name` claim，支持中文/emoji。这样 identity 进入服务端日志时不含 PII，也符合 §3.13.2 的生产目标。
- 每次签发 token 生成新的 identity（页面内通常只签发一次；Retry 会生成新身份，旧连接已随组件卸载断开）；刷新/新 tab 都是新参与者，不会互相顶掉。
- 不再使用 `sessionStorage`/cookie 保存后缀：省掉“复制标签页拿到相同后缀”“同名同 tab 刷新”这类边界，也不需要为 `Storage` 写测试。
- `ClientTokenProvider` 在签发前对 `participantName` 做 `trim()`；空串回退 `Guest`；`name`、返回的 `participantName` 用同一个值，避免 PreJoin 编辑后出现前导/尾随空格或三处不一致。
- 如果未来需要“同一用户多端合并/踢旧连接”，应由服务端下发稳定 user id，而不是用显示名推导 identity。

#### 3.5.5 TTL 与时钟

- 默认 600s（10 分钟），范围 120..3600s；连接成功后 LiveKit server 会自动刷新 token，长会议不会因初始 TTL 掉线。
- 浏览器时钟偏差：`nbf` 回拨 30s；如果用户系统时间严重错误，连接会失败，错误页提示检查系统时间。
- TTL 只能限制**泄露 JWT** 的重放窗口；本阶段 secret 在客户端，拿到 secret 的人可以重新签发，TTL 对 secret 泄露没有缓解作用（§3.13.2）。
- token 只在内存（React state）中保存；**不写 localStorage/sessionStorage**，刷新后重新签发。

### 3.6 首页设计

#### 3.6.1 线框

```
┌──────────────────────────────────────────────┐
│                LivaChat Meeting              │
│            Join a meeting by number          │
│                                              │
│  Meeting number                              │
│  ┌──────────────────────────────┐  ┌──────┐  │
│  │ 123-456-789                  │  │ 🎲   │  │
│  └──────────────────────────────┘  └──────┘  │
│                                              │
│  Your name                                   │
│  ┌──────────────────────────────────────────┐│
│  │ Alice                                    ││
│  └──────────────────────────────────────────┘│
│                                              │
│  ┌──────────────────────────────────────────┐│
│  │              Join meeting                ││
│  └──────────────────────────────────────────┘│
│                                              │
│  ⚠ Dev mode: LiveKit credentials are         │
│    embedded in this client. Do not deploy    │
│    publicly.                                 │
└──────────────────────────────────────────────┘
```

#### 3.6.2 字段与校验

| 字段 | 规则 | 错误文案（英文 UI） |
|------|------|---------------------|
| Meeting number (room name) | trim + 转小写后校验；`^[a-z0-9][a-z0-9_-]{0,63}$`；大小写不敏感 | `Enter a meeting number.` / `Use letters, numbers, hyphens and underscores (max 64).` |
| Your name | trim；1–40 个 Unicode code point（`Array.from` 计数） | `Enter your name.` / `Name must be 1–40 characters.` |

- 会议号在提交前统一 `trim().toLowerCase()`；`Design-Review` 与 `design-review` 视为同一个房间。

- 表单用 `<form onSubmit>`，Enter 可提交。
- 中文输入法组合期间按 Enter 不应提交：在输入框的 `onKeyDown` 里检查 `event.nativeEvent.isComposing`（`KeyboardEvent` 才有该属性；`onSubmit` 的 `nativeEvent` 是 `SubmitEvent`，不能直接用它判断），或维护 composition state 并在 submit 时跳过。
- Join 按钮在两项都合法前 disabled；提交中显示 loading，防止重复点击。
- 错误放在表单内 `role="alert"` 区域，不用 `alert()`。

#### 3.6.3 Meeting number 的语义

- 1:1 映射到 LiveKit room name（提交前 `trim + lowercase`）；**没有**会议目录/预注册。任何合法字符串第一次有人加入时，LiveKit 自动创建房间。
- 大小写不敏感：`Design-Review` 与 `design-review` 归一化为同一个房间。
- 不校验房间是否存在；不存在就创建。
- 没有 host/主持人；第一个加入的人也没有特殊权限。
- 房间名是唯一「凭证」；建议用 🎲 按钮生成随机号，降低被猜中的概率。
- **空房提示**：入会后 30 秒内如果 `room.numParticipants <= 1`，显示一行 “No one else has joined yet — check the meeting number.”，避免输错号码后一直干等；加入第二个参与者后移除提示。
- 首页“Meeting number”只是本阶段的房间名入口；未来接会议目录时，它会变成服务端校验的 `meeting_id`，语义变化要提前在 UI 文案里说明。

#### 3.6.4 随机会议号按钮（可选但建议）

```ts
function randomRoomId(): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
  const bytes = crypto.getRandomValues(new Uint8Array(8))
  const s = Array.from(bytes, (b) => alphabet[b % alphabet.length]).join('')
  return `${s.slice(0, 4)}-${s.slice(4, 8)}`
}
```

点击后填入 meeting number，并把焦点移到用户名输入框。

#### 3.6.5 本地记忆

- 成功进入 meeting 后，用 **PreJoin 最终提交的 `values.username`** 写入 `localStorage`（`livachat-meeting:display-name`），下次打开预填；可在设置里清除（可选）。如果 `showPreJoin=false`，用首页用户名。
- Home 的 `defaultRoomName` / `defaultParticipantName`（来自 App 的 `home` stage）优先于 localStorage：从 PreJoin/Meeting 返回时，必须显示用户刚输入的值，而不是上次保存的旧名字。
- 会议号不持久化，避免误入上次房间；如需要，可作为 P2 加「最近会议」。

#### 3.6.6 Dev 提示

- 当 `tokenMode === 'client'` 时，页面底部显示一行醒目的 dev 警告（红/橙色小字 + ⚠）。
- `import.meta.env.PROD` 时也不隐藏；这是有意为之，防止误部署。
- 控制台启动时 `console.warn` 一次同样内容；不要打印 secret。

### 3.7 PreJoin 设计

- 当 `showPreJoin=true`（非默认）时，首页提交后进入 `PreJoinStage`：
  ```tsx
  <PreJoin
    defaults={{ username: participantName, videoEnabled: true, audioEnabled: true }}
    persistUserChoices={false}
    joinLabel="Join meeting"
    micLabel="Microphone"
    camLabel="Camera"
    userLabel="Name"
    onSubmit={handlePreJoinSubmit}
    onError={handlePreJoinError}
  />
  ```
- **必须显式 `persistUserChoices={false}`**：`@livekit/components-core` 的 `loadUserChoices` 会用 localStorage 已存值覆盖 `defaults`，否则首页刚输入的用户名可能被旧值替换。关闭后设备选择也不持久化；如需记忆设备，后续可自行存 `videoDeviceId/audioDeviceId` 并在 `defaults` 里传。
- PreJoin 卸载会释放摄像头/麦克风；MeetingRoom 随后用同一个 `deviceId` 重新采集。macOS/iOS Safari 上可能出现 `NotReadableError`（设备仍在释放），§3.8.3 会做一次短延迟重试；这项要列入 Safari/iPhone 手工验收。
- 左上角 `← Back` 返回首页并保留进入 PreJoin 时的会议号/用户名。实现上由 App 的 `home` stage 携带 `roomName/participantName`，Home 作为 `defaultRoomName/defaultParticipantName` 读取——Home 组件在进入 PreJoin 时已卸载，不能依赖它自己的 state。如果用户在 PreJoin 里改了名字，Back 后首页显示的是旧值；接受这个行为，或让 `onBack` 回传当前 `values`（P2）。
- `PreJoinStage` 维护 `joining` state：`provider.getConnectionDetails` 期间禁用 Join、显示 loading，避免重复签发/重复进入。
- `handlePreJoinSubmit(values)`：
  1. `setPreJoinChoices(values)`
  2. `provider.getConnectionDetails({ roomName, participantName: values.username })`
  3. 成功 -> 进入 meeting 阶段
  4. 失败 -> toast + 留在 PreJoin，允许重试
- `onError`（摄像头/麦克风权限失败）：toast 提示；用户仍可关掉设备后 Join（至少能看/听别人）。
- 默认 `config.showPreJoin === false`：跳过该阶段，用 `{ username, videoEnabled: true, audioEnabled: true, videoDeviceId: '', audioDeviceId: '' }` 直接生成 token 并连接。此时设备选择只能在会中 SettingsMenu 做。

### 3.8 会中设计

#### 3.8.1 阶段状态机

```
                 ┌──────────────────────────────┐
                 │            Home              │
                 │  roomName + participantName  │
                 └───────┬───────────────┬──────┘
      showPreJoin=true   │               │  showPreJoin=false (默认)
                         ▼               │
                 ┌──────────────┐        │
                 │   PreJoin    │        │
                 │  camera/mic  │        │
                 │   preview    │        │
                 └──────┬───────┘        │
                        │ submit -> mint token
                        ▼                ▼
                 ┌──────────────────────────────┐
                 │          Meeting             │
                 │  Room + VideoConference      │
                 └──────┬───────────────┬───────┘
                        │ leave         │ error
                        ▼               ▼
                      Home        Error screen -> Home/Retry
```

```ts
type JoinParams = {
  roomName: string
  participantName: string
  choices: LocalUserChoices
}

type Stage =
  // roomName/participantName 用于 Back/Leave 后回填 Home（Home 组件会卸载，不能依赖它自己的 state）
  | { kind: 'home'; roomName?: string; participantName?: string }
  | { kind: 'prejoin'; roomName: string; participantName: string }
  | { kind: 'meeting'; details: ConnectionDetails; choices: LocalUserChoices }
  | { kind: 'error'; message: string; retry: JoinParams }
```

- 默认 `showPreJoin=false` 时，Home 的 `onJoin` 直接调用 provider 生成 details，并把默认 choices 放进 meeting 阶段，跳过 PreJoin。
- 离开/断线 -> `setStage({ kind: 'home' })`，并 toast「You left the meeting」。
- 连接失败 -> `MeetingRoom` 调 `onError(message, retry)`，进入 `error` 阶段；ErrorView 的 Retry 用 `retry.roomName/participantName` **重新签发 token**，成功后带原 `retry.choices` 回到 meeting；Back 回 Home。这样 token 过期/server 临时不可达都能重试。
- 不引入路由；刷新页面 = 离开会议，回到 Home（token 不持久化）。

#### 3.8.2 `Room` options（默认 vp8 + simulcast）

```ts
const svcCodec = isSVCCodec(config.videoCodec)

const roomOptions: RoomOptions = {
  adaptiveStream: true,
  dynacast: true,
  videoCaptureDefaults: {
    deviceId: choices.videoDeviceId || undefined,
    resolution: VideoPresets.h720,
  },
  audioCaptureDefaults: {
    deviceId: choices.audioDeviceId || undefined,
  },
  publishDefaults: {
    dtx: false,
    red: true,
    videoCodec: config.videoCodec,
    // SVC codec（vp9/av1）下该字段无效；只有 vp8/h264 需要 simulcast
    videoSimulcastLayers: svcCodec ? undefined : [VideoPresets.h540, VideoPresets.h216],
  },
  singlePeerConnection: true,
}
```

- 默认 `videoCodec: 'vp8'`（config 可改）：vp8 + simulcast 在 Chrome/Safari/Firefox 上行为一致，适合 demo 阶段的多浏览器覆盖。这是对 meet 的 `vp9` 默认的**有意偏离**（D12）。
- `vp9`/`av1` 走 SVC，**`videoSimulcastLayers` 不生效**（`livekit-client@2.23.0` `options.d.ts` 明确写了）；如果配置成 SVC codec，就不要再展示/依赖 simulcast 参数。Safari/iOS 的 vp9 协商要实测，不能假设可用。
- 不实现 `hq`/`region` 查询参数；codec 只走 config。如未来需要 hq，再加 config。
- 不启用 E2EE，因此 `red` 保持 true；meet 在 E2EE 时会关掉 vp9/av1 与 red。
- `singlePeerConnection: true` 是 `RoomOptions` 的默认值（不可用时 LiveKit 自动回退双 PC）；显式写出只是为了和 meet 对齐，不是必需项。
- 屏幕共享由 `VideoConference` 控制栏触发，grant 的 `canPublish` 已覆盖；iOS/iPadOS 26 及以前 Safari 不支持 `getDisplayMedia`，需要按能力隐藏按钮；iOS/iPadOS 27+ 已支持，当前 `is-ios` CSS 仍会隐藏按钮，启用列入 P2（§3.8.11）。

#### 3.8.3 连接、设备启用与清理

```tsx
function MeetingRoom({ config, details, choices, onLeave, onError }: Props) {
  // 本组件每次入会重新挂载；choices/config 在该生命周期内不变
  const room = useMemo(() => new Room(roomOptionsFrom(config.videoCodec, choices)), [])

  // 回调用 ref 固定，避免父组件每次渲染产生新函数导致 effect 重跑/重连
  const onLeaveRef = useRef(onLeave)
  const onErrorRef = useRef(onError)
  useEffect(() => {
    onLeaveRef.current = onLeave
    onErrorRef.current = onError
  }, [onLeave, onError])

  useEffect(() => {
    let cancelled = false
    let hasConnected = false
    const handleConnected = () => {
      hasConnected = true
    }
    const handleDisconnected = (reason?: DisconnectReason) => {
      if (cancelled || !hasConnected) return
      if (reason === DisconnectReason.CLIENT_INITIATED) {
        onLeaveRef.current()
        return
      }
      if (
        reason === DisconnectReason.DUPLICATE_IDENTITY ||
        reason === DisconnectReason.PARTICIPANT_REMOVED ||
        reason === DisconnectReason.ROOM_DELETED
      ) {
        // 回 Home；由 App 统一 toast 原因（见 §3.8.11）
        onLeaveRef.current(disconnectMessage(reason))
        return
      }
      // 网络不可恢复：进错误页，允许重新签发 token 重试
      onErrorRef.current(disconnectMessage(reason), {
        roomName: details.roomName,
        participantName: details.participantName,
        choices,
      })
    }
    const handleDeviceError = (err: Error) => {
      console.error(err)
      toast.error(`Media device error: ${err.message}`)
    }

    room.on(RoomEvent.Connected, handleConnected)
    room.on(RoomEvent.Disconnected, handleDisconnected)
    room.on(RoomEvent.MediaDevicesError, handleDeviceError)

    room.connect(details.serverUrl, details.participantToken, { autoSubscribe: true })
      .then(async () => {
        if (cancelled) return
        // 设备失败不应导致整个会议退出：摄像头/麦克风分别 catch。
        // PreJoin 刚释放设备时 Safari 可能抛 NotReadableError，短暂等待后重试一次。
        const enable = async (fn: () => Promise<unknown>) => {
          try {
            await fn()
          } catch (err) {
            if (cancelled) return
            if ((err as { name?: string } | null)?.name === 'NotReadableError') {
              await new Promise((r) => setTimeout(r, 250))
              if (cancelled) return
              await fn().catch(handleDeviceError)
              return
            }
            handleDeviceError(err as Error)
          }
        }
        if (choices.videoEnabled) {
          await enable(() => room.localParticipant.setCameraEnabled(true))
        }
        if (choices.audioEnabled) {
          await enable(() => room.localParticipant.setMicrophoneEnabled(true))
        }
      })
      .catch((err: Error) => {
        if (cancelled) return
        onErrorRef.current(err.message, {
          roomName: details.roomName,
          participantName: details.participantName,
          choices,
        })
      })

    return () => {
      cancelled = true
      room.off(RoomEvent.Connected, handleConnected)
      room.off(RoomEvent.Disconnected, handleDisconnected)
      room.off(RoomEvent.MediaDevicesError, handleDeviceError)
      room.disconnect()
    }
  }, [room, details, choices])

  const lowPower = useLowCPUOptimizer(room)
  // ...
}

function disconnectMessage(reason?: DisconnectReason): string {
  switch (reason) {
    case DisconnectReason.DUPLICATE_IDENTITY:
      return 'You joined from another tab or device.'
    case DisconnectReason.PARTICIPANT_REMOVED:
      return 'You were removed from the meeting.'
    case DisconnectReason.ROOM_DELETED:
      return 'The meeting has ended.'
    default:
      return 'Connection lost. Check your network and retry.'
  }
}
```

**设计要点**：

- 与 meet 不同，cleanup 里显式 `room.disconnect()`，确保离开页面/组件卸载时释放摄像头/麦克风。meet 只移除监听，依赖 GC；显式断开更稳。
- 可在 config 加载后调用 `new Room().prepareConnection(config.livekitUrl)` 预热 DNS/TLS（meet 的做法）；失败不影响入会，catch 掉即可。
- 不包 `StrictMode`，避免 dev 下双连接。
- `room.connect` 与 `setCameraEnabled` / `setMicrophoneEnabled` 要分别 catch：连接失败才进入 `error` 阶段；单个设备失败只 toast，会议继续（可看/听别人）。`handleDeviceError` 是 `toast.error(...)` + `console.error`，不调用 `onLeave`/`onError`。
- 设备启用对 `NotReadableError` 做一次 250ms 延迟重试：PreJoin 卸载后设备可能还没完全释放，Safari 上尤其明显。重试再失败才 toast。
- 监听 `RoomEvent.MediaDevicesError`（运行中设备拔出/权限被撤销），与上面的初始设备失败共用 `handleDeviceError`；cleanup 里 `room.off(...)`。
- `onLeave` / `onError` 在父组件里应稳定（`useCallback`），或在本组件用 ref 持有；否则它们每次渲染都变，effect 依赖会让 Room 重连。本节骨架用 ref 演示。
- effect 内维护 `let cancelled = false`；cleanup 置 true。`room.connect().then(...)` 里先检查 `cancelled`，避免组件卸载后仍去 `setCameraEnabled` / 调回调。
- 用 `let hasConnected = false` + `RoomEvent.Connected` 区分「连接成功后离开」与「connect 失败时 LiveKit 也触发的 Disconnected」；后者交给 `onError`，避免错误页被 Home 覆盖。
- `RoomEvent.Disconnected` 的 `DisconnectReason` 决定去向：`CLIENT_INITIATED` → Home（默认文案）；`DUPLICATE_IDENTITY` / `PARTICIPANT_REMOVED` / `ROOM_DELETED` → Home + 对应原因；其他（网络不可恢复等）→ Error + Retry。不要把所有情况都显示成 “You left the meeting”。
- `onLeave(message?)` 由 App 统一 toast（`message ?? 'You left the meeting'`）；MeetingRoom 只负责传原因，避免双 toast。
- 连接失败 -> `onError(message, { roomName, participantName, choices })` -> App 进入 `error` 阶段；Retry 重新签发 token，Back 回 Home。`ROOM_DELETED` 不走 Retry（重试会重新创建空房），直接回 Home。
- 会中组合里加 `<ConnectionStateToast />`（`components-react` 已导出），覆盖 `Reconnecting`/`Reconnected` 的可见反馈，不必自己写横幅。
- App 渲染 `<MeetingRoom key={details.participantToken} ... />`，保证 token 变化时强制重建 Room。

#### 3.8.4 会中组件组合

```tsx
// SettingsMenu 需要 config（mediapipeAssetBase / enableBackgroundEffects）。
// 用 useMemo 固定包装组件身份，避免每次渲染 remount SettingsMenu。
const Settings = useMemo(
  () =>
    function SettingsForMeeting() {
      return <SettingsMenu config={config} />
    },
  [config],
)

return (
  <div className="lk-room-container">
    <RoomContext.Provider value={room}>
      <KeyboardShortcuts />
      <VideoConference
        chatMessageFormatter={formatChatMessageLinks}
        SettingsComponent={config.enableSettingsMenu ? Settings : undefined}
      />
      {config.showDebugPanel && <DebugMode />}
      <RecordingIndicator />
      <ConnectionStateToast />
    </RoomContext.Provider>
  </div>
)
```

- `formatChatMessageLinks` 让聊天里的 URL 可点击。
- `VideoConference` 自带：控制栏（麦克风/摄像头/屏幕共享/聊天/参与者/离开）、网格/焦点布局、参与者列表、非持久聊天。
- `RecordingIndicator` 依赖的 `.lk-room-container { position: relative; }` 已由 `@livekit/components-styles` 提供（`dist/general/index.css`），不需要再写；`RecordingIndicator` 自己用 `position: absolute` + `pointer-events: none`。
- `ConnectionStateToast` 负责 `Reconnecting`/`Reconnected` 的提示；不要自己再写一套横幅。
- `SettingsComponent` 只有在 `VideoConference` 的 LayoutContext 内才能拿到 `toggle_settings`，因此必须作为 prop 传入，不要放到 Provider 外。

#### 3.8.5 SettingsMenu（媒体部分）

保留 meet 的 `media` tab：

- **Camera**
  - 本地预览（`VideoTrack`，镜像 `scaleX(-1)`）。
  - `TrackToggle` 摄像头开关 + `MediaDeviceMenu kind="videoinput"`。
  - 背景效果：None / Blur / 虚拟背景图片（缩略图卡片；config 可追加本地或 remote 图片）。
- **Microphone**
  - `TrackToggle` 麦克风开关 + `MediaDeviceMenu kind="audioinput"`。
  - **Krisp 本期不做**：不引入 `@livekit/krisp-noise-filter`，不 import `@livekit/components-react/krisp`；麦克风设置只保留开关与设备选择。
- **Speaker**
  - `MediaDeviceMenu kind="audiooutput"`。
- **去掉 recording tab**：没有后端、没有 Egress/S3 配置，避免一个点了会报错的按钮。
- Close 按钮继续用 `layoutContext.widget.dispatch({ msg: 'toggle_settings' })`。
- **config 注入**：`VideoConference` 的 `SettingsComponent` 只接受 `React.ComponentType`，不能直接传 props。用包装组件 `const Settings = () => <SettingsMenu config={config} />`（或 ConfigContext）把 `mediapipeAssetBase` / `enableBackgroundEffects` 传下去；不要让 CameraSettings 读可变模块全局。

#### 3.8.6 摄像头背景效果（新版 API）

meet 用的是 `BackgroundBlur()` / `VirtualBackground()`（已标记 deprecated）。`@livekit/track-processors@0.8.1` 提供 `BackgroundProcessor(options, name?)`，返回 `BackgroundProcessorWrapper`，带 **`get mode()`** 和 **`switchTo()`**。正确姿势是**在 Blur/图片背景之间复用同一实例 + `switchTo`**；**None 时用 `stopProcessor()` 销毁处理器**（见下方 0.8.1 的 disabled 行为说明），不要每次切换都新建：

```ts
import {
  BackgroundProcessor,
  supportsBackgroundProcessors,
  type BackgroundProcessorWrapper,
  type SwitchBackgroundProcessorOptions,
} from '@livekit/track-processors'

const processorRef = useRef<BackgroundProcessorWrapper | null>(null)

function getProcessor() {
  processorRef.current ??= BackgroundProcessor({
    mode: 'disabled',
    ...(mediapipeAssetBase
      ? {
          assetPaths: {
            tasksVisionFileSet: `${mediapipeAssetBase}wasm`,
            modelAssetPath: `${mediapipeAssetBase}selfie_segmenter.tflite`,
          },
        }
      : {}),
  })
  return processorRef.current
}

function modeOptions(): SwitchBackgroundProcessorOptions {
  return backgroundType === 'blur'
    ? { mode: 'background-blur', blurRadius: 16 }
    : backgroundType === 'image' && imagePath
      ? { mode: 'virtual-background', imagePath }
      : { mode: 'disabled' }
}

const onProcessorError = (err: unknown) => {
  console.error(err)
  toast.error(`Background effect failed: ${(err as Error)?.message ?? 'unknown error'}`)
}

// 摄像头 track 变化时（关开摄像头会重建 track）：重新挂上同一个 processor，
// 并立即应用当前模式；None 时不要创建 processor。
useEffect(() => {
  if (backgroundType === 'none') return
  if (!supportsBackgroundProcessors()) return
  const track = cameraTrack?.track
  if (!isLocalTrack(track)) return
  try {
    const p = getProcessor()
    void p.switchTo(modeOptions()).catch(onProcessorError)
    void track.setProcessor(p).catch(onProcessorError)
  } catch (err) {
    onProcessorError(err)
  }
}, [cameraTrack, backgroundType])

// 模式变化时：Blur/图片之间只 switchTo；None 必须 stopProcessor()（见下）
useEffect(() => {
  if (backgroundType !== 'none') {
    const p = processorRef.current
    if (!p) return
    void p.switchTo(modeOptions()).catch(onProcessorError)
    return
  }
  const track = cameraTrack?.track
  processorRef.current = null
  if (isLocalTrack(track)) {
    void track.stopProcessor().catch(onProcessorError)
  }
}, [backgroundType, imagePath, cameraTrack])
```

**与 meet 的差异（重要）**：

- 旧写法用 `getProcessor()?.name` 反推模式；新 API 的默认 name 是 `'background-processor'`，但 wrapper 直接暴露 **`mode`**（`'disabled' | 'background-blur' | 'virtual-background' | 'legacy'`）。要读模式就读 `.mode`（把 `getProcessor()` 的结果 cast 成 `BackgroundProcessorWrapper`），不要用 name 反推，也不要为此维护一份容易漂移的 React state（state 只用于 UI 选中态，不参与 processor 生命周期）。
- `BackgroundProcessor` 在浏览器不支持时**直接 throw**；用 `supportsBackgroundProcessors()` + WebGL float/linear 能力门控 processor 和预加载；背景控件仍显示，点击时再提示 unsupported；调用处 try/catch 并 toast。
- **`switchTo({ mode: 'disabled' })` 在 0.8.1 中不会停止处理**（2026-11-14 实测）：库内 `skipProcessingFrame = this.isDisabled ?? this.options.backgroundDisabled ?? false`，而 `isDisabled` 初始化为 `false`，`??` 不会回退到 `backgroundDisabled`；再加上切到 disabled 时 `blurRadius` 仍保留，分割/模糊每帧继续跑。表现是：用户点 None 后画面看似关闭，但 GPU/CPU 开销持续，Mali-G76 上还会持续到 WebGL context loss。
  因此 **None 必须调用 `LocalVideoTrack.stopProcessor()`**（会 destroy processor 并恢复原始 track），下次选 filter 时再重建。`mode: 'disabled'` 仍可用于构造/过渡，但不能作为“关闭”手段。
- **Safari/WebKit 重绘**：`stopProcessor()` 在同一个 MediaStream 内把已停止的 processed track 换回原始 track，Safari 不会重绘，本地预览会黑屏（Chrome/Firefox 正常）。停止后要把命中原始 track 的 `<video>` 的 `srcObject` 置空再重新赋值并调用 `play()`，强制 Safari 重新渲染。
- **WebGL context loss 必须兜底**：track-processors 不恢复丢失的 context，会以 30fps 持续输出黑帧。监听 processor 内部 canvas 的 `webglcontextlost`，触发时 `stopProcessor()`、UI 回到 None，并提示 `Background effects stopped because the GPU context was lost.`；否则视频会永久黑屏且切 None 也无法恢复。
- **顺序问题**：只依赖 `backgroundType/imagePath` 的 effect 在 processor 尚未创建时会直接 return；如果用户先选 Blur 再开摄像头，camera effect 新建的 processor 会保持 `disabled`。因此 camera effect 在 `setProcessor` 后必须立即 `switchTo(当前 mode)`。
- 摄像头关闭再打开会重建 track，processor 会丢失；在 `cameraTrack` 变化的 effect 里重新 `setProcessor(getProcessor())` 并应用当前 mode。
- 只在 `cameraTrack?.track` 存在且 `isLocalTrack(cameraTrack.track)` 时调用 `setProcessor`；否则等下一次 `cameraTrack` 变化再应用，避免在未发布的 track 上抛错。
- MediaPipe 默认从 `cdn.jsdelivr.net` 拉 wasm、从 `storage.googleapis.com` 拉 `selfie_segmenter.tflite`。部分网络会重置 `cdn.jsdelivr.net`（实测华为浏览器），此时用 `config.mediapipeAssetBase` 自托管：
  - 空串/缺省：走默认 CDN（demo 方便，但需要放宽 CSP，见 §3.12.4）；
  - 非空：传给 `BackgroundProcessor` 的 `assetPaths = { tasksVisionFileSet: `${base}wasm`, modelAssetPath: `${base}selfie_segmenter.tflite` }`。
  - **生产/内网部署**按 `personal_meeting_design.md` §7.1 自托管：把 `@mediapipe/tasks-vision/wasm` 复制到 `public/vendor/mediapipe/wasm/`；模型 `selfie_segmenter.tflite` **不包含在 npm 包里**，需要从默认 URL 下载一次并放到 `public/vendor/mediapipe/`；config 设 `mediapipeAssetBase: "/meeting/vendor/mediapipe/"`，CSP 只需 `'self'`。
  - 或直接 `enableBackgroundEffects=false`，不加载该功能。
- 内置背景图片放 `src/assets/backgrounds/`，用 Vite 的 `import`；不要从 `public/` 直接 import。建议用自有素材；如果沿用 meet 的 Unsplash 图，记录来源与许可。
- **背景 Filter 缩略图（2026-11-13）**：
  - None 保持文字按钮；Blur 使用 `src/assets/backgrounds/blur.svg` 静态效果缩略图，实际虚化仍由 `blurRadius` 控制；Desk/Nature/Lake/Flower 各使用 1280×720 照片 WebP + 独立缩略图（`desk.webp`/`desk-thumb.webp`、`nature.webp`/`nature-thumb.webp`、`lake.webp`/`lake-thumb.webp`、`flower.webp`/`flower-thumb.webp`）。
  - 设置页 filter 卡片固定 96×54，缩略图 `cover` 裁剪；Blur 文字居中显示，其他图片卡片只显示图片（label 仅作为 `aria-label`/`title`）；摄像头关闭/loading 时卡片禁用，None 保持可点击。
- **config 追加背景图片**：

  ```json
  "backgroundImages": [
    {
      "label": "Office",
      "image": "backgrounds/office.jpg",
      "thumbnail": "backgrounds/office-thumb.jpg"
    },
    {
      "label": "Library",
      "image": "https://cdn.example.com/meeting/library.jpg"
    }
  ]
  ```

  - 列表追加在内置 Desk/Nature/Lake/Flower 之后，内置项不能通过该字段删除。
  - 本地文件放 `apps/web/meeting/public/backgrounds/`；相对路径按 `BASE_URL` 解析，`backgrounds/office.jpg` 在 `/meeting/` 部署下即 `/meeting/backgrounds/office.jpg`；以 `/` 开头按站点根解析。
  - remote 图片必须允许 CORS（`Access-Control-Allow-Origin`），并在 CSP `img-src` 中放行；否则 processor 会回退为黑背景。
  - `thumbnail` 可选，缺省使用 `image`；建议单独提供小图，避免设置页加载整张大图。
  - 图片建议 16:9、1280×720 或 1920×1080、单张 ≤300–500 KB；运行时会按 cover 裁剪到摄像头帧尺寸。
- P0 先做一次 `BackgroundProcessor` + `jose` + `livekit-client` 的 `vite build` spike，确认 MediaPipe 在 Vite 8 下能构建；不要把这个问题留到 P1。

**实现机制与厂商（2026-11-11 核实）**

背景虚化/虚拟背景完全在**发送端本地**处理，LiveKit 服务端和接收端不参与：

1. `CameraSettings` 用 `LocalVideoTrack.setProcessor()` 把 `BackgroundProcessor` 挂到本地摄像头 track；Blur/图片背景之间复用同一实例、`switchTo()` 切换模式；**None 时 `stopProcessor()` 销毁并恢复原始 track**；WebGL context loss 时同样停止并提示。
2. `@livekit/track-processors@0.8.1` 内部：
   - WebGL2 canvas + `@mediapipe/tasks-vision` 的 `ImageSegmenter`；
   - 模型 `selfie_segmenter.tflite`（float16、GPU delegate、VIDEO 模式、输出 category mask），逐帧分割人像；
   - mask 再做约 8px 的 box blur 软化边缘；
   - 虚化：原始帧下采样 4x → 可分离高斯模糊（横向/纵向两 pass；App 传 `blurRadius=16`，库内部除以采样因子 4）→ 用 mask 将清晰人像合成到模糊背景；
   - 虚拟背景：背景图 resize 成 cover 后，用同一 mask 合成；
   - 结果写入 canvas，包装为 `VideoFrame` 交回 LiveKit 编码发送。
3. 远端参与者收到的是已处理的普通视频流，不加载 MediaPipe，也没有额外 GPU 开销。
4. 加载时机：只有用户实际选择 Blur / 虚拟背景后才创建并挂载 `BackgroundProcessor`；默认 `none` 不 attach processor。为避免首次点击等待，`backgroundEffectsPreload='auto'`（默认）会在入会后延迟预取 loader JS / WASM / model（仅资源，不创建 Blob URL、不 attach processor；能力门控与 15 秒超时回退见 [`webmeeting_background_filter.md`](webmeeting_background_filter.md)）。full reconnect / 摄像头重新发布后，通过 `LocalTrackPublished` 事件幂等重挂 processor，避免背景效果在重连后丢失。

厂商与许可：

| 组件 | 提供方 |
|------|--------|
| MediaPipe 框架 | Google 开源项目 `google-ai-edge/mediapipe`（Apache-2.0） |
| `@mediapipe/tasks-vision` | Google 官方 npm 包 |
| `selfie_segmenter.tflite` | Google 预训练的通用人像分割模型，托管在 Google `mediapipe-models` 存储桶 |

- 该模型不是 LiveKit 训练或专有的；LiveKit 只是封装调用。
- Google 未详细公开训练数据构成；商用/再分发前按官方 model card 复核许可条款。
- 依赖链：`@livekit/track-processors` → Google MediaPipe Tasks Vision → Google `selfie_segmenter.tflite`。

**现状边界与未来加强方向**：

- **质量**：`selfie_segmenter` 是轻量模型；头发丝、快速运动、逆光/低照度、多人重叠处可能出现边缘抖动或漏分割。`blurRadius` 当前固定为 16，不随分辨率/设备自适应。加强路径：换更高质量分割/matting 模型、提高 mask 分辨率、对 mask 做时序平滑。
- **性能**：逐帧 GPU 分割 + WebGL 合成，低端机或 1080p+ 采集可能掉帧；当前 `useLowCPUOptimizer` 只降视频质量，未与背景处理器联动。加强路径：按设备能力动态降分辨率/帧率、利用 `onFrameProcessed` 的统计做自适应、CPU 受限时自动 `stopProcessor()`。
- **可替换性**：`BackgroundProcessor` 的 `segmenterOptions` 仍绑定 MediaPipe `ImageSegmenter`；换非 MediaPipe 模型需要自定义 `VideoTransformer`（或换库），不是改一个路径就行。
- **资源与合规**：默认从 jsDelivr / Google storage 加载（demo）；生产按上文自托管 wasm + 模型，并把模型文件纳入发布物和许可清单。
- **浏览器**：依赖 `OffscreenCanvas` / `VideoFrame` / `createImageBitmap` / WebGL2；iOS Safari 版本差异需按 §3.10 矩阵验证。背景效果还要求 WebGL2 float/half-float color-buffer 及对应线性过滤扩展（float32 路径还要求 `EXT_float_blend`；half-float 回退要求 `EXT_color_buffer_half_float`）；不满足时（实测 Huawei Browser 及 Chrome for Android 158 的 Mali-G76 驱动均缺少 float linear）仍显示 filter 控件，点击后提示 unsupported，不创建 processor，避免 GPU abort 或性能不可接受的 CPU fallback。Adreno 740 同时具备这些扩展，Chrome for Android 158 上 Blur/图片背景/None 实测通过。
- **架构边界**：当前是纯客户端方案；若未来需要"服务端虚化/录制侧虚化"或统一由服务端下发背景，需要 Egress 或服务端视频处理链，超出 `BackgroundProcessor` 范围。

#### 3.8.7 键盘快捷键（修正 meet 的冲突）

meet 实际代码用 `Ctrl/Cmd+A` / `Ctrl/Cmd+V`，会劫持聊天输入框的全选/粘贴。本 app 改用 Google Meet 风格：

- `Ctrl/Cmd + D`：麦克风开关。
- `Ctrl/Cmd + E`：摄像头开关。
- **`Shift + D` 只由 `DebugMode` 自己注册**（meet 的原行为）；`KeyboardShortcuts` 不要再注册同一个键，否则两个 keydown handler 会把面板 toggle 两次（看起来没反应）。
- 所有快捷键：事件目标是 `input` / `textarea` / `select` / `contenteditable` 时直接忽略；`event.repeat` 为 true 时忽略（长按不连续切换）。
- **不要宣称这些组合“避开浏览器保留键”**：`Ctrl/Cmd+D` 在 Chrome/Safari 是书签、`Ctrl/Cmd+E` 是搜索/地址栏快捷键；Google Meet 能拦截不代表所有浏览器/版本都能。必须在 Chrome/Safari/Firefox 实测 `preventDefault()` 是否生效；如果被浏览器抢占，fallback 到 `M`/`V`（无 modifier，仍然忽略输入框）或 `Alt+M`/`Alt+V`，并把最终组合写进验收矩阵。
- 用 `event.key.toLowerCase()` 匹配，避免大小写锁定问题。

#### 3.8.8 调试面板

- 迁移 meet 的 `DebugMode`：Room SID、本地参与者 track/permissions、远端参与者/track/bitrate。
- 去掉 Datadog 分支；保留 `setLogLevel(logLevel ?? 'debug')` 与 `window.__lk_room`。
- `Shift+D` 开关；每秒重渲染。**handler 也要忽略 `input`/`textarea`/`select`/`contenteditable`**，否则在聊天框输入大写 D 会误开调试面板。
- 生产默认关闭：`showDebugPanel` 默认 `import.meta.env.DEV`，可在 config 覆盖。

#### 3.8.9 RecordingIndicator

- 原样迁移：`useIsRecording()`、toast、红色内嵌边框。
- 不提供开始/停止录制；如果房间被外部 Egress 录制，指示器仍会亮起。
- 这满足「follow meet」的视觉反馈，又不引入不可用的录制按钮。

#### 3.8.10 低 CPU 优化

- 原样迁移 `useLowCPUOptimizer`：
  - `ParticipantEvent.LocalTrackCpuConstrained` -> `prioritizePerformance()`，可选 `stopProcessor()`，远端 video quality 降 LOW；
  - 低功耗模式开启后，`RoomEvent.TrackSubscribed` 的新 track 也降 LOW。
- 迁移时修一个上游问题：第一个 effect 的依赖数组漏了 `opts.disableVideoProcessing`，导致该选项变化不生效；补上再提交。
- `disableVideoProcessing` 默认 false；如果用户开了背景效果且 CPU 受限，可置 true（会 `stopProcessor()`）。低功耗状态目前只 `console.warn`，P1 可以加一个不打扰的 UI 提示。

#### 3.8.11 错误、断线与离开

| 场景 | 行为 |
|------|------|
| 主动 Leave（控制栏） | 点击先弹确认对话框；`Cancel` / `Esc` / 点遮罩关闭且留在会议；确认后 `room.disconnect()` -> `Disconnected(CLIENT_INITIATED)` -> 回 Home，toast “You left the meeting” |
| 网络断线 | `ConnectionStateToast` 显示 `Reconnecting`；恢复后显示 `Reconnected`；不可恢复时 `Disconnected` -> 错误页 + Retry |
| 重复 identity（同一 token 被重复使用） | `DUPLICATE_IDENTITY` -> 回 Home + “You joined from another tab or device.” |
| 被主持人移除 | `PARTICIPANT_REMOVED` -> 回 Home + “You were removed from the meeting.” |
| 房间被删除/结束 | `ROOM_DELETED` -> 回 Home + “The meeting has ended.”；**不要** Retry（重试会重新创建空房） |
| token 过期/无效 | 连接失败 -> 错误页，提供 Retry（重新签发）与 Back |
| 摄像头/麦克风失败 | toast；会议继续（可看/听）；`NotReadableError` 自动延迟重试一次；可在 SettingsMenu 重试 |
| iOS/iPadOS 屏幕共享 | iOS/iPadOS **26 及以前** Safari 不支持 `getDisplayMedia`，应隐藏控制栏的共享按钮；**iOS/iPadOS 27+（Safari 27，2026-09-14 发布）已支持**。当前实现仍按 `is-ios` UA 隐藏，属于 P2 增强：移除 UA 判断、改为能力检测（LiveKit `supportsScreenSharing()` 已检查 `navigator.mediaDevices.getDisplayMedia`），并实机验证 |
| 扬声器选择 | `setSinkId` 在 Safari/iOS 支持不全：`MediaDeviceMenu kind="audiooutput"` 可能无效，浏览器矩阵里标注 |
| 只有自己 | 入会后 30s 若 `room.numParticipants <= 1`，提示 “No one else has joined yet — check the meeting number.” |
| 浏览器不支持 | 启动时检查 `navigator.mediaDevices`；显示浏览器建议 |
| 非安全上下文 | 启动时 `window.isSecureContext` 检查；显示 HTTPS/localhost 提示 |
| 自动播放被阻止 | `VideoConference` 控制栏内置 `StartAudio`；按提示点击 |
| 房间为空/别人都走了 | LiveKit `empty_timeout` 后关房；最后一人离开后回 Home |

- **ErrorView 脱敏**：只显示简短错误摘要（如 `Could not connect` + 状态码/原因），不要渲染完整 token/JWT、`apiSecret` 或带敏感查询参数的 URL；详细堆栈只进 console（内网测试）。
- **ErrorView Retry**：Retry 期间 disable 按钮（loading），避免并发签发/连接；Back 直接回 Home。
- **toast 归属**：`Disconnected` 的原因文案由 App 的 `onLeave(message?)` 统一 toast；MeetingRoom 不重复 toast，避免双提示。

> **iOS/iPadOS 27+ 网页屏幕共享（2026-11-11 调研，P2 增强项）**
>
> 结论：Safari 27（iOS/iPadOS 27，2026-09-14 发布）开始支持 `navigator.mediaDevices.getDisplayMedia()`，网页端屏幕共享不再是桌面专属能力。证据：
> - [MDN BCD issue #30654](https://github.com/mdn/browser-compat-data/issues/30654)：真实 iPhone、iOS 27.0 Safari 调用 `getDisplayMedia({video:true})`，系统 picker 返回包含 video track 的 `MediaStream`；
> - [MDN BCD PR #30732](https://github.com/mdn/browser-compat-data/pull/30732)：`safari_ios` 支持版本标为 27，Open Web Docs collector 在 Safari iOS 27.0 通过；
> - [caniuse](https://caniuse.com/mdn-api_mediadevices_getdisplaymedia)：Safari on iOS 27.0/27.1/27.2 均标为支持；
> - [Apple ScreenCaptureKit](https://developer.apple.com/documentation/ScreenCaptureKit)：iOS/iPadOS 27.0 起提供 `SCContentSharingPicker` 系统共享选择器；
> - [Safari 27 Release Notes](https://developer.apple.com/documentation/safari-release-notes/safari-27-release-notes)：修复了 `getDisplayMedia()` 屏幕共享初始画质极低、最长 30 秒才清晰的问题。
>
> 注意：网上仍有“iOS Safari 不支持屏幕共享”的旧文章，验证时间多早于 iOS 27 正式发布，不能作为 27+ 的结论。现有公开**实机测试是 iPhone iOS 27.0**，iPadOS 27 有 OS 级 ScreenCaptureKit 和 BCD 数据支持，但仍建议单独用 iPad 27 验证。
>
> 当前 `apps/web/meeting` 仍通过 `.is-ios .lk-control-bar [data-lk-source='screen_share']` 在所有 iPhone/iPad 隐藏共享按钮，因此会误伤 iOS 27+。**P2 启用工作**：
> 1. 移除 `is-ios` UA 隐藏 CSS（如无其他用途，同时删除 `isIOSLike()` / `is-ios` class）；LiveKit `ControlBar` 已用 `supportsScreenSharing()` 检查 `navigator.mediaDevices.getDisplayMedia`，能力不支持的旧 iOS 会自动不渲染按钮。
> 2. iPhone/iPad 27 实机验证：系统 picker 弹出、返回 MediaStream、`audio:true` 是支持还是被忽略、LiveKit 发布/其他端订阅、画质、Safari 后台后是否停止。
> 3. 评估 iOS 共享端只允许整屏/单 app（`SCContentSharingPicker` 约束），在 UI 文案/帮助里说明。
> 4. 补浏览器矩阵记录；iPadOS 27 单独验证。

### 3.9 UI/UX、响应式与可访问性

- 全站暗色：`html { color-scheme: dark; background: #111; }`；`<body data-lk-theme="default">`。
- 首页卡片参照 meet 的 `Home.module.css`：居中、max-width ~440px、半透明 1px 边框、0.5rem 圆角、`lk-button` 风格按钮。
- 会中直接用 LiveKit `VideoConference` 的布局；不自造顶栏，保持与 meet 一致。
- 移动端：
  - `viewport-fit=cover`；
  - Home/PreJoin 容器用 `min-height: 100dvh; overflow-y: auto;`，避免 `body { overflow: hidden }` 在小屏截断表单；
  - Home/PreJoin 加 `env(safe-area-inset-bottom)`；
  - 会中控制栏不要被 home indicator 遮挡（必要时给 `.lk-room-container` 加 padding）；
  - 横屏/竖屏均由 LiveKit 网格自适应；
  - 竖屏手机上 Settings 模态全宽：用 `@media (orientation: portrait) and (max-width: 640px)` 覆盖 LiveKit 的 `.lk-settings-menu-modal`（默认 `min-width: 50vw` 会留下侧边空隙）。**构建必须设置 `build.cssTarget` 包含 `chrome99`**：否则 Lightning CSS 会把媒体查询压成 Media Queries L4 range 语法 `(width<=640px)`，Huawei Browser 14（Chromium 99）解析为 `not all` 并丢弃整条规则，导致竖屏仍显示窄弹窗。
- 可访问性：
  - 表单 `<label htmlFor>`、`aria-invalid`、`aria-describedby`；
  - 错误区域 `role="alert"`；
  - 按钮有可读文本；
  - 不依赖颜色单独传达状态（同时给文本/toast）。
- 文案：MVP 英文（与 meet 一致）；错误文案也必须是英文，不要在中英文之间混用。后续如需中文，集中到 `src/messages.ts`，不在组件里散落。

### 3.10 浏览器、安全上下文与媒体权限

- 支持现代 Chrome / Edge / Firefox / Safari（桌面 + iOS/Android 浏览器）；不做 IE。
- 建议的验收矩阵（P0/P1 手测）：

  | 浏览器 | 会中 A/V | 屏幕共享 | 扬声器选择 | 备注 |
  |---|---|---|---|---|
  | Chrome / Edge desktop | ✅ | ✅ | ✅ | 主要验收目标 |
  | Firefox desktop | ✅ | ✅（用户选择） | ✅ | vp9/AV1 协商需实测 |
  | Safari macOS | ✅ | ✅ | 部分 | vp9 协商、`setSinkId` 支持需实测 |
  | iOS Safari | ✅ | ❌ | ❌ | 无 `getDisplayMedia`/`setSinkId`；音频播放需手势 |
  | Android Chrome | ✅ | 部分 | ✅ | 系统浏览器 |

- iOS/iPadOS **26 及以前** Safari 不支持 `getDisplayMedia`：会中不要显示可点击的屏幕共享按钮。**iOS/iPadOS 27+ 已支持**（见 §3.8.11 末尾的 P2 增强说明）：届时移除 `is-ios` CSS/UA 判断，依赖 LiveKit `supportsScreenSharing()` 的能力检测；`setSinkId` 支持仍不完整，`MediaDeviceMenu kind="audiooutput"` 可能无效。
- `VideoConference` **不暴露 `controls` prop**；当前用 `.is-ios .lk-control-bar [data-lk-source='screen_share']` 隐藏旧 iOS 的屏幕共享按钮。该规则在启用 iOS 27+ 共享时应删除（`VideoConference` 内部 `ControlBar` 已按 `supportsScreenSharing()` 隐藏/显示），或改用自定义 `ControlBar`（`controls` prop）。不要假设能通过 `VideoConference` prop 控制单个按钮。
- 如果配置 `videoCodec: 'vp9' | 'av1'`，必须在 Safari 上确认协商结果；不要假设所有目标浏览器都支持 SVC codec（默认 vp8 正是为了避开这个问题）。
- WebView：本 app 仍面向系统浏览器；Android 应用内 WebView 已于 2026-11-15 支持受信任会议页的摄像头/麦克风（见 `docs/android_webview_meeting_support.md`），但屏幕共享仍应使用系统浏览器。
- 安全上下文要求：
  - `crypto.subtle`（签 JWT）与 `getUserMedia`/`getDisplayMedia` 都需要 HTTPS 或 localhost。
  - `http://localhost`、`http://127.0.0.1` 可用；`http://192.168.x.x` 不可用。
  - 页面 HTTPS 时，LiveKit URL 必须是 `wss://`，否则混合内容被拦截。
- LiveKit 服务端要求：
  - demo server 已确认（livekit-server `v1.13.11`；实际 `wss://<livekit-host>:<port>` 只写本地 `public/config.json`）；若该端口是反向代理，必须支持 WebSocket upgrade 与长连接。
  - 可信 TLS 证书（自签证书需要设备/浏览器信任）；demo server 的证书已由本地 meet.git 在 Chrome/Safari 验证可用。
  - 信令 WSS 已验证可达：本地 meet.git 在 Chrome 与 Safari 均能正常入会。
  - 入会成功，信令与基本媒体协商可用；如需单独确认 UDP/TURN 端口（`7881/TCP`、`50000-60000/UDP` 或 TURN/TLS），可再看 LiveKit 日志/抓包。详见 `docs/personal_meeting_design.md` §3.2。
- 权限请求时机：
  - PreJoin 挂载时请求摄像头/麦克风；此时用户刚在首页点击 Join，通常具备用户手势上下文。
  - 不在首页加载时自动请求，避免权限弹窗被浏览器拦截。
  - 如果目标浏览器（尤其 iOS Safari）要求直接手势，PreJoin 的「Join meeting」按钮也可作为再次触发点。
- 权限被拒：
  - 摄像头拒绝 -> 可纯音频入会；
  - 麦克风拒绝 -> 可只看/听，不能发言；
  - 两者都拒绝 -> 仍可入会观看，toast 提示如何恢复权限。

### 3.11 构建与运行

#### 3.11.1 `package.json`

```json
{
  "name": "livachat-meeting",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "build:demo": "ALLOW_CLIENT_TOKEN=1 npm run build",
    "check": "tsc --noEmit",
    "test": "vitest run",
    "preview": "vite preview"
  },
  "dependencies": {
    "@livekit/components-react": "2.9.24",
    "@livekit/components-styles": "1.2.0",
    "@livekit/track-processors": "0.8.1",
    "jose": "6.1.3",
    "livekit-client": "2.23.0",
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "react-hot-toast": "2.5.2",
    "tinykeys": "3.0.0",
    "tslib": "2.8.1"
  },
  "devDependencies": {
    "@types/dom-mediacapture-transform": "0.1.11",
    "@types/node": "24.12.4",
    "@types/react": "18.3.28",
    "@types/react-dom": "18.3.7",
    "@vitejs/plugin-react": "6.0.5",
    "typescript": "5.9.3",
    "vite": "8.2.1",
    "vitest": "4.1.11"
  },
  "engines": {
    "node": "^20.19.0 || ^22.12.0 || >=24.0.0"
  }
}
```

> `@types/dom-mediacapture-transform` 是 `@livekit/track-processors` 的 peer dependency（`^0.1.9`），提供 `MediaStreamTrackProcessor` 的 DOM 类型；缺少它时 `tsc --noEmit` 可能报类型错误。npm 7+ 通常会自动装 peer，但显式列出更稳。
>
> `@types/node` 供 `vite.config.ts`（build gate 用到 `node:fs`/`process`）与 `vitest.config.ts` 使用；当前 `tsconfig.include` 只有 `src`，`tsc --noEmit` 暂不检查配置文件，但编辑器和后续扩展需要它。

#### 3.11.2 `vite.config.ts`

```ts
import { readFileSync } from 'node:fs'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/**
 * demo/验证阶段允许把 LiveKit key pair 放进 public/config.json，
 * 但构建产物会带 secret，必须显式确认后才允许 build。
 */
function guardClientTokenConfig(): Plugin {
  return {
    name: 'guard-client-token-config',
    apply: 'build',
    buildStart() {
      const url = new URL('./public/config.json', import.meta.url)
      try {
        const cfg = JSON.parse(readFileSync(url, 'utf8')) as {
          tokenMode?: string
          apiKey?: string
          apiSecret?: string
        }
        if (!cfg.apiSecret) return
        if (cfg.tokenMode === 'server') {
          this.error('tokenMode=server must not contain apiKey/apiSecret.')
        }
        if (process.env.ALLOW_CLIENT_TOKEN === '1') {
          this.warn('Building with an embedded LiveKit API secret (demo only).')
          return
        }
        this.error(
          'public/config.json contains a client-mode API secret. ' +
            'Set ALLOW_CLIENT_TOKEN=1 to build the demo bundle, or remove the file.',
        )
      } catch (err) {
        if ((err as { code?: string }).code === 'ENOENT') return
        throw err
      }
    },
  }
}

export default defineConfig({
  base: '/meeting/',
  plugins: [react(), guardClientTokenConfig()],
  server: {
    port: 5174,
    // 不需要 /api 代理：token 在浏览器生成，LiveKit 走 WSS 直连
  },
  build: { outDir: 'dist' },
})
```

- 端口 5174，避开 mylife 的 5173。
- `base` 固定 `/meeting/`；demo 与生产共用同一个 base，**不要用 base 区分阶段**（阶段差异走 `tokenMode` / 入口态 / 功能开关）。
- 构建闸门：`public/config.json` 是 client 模式且没有 `ALLOW_CLIENT_TOKEN=1` 时直接失败；demo 构建命令是 `ALLOW_CLIENT_TOKEN=1 npm run build`，并会在日志里打印警告。
- 开发 URL：`http://localhost:5174/meeting/`。

#### 3.11.3 `tsconfig.json`

对齐 mylife 的严格配置：

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "verbatimModuleSyntax": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "noEmit": true,
    "types": ["vite/client"]
  },
  "include": ["src"]
}
```

- `verbatimModuleSyntax` 要求类型导入写 `import type`；迁移 meet 代码时需逐处修正（如 `LocalUserChoices`、`RoomOptions`、`VideoCodec`）。

#### 3.11.4 `index.html`

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <meta name="theme-color" content="#111111" />
    <meta name="description" content="LivaChat Meeting - join by meeting number" />
    <meta name="robots" content="noindex,nofollow" />
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <title>LivaChat Meeting</title>
  </head>
  <body data-lk-theme="default">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- `href="/favicon.svg"` 不要写成 `/meeting/favicon.svg`：Vite build 会给 public 资源自动加 `base` 前缀；写死 base 会在部署前缀变化或根路径部署时 404。
- `<title>` 是首屏兜底；App 加载 config 后执行 `document.title = config.appTitle`，这样 `appTitle` 才真正生效。
- `index.html` 不放 `showDebugPanel` 之类的运行期开关，统一走 `config.json`。

#### 3.11.5 `main.tsx` 与样式导入顺序

```tsx
import { createRoot } from 'react-dom/client'
import '@livekit/components-styles'
import '@livekit/components-styles/prefabs'
import './styles/globals.css'
import App from './App'

createRoot(document.getElementById('root')!).render(<App />)
```

- LiveKit 样式先导入，自定义 `globals.css` 后导入，便于覆盖。
- 不包 `<StrictMode>`（§3.2.6）。

#### 3.11.6 `globals.css` 要点

```css
html {
  color-scheme: dark;
  background-color: #111;
}

html,
body,
#root {
  height: 100%;
  margin: 0;
}

body {
  overflow: hidden;
}
```

> `.lk-room-container` 的 `position: relative; width/height: 100%` 由 `@livekit/components-styles` 提供，不需要在 `globals.css` 重复；重复定义容易和升级后的组件样式打架。

#### 3.11.7 Makefile 目标

在根 `Makefile` 增加（对齐 `mylife-*`）：

```make
MEETING_DIR := apps/web/meeting

.PHONY: meeting-install meeting-dev meeting-build meeting-build-demo meeting-check meeting-test

meeting-install:
	cd $(MEETING_DIR) && npm install

meeting-dev:
	cd $(MEETING_DIR) && npm run dev

# 默认构建：public/config.json 存在 client secret 时会失败（防止误把 secret 打进 dist）
meeting-build:
	cd $(MEETING_DIR) && npm run build

# demo/验证：显式允许内置 key pair
meeting-build-demo:
	cd $(MEETING_DIR) && ALLOW_CLIENT_TOKEN=1 npm run build

meeting-check:
	cd $(MEETING_DIR) && npm run check

meeting-test:
	cd $(MEETING_DIR) && npm test
```

#### 3.11.8 本地开发

```bash
cd apps/web/meeting
npm install
cp config.example.json public/config.json
# livekitUrl = wss://<livekit-host>:<port>（实际值只写本地 public/config.json）
# apiKey / apiSecret 用 meet.git/.env.local 的专用 demo key（已确认可复用）
npm run dev
# 打开 http://localhost:5174/meeting/
```

- `localhost` 属于安全上下文，可以用 HTTP 开发。
- demo server 已确认（livekit-server `v1.13.11`；实际 `wss://<livekit-host>:<port>` 只写本地 `public/config.json`）。WSS 已用本地 meet.git 在 Chrome/Safari 验证可达，直接连它即可。
- 如果只想验证会中功能、不想依赖 demo server，可以本地起 `livekit-server --dev`（内置 `devkey`/`secret`，`ws://127.0.0.1:7880`）：
  ```bash
  livekit-server --dev
  # config.json: livekitUrl = "ws://127.0.0.1:7880",
  #              apiKey = "devkey", apiSecret = "secret"
  ```
  注意 `--dev` 只适合本机/localhost，不能给局域网其他设备或手机用。
- 如果从局域网其他设备访问，必须给 Vite 配 HTTPS（mkcert 或受信任证书），否则 `crypto.subtle`/`getUserMedia` 不可用：
  ```bash
  mkcert -install
  mkcert localhost 192.168.x.x
  # vite.config.ts: server.https = { key, cert }
  ```
  自签且设备不信任的证书仍不是可靠的安全上下文，优先用 mkcert 并把根 CA 装到测试设备。
- LiveKit URL 若是 `wss://`，页面用 `http://localhost` 也能连；页面一旦是 HTTPS，就不能连 `ws://`。
- demo 构建用 `ALLOW_CLIENT_TOKEN=1 npm run build`（或 `make meeting-build-demo`）；生产构建用 `npm run build`，确保配置是 `tokenMode=server`；两者 base 都是 `/meeting/`。

### 3.12 部署

#### 3.12.1 静态托管（推荐）

- demo 构建：`ALLOW_CLIENT_TOKEN=1 npm run build`（或 `make meeting-build-demo`）产出 `apps/web/meeting/dist/`。
- 任意静态服务器（nginx/Caddy/对象存储）托管，URL 前缀 `/meeting/`。
- 不需要 Node 运行时；不需要 SPA fallback（没有前端路由；`/meeting/` 只有 index）。
- 必须 HTTPS（`crypto.subtle` + getUserMedia）。
- **`dist/` 是 secret 材料**：client 模式下 `dist/config.json` 带 API secret，不得进公共镜像仓库、CI artifact、对象存储公共读、演示截图。demo 环境用专用 key；`dist` 只放到受控内网主机。
- 把 `config.json` 放在 `dist/config.json`（构建后写入/挂载），不要打进版本库；如果构建时已在 `public/config.json`，Vite 会复制过去——这正是构建闸门要拦住的情况。
- 缓存策略：`config.json` 用 `Cache-Control: no-store`（便于轮换）；`index.html` 用 `no-cache`；带 hash 的 `assets/*` 和自托管的 `vendor/mediapipe/*` 用 `immutable, max-age=31536000`（MediaPipe 资源依赖 HTTP cache 让预加载命中，不可省）。
- **内网测试形态**：可在开发机跑 `npm run dev -- --host`（配 HTTPS），或把 `dist` 放到内网 nginx/Caddy；不需要公网域名、对象存储或 CDN。局域网设备访问时必须 HTTPS（见 §3.11.8），否则媒体/JWT 签发的安全上下文不成立。

#### 3.12.2 可选：由 LivaChat FastAPI 挂载

如果希望与后端同源，可在 `services/app/application.py` 增加（**未来变更，不在本期**）：

```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles

dist = Path(__file__).resolve().parents[2] / "apps/web/meeting/dist"
app.mount("/meeting", StaticFiles(directory=str(dist), html=True), name="meeting")
```

- 同源的好处：config.json 同源、未来 token endpoint 同源、无 CORS。
- 需要部署时把 `dist/` 放进镜像；`infra/docker/server.Dockerfile` 目前只复制 `configs/`，需要额外 build stage/COPY。
- **base 与 mount 必须一致，且 demo/生产都是 `/meeting/`**：`base=/meeting/` + mount `/meeting`；阶段差异只体现在 `tokenMode`、入口与功能，不要换 base，也不要维护第二套代码。
- 本设计不要求改后端；仅记录可行路径。

#### 3.12.3 反向代理与 LiveKit 端口

- LiveKit 不要挂在 `/meeting` 路径下；它是独立子域名/端口，客户端直连 WSS。
- demo 的 LiveKit 是独立 host:port（实际值只写本地 `public/config.json`）；页面只需能连它，不要把它代理到 `/meeting` 下；CSP 的 `connect-src` 必须包含 `wss://<livekit-host>:<port>`（**含端口**，按实际值替换）。
- 反向代理必须支持 WebSocket upgrade（`Upgrade`/`Connection` 头）和长连接超时。
- 媒体面需要 UDP/TURN 端口可达；如果只开放 443，配置 LiveKit TURN over TLS。详见 `docs/personal_meeting_design.md` §3.2。

#### 3.12.4 安全响应头（建议）

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'wasm-unsafe-eval' [https://cdn.jsdelivr.net];
  style-src 'self' 'unsafe-inline';
  img-src 'self' data: blob:;
  media-src 'self' blob:;
  font-src 'self' data:;
  worker-src 'self' blob: [https://cdn.jsdelivr.net];
  connect-src 'self' wss://<livekit-host>:<port> [https://cdn.jsdelivr.net https://storage.googleapis.com];
  object-src 'none';
  frame-src 'none';
  manifest-src 'self';
  frame-ancestors 'none';
  base-uri 'none';
  form-action 'self'
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
Permissions-Policy: camera=(self), microphone=(self), display-capture=(self)
```

- CSP 必须由静态服务器/反向代理（或 FastAPI 中间件）注入；Vite 产物不自带响应头。示例（Caddy，假设 `dist/` 部署在 `/srv/meeting/`，URL 前缀固定 `/meeting/`）：
  ```
  meeting.internal {
    # base=/meeting/ 意味着请求路径是 /meeting/...，所以 root 必须是 /srv，而不是 /srv/meeting
    root * /srv
    # 必须显式开启压缩：file_server 默认不压缩；主 JS 未压缩约 1 MB，
    # 自托管 MediaPipe WASM 未压缩约 9.4 MB（Brotli/zstd 后约 2.0 MB）
    encode zstd gzip
    header {
      Content-Security-Policy "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; worker-src 'self' blob:; connect-src 'self' wss://<livekit-host>:<port>; object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
      Referrer-Policy no-referrer
      X-Content-Type-Options nosniff
      Permissions-Policy "camera=(self), microphone=(self), display-capture=(self)"
    }
    # 路径带 /meeting 前缀；不要写成 /config.json 或 /index.html
    @config path /meeting/config.json
    header @config Cache-Control "no-store"
    # 目录请求 /meeting/ 也会由 file_server 解析到 index.html，但请求路径不是 /meeting/index.html
    @index path /meeting /meeting/ /meeting/index.html
    header @index Cache-Control "no-cache"
    @assets path /meeting/assets/*
    header @assets Cache-Control "public, max-age=31536000, immutable"
    # 自托管 MediaPipe（config.json 的 mediapipeAssetBase="/meeting/vendor/mediapipe/"）
    # 必须长缓存，否则预加载的资源不会稳定命中 HTTP cache
    @vendor path /meeting/vendor/*
    header @vendor Cache-Control "public, max-age=31536000, immutable"
    file_server
  }
  ```
  > 传输体积与缓存：主 JS 约 1.0 MB（gzip ~291 KB、zstd 更小），CSS gzip ~5 KB，因此 `encode zstd gzip` 必须开启；未压缩部署时首次加载会真的传 ~1 MB。带 hash 的 `assets/*` 配 `immutable` 后，正常刷新/再次访问直接命中 HTTP 缓存；`index.html` 的 `no-cache` 只做 304 校验；`config.json` 的 `no-store` 只影响这个小文件。只有新版本 hash 变化或 hard reload 才会重新下载主 bundle。MediaPipe 的 WASM/模型不在主 bundle 中：自托管时由 `backgroundEffectsPreload` 在入会后后台预取（Brotli/zstd 后约 2.0 MB 传输、约 9.4 MB 解码），用户首次点击背景效果时命中 HTTP cache。
  > **自托管 MediaPipe 的传输体积取决于压缩配置**：Caddy 的 `encode zstd gzip` 默认内容类型已包含 `application/wasm`（2023-10 起），传输约 2.0 MB；nginx 的 `gzip_types` 默认只有 `text/html`，必须显式加 `application/wasm`，否则会真的传 ~9.4 MB；同时确认响应 `Content-Type: application/wasm`（nginx 检查 `mime.types`），否则预加载的 `fetchAsset` 校验会失败。压缩与缓存细节见 `webmeeting_background_filter.md` §4.3。
  > 上面的 CSP 按**生产自托管 MediaPipe**收紧；demo 若使用默认 CDN，需要在 `script-src`/`worker-src` 加 `https://cdn.jsdelivr.net`、在 `connect-src` 加 `https://storage.googleapis.com`，否则背景效果会被拦截（见下条与 §3.8.6）。
- `connect-src` 至少包含 LiveKit 的 WSS origin（**含端口**；非默认端口不能省略，否则 CSP 不匹配）。demo 环境把示例里的 `wss://<livekit-host>:<port>` 换成 config 中的实际 WSS URL（只写本地 `public/config.json`，不写入本文）。上面的方括号 `[...]` 表示**可选源**，不是 CSP 字面语法；部署时按实际启用的功能展开或删除。
- 如果 `backgroundImages` 使用 remote 图片，把对应 origin 加入 `img-src`；本地 `public/backgrounds/` 图片由 `'self'` 覆盖。remote 图片还必须允许 CORS，否则虚拟背景会回退为黑背景。
- 如果使用背景效果且不自托管 MediaPipe（`mediapipeAssetBase` 为空），默认会从 `cdn.jsdelivr.net` 加载 wasm loader JS（需要 `script-src` + `worker-src` + `connect-src`），并从 `storage.googleapis.com` 拉 `selfie_segmenter.tflite`（需要 `connect-src`）。MediaPipe 内部会用 `createElement('script')`/`importScripts` 加载资源，只放行 `connect-src` 不够。生产/内网部署按 §3.8.6 设 `mediapipeAssetBase: "/meeting/vendor/mediapipe/"` 自托管后，这些外部源都可以删掉，收紧到 `'self'`；`config.example.json` 默认走 CDN，本地 `public/config.json` 可覆写为自托管。Krisp 本期不做，因此不需要 `integrations.livekit.io`。不需要背景效果就删掉这些源。
- `style-src 'unsafe-inline'` 是因为 LiveKit 组件使用内联 style；不要为此把 script 也放开。
- `script-src 'wasm-unsafe-eval'` 用于 MediaPipe 的 WebAssembly；不用背景效果可去掉。Safari 对 `wasm-unsafe-eval` 的 CSP 支持不一致；如果 console 报 CSP 拦截，按 §3.10 矩阵处理（关闭背景效果或调整 CSP），不要假设所有浏览器都放行。
- 不添加 `Access-Control-Allow-Origin: *`；MVP 不需要 CORS。

#### 3.12.5 COOP/COEP

- meet 的 `next.config.js` 设置了 `Cross-Origin-Opener-Policy: same-origin` + `Cross-Origin-Embedder-Policy: credentialless`；这通常用于跨源隔离（E2EE/SharedArrayBuffer 等场景），meet 源码未注明具体用途。
- 本 app MVP 不做 E2EE，**默认不添加** COOP/COEP，避免 Safari 对 `credentialless` 支持差异导致背景图片/CDN 资源被拦。
- 如果未来启用 E2EE（SharedArrayBuffer/worker），再加这两个头，并做 Safari/Chrome 回归。

### 3.13 安全设计（重点）

#### 3.13.1 核心事实

- LiveKit API secret 是 HS256 的对称密钥。任何拿到它的人都可以为**该 server 上的任意房间**签发 token，甚至可以自行加 `roomAdmin` 等 grants。
- 本阶段已由需求方确认：**允许把 secret 放在 `public/config.json`，仅用于 demo/验证**。因此：
  - 任何能打开页面的人都能在 Network/DevTools 里看到 secret；
  - 这不是「前端加密」能解决的问题；混淆/加密配置没有意义；
  - 风险接受的前提是：**已确认使用专用 demo key**（`meet.git/.env.local`，非生产，meet demo 也在用）、只在内网/受控范围部署、不接入生产流量；未来必须切服务端签发（§3.13.4）。
- **受威胁的资产是 LiveKit server 本身**，不只是某个 meeting room：同一 server 上的其他应用（例如 meet demo）也会受影响。
- `livekit-server-sdk` 源码也明确警告不要把 API secret 放进 web bundle；本文接受该警告是 demo 阶段的已知例外。

#### 3.13.2 威胁与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| secret 泄露 | 攻击者可签发任意房间/管理员 token，滥用 SFU；**同 server 上的其他 demo 房间也受影响** | 已确认专用 demo key（与 meet demo 共用同一 server/key）；泄露半径限 demo；定期轮换；**TTL 对 secret 泄露无效**；未来切 server token |
| 会议号被猜中 | 陌生人入会 | 用 🎲 生成高熵会议号；内网部署；未来加邀请/鉴权 |
| 无鉴权 | 任何能加载页面的人都能入会 | 本阶段定位 demo；生产走 `personal_meeting_design.md` §7 的邀请 token 方案 |
| token 泄漏 | 短期可重放 | TTL 10m；只在内存；不写 URL/localStorage；不打印日志。**只限制 JWT 重放，不限制 secret 泄露** |
| 构建产物/镜像 | `dist/config.json` 带 secret 被传播 | 构建闸门（§3.11.2）；`dist` 不进公共 artifact/镜像仓库；部署清单检查 |
| XSS | 窃取 token/secret | React 默认转义；不用 `dangerouslySetInnerHTML`；CSP 禁 inline script |
| 点击劫持 | 诱导操作 | `frame-ancestors 'none'` |
| Referrer 泄漏 | 会议 URL/房间名外泄 | `Referrer-Policy: no-referrer` |
| 供应链 | 恶意依赖 | 锁定版本 + lockfile；不引入 CDN 运行时代码（MediaPipe 资源除外） |
| 隐私 | identity/room 进日志 | identity 用 opaque `u_<random>`；显示名只放 `name` claim；不要用姓名/邮箱做 room/identity |

#### 3.13.3 部署红线

- 本阶段定位 **demo/验证**，**不要**部署到公网；若未来要公网，必须先落地 §3.13.4 的服务端 token，再改 `tokenMode=server`，并补齐邀请/鉴权与页面状态。
- 使用**已确认的专用 demo key**：`meet.git/.env.local` 里的 key 就是 demo 专用（meet demo 也使用它），本 app 直接复用；不要用于生产。轮换会同时影响 meet demo，需与 meet 使用方协调。
- **不要**把 `public/config.json` 提交到 git；**`dist/` 是 secret 材料**，不要进公共镜像仓库、CI artifact、对象存储公共读。
- **不要**在 CI 日志、错误上报、截图、演示录屏中输出 `apiSecret` 或完整 token。
- 如果 secret 曾经进入公开仓库/公网页面/公共 artifact，立即在 LiveKit 侧轮换 key，并更新 `config.json`（运行期读取，不需要重新构建）。
- 廉价硬化：把 `/meeting/` 放到内网 SSO / basic auth 之后；这不能阻止已授权用户看到 secret，但能减少随机访问。
- UI 上的 dev 警告不是装饰；它是部署前的最后一道提醒。`npm run build` 的构建闸门是第二道。

#### 3.13.4 未来服务端签发

- 实现 `HttpTokenProvider`，config 改 `tokenMode: "server"`；客户端配置不再含 `apiKey/apiSecret`（运行时校验必须拒绝）。
- 服务端端点必须：
  - 校验调用者身份（现有 `token`/`device_id` 或邀请 token）；
  - 校验会议状态/参会权限/口令；
  - 用 `livekit-server-sdk` 的 `AccessToken` 签发短 TTL token；
  - 不把 API secret 返回给客户端。
- 生产路径与 `docs/personal_meeting_design.md` §7 保持一致（邀请 token 放 URL fragment，POST 换取 LiveKit JWT）；响应契约适配见 §3.4.7（`{ livekit_url, token }` → `ConnectionDetails`）。
- **同一个 `apps/web/meeting`**：生产页只增加邀请 token 入口、preview/join 调用、`ended` 等状态，并切 `tokenMode=server`；不要新建第二个 Web app。
- 开发期可选硬化：在 Vite dev server 加一个 middleware（或独立小服务）读取 `.env.local` 并签发 token，浏览器只拿 token；这能让 secret 不出现在 dev bundle/Network 里，但生产静态托管仍需独立端点。本阶段为满足「纯 JSON/hardcode」需求，默认走 client provider。

### 3.14 与既有 `personal_meeting_design.md` 的关系

- **结论：`apps/web/meeting` 是唯一 Web 实现。** `docs/personal_meeting_design.md` §7 只保留生产入会契约与页面行为（邀请 token、preview/join、多态页面、部署/CSP），不再维护第二套目录、技术栈或静态页实现；其 §7.1 已改为指向本文。
- `personal_meeting_design.md` §7 定义的是**生产形态**：
  - URL `/meeting/#t=<invite_token>`；
  - 服务端校验邀请 token 后签发 LiveKit JWT；
  - 静态页由 FastAPI 托管，同源调用 `/api/meetings/*`；
  - 构建/部署同样 `base=/meeting/`、mount `/meeting`。
- 本文的 demo 形态：
  - 首页直接输入 meeting number；
  - 没有邀请 token/会议状态/后端；
  - secret 在前端（仅 demo/验证），不能作为生产方案；
  - `base=/meeting/`、mount `/meeting`。
- 两者是**同一套代码的两个阶段配置**：
  - 会中 `MeetingRoom`（Room + VideoConference + Settings）直接复用，不抽包也不复制；
  - 生产页把 `ClientTokenProvider` 换成 `HttpTokenProvider`，入口从 Home 扩成 invite/preview，状态机从 4 态扩成 `loading/invalid/preview/joining/inMeeting/ended/error` 七态；
  - base 固定 `/meeting/`，**不随阶段变化**；差异只在 `tokenMode`、入口态与功能开关。
- **不要把本文的 client-side secret 方案写进 `personal_meeting_design.md` 的生产路径**；生产配置必须是 `tokenMode=server`，`apiKey/apiSecret` 不出现在客户端配置里。
- 文档职责：本文负责 Web 客户端实现与 demo 部署；`personal_meeting_design.md` 负责邀请/会议服务端与生产部署。改任一侧时同步另一侧的契约与链接。

### 3.15 测试计划

#### 3.15.1 单元测试（Vitest，Node 环境）

- `token.ts`：
  - 用 `jose.jwtVerify` 验证签名；
  - 断言 `iss`、`sub`、`name`、`video.room/roomJoin/canPublish/canSubscribe/canPublishData`；
  - 断言 `exp - nbf` 合理（TTL + 30s 回拨）。
- `config.ts`：
  - `validateConfig(raw, { isHttps })` 接收注入的 `isHttps`，不依赖全局 `location`；
  - 缺字段/空字符串/非法 URL/`ws` + HTTPS 页面 -> 抛错；
  - 合法配置 -> 默认值正确（`tokenTtlSeconds=600`、`videoCodec='vp8'`、`mediapipeAssetBase=''`、布尔默认值）；
  - `tokenMode=server` 且带 `apiKey/apiSecret` -> 抛错；
  - `loadConfig({ fetchImpl, isHttps })` 用 mock `fetch` 测 404、非 JSON、合法 JSON。
- `identity.ts`：
  - `makeIdentity()` 匹配 `^u_[a-z0-9]{16}$`；
  - 多次调用不重复（或至少极低碰撞）；
  - 不再需要 `sessionStorage`/tab 模拟。
- `Home` 校验函数（导出为纯函数）：
  - 合法/非法 meeting number；大小写归一化（`Design-Review` → `design-review`）；
  - 1/40/41 字符用户名。
- `polyfills`：Node 20+ 自带 `crypto`（WebCrypto）与 `fetch`；`jose` 直接可用。默认 Node 环境即可，不需要 jsdom；如果后续加组件测试，再单独 `environment: 'jsdom'`。
- 需要 `vitest.config.ts`（`test.environment: 'node'`、`include: ['src/**/*.test.ts']`）。

#### 3.15.2 组件测试（可选）

- 若引入 `@testing-library/react` + `jsdom`：Home 表单校验、阶段切换（mock provider）。
- MVP 可以先不做组件测试，靠 E2E + 手测。

#### 3.15.3 E2E（Playwright，建议 P1）

- 启动 Vite dev server + 本地 `livekit-server --dev`（`devkey`/`secret`，`ws://127.0.0.1:7880`）；E2E 优先用本地 server，保持可重复；对实际 demo server 的联调另跑手工用例（host/port 只写本地 `public/config.json`）。
- Chromium 启动参数：
  ```
  --use-fake-ui-for-media-stream
  --use-fake-device-for-media-stream
  --autoplay-policy=no-user-gesture-required
  ```
- 两个 browser context（或两个 tab）：
  1. 打开 `/meeting/`；
  2. 输入同一 room、不同 name；
  3. 通过 PreJoin；
  4. 断言双方参与者 tile 出现、控制栏可见；
  5. 测试 mute/camera/chat/leave。
- 注意：E2E 需要 `public/config.json`（`livekitUrl` 指向测试 server）；用本地 `--dev` 或已确认的专用 demo key，**不要用生产 key**。
- 仓库目前 `.github/` 下只有 CODEOWNERS 和 PR 模板，没有 workflow；E2E 先本地/手动跑。要进 CI 时补 `.github/workflows`（`docs/mono-repo.md` 里列的 ci-mobile/ci-backend 目前并不存在，别假设它们可用）。

#### 3.15.4 手工验收

- 两个浏览器窗口入同一房间，能互相看到/听到。
- demo server 已用**本地 meet.git + Chrome/Safari** 验证可正常入会（WSS/TLS 与基本媒体路径）；本 app 自己的构建完成后，用同一 server/专用 demo key 再复验一次。
- 摄像头/麦克风开关、设备切换、屏幕共享、聊天、参与者列表、设置面板、离开。
- 拒绝摄像头权限 -> 仍可音频入会；拒绝麦克风 -> 仍可观看。
- 刷新 -> 回 Home；不残留 token。
- 错误 meeting number 字符 -> 表单错误；大小写归一化生效。
- 错误 key/server -> 连接失败错误页 + Retry。
- 同一 identity 被顶掉（两个 tab 使用同一 token 或手工造 token）-> 收到 “You joined from another tab or device.”，不是 “You left the meeting”。
- 断网/恢复 -> 看到 `Reconnecting`/`Reconnected`；不可恢复时进错误页可 Retry。
- 只有自己入会 30s -> 出现空房提示。
- 手机 Safari/Chrome：布局、安全区、权限弹窗、音频播放；iOS 26 及以前不显示屏幕共享按钮；iOS 27+ 的共享能力属于 P2，启用后需单独验收。
- 构建闸门：`public/config.json` 含 client secret 时 `npm run build` 失败，`npm run build:demo` 成功。

### 3.16 分阶段实施

| 阶段 | 范围 | 验收 |
|------|------|------|
| P0（骨架） | 构建 spike（MediaPipe + jose + livekit-client）、脚手架、config loader + 构建闸门、token、identity、Home、PreJoin、Room 连接、VideoConference、Leave、DisconnectReason 映射、README | `npm run check` / `npm run build:demo` 通过；闸门在无 `ALLOW_CLIENT_TOKEN=1` 时失败；两人能入会互通；断线/被顶掉有正确文案 |
| P1（会中 parity） | SettingsMenu（camera/mic/speaker）、背景效果（`switchTo`）、KeyboardShortcuts、DebugMode、RecordingIndicator、LowCPUOptimizer、错误/权限 UX、Safari/iPhone 矩阵 | 与 meet 的会中操作对齐；§3.15.4 手测通过；iOS 26 及以前不显示屏幕共享按钮 |
| P2（可选） | E2EE、录制启停、i18n、分享链接/hash 路由、服务端 token provider、邀请/preview/ended 状态、PWA；**iOS/iPadOS 27+ Safari 网页屏幕共享启用**（移除 is-ios CSS/UA 判断、依赖能力检测、iPhone/iPad 27 实机验证） | 按需；生产化时与 `personal_meeting_design.md` §7 对齐 |
| Docs | 本文、`README.md`、Makefile、`.gitignore`、`REPO.md` / `docs/mono-repo.md` 目录更新 | 文档与代码一致；§3.14 的单一实现关系已写入两份文档 |

估算：P0 ~2–3 人日（含构建 spike、两人互通、Safari/iPhone 手测）；P1 ~1.5–2 人日；P2 另计。仅为量级参考，不是承诺。之前 1–1.5 人日的估法偏乐观，主要是移动端/设备竞态/背景效果的验证成本。

### 3.17 风险与已确认决策

#### 风险

| 风险 | 等级 | 说明 / 缓解 |
|------|------|-------------|
| API secret 前端暴露 | **高** | 已确认仅 demo/验证；使用专用 demo key；定期轮换；构建闸门；未来公网前切服务端签发 |
| 共享 key 波及同 server 其他 demo | 中 | key 已确认为 demo 专用（meet demo 同用）；不用于生产；轮换需与 meet 协调；泄露半径限 demo server |
| `dist/config.json` 被传播 | **高** | 构建闸门；`dist` 不进公共 artifact/镜像仓库；部署清单检查 |
| LiveKit TLS/UDP 不可达 | 中 | 可信证书；开放 7881/TCP、50000-60000/UDP 或 TURN/TLS 443 |
| 浏览器安全上下文 | 中 | 必须 HTTPS/localhost；非安全上下文直接提示 |
| MediaPipe/Vite 构建兼容 | 中 | P0 先做 build spike；自托管资源或关闭背景效果；CSP 收紧 |
| Safari/iOS 兼容 | 中 | `getDisplayMedia`/`setSinkId`/vp9 能力差异；按 §3.10 矩阵实测 |
| PreJoin 设备再采集竞态 | 中 | `NotReadableError` 延迟重试一次；macOS Safari/iPhone 必测 |
| React/LiveKit 版本漂移 | 低 | 锁定版本 + lockfile；升级单独验证 |
| 无 E2EE/录制 | 低 | 明确非目标；UI 不提供假按钮 |
| PreJoin 旧用户名覆盖 | 低 | `persistUserChoices={false}`（§3.7） |

#### 已确认决策（2026-11-11，评审后补充）

| # | 问题 | 决策 | 影响 |
|---|------|------|------|
| 1 | 部署范围 | **demo/验证**，不公网 | 允许客户端内置 key pair；dev 警告 + 构建闸门保留；未来公网前必须切服务端 token |
| 2 | 是否保留 PreJoin | **默认关闭**（`showPreJoin=false`） | Home -> Meeting；需要预览/设备选择时设 `showPreJoin=true` 走 Home -> PreJoin -> Meeting |
| 3 | E2EE / 录制 | **MVP 不做** | UI 不提供假按钮；RecordingIndicator 仍保留（显示外部录制状态） |
| 4 | Krisp 降噪 | **本期不做** | 移除 `@livekit/krisp-noise-filter` 依赖、`enableKrispNoiseFilter` 配置、相关 UI 与 CSP 源 |
| 5 | UI 语言 | **英文** | 文案/错误信息全部英文，集中到 `src/messages.ts`，后续可加 i18n |
| 6 | Base path | demo 与生产统一 **`/meeting/`** | 同一套代码、同一 base；阶段差异走 `tokenMode` / 入口态 / 功能开关 |
| 7 | 配置方式 | **运行期 `public/config.json`** | `config.example.json` 放 app 根目录；真实配置 gitignore；构建闸门 |
| 8 | 实现数量 | **`apps/web/meeting` 唯一 Web 实现** | `personal_meeting_design.md` §7 只保留生产契约，不再有第二套目录/技术栈 |
| 9 | identity | **opaque `u_<random>`** | 显示名只放 `name` claim；无 PII、无 storage 边界 |
| 10 | 默认视频编码 | **vp8 + simulcast** | vp9/av1 需显式配置，走 SVC、无 simulcast；Safari 需实测 |
| 11 | Key 归属 | 已确认 `meet.git/.env.local` 是**专用 demo key**（meet demo 同用），本 app 直接复用 | demo 专用；不用于生产；轮换会同时影响 meet demo，需协调 |

**后续再议**：公网部署/服务端 token、E2EE、录制、Krisp、i18n、分享链接、邀请/preview/ended 状态、**iOS/iPadOS 27+ 网页屏幕共享启用**（见 §3.8.11）。若未来要做公网，先把 §3.13.4 的 `HttpTokenProvider` 落地，再改 `tokenMode=server`。

**已验证 / 剩余验证**：

- **已验证**：demo server（livekit-server `v1.13.11`；host/port 只写本地 `public/config.json`）——本地 meet.git 在 Chrome、Safari 均可正常入会，WSS/TLS 与基本媒体路径已通。
- **剩余**：本 app 自己的构建/建连、设备切换、屏幕共享、背景效果、错误/重连等 §3.15.4 矩阵；UDP/TURN 未单独隔离验证（meet.git 入会已覆盖基本媒体路径，需要时可看 LiveKit 日志/抓包）。
- Safari/iOS 的 vp9 协商与 `getDisplayMedia` 行为仍要在 P1 手测里给出结论；桌面 Chrome/Safari 已随 meet.git 验证过基础入会，但不等于本 app 的屏幕共享/背景效果已验证。iOS/iPadOS 27+ 已支持网页屏幕共享，但当前 app 仍按 `is-ios` 隐藏按钮，启用与实机验证列入 P2（见 §3.8.11）。

### 3.18 评审意见处理（2026-11-11）

| 评审项 | 处理位置 |
|--------|----------|
| P0-1 目录/base path 与生产设计冲突 | §3.14 明确唯一实现；`personal_meeting_design.md` §7.1 改为指向本文；base 统一固定 `/meeting/`（§3.11.2） |
| P0-2 复用 meet key 与独立 key 矛盾 | D2/D10、§1.1、§3.13.3：已确认 `.env.local` 是专用 demo key，直接复用；TTL 只限制 JWT 重放（§3.5.5） |
| P0-3 `public/config.json` 进 `dist` | §3.4.3（example 移出 public）、§3.11.2（构建闸门）、§3.12.1/§3.13.3（`dist` 是 secret 材料） |
| P0-4 断线原因未映射 | §3.8.3（`DisconnectReason` 映射）、§3.8.11（场景表）、§4.2.5（`onLeave(message?)`） |
| P0-5 测试环境与 Node/jsdom 矛盾 | §3.15.1：Node 环境 + 注入 `isHttps`/`fetchImpl`；identity 测试改为格式/唯一性 |
| P1-1 BackgroundProcessor 用法 | §3.8.6：单实例 + `mode`/`switchTo`，不再用 name 反推/重建 |
| P1-2 identity 带显示名 | §3.5.4：opaque `u_<random>`；D7 |
| P1-3 VP9 与 simulcast 无效组合 | §3.8.2：默认 vp8；SVC codec 下不配 simulcast；D12 |
| P1-4 index/favicon/title/debug | §3.11.4（`/favicon.svg`、`document.title`）、§3.4.3（`showDebugPanel=false`） |
| P1-5 PreJoin 竞态/iOS 屏幕共享/扬声器 | §3.7、§3.8.3（`NotReadableError` 重试）、§3.8.11、§3.10（矩阵） |
| P1-6 快捷键冲突 | §3.8.7：`Ctrl/Cmd+D`/`E`，忽略输入框与 `event.repeat`；`Shift+D` 仍只由 `DebugMode` 注册，避免双 toggle |
| P1-7 TTL/lowCPU deps/HttpTokenProvider 校验 | §3.4.2/§3.4.4、§3.8.10、§4.2.3 |
| P1-8 meeting number 语义/空房 | §3.6.2/§3.6.3：trim+lowercase、30s 空房提示 |
| P2 文案/文件清单/重复代码/测试/部署/文档 | §3.9、§3.3、§4.2.6、§3.15、§3.12、§3.16、§3.17 |

---

## 4. 附录

### 4.1 `meet.git` → `apps/web/meeting` 文件映射

| meet.git | 新 app | 处理 |
|----------|--------|------|
| `app/page.tsx` | `src/components/Home.tsx` | 重写：双 tab -> 单表单 |
| `app/rooms/[roomName]/PageClientImpl.tsx` | `src/App.tsx` + `src/components/PreJoinStage.tsx` + `src/components/MeetingRoom.tsx` | 拆成阶段状态机 |
| `app/api/connection-details/route.ts` | `src/credentials/clientTokenProvider.ts` + `src/token.ts` | 服务端 -> 浏览器 `jose` |
| `app/custom/*` | 不迁移 | 首页不再支持自定义 server/token |
| `app/layout.tsx` | `index.html` + `src/main.tsx` | metadata/Toaster/styles |
| `lib/SettingsMenu.tsx` | `src/components/SettingsMenu.tsx` | 去掉 recording tab |
| `lib/CameraSettings.tsx` | `src/components/CameraSettings.tsx` | 新 `BackgroundProcessor` API；Vite 资源导入 |
| `lib/MicrophoneSettings.tsx` | `src/components/MicrophoneSettings.tsx` | 去掉 Krisp；仅保留麦克风/设备 |
| `lib/KeyboardShortcuts.tsx` | `src/components/KeyboardShortcuts.tsx` | 改用 `Ctrl/Cmd+D`/`E`，忽略输入框与 `event.repeat`（§3.8.7） |
| `lib/Debug.tsx` | `src/components/DebugMode.tsx` | 去掉 Datadog |
| `lib/RecordingIndicator.tsx` | `src/components/RecordingIndicator.tsx` | 原样 |
| `lib/usePerfomanceOptimiser.ts` | `src/hooks/useLowCPUOptimizer.ts` | 原样（改名） |
| `lib/useSetupE2EE.ts` | 不迁移 | MVP 不做 |
| `lib/client-utils.ts` | `src/identity.ts` / Home | 用 `crypto.getRandomValues` |
| `lib/getLiveKitURL.ts` | 不迁移 | config 直接给最终 URL |
| `lib/types.ts` | `src/credentials/types.ts` | `ConnectionDetails` 保留 |
| `styles/globals.css` | `src/styles/globals.css` | 精简 |
| `styles/Home.module.css` | `src/styles/home.module.css` | 改单卡片 |
| `styles/SettingsMenu.module.css` | `src/components/SettingsMenu.module.css` | 原样 |
| `styles/Debug.module.css` | `src/components/DebugMode.module.css` | 原样 |
| `public/background-images/*` | `src/assets/backgrounds/*` | 由 Vite 打包（注意 Unsplash 来源） |

### 4.2 关键代码骨架

#### 4.2.1 `src/credentials/types.ts`

```ts
export type JoinRequest = {
  roomName: string
  participantName: string
}

export type ConnectionDetails = {
  serverUrl: string
  roomName: string
  participantName: string
  participantToken: string
}

export interface CredentialProvider {
  getConnectionDetails(request: JoinRequest): Promise<ConnectionDetails>
}
```

#### 4.2.2 `src/credentials/clientTokenProvider.ts`

```ts
import type { ClientTokenConfig } from '../config'
import { makeIdentity } from '../identity'
import { createParticipantToken } from '../token'
import type { ConnectionDetails, CredentialProvider, JoinRequest } from './types'

export class ClientTokenProvider implements CredentialProvider {
  constructor(private readonly config: ClientTokenConfig) {}

  async getConnectionDetails({
    roomName,
    participantName,
  }: JoinRequest): Promise<ConnectionDetails> {
    const name = participantName.trim() || 'Guest'
    const identity = makeIdentity()
    const participantToken = await createParticipantToken({
      apiKey: this.config.apiKey,
      apiSecret: this.config.apiSecret,
      roomName,
      identity,
      displayName: name,
      ttlSeconds: this.config.tokenTtlSeconds,
    })
    return {
      serverUrl: this.config.livekitUrl,
      roomName,
      participantName: name,
      participantToken,
    }
  }
}
```

#### 4.2.3 `src/credentials/httpTokenProvider.ts`（预留）

```ts
import type { ConnectionDetails, CredentialProvider, JoinRequest } from './types'

type TokenResponse = Partial<ConnectionDetails> & {
  // personal_meeting_design.md §7.2 的生产字段
  livekit_url?: string
  token?: string
}

function parseConnectionDetails(raw: TokenResponse, req: JoinRequest): ConnectionDetails {
  const serverUrl = raw.serverUrl ?? raw.livekit_url ?? ''
  const participantToken = raw.participantToken ?? raw.token ?? ''
  if (!/^wss?:\/\//.test(serverUrl)) {
    throw new Error('Token endpoint returned an invalid serverUrl')
  }
  if (!participantToken) {
    throw new Error('Token endpoint returned an empty token')
  }
  if (raw.roomName && raw.roomName !== req.roomName) {
    throw new Error('Token endpoint returned a mismatched roomName')
  }
  if (raw.participantName && raw.participantName !== req.participantName) {
    throw new Error('Token endpoint returned a mismatched participantName')
  }
  return {
    serverUrl,
    roomName: req.roomName,
    participantName: req.participantName,
    participantToken,
  }
}

export class HttpTokenProvider implements CredentialProvider {
  constructor(private readonly endpoint: string) {}

  async getConnectionDetails(request: JoinRequest): Promise<ConnectionDetails> {
    const res = await fetch(this.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      credentials: 'same-origin',
    })
    if (!res.ok) {
      throw new Error(`Token endpoint failed: HTTP ${res.status}`)
    }
    return parseConnectionDetails((await res.json()) as TokenResponse, request)
  }
}
```

> 预留实现：生产契约的请求体是 `{ invite_token, display_name, passcode? }`（不是 `roomName/participantName`），响应是 `{ livekit_url, token, meeting, role }`。实现 server 模式时把 `JoinRequest` 扩成 union（client join vs invite join），不要直接把当前类型复用到生产端点。上面的 `parseConnectionDetails` 同时接受 camelCase 与生产 snake_case 两种响应，避免把任意 JSON 当 `ConnectionDetails`。

#### 4.2.4 `src/credentials/index.ts`

```ts
import type { MeetingConfig } from '../config'
import { ClientTokenProvider } from './clientTokenProvider'
import { HttpTokenProvider } from './httpTokenProvider'
import type { CredentialProvider } from './types'

export function createCredentialProvider(config: MeetingConfig): CredentialProvider {
  if (config.tokenMode === 'server') {
    if (!config.tokenEndpoint) {
      throw new Error('tokenMode=server requires tokenEndpoint')
    }
    return new HttpTokenProvider(config.tokenEndpoint)
  }
  return new ClientTokenProvider(config)
}
```

#### 4.2.5 `App.tsx` 阶段骨架

```tsx
type JoinParams = {
  roomName: string
  participantName: string
  choices: LocalUserChoices
}

type Stage =
  // roomName/participantName 用于 Back/Leave 后回填 Home（Home 组件会卸载，不能依赖它自己的 state）
  | { kind: 'home'; roomName?: string; participantName?: string }
  | { kind: 'prejoin'; roomName: string; participantName: string }
  | { kind: 'meeting'; details: ConnectionDetails; choices: LocalUserChoices }
  | { kind: 'error'; message: string; retry: JoinParams }

const DEFAULT_CHOICES: LocalUserChoices = {
  username: '',
  videoEnabled: true,
  audioEnabled: true,
  videoDeviceId: '',
  audioDeviceId: '',
}

export default function App() {
  const [config, setConfig] = useState<MeetingConfig | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [stage, setStage] = useState<Stage>({ kind: 'home' })
  const [provider, setProvider] = useState<CredentialProvider | null>(null)

  useEffect(() => {
    loadConfig()
      .then((cfg) => {
        setConfig(cfg)
        setProvider(createCredentialProvider(cfg))
      })
      .catch((err: Error) => setConfigError(err.message))
  }, [])

  useEffect(() => {
    if (config) document.title = config.appTitle
  }, [config])

  if (configError) return <ConfigError message={configError} />
  if (!config || !provider) return <Loading />

  const enterMeeting = async (params: JoinParams) => {
    try {
      const details = await provider.getConnectionDetails({
        roomName: params.roomName,
        participantName: params.participantName,
      })
      setStage({ kind: 'meeting', details, choices: params.choices })
    } catch (err) {
      setStage({
        kind: 'error',
        message: (err as Error).message,
        retry: params,
      })
    }
  }

  const handleHomeJoin = async (roomName: string, participantName: string) => {
    if (config.showPreJoin) {
      setStage({ kind: 'prejoin', roomName, participantName })
      return
    }
    // showPreJoin=false：跳过 PreJoin，用默认设备选择直接入会
    await enterMeeting({
      roomName,
      participantName,
      choices: { ...DEFAULT_CHOICES, username: participantName },
    })
  }

  return (
    <>
      <Toaster />
      {stage.kind === 'home' && (
        <Home
          config={config}
          defaultRoomName={stage.roomName}
          defaultParticipantName={stage.participantName}
          onJoin={handleHomeJoin}
        />
      )}
      {stage.kind === 'prejoin' && (
        <PreJoinStage
          config={config}
          provider={provider}
          roomName={stage.roomName}
          participantName={stage.participantName}
          onBack={() =>
            setStage({
              kind: 'home',
              roomName: stage.roomName,
              participantName: stage.participantName,
            })
          }
          onJoined={(details, choices) =>
            setStage({ kind: 'meeting', details, choices })
          }
        />
      )}
      {stage.kind === 'meeting' && (
        <MeetingRoom
          key={stage.details.participantToken}
          config={config}
          details={stage.details}
          choices={stage.choices}
          onLeave={(message) => {
            toast(message ?? 'You left the meeting')
            setStage({
              kind: 'home',
              roomName: stage.details.roomName,
              participantName: stage.details.participantName,
            })
          }}
          onError={(message, retry) => setStage({ kind: 'error', message, retry })}
        />
      )}
      {stage.kind === 'error' && (
        <ErrorView
          message={stage.message}
          onRetry={() => void enterMeeting(stage.retry)}
          onBack={() =>
            setStage({
              kind: 'home',
              roomName: stage.retry.roomName,
              participantName: stage.retry.participantName,
            })
          }
        />
      )}
    </>
  )
}
```

#### 4.2.6 `MeetingRoom.tsx`

完整实现见 §3.8.3（**唯一完整代码**；包含 `DisconnectReason` 映射、`NotReadableError` 重试、cleanup `disconnect`、设备错误处理）。§4.2 不再复制一份，避免两处漂移。

要点回顾：

- `room = useMemo(() => new Room(roomOptionsFrom(config.videoCodec, choices)), [])`；组件每次入会重新挂载。
- `onLeave(message?)` / `onError(message, retry)` 用 ref 固定，避免 effect 重跑导致重连。
- 会中组合 `<KeyboardShortcuts /> + <VideoConference ... /> + <DebugMode /> + <RecordingIndicator /> + <ConnectionStateToast />`；`SettingsComponent` 由 `config.enableSettingsMenu` 控制，`DebugMode` 由 `config.showDebugPanel` 控制。
- 连接失败才进 `error` 阶段；单设备失败只 toast；`ROOM_DELETED`/`PARTICIPANT_REMOVED`/`DUPLICATE_IDENTITY` 回 Home，不 Retry。

### 4.3 版本来源

- `meet.git` 版本：`~/github/meet.git/package.json`；`livekit-server-sdk` claims 行为：`node_modules/livekit-server-sdk/dist/AccessToken.js`、`dist/grants.js`。
- LiveKit server：demo 为配置中的 `wss://<livekit-host>:<port>`（host/port 只写本地 `public/config.json`），版本 `livekit-server v1.13.11`（2026-11-11 需求方确认）；端口/TURN 要求见 `personal_meeting_design.md` §3.2。
- `@livekit/components-react` 类型：`node_modules/@livekit/components-react/dist/prefabs/PreJoin.d.ts`、`VideoConference.d.ts`；`LocalUserChoices`：`@livekit/components-core/dist/persistent-storage/user-choices.d.ts`；合并顺序实现：`components-core/dist/index.mjs` 的 `loadUserChoices`。
- 背景处理器 API/资源路径：`node_modules/@livekit/track-processors/dist/src/index.d.ts`（`BackgroundProcessor`、`BackgroundProcessorWrapper.mode/switchTo`）、`dist/index.mjs`；MediaPipe 默认 URL 同文件。
- 视频编码/SVC：`livekit-client@2.23.0` `dist/src/room/track/options.d.ts`（SVC codec 下 `videoSimulcastLayers` 无效）、`dist/src/index.d.ts`（`isSVCCodec`）。
- Base/favicon：Vite 8 `build-html` 的 `toOutputPublicFilePath`（public 资源自动加 `base` 前缀）。
- 当前仓库脚手架：`apps/web/mylife/package.json`、`vite.config.ts`、`tsconfig.json`、`index.html`、`src/main.tsx`。
- 静态托管先例/现状：`services/app/application.py`、`docs/webroot_feature.md` §2.1。
- 既有 meeting 设计：`docs/personal_meeting_design.md` §3（LiveKit 调研）、§7（生产入会契约；Web 实现以本文为准）。

### 4.4 参考资料

- `~/github/meet.git`（LiveKit Meet，Apache-2.0）
- LiveKit JS SDK / Components：`livekit-client@2.23.0`、`@livekit/components-react@2.9.24`
- LiveKit Access Token grants 文档（roomJoin/canPublish/canSubscribe/canPublishData）
- `jose` JWT 库（WebCrypto，HS256）
- `@livekit/track-processors` 背景处理器文档（`node_modules/@livekit/track-processors/README.md`）

> 许可：`meet.git` 与 LiveKit 组件均为 Apache-2.0。若直接复制大段源码/样式，保留版权与许可声明；本设计主要是复用公开 API 与实现模式。

---

*本文为设计文档，未修改任何代码。实施时请按 §3.16 分阶段提交，并在每个阶段更新本文状态。*

**修订记录**

- 2026-11-11：设计确认。
- 2026-11-11：确认 demo 环境：LiveKit server `v1.13.11`（host/port 只写本地 `public/config.json`）；`meet.git/.env.local` 是专用 demo key，继续复用。
- 2026-11-11：WSS 可达性已用本地 meet.git 验证，Chrome/Safari 均可正常入会。
- 2026-11-11：确认 demo/生产统一 `base=/meeting/`、同一份代码、不同开发测试阶段；删除 `MEETING_BASE` 环境开关；生产邀请 URL 同步改为 `/meeting/#t=`。
- 2026-11-11（评审后修订）：确认 `apps/web/meeting` 是唯一 Web 实现；demo/验证阶段允许客户端内置 key pair，未来切服务端签发；修正 BackgroundProcessor（`mode`/`switchTo`）、identity（opaque）、默认 codec（vp8）、构建闸门、`DisconnectReason` 映射、测试环境、CSP/缓存/部署红线，以及与 `personal_meeting_design.md` §7 的关系。
- 2026-11-11（二次评审）：修正 Caddy `root` 与 `/meeting/` 前缀不匹配、BackgroundProcessor「先选模式后开摄像头」顺序、PreJoin Back 回填 Home、SettingsComponent 的 config 注入、`Shift+D` 双注册、MediaPipe 自托管配置 `mediapipeAssetBase`、Caddy CSP/缓存示例、`enable` 回调类型、`@types/node`、`HttpTokenProvider` 回显校验。
- 2026-11-11：`apps/web/meeting` P0/P1 实施完成（骨架、token/identity、Home/PreJoin/Meeting、Settings/背景效果、快捷键、Debug、空房提示、iOS 屏幕共享隐藏）；23 个单测通过；Chrome 桌面完成 camera/mic、静音/摄像头切换、背景效果、聊天、signal/full reconnect、刷新/错误页的完整 E2E；屏幕共享/设备切换/Safari/iOS 待手测。详见 [`docs/webmeeting_impl_plan.md`](webmeeting_impl_plan.md)。
- 2026-11-11：补充 §3.8.6「实现机制与厂商」：背景虚化由发送端 `BackgroundProcessor`（WebGL2 + MediaPipe `ImageSegmenter` + `selfie_segmenter.tflite`）完成；模型由 Google 提供，非 LiveKit 专有；记录质量/性能/可替换性/合规等边界与未来加强方向。
- 2026-11-11（实现评审修复）：背景 processor 改为用户选择效果后才懒加载，避免入会即下载 MediaPipe WASM/模型；缓存 `supportsBackgroundProcessors()`；full reconnect 后通过 `LocalTrackPublished` 幂等重挂；修复 `body overflow:hidden` 下 Home/PreJoin/Error 页面在短视口不可滚动；处理 `prioritizePerformance()` 的 rejected promise。
- 2026-11-11（iOS 屏幕共享显示修复）：`.is-ios [data-lk-source='screen_share']` 会同时匹配 LiveKit 远端 ParticipantTile/VideoTrack，导致 iPhone 端把别人共享的画面整块隐藏；选择器收窄为 `.is-ios .lk-control-bar [data-lk-source='screen_share']`，只隐藏本机屏幕共享按钮。
- 2026-11-11（Leave 确认）：控制栏 Leave 点击改为捕获阶段拦截并弹确认框，Cancel/Esc/遮罩关闭不离开；确认后才 `room.disconnect()`。避免误触立即退会。
- 2026-11-11（P2 调研）：确认 Safari 27 / iOS 27 / iPadOS 27 起支持网页 `getDisplayMedia`；当前 `is-ios` CSS 会在 27+ 误隐藏共享按钮。列入 P2 增强：移除 UA 隐藏、依赖 LiveKit 能力检测，并做 iPhone/iPad 27 实机验证（详细证据与步骤见 §3.8.11）。
- 2026-11-13：需求变更，`showPreJoin` 默认改为 **false**（Home 直接入会）；PreJoin 保留为可选步骤，配置 `showPreJoin=true` 时启用。`config.example.json`、单测和本文档同步。
- 2026-11-14：部署章节补充自托管 MediaPipe 的压缩与缓存要求：`vendor/mediapipe/*` 增加 `immutable` 长缓存；说明 Caddy 默认压缩 wasm、nginx 需显式 `gzip_types application/wasm`、`Content-Type: application/wasm` 校验，以及预加载后的传输体积（约 2.0 MB）/解码体积（约 9.4 MB）。
- 2026-11-14：明确 `@livekit/track-processors@0.8.1` 的 disabled 行为：`switchTo({ mode: 'disabled' })` 不会停止分割/模糊处理；None 改为 `stopProcessor()` 销毁并恢复原始 track；新增 WebGL context loss 兜底（停止处理、回 None、提示），避免持续黑帧。
- 2026-11-14：修复 Huawei Browser 竖屏 Settings 未全宽：生产构建设置 `build.cssTarget` 包含 `chrome99`，防止 Lightning CSS 输出 `(width<=640px)` range 语法（Chromium 99 解析为 `not all` 并丢弃竖屏全宽规则）；实测 Huawei Browser 14 竖屏 Settings 已覆盖全屏。
- 2026-11-14：修复 Safari（Mac/iPad/iPhone）Blur→None 后本地预览黑屏：`stopProcessor()` 在同一 MediaStream 内换回原始 track，Safari 不重绘；停止后重置命中原始 track 的 `<video>` 的 `srcObject` 并重新 `play()`。Chrome/Firefox 不受影响。
