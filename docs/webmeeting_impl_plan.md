# Web Meeting App 实施与测试计划

> **依据**：[`docs/webmeeting_design.md`](webmeeting_design.md)
> **目标**：在 `apps/web/meeting/` 实现 React + Vite + LiveKit SPA；demo/验证阶段允许浏览器内置 key pair，未来切 `tokenMode=server`
> **基线**：`dev/meeting`（工作树）
> **状态**：P0 完成；P1 主体完成，Chrome 桌面媒体 E2E 通过；Safari/iOS、屏幕共享、设备切换待手测
> **原则**：先 P0 可运行骨架，再 P1 会中 parity，再 P2 可选能力；每一步都可构建、可测试、可回滚。

---

## 1. 阶段总览

| 阶段 | 内容 | 状态 | 验收 |
|------|------|------|------|
| P0 | 脚手架、config + 构建闸门、token/identity、Home、PreJoin、App 状态机、Room + VideoConference、ErrorView、单元测试 | ✅ 完成 | `npm run check`、`npm test`、`npm run build:demo` 通过；两人已通过 demo server 入会；构建闸门生效 |
| P1 | SettingsMenu（camera/mic/speaker）、背景效果、KeyboardShortcuts、DebugMode、RecordingIndicator、LowCPU、Empty hint、iOS 屏幕共享隐藏 | ✅ 主体完成 | 会中操作与 meet 对齐；Chrome 桌面已冒烟；Safari/iPhone 与设备相关项待手测 |
| P2 | E2EE、录制启停、i18n、分享链接、服务端 token provider、邀请/preview/ended、PWA；背景效果增强（模型质量/性能/可替换性，见设计文档 §3.8.6） | ⏳ 未开始 | 与生产契约对齐 |

---

## 2. P0：可运行骨架

### 2.1 脚手架与配置

- [x] 新建 `apps/web/meeting/`
- [x] `package.json`（依赖/脚本/engines）
- [x] `tsconfig.json`、`vite.config.ts`、`vitest.config.ts`
- [x] `index.html`、`public/favicon.svg`
- [x] `config.example.json`（app 根目录）
- [x] `README.md`
- [x] 根 `.gitignore` 增加 `apps/web/meeting/public/config.json`
- [x] 根 `Makefile` 增加 `meeting-install/dev/build/build-demo/check/test`

### 2.2 配置、构建闸门、token、identity

- [x] `src/config.ts`：类型 + `loadConfig` + `validateConfig`
- [x] `src/token.ts`：`jose` HS256 签 LiveKit JWT
- [x] `src/identity.ts`：opaque `u_<random16>`
- [x] `src/credentials/*`：`ClientTokenProvider`、`HttpTokenProvider` 预留、`createCredentialProvider`
- [x] `vite.config.ts` 构建闸门：
  - client secret 且无 `ALLOW_CLIENT_TOKEN=1` -> 失败
  - server 模式带 secret -> 失败
  - `build:demo` 警告并允许

### 2.3 UI 骨架

- [x] `src/main.tsx`、`src/styles/globals.css`
- [x] `src/messages.ts`
- [x] `src/App.tsx`：Home / PreJoin / Meeting / Error 四态
- [x] `src/components/Home.tsx`：会议号 + 用户名、随机号、校验、IME、loading、默认值回填
- [x] `src/components/PreJoinStage.tsx`：PreJoin（`persistUserChoices=false`）、Back、joining state、错误处理
- [x] `src/components/ConfigError.tsx`、`Loading.tsx`
- [x] `src/components/ErrorView.tsx`：Retry/Back、脱敏、loading
- [x] `src/components/MeetingRoom.tsx`：
  - Room options（默认 vp8 + simulcast；vp9/av1 走 SVC）
  - `RoomEvent.Connected` / `Disconnected`(reason) / `MediaDevicesError`
  - `NotReadableError` 延迟重试一次
  - `ConnectionStateToast`、cleanup `disconnect`
- [x] `src/hooks/useLowCPUOptimizer.ts`（含上游 deps 修复）

### 2.4 P0 测试

- [x] `src/token.test.ts`：JWT 签名与 claims
- [x] `src/config.test.ts`：默认值、非法值、server 模式拒绝 secret、`loadConfig` 404/非 JSON
- [x] `src/identity.test.ts`：格式 `u_[a-z0-9]{16}`、唯一性
- [x] `src/validation.test.ts`：会议号 trim+lowercase、长度、用户名 1/40/41
- [x] `src/credentials/httpTokenProvider.test.ts`：camelCase/snake_case/回显校验
- [x] `npm run check`：通过
- [x] `npm test`：5 个文件 / 23 个测试通过
- [x] 构建闸门：
  - client secret + `npm run build` -> 失败（已实测）
  - `npm run build:demo` -> 成功（已实测）
  - server 模式无 secret -> 成功（已实测）
  - server 模式带 secret -> 失败（已实测）
- [x] 浏览器冒烟（Chromium）：
  - Home 校验、大小写归一化
  - PreJoin、Back 回填
  - 两个 browser context 通过 demo server 入同一房间，双方参与者 tile 可见
  - Leave -> Home + toast + 回填
  - 错误 server -> ErrorView + Retry/Back
  - Settings 打开/关闭，camera/mic/speaker/背景效果 UI 可见
  - DebugMode Shift+D 显示 room/permissions/identity
  - 空房 30s hint 显示
  - Chat 打开/发送无报错

---

## 3. P1：会中 parity

- [x] `SettingsMenu`：media tab（camera/mic/speaker），去掉 recording tab
- [x] `CameraSettings`：预览、TrackToggle、设备选择、背景效果（`BackgroundProcessor` 单实例 + `switchTo` + `mediapipeAssetBase`）
- [x] `MicrophoneSettings`：开关 + 设备选择；不引入 Krisp
- [x] `KeyboardShortcuts`：`Ctrl/Cmd+D/E`；忽略输入框/repeat；`Shift+D` 只由 DebugMode 注册
- [x] `DebugMode`：Shift+D、输入框忽略、room/track/bitrate、无 Datadog
- [x] `RecordingIndicator`：`useIsRecording` toast + 红框
- [x] iOS 屏幕共享：根节点 `is-ios` class；CSS 只隐藏控制栏按钮（`.is-ios .lk-control-bar [data-lk-source='screen_share']`），不隐藏远端 ParticipantTile；iPhone 实机确认可显示他人共享
- [x] Leave 确认：捕获阶段拦截 `.lk-disconnect-button`，弹窗确认；Cancel/Esc/遮罩取消不离开
- [x] 空房提示：30s 内 `room.numParticipants <= 1`
- [x] 浏览器矩阵：Chrome（桌面）已完成摄像头/麦克风/聊天/重连的完整 E2E；Firefox、Safari macOS、iOS Safari、Android Chrome 待手测
- [x] 背景效果实际帧处理：Blur / Desk 均进入 `background-blur` / `virtual-background`，本地与远端视频保持 live
- [ ] 屏幕共享实际发布：自动化 Chromium 的屏幕选择器不可用（按钮变 disabled）；需桌面浏览器手测
- [ ] 设备切换：设备菜单 UI 可见，实际切换待手测
- [ ] `NotReadableError` 真实复现（Safari/iPhone）
- [ ] RecordingIndicator 真实 Egress 状态（当前无录制）

---

## 4. 测试矩阵

### 4.1 自动化

| 层级 | 工具 | 内容 | 状态 |
|------|------|------|------|
| 单元 | Vitest (Node) | token/config/identity/validation/http parser | ✅ 23 passed |
| 构建 | Vite | `npm run check`、`npm run build:demo` | ✅ |
| 闸门 | Vite plugin | client secret 无 ALLOW 失败；server 模式带 secret 失败 | ✅ |
| E2E | Playwright MCP (Chromium) | 两 context 入会、camera/mic/背景/chat/重连/leave/error/settings/debug/empty hint | ✅ Chrome 桌面完整媒体 E2E |

### 4.2 手工验收

- [x] 两个浏览器窗口入同一房间，参与者列表互相可见；摄像头/麦克风实际采集（本地 1280x720，远端订阅 live；simulcast 层出现 960x540）
- [x] 麦克风静音/取消静音、摄像头关闭/打开，远端 publication 的 `muted` 状态同步
- [x] 聊天：Alice 发送，Bob 收到（含未读 badge 与消息列表）
- [x] 背景效果：Blur / Desk 实际进入 `background-blur` / `virtual-background`，远端视频保持 live
- [ ] 设备切换（摄像头/麦克风/扬声器菜单实际切换）
- [ ] 屏幕共享实际发布（自动化 Chromium 的屏幕选择器不可用）
- [ ] 拒绝摄像头 -> 仍可音频入会；拒绝麦克风 -> 仍可观看
- [x] 刷新 -> 回 Home；`window.__lk_room` 清空、video 元素归零、媒体释放
- [x] 非法会议号/用户名 -> 表单错误；大小写归一化
- [x] 错误 key/server -> 错误页 + Retry/Back（用不可达 WSS 实测）
- [x] 重连：`room.simulateScenario('signal-reconnect')` 与 `'full-reconnect'` 后 state 回到 connected，本地 camera/mic 与远端订阅保持
- [x] 只有自己 30s -> 空房提示
- [ ] 手机 Safari/Chrome：布局、安全区、权限、音频播放；iOS 无屏幕共享按钮
- [x] 构建闸门行为

### 4.3 媒体 E2E 记录（2026-11-11）

环境：Vite dev `http://127.0.0.1:5174/meeting/`、Chromium（Chrome/156 UA）、真实 demo LiveKit server；两个 browser context（Alice / Bob），camera/mic 权限已授予。

| 项 | 结果 |
|----|------|
| PreJoin 预览 | Alice / Bob 均为 `videoWidth=1280, videoHeight=720, readyState=4`，live video track |
| 入会后本地媒体 | Alice/Bob `isCameraEnabled=true`、`isMicrophoneEnabled=true`；camera+microphone publication 均 live |
| 远端媒体 | 双方能看到对方 camera/microphone publication，`subscribed=true`；远端视频层包含 960x540 simulcast |
| 麦克风静音 | Bob 静音后本地 `isMicrophoneEnabled=false`、mic publication `muted=true`；Alice 侧远端 mic `muted=true` |
| 摄像头关闭 | Bob 关闭后本地 `isCameraEnabled=false`、camera publication `muted=true`；Alice 侧远端 camera `muted=true` |
| 背景效果 | Bob 选择 Blur -> processor `mode=background-blur`；Desk -> `mode=virtual-background`；本地/远端视频均保持 live |
| 聊天 | Alice 发送 “hello from Alice E2E”，Bob 收到未读 badge 与消息列表 |
| 重连 | `simulateScenario('signal-reconnect')` / `'full-reconnect'` 后 `room.state=connected`，本地 camera/mic 与远端订阅恢复 |
| 刷新 | Home 重新出现，`window.__lk_room=undefined`，video 元素 0，媒体释放 |
| 屏幕共享 | 自动化 Chromium 中按钮最终 disabled，无法完成系统屏幕选择；需桌面浏览器手测 |
| 设备切换 | 设备菜单 UI 存在；自动化未完成实际切换 |
| HMR | 已入会状态下热更新 LiveKit 组件会出现一次 `Element not part of the array: ..._camera_placeholder`；刷新后消失，生产无 HMR |

### 4.4 实现评审修复（2026-11-11）

| 问题 | 修复 | 验证 |
|------|------|------|
| 入会即加载 MediaPipe（`BackgroundProcessor` 在 camera track 出现时就 init，约 9 MB WASM + 模型） | 仅在用户选择 Blur/虚拟背景后才创建并挂载 processor；`None` 不加载 | Chrome 实测：入会并开摄像头后无 MediaPipe 请求；点击 Blur 后才出现 `vision_wasm_internal.wasm` 与 `selfie_segmenter.tflite` |
| 每次渲染调用 `supportsBackgroundProcessors()`，重复创建 WebGL2 context | 模块级缓存结果 | 类型检查/构建通过 |
| full reconnect / 摄像头重发布后 processor 可能丢失 | 监听 `LocalTrackPublished`，用 revision 触发幂等重挂（`getProcessor()` 比对，避免重复 init） | 代码审查；待真实 full reconnect + 背景效果联合手测 |
| `body { overflow:hidden }` 下 `.page` 使用 `min-height`，短视口无法滚动、内容被裁切 | `.page` 改为 `height:100vh/100dvh` + `overflow-y:auto` | 375×320 下 Home/PreJoin 可滚动，Join 按钮可达 |
| iOS 端看不到他人屏幕共享：`.is-ios [data-lk-source='screen_share']` 同时匹配了 LiveKit 的 ParticipantTile/VideoTrack，整块远端屏幕共享 tile 被隐藏 | 选择器收窄为 `.is-ios .lk-control-bar [data-lk-source='screen_share']`，只隐藏控制栏的共享按钮 | 已按 LiveKit `useParticipantTile`/`useTrackToggle` 源码核对；iPhone 实机确认他人共享可显示 |
| 误点控制栏 Leave 立即退会 | 捕获阶段拦截 `.lk-disconnect-button`，弹 `LeaveConfirmDialog`；Cancel / Esc / 点遮罩取消，确认后才 `room.disconnect(true)` | Chrome E2E：弹窗出现且仍在会中；Cancel/Esc 保持会议；确认后回 Home、video 元素归零 |
| `track.prioritizePerformance()` 返回 Promise 但未处理 rejection | `void ... .catch(...)` | 类型检查通过 |

---

## 5. 实施顺序与提交策略

1. ✅ 脚手架 + 配置/构建闸门
2. ✅ token/identity/credentials + 单测
3. ✅ Home/PreJoin/App/ErrorView
4. ✅ MeetingRoom + VideoConference + 测试
5. ✅ P0 构建/浏览器冒烟
6. ✅ P1 SettingsMenu/背景/快捷键/Debug/Recording/LowCPU/Empty hint/iOS 隐藏
7. ⏳ P1 浏览器矩阵 + 设备相关手测
8. ⏳ P2 按需

每个阶段单独提交/报告，避免把 P0 和 P1 混在一个大变更里。

---

## 6. 当前进度

| 日期 | 阶段 | 状态 | 备注 |
|------|------|------|------|
| 2026-11-11 | 计划 | 完成 | 本文 |
| 2026-11-11 | P0 | 完成 | 23 单测 + demo server 双人入会 + 构建闸门 |
| 2026-11-11 | P1 | 主体完成 | Settings/背景/快捷键/Debug/Recording/LowCPU/空房提示/iOS 隐藏；Chrome 桌面媒体 E2E（camera/mic/背景/聊天/重连）通过；屏幕共享/设备切换/Safari/iOS 待手测 |
| 2026-11-11 | P2 | 未开始 | 按需 |

### 本地测试配置

- `apps/web/meeting/public/config.json` 已写入本地 demo 值（来自 `~/github/meet.git/.env.local`），**该文件被 `.gitignore` 忽略，不提交**。
- 测试用 `npm run dev`，地址 `http://127.0.0.1:5174/meeting/`。
- `apps/web/meeting/dist/` 测试后已删除；demo 构建用 `npm run build:demo`，产物含 secret，不要提交/上传。

### 已知非阻塞问题

- Vite HMR 在**已入会状态下热更新** LiveKit 组件时，控制台可能出现一次 `Element not part of the array: ..._camera_placeholder`；这是开发期 HMR 重挂载产物，页面刷新后消失，生产构建无 HMR。
- Chrome 控制台偶见 LiveKit 的 `error waiting for media permissons` warning；本次 camera/mic 采集与远端订阅均正常，属于非阻塞告警。
- 大 chunk 约 1 MB（LiveKit + MediaPipe）；P2 可考虑动态 import 背景效果/组件拆分。
