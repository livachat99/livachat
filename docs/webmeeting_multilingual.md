# Web Meeting App 多语言（15 locales，不含阿拉伯语）分析与设计

> **状态**：第一阶段（Homepage、ErrorView、LocalizedVideoConference、LocalizedControlBar）已实施（2026-11-16），待效果 review；另已修复 Android 应用内 WebView 的语言桥。全量方案尚未完成。
> **基线**：2026-11-15，`dev/meeting @ efef2c6`；`apps/web/meeting`。
> **范围**：`apps/web/meeting` 的用户可见 UI、LiveKit 预制 UI、客户端格式化、无障碍与文本膨胀；Android 仅新增 WebView locale 传递（`?lang=`）；不改 iOS、LivaChat server/BFF、LiveKit server、协议与数据库。
> **目标语言**：**15 个 canonical locale**（English、简体/繁體中文、日、韩、俄、德、法、意、西、葡、印地、印尼、泰、越）；Web 不支持阿拉伯语 `ar`。
> **依据**：直读 `apps/web/meeting`、`@livekit/components-react@2.9.24`、`livekit-client@2.23.0`、Android `AppLanguage.kt`/`locales_config.xml`/`values*/strings.xml`、iOS `AppLocale.swift`/`Localizable.xcstrings`。文中标 **事实** 的内容可在对应文件核对；标 **设计/建议** 的内容为设计决策，第一阶段已实施部分见 §0.3。
> **关联文档**：`docs/webmeeting_design.md`（其中 D9「MVP 不做 i18n」由本文更新为 P2 设计）、`docs/webmeeting_impl_plan.md`（P2 i18n）、`docs/android_multilingual.md`、`docs/ios_multilingual.md`。
> **2026-11-15 决策**：Web 不支持阿拉伯语 `ar`（15 locale）；DebugMode 不翻译；翻译使用机器翻译；其余按本文推荐。
> **2026-11-16 实施策略**：分阶段实施。第一阶段只做 **Homepage、ErrorView、LocalizedVideoConference、LocalizedControlBar**；其余界面和 LiveKit 组件保持英文，完成效果评估后再决定是否继续。**第一阶段已落地，当前等待效果 review。**

---

## 目录

0. 结论摘要
1. 需求与边界
2. 现状分析（codebase 事实）
3. 语言选择设计
4. 消息目录与 React 集成设计
5. 组件改造清单
6. LiveKit 预制 UI 本地化
7. 格式化与字体
8. 错误、动态内容与边界
9. 测试与验收
10. 实施顺序、风险与决策记录

---

## 0. 结论摘要

### 0.1 核心结论

1. **语言集合与 App 基本一致，但不含阿拉伯语**：`en`、`zh-Hans`、`zh-Hant`、`ja`、`ko`、`ru`、`de`、`fr`、`it`、`es`、`pt`、`hi`、`id`、`th`、`vi`，共 15 个；Android/iOS 的 `ar` 不在 Web 支持范围。
2. **语言选择**：外部浏览器读取 `navigator.languages`（按用户偏好顺序），逐个归一化；命中第一个支持的语言即选中；全部不支持则回落 `en`。App 内 Android WebView 由原生显式追加 `?lang=<app locale>`，Web 优先使用该值（见 §3.6）。
3. **改造前 Web 是纯英文单对象**：`src/messages.ts` 共 59 个 key，多数界面静态导入；另有若干硬编码英文；`index.html` 固定 `lang="en"`。第一阶段已接入 locale 解析、Provider 和 Homepage/ErrorView/ControlBar。
4. **LiveKit 预制组件没有通用 i18n API**：`PreJoin` 只支持 4 个 label prop；`VideoConference`/`ControlBar`/`Chat`/`ConnectionStateToast` 的控制栏、聊天、连接提示等大量文案写死英文。**只替换 `messages.ts` 不能达到与 App 同等级的语言支持**。
5. **严格 parity 需要本地化的会议壳层**：基于 LiveKit 2.9.24 的公开组件/公开 API，派生 `VideoConference`、`ControlBar`、`Chat`、`ChatEntry`、`ConnectionStateToast`、`ParticipantTile` 和 `StartMediaButton` 的本地化版本；保留原有 class、行为与布局。仅做 App 自有文案（P1 结束时的状态）会留下英文控制栏/聊天/连接提示，不算完整满足需求。
6. **Web 不支持阿拉伯语/无 RTL**：`ar` 不在支持列表；`ar-*` 按「不支持语言」跳过，若浏览器语言列表没有其他支持项则回落英文。本轮不实现 RTL 布局、bidi 专项或阿拉伯数字输入。
7. **不引入 i18n 框架**：使用类型安全的 TypeScript 目录 + React Context + 一个简单 `{placeholder}` 格式化函数即可；当前没有复数、富文本或运行时切换需求。

### 0.2 设计决策摘要

| # | 决策 | 建议 | 理由 |
|---|---|---|---|
| D1 | 支持语言 | 15 个 canonical code；与 Android/iOS 相比只排除 `ar` | 用户确认 Web 不支持阿拉伯语；其余与 App 保持一致 |
| D2 | 选择方式 | 外部浏览器按浏览器语言；App 内 WebView 由原生传 app locale；不新增用户语言选择器 | 与 Android/iOS「跟随系统 / per-app language」的产品口径对应；解决厂商 WebView locale 不可靠问题 |
| D3 | 回退 | 第一个支持的浏览器语言；全部不支持 → `en` | 与 iOS `AppLocale.resolvePreferred` 的遍历语义一致 |
| D4 | 消息存储 | 15 个 TS 目录文件，`satisfies Messages` 类型约束 | 构建期发现缺 key；无需新依赖 |
| D5 | LiveKit | 派生本地化壳层，不改 `node_modules`、不引入 DOM 替换黑科技 | 只有这样才能覆盖控制栏/聊天/连接提示 |
| D6 | 品牌名 | `LivaChat Meeting` 不翻译；`config.appTitle` 按部署原样使用 | 与 Android/iOS 的 `LivaChat` 品牌名一致；运营配置不应被自动改写 |
| D7 | 技术错误 detail | 标题/摘要本地化；原始 SDK/浏览器 detail 可保留原文 | 与 Android/iOS 现有「本地化 banner 标题 + 原始 detail」边界一致 |
| D8 | 服务端 | 不改 BFF、不改 `Accept-Language`、不新增语言字段 | SPA 静态托管；语言只在客户端解析 |
| D9 | 目录加载 | 15 个目录静态打包 | 字符串量相对 LiveKit/MediaPipe 很小；避免异步加载闪烁 |
| D10 | DebugMode | **保持英文，不进入翻译目录**；技术标识也不翻译 | 用户确认 debug 不用翻译；面板仅 `showDebugPanel` 开启 |

### 0.3 第一阶段实施范围（2026-11-16）

第一阶段只做 4 个可见成果，加上支撑它们所需的 i18n 基础设施：

| # | 第一阶段成果 | 说明 |
|---|---|---|
| 1 | **Homepage** | `Home.tsx` 的标题/副标题/字段/校验/按钮；`App.tsx`/`main.tsx` 接入 locale 与 `I18nProvider` |
| 2 | **ErrorView** | `ErrorView.tsx` 标题/返回按钮；`formatMeetingError(error, messages)` 的已知 BFF 错误映射 |
| 3 | **LocalizedVideoConference** | 派生本地化会议壳层，替换 `VideoConference`；保持布局、Focus、屏幕共享、音频播放行为 |
| 4 | **LocalizedControlBar** | 派生本地化控制栏，覆盖麦克风/摄像头/屏幕共享/Chat/设置/离开标签与 aria |

**必要的支撑改动**（不增加产品范围）：

- `src/i18n/`：15 个 locale 目录、locale 解析、`I18nProvider`/`useMessages`，以及 `?lang=` 显式语言桥（§3.6）；
- `main.tsx` 设置 `html.lang`（`dir` 固定 `ltr`）；
- `App.tsx` 接入当前 messages，用于 ErrorView 和必要的首页/离开提示；
- `MeetingRoom.tsx` 用 `LocalizedVideoConference` 替换 `VideoConference`，并删除外层重复的 `ConnectionStateToast`；
- Android `WebViewScreen` 在加载 URL 前追加 app locale 对应的 `lang` 查询参数（`WebViewLocale.kt`）；
- Web `installDeviceLabelFallback` 为 WebView 中空 label 的设备生成「摄像头 1 / 麦克风 1 / 扬声器 1」等本地化名称；仅在对应设备类型已成功取得 `getUserMedia` 后应用，避免掩盖 LiveKit 的权限探测（`deviceLabels.ts`）；
- 目录当前包含第一阶段 + 设置页 + 离开确认 + 聊天 + 空房提示 + PreJoin key（54 个）；未迁移组件继续使用现有 `src/messages.ts` 英文对象；其余阶段再合并为完整目录。

**第一阶段明确保持英文**（不作为本阶段失败项）：

- `SettingsMenu`、`CameraSettings`、`MicrophoneSettings`、`SpeakerSettings`、背景效果（**第二阶段已完成**）；
- `RecordingIndicator`、`MeetingRoom` 的断开/媒体错误 toast；`LeaveConfirmDialog`、空房提示和 `PreJoinStage` 已在后续阶段完成；
- LiveKit `ConnectionStateToast`、`ParticipantTile`、`StartMediaButton`；`Chat`/`ChatEntry` 已在后续聊天阶段完成。
- `ConfigError`/`Loading` 及其他未列入第一阶段的产品文案；
- DebugMode（按 D10 长期保持英文）；
- SDK/浏览器技术 detail。

**效果评估点**：15 个 locale 下 Homepage/ErrorView/控制栏是否可读、布局是否可接受；控制栏 localized 后设置菜单/聊天/连接提示仍有英文时，整体混排是否可接受；`LocalizedVideoConference` 是否保持原有功能。评估通过后再进入第二阶段。

**第一阶段实施结果（2026-11-16）**：

- 已新增 `src/i18n/`（15 locale 目录、locale 解析、Provider）和 `src/components/livekit/`（`LocalizedVideoConference`、`LocalizedControlBar`、本地 `useMediaQuery`）。
- 已接入 `Home`、`ErrorView`、`App` 的错误映射，以及 `MeetingRoom` 的会议壳层替换；删除外层重复 `ConnectionStateToast`。
- `npm run check` 通过；`npm test` 为 14 个文件 / 77 个测试通过；`npm run build` 通过。
- 手工浏览器验证：`zh-CN` 下 Homepage、已知会议错误页、控制栏文案均为中文；设置菜单/聊天/连接提示等仍为英文，符合本阶段范围；成功加入会议并打开设置 modal。
- Android WebView 验证：系统/App 中文、外部浏览器中文；修复前 WebView 显示英文。安装带 `?lang` 桥的 release APK 后，应用内 WebView 的会议首页显示中文。
- 设备菜单验证：Huawei WebView 的 `enumerateDevices()` 返回空 label；Web 端 fallback 后，摄像头下拉显示「摄像头 1..6」，可切回摄像头 1 恢复画面；系统浏览器不受影响。fallback 只在对应媒体权限成功后才生效，未破坏 LiveKit 在权限前通过空 label 触发 `getUserMedia` 的流程。
- 官方 LiveKit 包、`node_modules`、`patch-package` 无改动。

**第二阶段（设置页）与第三阶段（离开确认）实施结果（2026-11-16）**：

- `SettingsMenu`、`CameraSettings`、`MicrophoneSettings`、`SpeakerSettings` 已接入 `useMessages`；设置 tab/关闭、摄像头/麦克风/扬声器标题、背景效果、内置背景名称、加载/不支持/需要摄像头提示和错误 toast 均本地化。
- `CameraSettings` 的内置背景改为组件内 `useMemo`，配置的 `backgroundImages[].label` 保持运营原文；错误 toast 使用 `backgroundEffectErrorWithDetail` 模板，`{detail}` 保留原始技术信息。
- 15 个目录新增 13 个设置/背景 key 和 3 个离开确认 key，当前共 **47 key**；目录 key/placeholder 校验通过。
- 中文设置页手工验证：`设置 / 关闭 / 摄像头 / 背景效果 / 无 / 模糊 / 办公桌 / 自然 / 湖泊 / 花卉 / 麦克风 / 扬声器` 均为中文；设备名保持 OS 原文。
- `npm run check`、`npm test`（14 个文件 / 77 个测试）、`npm run build` 通过。
- `LeaveConfirmDialog` 标题、正文、取消/离开按钮已本地化（第三阶段）；中文实测显示「要离开会议吗？」/「离开会议并返回首页。」/「取消」/「离开」。

**第四阶段（聊天）实施结果（2026-11-16）**：

- 新增派生组件 `LocalizedChat` / `LocalizedChatEntry`：聊天头部、输入框 placeholder、发送按钮、关闭按钮 aria-label、编辑标记和消息时间均本地化；保留 LiveKit 的发送、滚动、未读计数、消息格式化和附件显示行为。
- 15 个目录新增 5 个聊天 key，当前共 **52 key**；`ChatEntry` 时间使用 App `formattingLocale`，不再使用 WebView `navigator.language`。

**空房提示本地化（2026-11-16）**：

- 空房提示文案缩短为 `No one else has joined yet.`，并在 15 个目录新增 `noOneElse`；`MeetingRoom` 使用当前 locale 的文案，当前共 **53 key**。
- 30 秒定时逻辑不变；有人加入后提示仍会消失。

**PreJoinStage 本地化（2026-11-16）**：

- `PreJoinStage` 改用 `useMessages`：返回/加入中/加入会议/麦克风/摄像头/姓名 placeholder 和令牌错误均使用当前 locale。
- 媒体设备错误 toast 使用 `mediaDeviceErrorWithDetail` 模板，`{detail}` 保留原始技术信息；15 个目录新增 1 个 key，当前共 **54 key**。
- 中文 PreJoin 页面实测显示「返回 / 你的名字 / 加入会议 / 麦克风 / 摄像头」。
- `npm run check`、`npm test`（14 个文件 / 77 个测试）、`npm run build` 通过。
- 中文聊天页手工验证：`消息 / 输入消息... / 发送 / 关闭聊天 / 已编辑`；设备名、参与者名、消息正文保持原文。
- `npm run check`、`npm test`（14 个文件 / 77 个测试）、`npm run build` 通过。

---

## 1. 需求与边界

### 1.1 需求拆解

| # | 需求原文/含义 | 本文对应 |
|---|---|---|
| N1 | 语言支持程度除阿拉伯语外与 Android App、iPhone App 一致 | §2.2、§3.1、§9.4 |
| N2 | 根据 client browser 语言选择合适的语言；不支持默认英文 | §3.2–§3.5 |
| N3 | 以 codebase 为基础，不猜测 | 全文的事实表、文件路径和 LiveKit 源码核对 |
| N4 | 将分析和设计保存到 `docs/webmeeting_multilingual.md` | 本文 |

### 1.2 范围

**在范围内**

- `apps/web/meeting` 的全部产品 UI：Home、PreJoin（可选）、Meeting 控制栏、聊天、设置、背景效果、离开确认、录制提示、空房提示、错误页、连接状态提示。
- LiveKit 预制组件的用户可见文案（控制栏、聊天、连接状态、参与者屏幕共享标签）。
- 无障碍名称/提示（`aria-label`、`title`、`aria-live` 等）和 `lang`/`dir`。
- 客户端日期/时间/数字格式；当前 Web 唯一明显的日期/时间显示是聊天时间戳。
- 文本膨胀（de/ru/hi/th/vi 等较长文案）与字体覆盖。
- 15 个 locale 的翻译资源、类型检查、单元测试与手工验收方案。

**不在范围内**

- 用户输入：参与者姓名、聊天消息、会议号本身的内容不翻译。
- 浏览器/操作系统提供的设备名（麦克风/摄像头/扬声器 label）不翻译；仅 App 的 fallback label 本地化。
- LivaChat server/BFF 返回的原始 `error.message`、LiveKit client/SDK 的原始技术错误字符串、console 日志。
- `config.json` 中运营方配置的 `appTitle`、`backgroundImages[].label` 不自动翻译；`homepageSlogan` 由 config 按 locale 提供（缺失回落 `en`），不由 i18n 目录自动翻译（见 §8.5）。
- LiveKit 的协议/标识值（`camera`、`microphone`、`screen_share`、`kbps` 等）不翻译。
- 品牌名 `LivaChat` / `LivaChat Meeting` 不翻译。
- 阿拉伯语 `ar` 与 RTL 布局；`ar-*` 没有其他支持项时回落英文。
- DebugMode 面板文案翻译（不翻译，保持英文）。
- 新增第 16 种 Web 语言、App 内语言选择器、用户手动 `?lang=` 覆盖、服务端 `Accept-Language` 协商。App 内 WebView 的内部 `?lang` 桥不在排除范围内（§3.6）。
- 阿拉伯数字输入归一化（`ar` 不支持，会议号只接受 ASCII 数字）。
- Android/iOS 原生 App 的本地化改动。

### 1.3 “除阿拉伯语外与 App 一样”的验收口径

本文把「一样」定义为：

1. 15 个 locale 的**所有 Web 产品可见字符串**都有完整目录，不是部分翻译。
2. 15 个 locale 都能由浏览器语言自动选中；不支持的语言（包括 `ar`）按列表继续查找，最终回落英文。
3. 所有支持语言都是 LTR；使用对应语言的日期/时间/数字格式规则。
4. Web 的功能面（Home、PreJoin、会中控制、聊天、设置、错误提示等）逐项本地化，不要求照搬 App 中 Web 不存在的功能字符串。
5. 用户生成内容、设备名、SDK 技术 detail 保持原文；这与 App 现有错误/内容边界一致。

### 1.4 术语

| 术语 | 含义 |
|---|---|
| canonical locale | Web 内部使用的 15 个 code，如 `zh-Hans`、`zh-Hant`、`id` |
| catalog | 某个 locale 的完整消息对象 |
| Prefab | LiveKit 的成套预制组件，如 `VideoConference`、`ControlBar`、`Chat` |
| formatting locale | 传给 `Intl.*` 的 BCP-47 标识；可能与 canonical code 不同（如 `pt` → `pt-BR`） |
| RTL | 从右到左布局；本轮 Web 不支持（`ar` 不在集合） |
| technical detail | SDK/浏览器/服务端返回的原始错误文本，不保证目标语言 |

---

## 2. 现状分析（codebase 事实）

> 本节主要是第一阶段改造前的基线事实；第一阶段已实施的内容和最新测试结果见 §0.3、§9.5。

### 2.1 当前 Web 的文案架构

**事实**

- `apps/web/meeting/src/messages.ts` 是唯一的英文消息对象，共 **59 个 key**，没有 locale 参数、目录或语言解析。
- 多数界面直接 `import { messages } from '../messages'`；没有 `I18nProvider`、`useMessages` 或类似抽象。
- `apps/web/meeting/index.html` 固定 `<html lang="en">`，`<title>`、`<meta name="description">` 也是英文静态值。
- `src/config.ts` 使用 `messages.appTitle` 作为默认标题，并直接使用 `messages.invalidServerUrl`、`messages.configErrorHint` 拼配置错误。
- `src/utils.ts` 的 `formatMeetingError` 直接导入英文 `messages`；`errorMessage` 的兜底是硬编码 `'Unknown error'`。
- `src/components/CameraSettings.tsx` 的 `BACKGROUND_IMAGES` 是模块级常量，在模块加载时读取英文 `messages.background*`；模块内还有硬编码 `"Camera"` 和 `"Background effects"`。
- `src/components/RecordingIndicator.tsx` 的 toast 文案硬编码 `"This meeting is being recorded"`。
- `src/components/DebugMode.tsx` 的面板标签、表头和状态值硬编码英文（`Published tracks`、`Permissions`、`Remote participants`、`Kind`、`Bitrate`、`Status`、`unsubscribed`/`enabled`/`disabled` 等）。按 D10，本轮保留英文，不视为翻译缺口。
- `src/components/MeetingRoom.tsx` 使用 LiveKit `VideoConference`，同时又在 `VideoConference` 外层渲染了一个 `<ConnectionStateToast />`；**`VideoConference` 内部本身也渲染 `ConnectionStateToast`**，当前存在重复连接状态 toast 的代码路径（见 §6.4）。
- `src/components/PreJoinStage.tsx` 只把 `joinLabel`、`micLabel`、`camLabel`、`userLabel` 传给 LiveKit `PreJoin`；这是当前唯一已接入 LiveKit label prop 的地方。
- 改造前自动化基线：`npm run check` 通过；`npm test` 为 **11 个测试文件 / 58 个测试通过**；没有 locale/目录/格式化测试。第一阶段后为 14 个文件 / 77 个测试。

**现有 59 个 key 的使用分类（事实，按代码用途）**

| 类别 | key |
|---|---|
| 首页/表单 | `appTitle`、`homeSubtitle`、`meetingNumberLabel`、`meetingNumberRequired`、`meetingNumberInvalid`、`meetingNumberFormat`、`participantNameLabel`、`participantNameRequired`、`participantNameTooLong`、`joinMeeting`、`joining`、`back`、`devWarning` |
| 离开/会议状态 | `leave`、`cancel`、`leaveConfirmTitle`、`leaveConfirmMessage`、`noOneElse`、`youLeft`、`duplicateIdentity`、`participantRemoved`、`roomDeleted`、`connectionLost` |
| 设备/背景 | `mediaDeviceError`、`backgroundEffectError`、`backgroundContextLost`、`settingsCamera`、`settingsMicrophone`、`settingsSpeaker`、`backgroundNone`、`backgroundBlur`、`backgroundDesk`、`backgroundNature`、`backgroundLake`、`backgroundFlower`、`backgroundLoading`、`backgroundCameraRequired`、`backgroundUnsupported` |
| 设置/关闭 | `settingsMedia`、`settingsClose` |
| 加载/致命错误 | `couldNotJoin`、`configErrorTitle`、`configErrorHint`、`secureContextTitle`、`secureContextHint`、`unsupportedBrowserTitle`、`unsupportedBrowserHint`、`waitingForConfig` |
| 会议/BFF 错误 | `meetingNotFound`、`meetingNotStarted`、`meetingExpired`、`meetingRateLimited`、`meetingDisabled`、`meetingServiceUnavailable` |
| 当前未引用（死键） | `meetingEnded`、`joinFailed`、`tokenFailed`、`mixedContent` |
| 品牌 | `appTitle`（`config.appTitle` 的默认值） |

> 设计建议：`joinFailed`/`tokenFailed` 可复用为通用连接/令牌错误；`meetingEnded` 与 `roomDeleted`/`meetingExpired` 重复，`mixedContent` 与 `invalidServerUrl` 重复，实施时可删除或合并，减少翻译量。

### 2.2 Android/iOS 的语言支持（事实）

**语言集合与资源映射**

| canonical code | 语言 | Android 资源 | iOS 资源 | 方向 |
|---|---|---|---|---|
| `en` | English | `values/` | `en.lproj` | LTR |
| `zh-Hans` | 简体中文 | `values-b+zh+Hans/` | `zh-Hans.lproj` | LTR |
| `zh-Hant` | 繁體中文 | `values-b+zh+Hant/` | `zh-Hant.lproj` | LTR |
| `ja` | 日本語 | `values-ja/` | `ja.lproj` | LTR |
| `ko` | 한국어 | `values-ko/` | `ko.lproj` | LTR |
| `ru` | Русский | `values-ru/` | `ru.lproj` | LTR |
| `de` | Deutsch | `values-de/` | `de.lproj` | LTR |
| `fr` | Français | `values-fr/` | `fr.lproj` | LTR |
| `it` | Italiano | `values-it/` | `it.lproj` | LTR |
| `es` | Español | `values-es/` | `es.lproj` | LTR |
| `pt` | Português | `values-pt/` | `pt.lproj` | LTR |
| `hi` | हिन्दी | `values-hi/` | `hi.lproj` | LTR |
| `id` | Bahasa Indonesia | `values-in/`（Android 遗留限定符） | `id.lproj` | LTR |
| `th` | ไทย | `values-th/` | `th.lproj` | LTR |
| `vi` | Tiếng Việt | `values-vi/` | `vi.lproj` | LTR |
| `ar` | العربية | `values-ar/` | `ar.lproj` | **RTL**（仅 App） |

> 上表是 Android/iOS 的 App 支持集合（16 个）；Web 本轮支持除 `ar` 外的 **15 个**。`ar` 行仅用于说明差异，不进入 Web 翻译目录，也不作为支持语言验收；仅保留 `ar` 回落英文的测试。

**Android 事实**

- `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/util/AppLanguage.kt` 的 `appLanguageCodes` 为 App 的 16 个 canonical code；Web 使用其中 15 个（不含 `ar`）。
- `locales_config.xml` 列出 App 的 16 个条目；其中中文使用 `zh-CN`/`zh-TW`，印尼语使用 `id`，资源目录使用 `values-in`。Web 不需要使用 `ar` 条目。
- `appLanguageCode(locale)` 的关键规则：
  - `zh`：`Hant` → `zh-Hant`；`Hans` → `zh-Hans`；国家 `TW`/`HK`/`MO` → `zh-Hant`；其他 → `zh-Hans`；
  - `in` → `id`；
  - 不在集合中 → `en`。
- App UI 语言跟随系统/Android 13+ per-app language；没有 App 内 UI 语言选择器（`docs/android_multilingual.md` 已记录）。
- Android 翻译资源是 Web 共享术语的事实源之一：`apps/android/LivaChatApp/src/main/res/values*/strings.xml`。

**iOS 事实**

- `apps/ios/LivaChatApp/Core/UI/AppLocale.swift` 的 `supportedCodes` 为 App 的 16 个 canonical code；Web 使用其中 15 个（不含 `ar`）。
- `resolvePreferred(_:)` 逐项遍历 bundle preferred localizations：先精确命中，再 `styleKey(for:)`，全部不中才 `en`。
- `styleKey(for:)` 与 Android 规则一致：`zh` + `hant`/`tw`/`hk`/`mo` → `zh-Hant`，否则 `zh-Hans`；`in` → `id`；其他取语言前缀。
- App 的 `formattingLocale(for:)` 使用 `ar` → `ar-SA`、`pt` → `pt-BR`、`id` → `id`；`AppLocale.calendar` 固定 Gregorian；`isRTL` 使用 `Locale.characterDirection(forLanguage:)`。Web 不使用 `ar`，其余格式化映射沿用（§7.3）。
- `apps/ios/LivaChatApp/Resources/Localizable.xcstrings` 当前 244 个 key；本次抽查 App 的 16 个 locale key 数量一致，均为 244。Web 只需 15 个 locale（不含 `ar`）。`AppLocaleTests.swift` 覆盖 `zh-TW`、`zh-CN`、`pt-BR`、`ar-SA`、`in`、不支持语言、格式化 locale 与 RTL。
- iOS App 名称/品牌未按语言翻译（Android `app_name` 同样为 `LivaChat`），这与本文 D6 一致。

### 2.3 LiveKit 预制组件的本地化能力（事实）

当前依赖：`@livekit/components-react@2.9.24`、`livekit-client@2.23.0`（`apps/web/meeting/package.json`）。

**可直接传 label 的 API**

| 组件 | 可用 props | 覆盖内容 |
|---|---|---|
| `PreJoin` | `joinLabel`、`micLabel`、`camLabel`、`userLabel` | 加入按钮、麦克风、摄像头、姓名 placeholder |
| `StartAudio` | `label` | 允许音频播放按钮 |
| `StartMediaButton` | `label`（静态字符串） | 但上游会根据 audio/video 自动选择 `Start Audio`/`Start Video`；静态 label 不能同时覆盖两种状态 |
| `Chat` | `messageFormatter`/`messageEncoder`/`messageDecoder` | 只覆盖消息内容格式化，不覆盖标题/输入框/按钮 |
| `VideoConference` | `chatMessageFormatter`、`chatMessageDecoder`、`chatMessageEncoder`、`SettingsComponent` | **没有控制栏 label props** |

**上游写死的用户可见文案（源码核对）**

| UI 位置 | 上游英文 | 来源/说明 |
|---|---|---|
| ControlBar 麦克风 | `Microphone` | `prefabs/ControlBar.tsx`，`showText && 'Microphone'` |
| ControlBar 摄像头 | `Camera` | 同上 |
| ControlBar 屏幕共享 | `Share screen` / `Stop screen share` | 同上 |
| ControlBar 聊天 | `Chat` | 同上 |
| ControlBar 设置 | `Settings` | 同上 |
| ControlBar 离开 | `Leave` | 同上 |
| Chat 标题 | `Messages` | `prefabs/Chat.tsx` |
| Chat 输入框 | `Enter a message...` | 同上 |
| Chat 发送 | `Send` | 同上 |
| Chat 时间/编辑标记 | `edited ` | `LocalizedChatEntry` 使用 `formattingLocale`，编辑标记用 `chatEdited` |
| ConnectionStateToast | `Connecting`、`Reconnecting`、`Disconnected` | `components/ConnectionStateToast.tsx` |
| ParticipantTile 屏幕共享 | `&apos;s screen` | `components/participant/ParticipantTile.tsx`；当前拼接在 `ParticipantName` 后 |
| StartMediaButton | `Start Audio` / `Start Video` | `components/controls/StartMediaButton.tsx` |
| MediaDeviceMenu 标题 | `Audio inputs` / `Video inputs` | 仅在未传 `kind` 时渲染；当前所有调用都传了 `kind`，不作为主要风险 |
| PreJoin 默认值 | `Join Room`、`Microphone`、`Camera`、`Username` | `prefabs/PreJoin.tsx`；当前已通过 props 覆盖 |
| PreJoin debug | `User Choices:` 等 | 仅 `debug=true`；当前未开启 |

**结论**

- 如果只替换 `messages.ts`，会得到「Home/设置/错误本地化，但控制栏、聊天、连接提示仍为英文」的半成品。
- 只用 CSS 隐藏上游文字或在 DOM 上做 `MutationObserver`/文本替换，会与 React 重渲染冲突，且无法可靠维护 `aria-label`，不建议。
- 通过 `patch-package` 改 `node_modules` 需要新增补丁工具和升级流程，当前仓库没有该基础设施；不建议。
- **推荐：在 App 内基于 LiveKit 2.9.24 的公开 API 派生本地化壳层**。详见 §6。

### 2.4 当前格式化 / 字体基线（事实）

- Web 没有 locale 层，也没有设置 `document.documentElement.lang`；`index.html` 固定英文。
- `LocalizedChatEntry` 的时间使用 App 的 `formattingLocale`，不受 WebView `navigator.language` 影响。
- 输入框没有 `dir` 控制；会议号是 ASCII 数字标识。Web 不支持 `ar`，因此不需要 RTL/bidi 或阿拉伯数字输入专项。
- `src/styles/globals.css` 与模块 CSS 主要是 LTR 物理属性；由于 15 个支持语言全部为 LTR，不需要 RTL 改造。
- 字体栈为系统字体 + `PingFang SC`/`Hiragino Sans GB`/`Noto Sans SC`，未显式列出天城文、泰文字体；依赖系统 fallback，需要实测 hi/th 字形覆盖。
- `Home` 的 `.title` 有 `letter-spacing: 0.01em`；对支持的 LTR 语言影响有限，保留现状。
- 当前没有会议日期/时间展示（`ErrorView` 已移除 `opens_at_ms`/`expires_at_ms` 的括号说明）；聊天时间戳是本轮主要的 `Intl` 格式点。
- App 内 Huawei WebView 的 locale 与系统/App 不一致问题及修复见 §3.6。

### 2.5 测试与构建基线（事实）

| 项 | 当前状态 |
|---|---|
| 类型检查 | `npm run check`（`tsc --noEmit`）通过 |
| 单元测试 | 改造前 `npm test`：11 个文件 / 58 个测试通过；第一阶段后 14 个文件 / 77 个测试；Vitest node 环境，无 DOM/组件测试 |
| 构建 | `npm run build` / `npm run build:demo` 已有既有构建闸门 |
| 依赖 | `package.json` 无 i18n 库；新增目录不需要安装依赖 |
| CI | 当前 Web 没有独立的 locale 资源门禁；靠 `tsc` 与 `npm test` |

---

## 3. 语言选择设计

### 3.1 支持的 canonical locale

```ts
export const SUPPORTED_LOCALES = [
  'en',
  'zh-Hans',
  'zh-Hant',
  'ja',
  'ko',
  'ru',
  'de',
  'fr',
  'it',
  'es',
  'pt',
  'hi',
  'id',
  'th',
  'vi',
] as const
```

- 与 Android/iOS 的 App 支持集合相比，只排除 `ar`；其余 15 个 canonical code 一致。
- 不使用 `tr`（Android/iOS 已移除）；不新增其他语言。
- **不使用 `ar`**：浏览器首选阿拉伯语时，`ar-*` 不会命中，按 §3.2 跳过；若没有其他支持项则回落 `en`；不加载阿拉伯语目录，不做 RTL。
- `pt` 只维护一套 pt-BR 文案，`pt-PT` 用户也使用这套（与两端一致）。
- `zh-Hant` 使用繁體中文/台灣慣用詞；TW/HK/MO 共用（与两端一致）。
- `id` 为规范 code；兼容浏览器遗留 `in`。

### 3.2 浏览器语言解析

**输入**：`navigator.languages`（`readonly string[]`），没有或为空时用 `[navigator.language]`，仍无则 `[]`。

**规则**

1. 去掉 `_` → `-`，大小写不敏感。
2. 对每个 tag 求 canonical code：
   - 语言 `zh`：
     - 含脚本 `Hant`，或地区 `TW`/`HK`/`MO` → `zh-Hant`；
     - 含脚本 `Hans` → `zh-Hans`；
     - 其他（含裸 `zh`、`zh-CN`、`zh-SG`）→ `zh-Hans`。
   - 语言 `in` → `id`。
   - 其他取语言前缀；若正好在 `SUPPORTED_LOCALES` 中则命中，否则未命中。`ar` 不在列表中，因此 `ar-*` 一律未命中。
3. 按 `navigator.languages` 顺序返回**第一个命中**的 canonical code。
4. 全部未命中 → `en`。
5. 解析过程中任何异常（`navigator` 不存在、tag 非法、空数组）都安全地视为未命中。

**伪代码**

```ts
export function resolveLocale(tags: readonly string[]): Locale {
  for (const tag of tags) {
    const code = canonicalLocale(tag)
    if (code) return code
  }
  return 'en'
}

function canonicalLocale(tag: string): Locale | null {
  const parts = tag.replace(/_/g, '-').toLowerCase().split('-').filter(Boolean)
  if (parts.length === 0) return null
  const [language, ...subtags] = parts

  if (language === 'zh') {
    if (subtags.includes('hant')) return 'zh-Hant'
    if (subtags.includes('hans')) return 'zh-Hans'
    if (subtags.some((part) => ['tw', 'hk', 'mo'].includes(part))) {
      return 'zh-Hant'
    }
    return 'zh-Hans'
  }

  const base = language === 'in' ? 'id' : language
  return SUPPORTED_LOCALES.includes(base as Locale) ? (base as Locale) : null
}

export function browserLanguages(): string[] {
  if (typeof navigator === 'undefined') return []
  const languages = navigator.languages
  if (Array.isArray(languages) && languages.length > 0) return [...languages]
  return navigator.language ? [navigator.language] : []
}
```

> 重要：不是「只看第一项」。若浏览器偏好为 `['tr-TR', 'fr-FR']`，应选 `fr`（与 iOS `resolvePreferred` 遍历语义一致）；只有全部不支持才 `en`。

### 3.3 映射示例

| 浏览器语言列表 | 选中 |
|---|---|
| `['en-US']` | `en` |
| `['en-GB', 'zh-Hans']` | `en` |
| `['zh-CN']` / `['zh-SG']` / `['zh']` / `['zh-Hans-HK']` | `zh-Hans` |
| `['zh-TW']` / `['zh-HK']` / `['zh-MO']` / `['zh-Hant-CN']` | `zh-Hant` |
| `['ja-JP']` | `ja` |
| `['ko-KR']` | `ko` |
| `['ru-RU']` | `ru` |
| `['de-AT']` | `de` |
| `['fr-CA']` | `fr` |
| `['it-CH']` | `it` |
| `['es-419']` | `es` |
| `['pt-PT']` / `['pt-BR']` | `pt` |
| `['hi-IN']` | `hi` |
| `['id-ID']` / `['in-ID']` | `id` |
| `['th-TH']` | `th` |
| `['vi-VN']` | `vi` |
| `['ar-EG']` / `['ar-SA']` | `en`（Web 不支持 `ar`） |
| `['ar-EG', 'fr-FR']` | `fr` |
| `['tr-TR']` | `en` |
| `['tr-TR', 'fr-FR']` | `fr` |
| `[]` | `en` |

### 3.4 不采用的服务端方案

- 不读取 `Accept-Language`，不在 BFF/静态托管层做 locale 分流：当前 `index.html` 是静态资源，接入服务端协商会增加缓存 `Vary`、部署和测试复杂度，且需求明确「client browser 语言」。
- 不在 LiveKit token 或 BFF join 请求中加入 `locale` 字段：语言只影响本地 UI，不影响房间/令牌。
- 不新增**用户可见**的 `?lang=` 手动覆盖、localStorage 或语言选择器：本轮没有运行时切换需求；要测试可在浏览器/自动化上下文中切换 locale。App 内 WebView 的内部 `?lang=` 语言桥（§3.6）不属于用户选择器。

### 3.5 文档级语言

在 `main.tsx` 首次渲染前同步执行：

```ts
const requestedLocale = localeFromQuery(window.location.search)
const locale = requestedLocale
  ? resolveLocale([requestedLocale])
  : resolveLocale(browserLanguages())
document.documentElement.lang = locale
document.documentElement.dir = 'ltr'
document.documentElement.dataset.locale = locale
```

- `lang` 使用 canonical code（`zh-Hans`/`zh-Hant` 是合法 BCP-47 tag）；不要写 `zh-CN` 或 `zh_TW`。
- 本轮 15 个 locale 全部为 LTR，`dir` 固定为 `ltr`；不实现 RTL 切换。若未来加入 `ar`，再扩展为按 locale 设置 `dir`。
- `index.html` 的 `lang="en"` 保留为无 JS fallback；有 JS 时在首屏 React 渲染前覆盖。
- 不监听 `languagechange` 自动换语言；浏览器语言变化后刷新页面生效。这与 App「系统语言变化后重建 UI」的语义接近，避免会议中途界面突变。
- `document.title`/`config.appTitle` 是品牌，不按 locale 翻译；如果未配置，使用常量 `LivaChat Meeting`。`meta[name=description]` 可在初始化时用 `homeSubtitle` 更新，但不是验收关键项。

### 3.6 App 内 Android WebView 的语言桥

**实测问题（2026-11-16）**

- 设备：Huawei 设备，系统/App 语言 `zh-Hans-CN`；WebView provider 为 `com.huawei.webview 14.0.2.319`（Chromium 99）。
- 同一会议 URL：系统浏览器显示中文；应用内 WebView 显示英文。
- 说明：该 WebView 提供给 `navigator.language(s)` 的 locale 不能可靠代表系统/App 语言；不能只依赖浏览器的语言探测。

**设计**

1. Android `WebViewScreen` 在首次加载前，把 App 资源语言归一为 canonical code：
   `appLanguageCode(context.resources.configuration.locales[0])`。
2. 仅对**受信任会议页**通过 `localizedWebUrl(url, language, serverBaseUrl, allowDebugLoopback)` 调用 `withLanguageQuery` 追加 `lang`；已有 `lang` 会被替换，保留其他 query 与 fragment。外部页面不追加，避免破坏第三方/签名 URL。
3. Web `main.tsx` 先读取 `localeFromQuery(window.location.search)`；存在 `lang` 时优先 `resolveLocale([value])`，否则才使用 `navigator.languages`。
4. 若原生传入了 Web 不支持的 `ar`，按 Web 规则回落 `en`；不会加载阿拉伯语目录。
5. 外部浏览器 URL 不追加 `lang`，继续按浏览器语言；WebView 的“在浏览器中打开”仍使用原始 URL。
6. `lang` 只影响 UI 文案，不改变会议号、鉴权、媒体权限或信任边界。

**验证**

- Web：`localeFromQuery` 单测覆盖 `?lang=zh-CN`、`?foo=1&lang=ja-JP`、空值等。
- Android：`withLanguageQuery` / `localizedWebUrl` Robolectric 单测覆盖新增、替换、保留 query/fragment，以及“只对受信任会议页追加”
- 真机：安装带桥接的 release APK 后，系统/App 中文时应用内会议首页显示中文；外部浏览器行为不变。

**未做**

- iOS WKWebView 尚未加同类桥接；本次只处理 Android WebView。若 iOS 也出现同样问题，再按同一 `lang` 协议接入。
- 不把 `lang` 写进 LiveKit token、BFF join 请求或会议记录；它只是页面 UI 提示。

---

## 4. 消息目录与 React 集成设计

### 4.1 方案选择

**推荐：轻量、类型安全的目录 + React Context。**

不引入 `i18next`/`react-i18next`：

- 当前没有复数、嵌套 key、富文本、运行时语言切换或按需加载需求。
- 目录是编译期常量，TypeScript 可以在构建时检查缺 key。
- 不增加依赖体积和初始化复杂度。

### 4.2 目录文件结构

```text
src/i18n/
  types.ts                 # Locale, 常量
  resolveLocale.ts         # 纯函数：canonicalLocale / resolveLocale / browserLanguages
  locales/
    en.ts                  # 源语言目录；定义 Messages 类型
    zh-Hans.ts
    zh-Hant.ts
    ja.ts
    ko.ts
    ru.ts
    de.ts
    fr.ts
    it.ts
    es.ts
    pt.ts
    hi.ts
    id.ts
    th.ts
    vi.ts
  I18nProvider.tsx         # Context + useI18n/useMessages
  index.ts                 # catalogs, getMessages, formattingLocale
  format.ts                # formatMessage（{placeholder} 插值）
```

**类型约束**

```ts
// locales/en.ts
export const messages = {
  homeSubtitle: 'Your own meeting',
  // ...
} as const

export type Messages = { [K in keyof typeof messages]: string }
```

其他目录：

```ts
import type { Messages } from './en'

export const messages = {
  homeSubtitle: '你的专属会议',
  // ...
} satisfies Messages
```

- 缺少 key 或值不是 string 时 `npm run check` 失败。
- 额外添加单元测试比较 `Object.keys`，防止多余/缺失 key 和空字符串。
- 目录是扁平 key（不嵌套），便于翻译和审查；数组/复数等复杂结构不在本轮范围。

### 4.3 运行时结构

```ts
export type I18n = {
  locale: Locale
  messages: Messages
  formattingLocale: string
}

const I18nContext = React.createContext<I18n | null>(null)

export function useI18n(): I18n {
  const value = React.useContext(I18nContext)
  if (!value) throw new Error('useI18n must be used inside I18nProvider')
  return value
}

export function useMessages(): Messages {
  return useI18n().messages
}
```

`main.tsx`：

```tsx
const locale = resolveLocale(browserLanguages())
const i18n = createI18n(locale)
applyDocumentLocale(i18n)

createRoot(document.getElementById('root')!).render(
  <I18nProvider value={i18n}>
    <App />
  </I18nProvider>,
)
```

- 语言在 React 首次渲染前确定，不出现「先英文再切语言」的闪烁。
- `I18nProvider` 不负责探测浏览器；探测是 `resolveLocale` 的纯函数，便于测试。
- `getMessages(locale)` 对未知 locale 返回 `en`；目录类型完整时不会缺 key，运行时仍保留 `catalog[key] ?? en[key]` 的兜底，避免未来部分翻译导致空白。
- 不提供全局可变的 `setLocale`；本轮没有运行时切换。未来若加语言选择器，再引入状态并让 Provider 重新渲染。

### 4.4 非组件代码

当前以下代码在 React 之外使用英文：

| 位置 | 现状 | 设计 |
|---|---|---|
| `src/config.ts` | 默认标题、配置错误 | 默认标题改为品牌常量；`validateConfig`/`loadConfig` 接受可选 `Messages` 参数（默认英文），`App` 传入当前目录 |
| `src/utils.ts` | `formatMeetingError` 静态 `messages`；`'Unknown error'` | `formatMeetingError(error, messages)`；`errorMessage` 增加可选 fallback（调用方传 `messages.unknownError`） |
| `src/components/CameraSettings.tsx` | 模块级 `BACKGROUND_IMAGES` 使用英文 | 改为组件内 `useMemo`/函数，依赖当前 `messages` |
| `src/credentials/clientTokenProvider.ts` | 空姓名 fallback `'Guest'` | 正常 UI 不可达（Home/PreJoin 已校验姓名）；若保留，可通过可选参数注入 `guestName`，不阻塞本轮 |
| `src/components/DebugMode.tsx` | 模块内硬编码 | **保持英文，不接入目录**；仅保留技术诊断 |

**约束**：不要让 `i18n` 反向导入 `config`/`utils`，避免循环依赖；`config` 只依赖 `Messages` 类型或接收注入对象。

### 4.5 key 规划

> 第一阶段实现 Homepage/ErrorView/ControlBar 用到的约 31 个 key；设置页/离开确认/聊天/空房提示/PreJoin 完成后当前为 54 个；本节完整清单（约 77 个）是全量目标。

**现有 key 处理**

- 保留并翻译：Home/PreJoin/设置/背景/离开/连接/错误等在用 key（约 56 个，含重新启用的 `joinFailed`/`tokenFailed`；`appTitle` 改为品牌常量）。
- 复用：`joinFailed`、`tokenFailed` 作为通用连接/令牌错误；`settingsCamera`/`settingsMicrophone`/`settingsMedia`/`leave` 可作为 LiveKit 控制栏对应标签，避免重复 key。
- 删除/合并：`meetingEnded`（与 `roomDeleted`/`meetingExpired` 重叠）、`mixedContent`（与 `invalidServerUrl` 重叠）。若担心外部引用，先确认无动态用法再删。
- 移除 `appTitle`：品牌标题使用 `DEFAULT_APP_TITLE = 'LivaChat Meeting'` 常量，`config.appTitle` 仍可覆盖；不进入 15 份翻译目录。

**新增 key（21 个；DebugMode 不计入）**

| key | 建议英文源 | 使用位置 |
|---|---|---|
| `backgroundEffects` | Background effects | `CameraSettings` 小标题 |
| `recordingStarted` | This meeting is being recorded | `RecordingIndicator` toast |
| `unknownError` | Unknown error | `errorMessage` fallback |
| `mediaDeviceErrorWithDetail` | Media device error: {detail} | 媒体设备错误 toast |
| `backgroundEffectErrorWithDetail` | Background effect failed: {detail} | 背景处理失败 toast |
| `startAudio` | Start Audio | 自定义 StartMediaButton |
| `startVideo` | Start Video | 同上 |
| `chat` | Chat | 控制栏 |
| `chatMessages` | Messages | 聊天头部 |
| `chatPlaceholder` | Enter a message... | 聊天输入框 |
| `chatSend` | Send | 发送按钮 |
| `chatClose` | Close chat | 聊天关闭按钮 aria-label |
| `chatEdited` | edited | 聊天编辑标记 |
| `screenShare` | Share screen | 控制栏 |
| `stopScreenShare` | Stop screen share | 控制栏 |
| `screenShareOf` | {name}'s screen | 参与者屏幕共享 tile |
| `connecting` | Connecting | 连接状态 toast |
| `reconnecting` | Reconnecting | 同上 |
| `disconnected` | Disconnected | 同上 |
| `focusParticipant` | Focus | 参与者 FocusToggle aria-label |
| `unfocusParticipant` | Unfocus | 同上 |

> DebugMode 保持英文，不进入目录；因此不新增 `debug*` key。

### 4.6 插值与拼接

- 使用简单 `{name}` 占位符：

```ts
export function formatMessage(
  template: string,
  values: Record<string, string>,
): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => values[key] ?? `{${key}}`)
}
```

- **禁止**把翻译片段用 `+`/模板字符串拼成句子（例如当前 `${messages.mediaDeviceError}: ${detail}`）。不同语言语序、标点不同；必须使用完整模板。
- 参与者姓名等动态值通过 `formatMessage` 注入；模板必须完整。Web 不支持 RTL 语言，本轮不做 `<bdi>`/RTL 重排专项。
- 技术 detail 可保留原文，但应作为单独文本节点，不改变本地化模板的语序。

### 4.7 翻译来源与质量

- **共享术语**优先复用 Android/iOS 已有翻译，保持三端一致：
  - `apps/android/LivaChatApp/src/main/res/values*/strings.xml`
  - `apps/ios/LivaChatApp/Resources/Localizable.xcstrings`
  - 例如 `Settings`、`Camera`、`Microphone`、`Speaker`、`Cancel`、`Back`、`Leave` 等。
- **Web 独有文案**（Home 副标题、连接错误、聊天、背景效果、录制提示等）使用**机器翻译**；本轮不要求专业翻译或母语 review。
- 翻译要求：
  - 保持 `{placeholder}` 名称和数量不变；
  - 不翻译品牌、URL、会议号、设备名、技术标识；
  - 使用目标语言的标点/引号/大小写习惯；
  - 避免逐词拼接，按整句翻译；
  - `pt` 统一 pt-BR；`zh-Hant` 使用繁體/台灣慣用詞。
- 发布门槛：15 个目录全部完整；`tsc` 和目录测试通过；缺 key 不进入发布。

### 4.8 目录加载与体积

- 第一阶段加载约 31 个 key，设置页/离开确认/聊天/空房提示/PreJoin 完成后共 54 个；最终全量约 77 个 key × 15 ≈ 1,150 条短字符串，体积相对现有 LiveKit/MediaPipe bundle 很小，且避免异步加载造成的首屏语言闪烁。
- 如果后续目录显著增长，再考虑按 locale 动态 import；本轮不做，避免引入 loading 状态和首屏延迟。

---

## 5. 组件改造清单

### 5.1 App 自有组件

| 文件 | 改造 | 涉及 key/注意点 |
|---|---|---|
| `src/main.tsx` | 解析 locale、创建 `I18n`、设置 `html.lang`（`dir` 固定 `ltr`）、包 `I18nProvider` | 首屏前完成；不改变现有 `createRoot` 结构 |
| `src/App.tsx` | `useMessages`；`loadConfig({ messages })`；`formatMeetingError(error, messages)`；`youLeft`；`document.title` 使用品牌/config | 不在 App 外静态导入英文 |
| `src/components/Home.tsx` | `useMessages`；校验错误、标签、按钮文案；会议号保持 ASCII/LTR；不做阿拉伯数字归一化 | 保留 IME/autofocus 行为 |
| `src/components/PreJoinStage.tsx` | `useMessages`；4 个 `PreJoin` label 已存在，改为当前目录；错误 toast | 不需要 fork PreJoin |
| `src/components/ErrorView.tsx` | 标题/返回按钮用目录；保留 JWT/secret sanitize | 居中布局不变 |
| `src/components/ConfigError.tsx` | 标题/hint 用目录；技术 message 可保留原文 | 配置错误是部署/运维界面 |
| `src/components/Loading.tsx` | `waitingForConfig` 用目录 | 简单 |
| `src/components/SettingsMenu.tsx` | 标签/标题用目录 | 现有 key 已覆盖 |
| `src/components/CameraSettings.tsx` | `useMessages`；`BACKGROUND_IMAGES` 移入组件/Memo；`Camera`、`Background effects` 改为 key；toast 模板 | 配置自定义背景 label 不翻译 |
| `src/components/MicrophoneSettings.tsx` | fallback label 用目录 | 设备名保持 OS 原文 |
| `src/components/SpeakerSettings.tsx` | fallback label 用目录 | 同上 |
| `src/components/LeaveConfirmDialog.tsx` | 标题/正文/按钮用目录 | 焦点陷阱/键盘行为不变 |
| `src/components/RecordingIndicator.tsx` | toast 用目录 | `REC` 图标不翻译 |
| `src/components/DebugMode.tsx` | **保持英文，不接入目录** | 仅 `showDebugPanel` 展示；用户确认 debug 不翻译 |
| `src/components/MeetingRoom.tsx` | `useMessages`；`disconnectMessage(reason, messages)`；媒体错误模板；替换为本地化会议壳层；移除重复 `ConnectionStateToast` | 保留离开确认拦截、空房提示、iOS 屏幕共享 CSS |
| `src/config.ts` | 品牌常量；接受 `Messages` 注入；默认标题不再从静态英文对象取 | 配置测试需更新 |
| `src/utils.ts` | `formatMeetingError(error, messages)`；`errorMessage` fallback | 测试需更新 |
| `src/styles/globals.css` | 本轮不新增 RTL 覆盖；仅在文本膨胀/字体需要时做最小调整 | 15 个 locale 全为 LTR |
| `src/styles/home.module.css` | 本轮不修改；无 RTL 需求 | 不改视觉主题 |
| `index.html` | 保留品牌 title；`lang="en"` 作为无 JS fallback；可选更新 meta description | 不把翻译硬编码进 HTML |

### 5.2 PreJoin

- 继续使用 LiveKit `PreJoin`，不 fork：
  - `joinLabel`、`micLabel`、`camLabel`、`userLabel` 已覆盖主要文案；
  - `persistUserChoices={false}` 保持不变；
  - 设备菜单均传 `kind`，不会触发上游 `Audio inputs`/`Video inputs` 标题；
  - `PreJoin` 的 debug 模式未启用，不需要翻译 `User Choices:`。
- 姓名输入保持默认 LTR；Web 不支持 RTL 语言，不引入 `dir="auto"`/bidi 专项。

### 5.3 会议壳层（设计重点）

> 第一阶段只派生 `LocalizedVideoConference` + `LocalizedControlBar`；`Chat`/`ChatEntry`/`ConnectionStateToast`/`ParticipantTile`/`StartMediaButton` 继续使用官方英文组件，待效果评估后再决定是否进入后续阶段。

把 `MeetingRoom` 中的 `<VideoConference>` 替换为本地化的 `LocalizedVideoConference`，结构参考上游：

```tsx
<LocalizedVideoConference
  chatMessageFormatter={formatChatMessageLinks}
  SettingsComponent={config.enableSettingsMenu ? Settings : undefined}
/>
```

内部的 `LocalizedVideoConference` 保持上游的：

- `useTracks` 摄像头/屏幕共享轨道；
- 屏幕共享自动 pin/unpin；
- `GridLayout` / `FocusLayoutContainer` + `CarouselLayout` + `FocusLayout`；
- `LayoutContextProvider` 的 widget state（chat/settings）；
- `RoomAudioRenderer`；
- 只保留**一个** `LocalizedConnectionStateToast`。

新增/派生的文件建议：

```text
src/components/livekit/
  LocalizedVideoConference.tsx
  LocalizedControlBar.tsx
  LocalizedChat.tsx
  LocalizedChatEntry.tsx
  LocalizedConnectionStateToast.tsx
  LocalizedParticipantTile.tsx
  LocalizedStartMediaButton.tsx
  useMediaQuery.ts           # 仅用于复制 ControlBar 的 verbose/minimal 断点
```

每个文件顶部标注：

```text
Derived from @livekit/components-react@2.9.24 (Apache-2.0).
Keep behavior/class names in sync with upstream; see docs/webmeeting_multilingual.md §6.
```

> 复制/派生上游源码时保留 Apache-2.0 许可声明与来源说明；升级 `@livekit/components-react` 时要按 §10 的风险清单重新 diff。

---

## 6. LiveKit 预制 UI 本地化

> 第一阶段只实现 §6.2 `LocalizedControlBar` 和 §5.3 的 `LocalizedVideoConference`；§6.3–§6.6 为后续阶段，不在第一阶段范围。

### 6.1 总体策略

**推荐方案：派生本地化壳层，使用 LiveKit 的公开导出。**

不使用：

- CSS `::after` 覆盖文字（无法维护无障碍名称，翻译会与 DOM 不一致）；
- `MutationObserver` 替换文本（React 重渲染会覆盖，脆弱）；
- `patch-package`/直接改 `node_modules`（仓库无补丁基础设施，升级不可重复）；
- 升级到未经验证的版本并假设存在 i18n API（2.9.24 没有通用 API）。

### 6.2 `LocalizedControlBar`

以 `prefabs/ControlBar.tsx` 为基准，保留：

- `visibleControls` 的权限判断（`useLocalParticipantPermissions`）；
- 屏幕共享能力检测（上游 `supportsScreenSharing()` 未从 root 导出；用 `navigator.mediaDevices?.getDisplayMedia` 做等价能力检测，或按依赖树引入 `@livekit/components-core` 的公开函数，二者择一并在实现时验证）；
- `mediaQuery` 的 `minimal`/`verbose` 断点与 `saveUserChoices`；
- `lk-control-bar`、`lk-button-group`、`lk-button-group-menu` 等 class，保证现有 CSS 和 iOS 屏幕共享隐藏规则继续生效；
- `onDeviceError` 行为。

改为：

- `TrackToggle`/`ChatToggle`/`DisconnectButton` 的 children 使用目录文案；
- 给只在图标模式显示的按钮补 `aria-label`/`title`；
- 用 `useLayoutContext().widget.dispatch({ msg: 'toggle_settings' })` 实现设置按钮（上游 `SettingsMenuToggle` 未从 root 导出；不要导入内部路径）；
- 用 `useStartAudio`/`useStartVideo` 实现本地化 `LocalizedStartMediaButton`，根据 `canPlayAudio`/`canPlayVideo` 选择 `startAudio`/`startVideo`；
- 保留 `DisconnectButton` 的 `lk-disconnect-button` class，`MeetingRoom` 的捕获阶段离开确认拦截不能失效；
- 保留 `TrackToggle` 的 `data-lk-source`，iOS CSS 的 `.is-ios .lk-control-bar [data-lk-source='screen_share']` 才能继续工作。

### 6.3 `LocalizedChat` / `LocalizedChatEntry`

以 `prefabs/Chat.tsx`、`components/ChatEntry.tsx` 为基准：

- 标题 `chatMessages`、输入 placeholder `chatPlaceholder`、发送按钮 `chatSend`、关闭按钮 `chatClose`（aria-label）；
- 使用 `useChat`、`formatChatMessageLinks`；消息内容不翻译；
- 自定义 entry：
  - 时间用 `formattingLocale`，而不是 `navigator.language`；
  - `chatEdited` 替代 `edited `；
  - 消息正文按原文显示；本轮不支持 RTL 语言，不做 bidi 专项；
  - 链接仍用 `formatChatMessageLinks`，不改变 URL；
- 保留 `lk-chat`、`lk-chat-header`、`lk-list lk-chat-messages`、`lk-chat-form` 等 class，保证 LiveKit 样式与现有 CSS 覆盖生效。

### 6.4 `LocalizedConnectionStateToast`

以 `components/ConnectionStateToast.tsx` 为基准，映射：

| ConnectionState | key |
|---|---|
| `Connecting` | `connecting` |
| `Reconnecting` | `reconnecting` |
| `Disconnected` | `disconnected` |

同时修复当前重复渲染：

- `VideoConference` 内部已有 `ConnectionStateToast`；
- `MeetingRoom` 外层又渲染了一个 `<ConnectionStateToast />`；
- 本地化壳层只在内部保留一个；删除 `MeetingRoom` 外层重复项，避免两个 toast 叠加。

### 6.5 `LocalizedParticipantTile`

以 `components/participant/ParticipantTile.tsx` 为基准：

- 摄像头 tile 保留静音图标、姓名、连接质量、FocusToggle；
- 屏幕共享 tile 的 `{name}'s screen` 改为 `screenShareOf` 模板；
- 保留上游的 LTR 间距实现；本轮无 RTL，不需要 `marginInlineEnd` 改造；
- FocusToggle 补 `aria-label`：`focusParticipant`/`unfocusParticipant`；
- 保留 `lk-participant-*` class 和 `VideoTrack`/`AudioTrack` 订阅行为；
- 保留 `TrackRefContext`/`ParticipantContext` 上下文，确保 Grid/Focus 布局正常。

### 6.6 `LocalizedStartMediaButton`

上游 `StartMediaButton` 的 `label` 是静态字符串，无法同时表达 `Start Audio`/`Start Video`。派生版本：

- 使用 `useStartAudio`/`useStartVideo` 获取 `canPlayAudio`/`canPlayVideo`；
- `style.display` 逻辑与上游一致；
- 根据状态选择 `startAudio` 或 `startVideo`；
- 保留按钮 class 与自动隐藏行为。

### 6.7 `PreJoin` / `MediaDeviceMenu`

- `PreJoin` 只传 props，无需派生。
- `MediaDeviceMenu` 当前始终传 `kind`，不会显示上游 `Audio inputs`/`Video inputs` 标题；本地化控制栏必须继续保持传 `kind`。
- 设备名来自浏览器/OS，保持原文；fallback label 用目录（当前 `MicrophoneSettings`/`SpeakerSettings` 已这样处理）。WebView 的 fallback 只在对应 kind 的 `getUserMedia` 成功后生效。

### 6.8 上游升级维护

派生代码必须：

1. 记录来源版本 `2.9.24` 与替代的上游文件；
2. 升级时 diff 上游同名文件，合并行为/class 变化；
3. 用现有会议 E2E 检查：离开确认、屏幕共享隐藏规则、聊天、设置 modal、Focus/Grid、音频播放、重连 toast；
4. 若未来上游提供通用 i18n API，优先迁移回上游，删除派生代码。

---

## 7. 格式化与字体（无 RTL）

### 7.1 方向

- Web 支持列表不含 `ar`；15 个 locale 全部为 LTR。本轮不实现 RTL 布局、bidi 专项、方向性 CSS 镜像或阿拉伯数字输入。
- `main.tsx` 可设置 `document.documentElement.dir = 'ltr'`；`lang` 仍按 canonical code 更新。
- 现有 LTR 布局保留，不引入逻辑方向 CSS 的大规模改造。

### 7.2 输入框

- 会议号：保持 ASCII/LTR（`dir="ltr"`、`inputMode="numeric"`）；只接受 ASCII 数字，不做阿拉伯-印度数字映射。
- 参与者姓名/聊天输入：不引入 `dir="auto"`/bidi 专项；用户内容按原文显示。
- 错误 detail/JWT：沿用现有 sanitize；技术标识可保持 LTR。

### 7.3 日期、时间和数字格式

- 新增 `formattingLocale` 映射（不含 `ar`）：

| canonical | formatting locale | 备注 |
|---|---|---|
| `en` | `en` | |
| `zh-Hans` / `zh-Hant` | `zh-Hans` / `zh-Hant` | |
| `ja`/`ko`/`ru`/`de`/`fr`/`it`/`es` | 同 canonical | |
| `pt` | `pt-BR` | 与 iOS/Android 单套 pt-BR 一致 |
| `hi`/`id`/`th`/`vi` | 同 canonical | |

- 聊天时间使用 `Intl.DateTimeFormat(formattingLocale, { timeStyle: 'short' })`，完整时间用 `timeStyle: 'full'`（对应上游 `title`）。
- 若未来展示日期，统一指定 `calendar: 'gregory'`：
  - iOS `AppLocale.calendar` 固定 Gregorian；
  - Android 文档明确 `th` 使用 Gregorian；
  - `Intl` 的 `th-TH` 默认日历可能是 Buddhist，不能依赖默认值。
- 会议号、协议 ID、技术数据（`kbps`、`trackSid`、`camera` 等）保持 ASCII/原文；聊天未读 badge 的 `9+` 可保持上游行为，若要求完全数字本地化，再单独处理。
- 当前没有复数 UI；未来若加入计数文案，使用 `Intl.PluralRules` 并为 ru 等语言提供对应形式，不把数字直接拼英文。

### 7.4 字体与文本膨胀

- 依赖系统字体 fallback；在 hi/th/vi 真机与桌面浏览器检查天城文、泰文声调符号、越南文声调符号是否有缺字/截断。
- 如果系统字体覆盖不足，再考虑在字体栈加入 `Noto Sans Devanagari`/`Noto Sans Thai` 等；本轮不默认打包 Web font（避免体积和加载闪烁）。
- 检查较长语言（de/ru/hi/th/vi）在 320/375px 宽度下的：
  - Home 标题/副标题/校验错误；
  - PreJoin 按钮；
  - 控制栏（verbose 文案与图标是否挤压）；
  - 聊天 header/发送按钮；
  - 设置菜单 tab/header；
  - 离开确认弹窗；
  - 错误页长消息。
- 保持现有响应式策略：小屏控制栏使用 `minimal` 图标模式，但必须补 `aria-label`，不能只靠图标。

---

## 8. 错误、动态内容与边界

### 8.1 已知 BFF 错误码

`formatMeetingError(error, messages)` 对 `TokenEndpointError` 的稳定 code 映射：

| code | key |
|---|---|
| `meeting_not_found` | `meetingNotFound` |
| `meeting_not_started` | `meetingNotStarted` |
| `meeting_expired` | `meetingExpired` |
| `rate_limited` | `meetingRateLimited` |
| `meeting_disabled` | `meetingDisabled` |
| `upstream_unavailable` | `meetingServiceUnavailable` |
| 其他 token 错误 | `tokenFailed` |
| `room.connect` / 网络错误 | `joinFailed` |
| 未知异常 | `unknownError` 或通用本地化错误 |

- 已知 code 不直接展示 BFF 的英文 `message`，避免已知场景出现英文。
- 未知 code 保留原始 detail 仅用于诊断（console 或受控 detail 区域），摘要使用目标语言。

### 8.2 技术 detail

- 媒体设备/背景处理错误：使用 `mediaDeviceErrorWithDetail`/`backgroundEffectErrorWithDetail` 的本地化模板；`{detail}` 可能来自浏览器/SDK，可能是英文。这与 Android/iOS 现有「本地化标题 + 原始 detail」边界一致。
- `ErrorView` 的 JWT/secret sanitize 保持不变；不能因为本地化而移除脱敏。
- DebugMode 保持英文，不接入翻译目录；SDK 标识、track source、kbps 等也不翻译。

### 8.3 配置错误

- `ConfigError` 的 title/hint 本地化；`config.json` 解析/校验的技术 detail 可能保留英文（部署/运维可见）。
- 若希望配置错误也完全本地化，需要把 `config.ts` 的校验异常改为结构化 code；本轮可作为 P2 增强，不作为严格 parity 前置条件。

### 8.4 内容边界

| 内容 | 处理 |
|---|---|
| 参与者姓名、聊天消息 | 原文；不翻译；不做 RTL/bidi 专项 |
| 会议号 | ASCII 标识；输入/显示 LTR；只接受 ASCII 数字 |
| 设备名 | 浏览器/OS 原文；App fallback label 本地化 |
| `config.appTitle` | 运营配置原样；品牌不翻译 |
| `config.homepageSlogan` | 按 locale 从 config 读取；缺失回落 `en`；不由 i18n 目录翻译 |
| `config.backgroundImages[].label` | 运营配置原样；内置背景名翻译 |
| BFF `error.message` | 只对稳定 code 映射；未知 detail 保留原文/日志 |
| SDK/track 技术标识 | 原文 |
| 品牌 `LivaChat Meeting` | 不翻译 |

### 8.5 未来扩展（不在本轮）

- 若运营需要多语言 `appTitle`/背景标签，可在 config 中增加 `Record<Locale, string>` 映射；本轮不设计，避免改变现有 config schema。
- 若产品需要手动语言选择器，可增加 `?lang=`/localStorage 覆盖，并定义优先级；本轮不做。

---

## 9. 测试与验收

### 9.1 自动化单元测试（Vitest，沿用 node 环境）

新增/更新：

- `src/i18n/resolveLocale.test.ts`
  - 15 个语言各自的常见 tag；
  - `zh` 的 Hans/Hant/TW/HK/MO/裸 `zh`/`zh-SG`/`zh-Hans-HK`；
  - `in` → `id`；
  - `pt-BR`/`pt-PT` → `pt`；
  - 不支持 `tr-TR`/`pl`/`xx` → `en`；
  - `ar-EG`/`ar-SA` → `en`（Web 不支持阿拉伯语）；
  - `['ar-EG','fr-FR']` → `fr`；
  - `['tr-TR','fr-FR']` → `fr`；
  - 空数组 / `navigator` 不可用 → `en`；
  - 大小写、下划线、扩展 subtag。
  - `localeFromQuery`：`?lang=zh-CN`、`?foo=1&lang=ja-JP`、空值/缺省。
- `src/i18n/messages.test.ts`
  - 15 个目录 key 集合与 `en` 完全一致；
  - 无空字符串；
  - 每个模板的 `{placeholder}` 集合与英文一致；
  - 不包含 `ar` 目录；所有支持语言均为 LTR；
  - `formattingLocale` 映射（`pt`→`pt-BR`、`th`/`vi`/`hi` 等）。
- `src/validation.test.ts`
  - 现有 8 位/全角/连字符/空格用例不回归；
  - 不新增阿拉伯数字归一化（`ar` 不支持）。
- `src/utils.test.ts`
  - `formatMeetingError(error, messages)` 的 6 个稳定 code；
  - 未知 error 的本地化 fallback；
  - 现有“不展示 opens_at/expires_at 括号详情”不回归。
- `src/config.test.ts`
  - 默认品牌标题不依赖 locale；
  - 传入非英文 `messages` 时配置错误文案可本地化（若实现该参数）；
  - 现有 client/server/wss 闸门不回归。
- `src/CameraSettings` 相关纯函数/背景选项（若可拆出）不回归。

> 当前 Vitest 仅 node 环境，不新增 jsdom/Testing Library；组件行为靠手工/浏览器 E2E。若未来要组件测试，再单独引入 jsdom 依赖。

### 9.2 构建/类型门禁

```bash
cd apps/web/meeting
npm run check
npm test
npm run build          # 生产 server 模式 config
# 或 ALLOW_CLIENT_TOKEN=1 npm run build  # 本地 demo config
```

`tsc` 必须通过；目录类型不完整直接失败。

### 9.3 手工/浏览器验收矩阵

**语言探测与 HTML 状态**

- [ ] 每个 locale 设置浏览器首选语言，刷新 `/meeting/`，确认 `document.documentElement.lang` 为 canonical code。
- [ ] `dir` 固定 `ltr`；不存在 `ar` 目录或 RTL 布局。
- [ ] 不支持语言（如 `tr-TR`、`ar-EG`）→ 英文。
- [ ] `['ar-EG','fr-FR']` → 法语（继续查找第一个支持项）。
- [ ] `['tr-TR','de-DE']` → 德语（第一个支持项），不是英文。
- [ ] `zh-TW` → 繁体；`zh-CN`/`zh-SG` → 简体；`zh-HK` → 繁体。
- [ ] `in-ID` → 印尼语。
- [ ] 刷新后语言保持（同一浏览器语言），不闪英文。

**功能界面**（每个 locale 至少抽查 Home、PreJoin、会中）

- [ ] Home：标题/副标题、会议号/姓名标签、校验错误、加入按钮、loading。
- [ ] PreJoin（若开启）：Back、Joining、四个 label；姓名 placeholder。
- [ ] 会中：麦克风/摄像头/屏幕共享/Chat/设置/Leave 控制栏；图标模式有本地化 aria-label。
- [ ] 聊天：Messages、输入框、Send、关闭、时间格式、edited；发送/接收/未读 badge。
- [ ] 设置：Camera/Microphone/Speaker/Close、背景 None/Blur/Desk/Nature/Lake/Flower、加载/不支持/需要摄像头提示。
- [ ] 离开确认：标题/正文/Cancel/Leave；Esc/遮罩/焦点陷阱。
- [ ] 录制提示、空房提示、连接中/重连/断开 toast。
- [ ] 错误页：Could not join、已知 BFF code 的本地化文案、Back；JWT/secret 脱敏。
- [ ] DebugMode（如开启）：保持英文；不要求本地化。


**文本膨胀/移动端**

- [ ] de/ru/hi/th/vi 在 320/375px 下 Home/PreJoin/设置/聊天/离开确认不截断。
- [ ] Verbose 控制栏在窄屏能退回图标模式；无不可点击的挤压。
- [ ] iOS Safari / Android Chrome / Huawei Browser 的字体与安全区正常。

### 9.4 全量验收完成定义（最终目标，非第一阶段）

> 第一阶段先按 §9.5 验收；本节是全量方案完成后的最终目标，未迁移组件在第一阶段保留英文是预期行为。

- [ ] 15 个目录完整，`tsc` 与目录测试通过。
- [ ] 浏览器语言选择/回退测试全部通过；`ar` 无其他支持项时回落英文。
- [ ] `html.lang` 正确，`dir` 固定 `ltr`。
- [ ] App 自有 UI 无硬编码英文（除 §8.4 边界）；DebugMode 保持英文符合预期。
- [ ] LiveKit 控制栏/聊天/连接提示/屏幕共享标签在 15 个 locale 下无英文残留。
- [ ] 基线 58 个测试 + 后续新增测试全部通过；生产/demo 构建通过。
- [ ] 现有会议功能回归：离开确认、聊天、设置、背景处理、屏幕共享、重连、空房提示、录制提示。
- [ ] 机器翻译覆盖全部 15 个目录；key/placeholder 校验通过。

### 9.5 第一阶段验收（4 项成果）

- [ ] 15 个 locale 的浏览器语言解析、`ar` 回落、`html.lang` 正确。
- [ ] 第一阶段 15 个目录（约 31 个 key）完整；机器翻译；`tsc`/目录测试通过。
- [ ] Homepage 的表单标题/标签/校验/按钮/开发提示本地化。
- [ ] ErrorView 标题/Back、已知 BFF 错误码本地化；技术 detail 可保留英文。
- [ ] LocalizedControlBar 的麦克风/摄像头/屏幕共享/Chat/设置/离开标签及图标模式 aria-label 本地化。
- [ ] LocalizedVideoConference 保持 Grid/Focus、屏幕共享自动 pin、设置 modal、音频播放、离开确认拦截、iOS 屏幕共享隐藏规则。
- [ ] `MeetingRoom` 只保留一个连接状态 toast（官方英文 toast），不再重复。
- [x] `Chat`/`ChatEntry` 已完成本地化；`ConnectionStateToast`、`ParticipantTile`、`StartMediaButton` 仍为英文。
- [x] `SettingsMenu`/背景效果已本地化（第二阶段），`LeaveConfirmDialog` 已本地化（第三阶段），空房提示和 `PreJoinStage` 已本地化；录制提示仍保持英文。
- [ ] 官方 LiveKit 包、`node_modules`、`patch-package` 无改动。
- [ ] `npm run check`、`npm test`、生产/demo 构建通过；基线 58 个测试不回归（第一阶段后共 77 个测试通过）。
- [ ] Android WebView 对受信任会议页首次加载会追加/替换 `lang`，WebView 按 App 资源语言显示；外部页面与外部浏览器不改。
- [ ] WebView `enumerateDevices()` 返回空 label 时，在对应媒体权限成功后设备菜单显示本地化 fallback 名称；选择可用设备后画面可恢复；权限前仍保持空 label 供 LiveKit 触发 `getUserMedia`。
- [ ] `withLanguageQuery` / `localizedWebUrl` 保留 query/fragment、替换旧 `lang`、只对受信任会议页追加的 Robolectric 测试通过。
- [ ] 记录 15 个 locale 的 Homepage/ErrorView/ControlBar 截图或手测结果，评估是否继续第二阶段。

---

## 10. 实施顺序、风险与决策记录

### 10.1 建议实施顺序（分阶段）

**P0：第一阶段 i18n 基础设施（已完成）**

- `SUPPORTED_LOCALES`、`canonicalLocale`/`resolveLocale`/`browserLanguages`、`formattingLocale`；
- 15 个 locale 目录，先只包含第一阶段 Home/Error/ControlBar 用到的 key（约 31 个）；
- `I18nProvider`/`useMessages`，`main.tsx` 初始化 `lang`（`dir` 固定 `ltr`）；
- 保留现有 `src/messages.ts` 作为未迁移组件的英文源；第二阶段再合并为完整目录。
- `installDeviceLabelFallback`（`deviceLabels.ts`）在 React 挂载前包装 `enumerateDevices()` 和 `getUserMedia()`；只有对应 kind 的 `getUserMedia` 成功后，才为空 label 生成 fallback 名称，保留 LiveKit 权限前的空-label 探测。

**P1：Homepage + ErrorView（已完成）**

- `Home.tsx`：标题/副标题、会议号/姓名标签、校验、加入按钮、开发提示；
- `App.tsx`：接入当前 messages；ErrorView 的 `formatMeetingError(error, messages)`；必要的加载/离开提示；
- `ErrorView.tsx`：标题/返回按钮；已知 BFF code 的本地化错误；
- `utils.ts`：`formatMeetingError` 接受 messages；`errorMessage` fallback 可本地化；
- Android `WebViewScreen` 通过 `WebViewLocale.localizedWebUrl`（仅受信任会议页）追加 `lang`；Web 通过 `localeFromQuery` 优先使用；
- 本阶段不迁移 `ConfigError`/`Loading` 等；`SettingsMenu`、`LeaveConfirmDialog`、`PreJoinStage` 已在后续阶段完成。

**P2：LocalizedVideoConference + LocalizedControlBar（已完成）**

- 派生 `LocalizedVideoConference`：保留 `VideoConference` 的 Grid/Focus/屏幕共享自动 pin/音频播放行为；第一阶段只把 `ControlBar` 替换为 `LocalizedControlBar`，Chat/ConnectionStateToast/ParticipantTile 继续使用官方组件；
- 派生 `LocalizedControlBar`：本地化麦克风/摄像头/屏幕共享/Chat/设置/离开；保留 `lk-*` class、权限判断、设备菜单、离开确认拦截和 iOS 屏幕共享隐藏规则；
- 增加本地 `useMediaQuery` 小 hook（官方内部 hook 未从包根导出）；
- 第一阶段使用官方 `Chat`、`ChatEntry`、`ConnectionStateToast`、`ParticipantTile`、`StartMediaButton`；其中 `Chat`/`ChatEntry` 已在第四阶段替换为本地化版本，其余仍为英文；
- `MeetingRoom.tsx` 用 `LocalizedVideoConference` 替换 `VideoConference`，并删除外层重复的 `ConnectionStateToast`；
- 不修改官方 LiveKit 包或 `node_modules`。

**P3：第一阶段效果评估（待 review）**

- 15 个 locale 下验证 Homepage/ErrorView/控制栏；
- 设置菜单已本地化；确认聊天、连接提示、参与者标签等英文残留是否可接受；
- 确认 `LocalizedVideoConference` 保持布局/屏幕共享/离开确认/设置 modal/音频播放行为；
- 通过后再进入第二阶段；否则先修复第一阶段问题或调整范围。

**P4：后续阶段（暂缓，效果评估后决定）**

- App 自有界面：`RecordingIndicator`、`MeetingRoom` 的断开/媒体错误文案；`SettingsMenu`/`CameraSettings`/`MicrophoneSettings`/`SpeakerSettings`/`LeaveConfirmDialog`/空房提示/`PreJoinStage` 已完成；
- LiveKit 残留：`ConnectionStateToast`、`ParticipantTile`、`StartMediaButton`；`LocalizedChat`/`LocalizedChatEntry` 已完成（第四阶段）；
- 补齐 15 个目录的全部 key（约 77 个）；
- 全量验收与浏览器矩阵。

### 10.2 主要风险

| 风险 | 级别 | 缓解 |
|---|---|---|
| LiveKit 派生组件与上游升级漂移 | 高 | 只使用公开 API；标注 2.9.24 来源；升级时 diff；保留 E2E 清单 |
| 机器翻译质量/术语不一致 | 高 | key/placeholder 校验；共享术语复用 App 资源；发布前抽查关键 UI 文案 |
| 长文本/天城文/泰文/越南文渲染问题 | 中 | 320/375px 文本膨胀测试；系统字体检查；必要时增加 fallback 字体 |
| 未知 SDK/浏览器错误 detail 仍为英文 | 中 | 本地化标题/摘要；detail 作为技术信息；与 App 边界一致 |
| 目录静态打包增加 bundle | 低 | 约 1,150 条短字符串；相对现有 bundle 很小；后续再按需拆分 |
| 浏览器语言设置特殊/空值 | 低 | 遍历 + fallback；纯函数单测 |
| 厂商 WebView 的 `navigator.language` 与系统/App 语言不一致 | 中 | Android WebView 已加 `?lang` 语言桥（§3.6）；继续用真机验证 WebView；外部浏览器不受影响 |
| Android WebView `enumerateDevices()` 返回空设备 label | 中 | Web 端 `deviceLabels.ts` 在对应 kind 取得媒体权限后生成本地化 fallback 名称；设备 ID 仍来自原生枚举 |

### 10.3 已确认决策（2026-11-15 / 2026-11-16）

| # | 决策 | 结论 |
|---|---|---|
| 1 | Web 阿拉伯语 | **不支持 `ar`**；Web 支持 15 个 locale，`ar` 本身不支持；无其他支持项时回落英文，不做 RTL/阿拉伯数字输入 |
| 2 | DebugMode | **不翻译**；面板保持英文，不进入目录 |
| 3 | 翻译方式 | **机器翻译**；不要求专业翻译或母语 review |
| 4 | LiveKit | **接受派生本地化壳层**，覆盖控制栏/聊天/连接提示/屏幕共享标签 |
| 5 | 品牌标题 | **不翻译**；`config.appTitle` 按部署原样使用 |
| 6 | 技术 detail | **保留原文**；标题/摘要本地化 |
| 7 | 目录加载 | **15 个目录静态打包** |
| 8 | 手动语言覆盖 | **不做用户选择器**；外部浏览器按浏览器语言，App 内 WebView 由原生 `?lang` 桥传 App locale |
| 9 | 实施节奏 | **分阶段**：第一阶段只做 Homepage、ErrorView、LocalizedVideoConference、LocalizedControlBar；**第一阶段已完成**，其余保持英文，效果评估后再决定 |
| 10 | Android WebView 语言 | **原生传 app locale（`?lang`）+ Web 优先读取**；外部浏览器仍按浏览器语言；iOS 暂不处理 |

> 以上为定稿决策；除明确变更外，按本文其余推荐实施。

### 10.4 明确不在本轮实现

- 不修改 LivaChat server/BFF/LiveKit server；Android 仅做 WebView locale 传递，iOS 不改。
- 不支持阿拉伯语/RTL；不新增第 16 种 Web 语言、不实现语言选择器、不做服务端 `Accept-Language`。
- 不翻译 DebugMode、用户内容、设备名、SDK 技术标识、运营配置文本。
- 不要求专业翻译/母语 review；使用机器翻译。
- 不重构 LiveKit 的布局/媒体逻辑；只派生并本地化标签，保留上游行为。
- 不承诺未知错误 detail 的语言一致；只保证已知错误和 App 自有文案。

---

## 11. 参考文件（事实来源）

**Web**

- `apps/web/meeting/src/messages.ts`
- `apps/web/meeting/src/App.tsx`
- `apps/web/meeting/src/components/{Home,PreJoinStage,MeetingRoom,SettingsMenu,CameraSettings,MicrophoneSettings,SpeakerSettings,LeaveConfirmDialog,RecordingIndicator,DebugMode,ErrorView,ConfigError,Loading}.tsx`
- `apps/web/meeting/src/{config,utils,validation,deviceLabels}.ts`
- `apps/web/meeting/src/styles/globals.css`
- `apps/web/meeting/index.html`
- `apps/web/meeting/package.json`

**Android**

- `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/util/AppLanguage.kt`
- `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewScreen.kt`
- `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewLocale.kt`
- `apps/android/LivaChatApp/src/main/res/xml/locales_config.xml`
- `apps/android/LivaChatApp/src/main/res/values*/strings.xml`
- `docs/android_multilingual.md`
- `docs/android_webview_meeting_support.md`

**iOS**

- `apps/ios/LivaChatApp/Core/UI/AppLocale.swift`
- `apps/ios/LivaChatApp/Resources/Localizable.xcstrings`
- `apps/ios/LivaChatApp/LivaChatAppTests/AppLocaleTests.swift`
- `docs/ios_multilingual.md`

**LiveKit（当前锁定版本）**

- `apps/web/meeting/node_modules/@livekit/components-react/package.json`：`2.9.24`，Apache-2.0
- `dist/prefabs/VideoConference.d.ts`
- `dist/prefabs/ControlBar.d.ts`
- `dist/prefabs/Chat.d.ts`
- `dist/prefabs/PreJoin.d.ts`
- `dist/components/ConnectionStateToast.d.ts`
- `dist/components/ChatEntry.d.ts`
- `dist/components/participant/ParticipantTile.d.ts`
- `dist/components/controls/StartMediaButton.d.ts`
- 上游源码（通过包内 sourcemap 核对）：`prefabs/ControlBar.tsx`、`prefabs/Chat.tsx`、`prefabs/VideoConference.tsx`、`components/ConnectionStateToast.tsx`、`components/participant/ParticipantTile.tsx`
