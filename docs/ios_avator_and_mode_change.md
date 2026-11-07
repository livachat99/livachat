# LivaChat iOS 头像自定义与同声翻译模式 分析与设计

**状态**：已实施（2026-10-21）；模拟器/真机自动化通过，剩余手工验收项见 §15。设计 review/拍板记录见 §13/§14。
**日期**：2026-10-21
**目标**：在 iOS 上实现与 Android 当前实现一致的头像自定义、头像正常/缩小、以及「同声翻译」四种模式（对话/听众/转录/全模态）的 UI/UX 与完整状态链路。

**参考与代码基线**
- `docs/fd_avator_customization.md`（头像自定义 + 头像隐藏到标题栏，已拍板设计）
- Android 当前源码（以代码为准，不按旧设计稿）：
  - `AiAvatar.kt`、`AvatarTitleBarButton`
  - `AvatarCustomizationSheet.kt`
  - `AvatarCropScreen.kt`
  - `AvatarRepository.kt`
  - `ConversationScreen.kt`（顶部头像区、隐藏态标题栏簇、ModeBadge、模式弹窗）
  - `ConversationViewModel.kt`、`ConversationListenerAdapter.kt`
  - `ConversationUiState.kt`、`ConversationEvent.kt`、`ModeChangeRequest.kt`
  - `LanguagePickerSheet.kt`、`TranscribeSourcePicker.kt`
  - `MessageBubble.kt`（模式后缀）、`ChatMessage.kt`
  - `PreferencesDataSource.kt`（`avatar_visible`）
- iOS 当前源码：
  - SDK：`LivaChatSDK.swift`、`LivaChatEvent.swift`、`CoreEnums.swift`、`SessionManager.swift`
  - App：`ConversationViewModel.swift`、`ConversationCoordinator.swift`、`ConversationView.swift`、`ConversationModels.swift`、`MessageBubbleView.swift`
  - 历史：`HistoryService.swift`、`HistoryModels.swift`、`HistoryDatabase.swift`
  - 音频：`AudioSessionController.swift`、`AudioCaptureController.swift`、`AudioProtocols.swift`
  - 设置/容器：`SettingsStore.swift`、`AppContainer.swift`、`AppRootView.swift`
  - 图片选择：`PHPickerView.swift`、`GalleryPreviewView.swift`
  - 已实现的 Agent Job：`AgentJobStatusModel.swift`、`AgentJobListPanel.swift`、`AgentJobStatusBadge.swift`

> 本文覆盖 **头像展示/选择/裁剪/持久化/正常缩小** 与 **同声翻译四模式 + 模式消息展示**；不涉及 MyLife/HotSlot、屏幕录制、服务端 protocol/SDK wire 改动。

---

## 1. 范围

### 1.1 本期做

**头像**
- 大头像 96pt（**保持 iOS 现有尺寸，不移植 Android 120pt 外框与状态光环**）显示默认渐变 `~` 或自定义照片；
- 点击大头像/标题栏头像按钮 → 头像自定义面板；
- 面板：同声翻译四开关 + 头像区（正常/缩小胶囊、从相册选择、拍照、恢复默认头像）；
- 相册选择 → 裁剪页（拖动/缩放、圆形遮罩）→ 保存；
- 拍照（系统相机）→ 裁剪页 → 保存；权限不足/无相机时引导，不崩溃；
- 自定义头像仅存本地，重启保留；恢复默认；
- 正常/缩小：缩小后大头像区消失、消息区上移 96pt，头像缩为标题栏圆形按钮；模式徽章在右下、Agent Job 徽章在右侧固定槽；状态持久化；
- 头像保持静态 96pt，**不实现 Android 状态光环**；连接/会话状态继续由 header 状态文案/颜色表达（2026-10-21 拍板）。

**同声翻译模式**
- 对话模式（translate + dialogue）、听众模式（translate + lecture）、转录模式（transcribe）、全模态（omni）；四者互斥；
- 对话/听众语言选择（含自动识别；对话 auto 原样下发、听众 auto 归一为 en）；
- 转录选择原声/语音（本地采集源；不进入 session.change payload）；
- 全模态说明弹窗；确认后才发送；
- 乐观 UI + 服务端 `session.changed` 回声结算；
- 1009 `session.change` 被拒回滚；其它错误不误回滚；
- 断网重连后按最近一次用户模式请求恢复；
- 模式徽章（大头像 26pt / 隐藏态标题栏 20pt）；
- 消息模式后缀：对话/听众用户「原文」、AI「译文」；转录用户「转录」；
- 听众模式转写气泡显示实时 AI 译文第二行；
- 模式随消息落本地历史（`mode` 列），历史详情显示后缀；
- 头像/模式的 UI 测试、真机与真实 server 验证。

### 1.2 不做

- 修改 server / BFF / WS protocol / SDK wire；SDK 已具备 `sendSessionChange` 与 `.sessionChanged`；
- 服务端模式能力改造（Android 已覆盖）；
- MyLife/HotSlot/EventList 的模式后缀（Android 有，iOS 尚无该功能面）；
- 屏幕录制/抓屏（iOS 无该功能）；
- 头像上传/跨设备同步；
- 相机模式重做、横屏专项、iPad 多窗口专项；
- 系统相机内部的编辑裁剪（iOS 使用自有裁剪页，`allowsEditing=false`）。

---

## 2. Android 基线（当前源码事实）

> 本节记录 Android 当前实现，供对照；iOS 最终取舍见 D2——**保持 96pt、不移植 120 外框/光环**。

### 2.1 头像组件

`AiAvatar.kt`：
- 外层 120dp，可点击（无 ripple）；内圆 96dp，`clip(CircleShape)`；
- 有自定义照片 → `Image(ContentScale.Crop)`；否则渐变 `AvatarGradientStart→End`（=BrandIndigo→BrandViolet）+ `~`（headlineLarge/TextPrimary）；
- 状态光环由 `AvatarState` 驱动，`IDLE` 不绘制光环：
  - `LISTENING`：Info 色 3 圈扩散 ripple（1.5s）；
  - `THINKING`：Warning 色 8 个粒子绕轨道旋转（1.2s）；
  - `SPEAKING`：Success 色呼吸环（0.6s）；
  - `CONNECTING` 枚举存在但当前 `mapSessionToAvatarState` 不使用；
- `Crossfade(300ms)` 切换状态；光环不因自定义照片改变。

`AvatarTitleBarButton`（隐藏态标题栏按钮）：
- 视觉 Ø36 圆；48×48 触控层；外层不裁剪，`overlay` 槽用于模式徽章；
- 有自定义头像显示照片，否则渐变 + `~`；
- 模式徽章在右下（20dp，图标 11dp），悬于圆外不被头像裁切。

`mapSessionToAvatarState`（`ConversationScreen.kt:1180`）：
- `connectionState == DISCONNECTED` → IDLE；
- 否则 sessionState IDLE→IDLE、LISTENING→LISTENING、THINKING→THINKING、SPEAKING→SPEAKING。

### 2.2 头像存储/裁剪

`AvatarRepository.kt`：
- 正式文件 `filesDir/avatar/custom_avatar_<ts>.jpg`；**每次唯一命名**，保存成功后删除旧文件；`getAvatarUri` 取最新；
- 相册/相机源 URI 只在裁剪阶段使用；
- 相机临时源 `cacheDir/avatar_photos/`、裁剪中间产物 `cacheDir/avatar_crop_temp.jpg`，有明确清理责任；
- 头像不上传、不落业务 DB。

`AvatarCropScreen.kt`：
- 全屏深色；裁剪圆**半径 96dp（Ø192dp）**，圆外黑色 0.6 遮罩，BrandIndigo 0.5/2dp 圆环；
- 解码先做 bounds + 降采样（长边 >2048）；按 EXIF orientation 校正；
- 单指拖动 + 双指缩放；`minScale = 2×cropCirclePx/minDim`，初始 scale 填满裁剪区，最大 `initialScale×3`；偏移 clamp 保证圆内不露白；
- 确认：按当前 scale/offset 反算图像空间中心与半径，取图片边界交集后裁正方形，写 JPEG 90 临时文件。

### 2.3 头像面板（`AvatarCustomizationSheet.kt`）

`ModalBottomSheet`（Surface、顶部圆角 24dp、内容 24dp padding）：
```
「同声翻译」SectionHeader
  SwitchRow ×4：对话模式 / 听众模式 / 转录模式 / 全模态
    （title labelLarge/TextPrimary + subtitle bodySmall/TextSecondary + Switch 48dp）
  四者互斥：enabled = 其它三个模式均关闭
HorizontalDivider（SurfaceVariant，上下 8dp）
「头像选择」SectionHeader + 右侧「正常｜缩小」胶囊
  SheetOption「从相册选择」、「拍照」、[有自定义头像时]「恢复默认头像」
  SheetOption 48dp 高，无额外垂直 padding（item pitch 48dp）
```
- 头像 option 点击时发起选图/拍照/恢复并关闭面板（`onPickFromGallery()/onTakePhoto()/onRemoveCustomAvatar()` 与 `onDismiss()` 同一回调）；
- 模式开关翻动不关闭面板；模式确认（语言/音源/全模态）提交时一并关闭面板；
- 模式选择器取消 → 面板保留；
- 「正常/缩小」胶囊：容器 SurfaceVariant、2dp 内边距；选中段 BrandIndigo + TextPrimary，未选段透明 + TextSecondary；段水平 12dp/垂直 4dp；切换不关面板；Android 接受段视觉 ~28dp 触控（决策 #7）；
- 开关行本身不可点（只有 Switch）。

### 2.4 头像隐藏到标题栏（`fd_avator_customization.md` §头像隐藏）

- `avatarVisible` 默认 true，DataStore `avatar_visible` 持久化；
- 大头像区（120dp）不渲染时消息区上移（128dp = 120+8 top padding）；
- 隐藏态在 `TopAppBar` 外层 Box 叠**固定居中簇**：`AvatarTitleBarButton(36 视觉/48 触控) + 8dp + job 徽章固定 48dp 槽`；头像锚定屏中线，不随状态文案/徽章出现移动；
- 状态文案在左、菜单在右；固定簇覆盖层避免布局漂移；
- 可见态：大头像 120 + ModeBadge 26 top-end + job badge 右侧 offset（`AvatarSize + 8dp`）；
- 隐藏态：ModeBadge 20 bottom-end；job badge 固定槽；
- 切换瞬时，无动画；面板 pill 切换不关闭面板，子项两态都显示；
- 持久化决定（#3）：`avatarVisible` 重启保持；
- 触控决定（#7）：标题栏按钮 48×48，pill 维持 28dp（唯一接受偏离项）。

### 2.5 同声翻译四模式（Android 当前行为）

**面板开关状态**（`ConversationScreen.kt:756-788`）：
- 对话 on = `isTranslateMode && !isLectureMode || showLanguagePicker`
- 听众 on = `isLectureMode || showLecturePicker`
- 转录 on = `isTranscribeMode || showTranscribeSourcePicker`
- 全模态 on = `isOmniMode || showOmniIntroDialog`
- 每个开关 enabled = 其它三个模式均关闭（互斥）。

**对话模式**（`ConversationViewModel.kt:499-538`）：
1. 开关原本关闭 → 打开语言选择（不立即切换）；
2. 选语言确认：
   - 关闭其它三 flag；
   - `myLanguage = Locale.getDefault().language`（设备语言，2 字母）；
   - `localLanguage = 所选值（可为保留字 "auto"）`；
   - `usecase = "dialogue"`；
   - 乐观置 `isTranslateMode=true`、关闭面板、`sendSessionChange("translate", myLanguage, localLanguage, usecase="dialogue")`；
   - 关闭时：四 flag 全复位、清 last mode request、`sendSessionChange("chat")`，面板不关；
3. 语言选择：`LanguagePickerSheet` 标题「选择对方语言」；「自动识别」置顶；默认选中 `en`；候选 `zh,en,ja,ko,fr,it,de,es,pt,ru,tr,hi,id,th,vi,ar`（Android 与 iOS 已于 2026-10-27 对齐，见 `docs/ios_multilingual.md` §2.9 与 D6）；显示名按 UI locale；取消 → 面板保留。

**听众模式**（`:539-578`）：
- 标题「选择演讲者的语言」；同样有「自动识别」；
- **auto 归一为 `en`** 后才发（与对话的 auto 原样下发不同）；`usecase="lecture"`；
- 乐观 `isTranslateMode=true` + `isLectureMode=true`；模式徽章显示 Translate。

**转录模式**（`:579-621`）：
- 打开先弹「选择现场录音模式」：原声/语音，默认当前 `transcribeAudioSource`（默认 VOICE）；
- 确认后：四 flag 中仅 `isTranscribeMode=true`；`transcribeAudioSource=所选`；关闭面板；发 `sendSessionChange("transcribe")`；
- 原声/语音是**本地采集源**：RAW→`MediaRecorder.AudioSource.MIC`，VOICE→`VOICE_COMMUNICATION`；不进入 session.change；离开转录模式时采集源归位 VOICE_COMMUNICATION；
- 关闭：四 flag 复位 + 发 chat。

**全模态**（`:622-659`）：
- 打开先弹说明「使用全模态AI模型，可以降低交互延迟」（标题「全模态」，取消/开启）；
- 确认后仅 `isOmniMode=true`，关闭面板，发 `sendSessionChange("omni")`；
- 关闭：四 flag 复位 + chat。

### 2.6 模式消息与历史

`ConversationListenerAdapter.kt`：
- 用户 final transcription：
  - `isTranscribeMode` → `mode="transcribe"`；
  - 否则 `isTranslateMode` → `mode="translate"`（含听众）；
  - 否则 null；
- AI final：`isTranslateMode && isFinal` → `mode="translate"`；否则 null；
- 模式标记随消息落 Room；后缀只在展示时拼（`MessageBubble.kt:79-82/207-213`）：
  - transcribe → `(transcript)` / `(转录)`；
  - translate + USER → `(original)` / `(原文)`；
  - translate + AI → `(translation)` / `(译文)`；
  - 显示时 `text + " " + suffix`；历史旧行 `mode=null` 不再加后缀（兼容已并入 text 的历史）；
- 听众模式：`TranscriptionBubble` 的 `aiText` 第二行显示 `streamingAiText`（黑底圆角），普通消息流中不额外显示流式 AI 气泡（Android 消息列表只有 final）。

### 2.7 模式回声/重连/错误（关键纪律）

`ConversationListenerAdapter.kt`：
- `onSessionReady`：服务端每次 `session.start`（含重连/恢复）把模式重置回 chat；先清 `pendingModeChange`，显式复位 translate/lecture/transcribe。**注意当前 Android 代码未复位 omni**（`ConversationListenerAdapter.kt:260-263` 只置前三个 false）；iOS 设计应四 flag 全 false（修正该遗漏，见 §13 S4）。
  若有 `lastModeRequest`（用户最近一次主动模式）→ 乐观置对应 flag（translate+lecture / transcribe / omni），`pendingModeChange=request.mode`，通知 VM 补发 `session.change`；
- `onSessionChanged(mode, myLanguage, localLanguage, usecase)`：清 pending；按 mode 设置四 flag；`lecture = mode=="translate" && usecase=="lecture"`；
- `onError(code=1009)`（`session.change` 被拒专属码）：仅当有 pending 时回滚被拒模式 flag，清 pending 与 `lastModeRequest`（避免每次重连“乐观置位→被拒→弹错”循环）；其它错误（1001 ASR/1007 omni 流/1008 lecture 降级/网络）**不回滚、不清 pending**；
- 离开会话/新建会话（`clearSession`）：清四 flag、pending、last request；
- 断连（非 CONNECTED）不清模式 flag；重连后由 `sessionReady` 重置/恢复；
- 模式不持久化到磁盘；App 重启回 chat；`avatarVisible` 持久化；
- Android SDK 的 `sendSessionChange` 未做 session 建立门控（`SessionManager.kt:203-228` 直接 `encodeAndSend`）；iOS SDK 在 `sessionStartedOnConnection=false` 时丢弃 `session.change`（`SessionManager.swift:101-110`）。因此 iOS 应在 App 层按 sessionReady 显式门控（见 D5），不能照搬 Android VM。

### 2.8 文案与图标

| 模式 | 文案 en/zh | 徽章图标（Android） | 徽章 a11y |
|---|---|---|---|
| 对话/听众/翻译 | Live translation / 同声翻译；Conversation/Lecture | Translate | Translate mode / 翻译模式 |
| 转录 | Transcribe mode / 转录模式 | Subtitles | Transcribe mode |
| 全模态 | Omni / 全模态；Omni mode | AutoAwesome | Omni mode / 全模态模式 |

Android 语言候选固定 14 个；默认 en；自动识别置顶；`conv_picker_confirm`「OK/确定」；`conv_enable`「Enable/开启」。

---

## 3. iOS 现状与差距

> 本节是设计前的 iOS 现状快照；实施后的实际文件与行为见 §15。

| 能力 | iOS 当前事实 |
|---|---|
| SDK 模式发送 | `LivaChatSDK.sendSessionChange(mode:myLanguage:localLanguage:usecase:)` 已存在（`LivaChatSDK.swift:373`），内部 `sessionStartedOnConnection` 守卫（`SessionManager.swift:101`） |
| SDK 模式事件 | `.sessionChanged(mode:myLanguage:localLanguage:usecase:)` 已存在（`LivaChatEvent.swift:38`）；SDK 解析 payload（`SessionManager.swift:195`） |
| SDK 协议 | `LivaChatSDKProviding` 只有 connect/startSession/interrupt/state/events（`ConversationProtocols.swift:5`），**没有 sendSessionChange** |
| Coordinator | `.videoAck, .sessionChanged` 与 `.jobStatus` 分支中，`.sessionChanged` 当前直接 `break`（`ConversationCoordinator.swift:464`）；无模式 flag/pending/last request |
| ViewModel | 无 `isTranslateMode/isLectureMode/isTranscribeMode/isOmniMode`、四模式 picker/dialog、transcribe source |
| 大头像 | 静态 96pt 渐变圆 + `~`（`ConversationView.swift:294-331`），无点击、无自定义照片；Agent Job badge 在右侧 64pt 槽。**拍板保持 96pt、不移植 Android 120 外框/光环**（只补点击/照片/模式徽章） |
| 头像隐藏 | 无 `avatarVisible`；大头像始终显示；标题栏无隐藏头像簇 |
| 模式徽章 | 无 |
| 头像自定义 | 无 sheet/crop/camera/持久化；仅聊天相册按钮复用了 `PHPickerView` |
| 图片选择 | `PHPickerView` 只返回 Data（`PHPickerView.swift`）；无相机静态拍照（现有 `CameraController` 是实时视频流，不是 still capture）；无裁剪 |
| 历史 mode | DB 已有 `messages.mode` 列（`HistoryDatabase.swift:250`），但 `HistoryService.processAppend` 写入固定 `mode: nil`（`:251`），`chatMessages(from:)` 加载未映射 mode；`ChatMessage` 无 mode（`ConversationModels.swift:9-18`） |
| 消息后缀 | 无；`MessageBubbleView` 不含模式后缀 |
| 听众第二行 | `ConversationView.transcriptionOverlay` 只有转写文本 + dots（`:493`）；CameraModeView 另有转写/AI 覆盖层，无 lecture 第二行 |
| 音频源 | `AudioSessionController.activate` 固定 `.voiceChat` + `setVoiceProcessingEnabled(true)`（`AudioSessionController.swift:41-58`）；`AudioCaptureController` 无 RAW/VOICE 切换；`AudioProtocols` 无 capture source 抽象 |
| 设置 | `SettingsStore` 用 UserDefaults，已有 haptic/camera 等偏好（`SettingsStore.swift:43-77`）；无 avatarVisible |
| 布局 | header 44pt；大头像行 HStack（64 左槽 + 8 + 96 头像 + 8 + 64 右槽 = 240）；Agent Job 面板顶部 180pt；相机模式下 header 显示 job badge |
| 测试 | 已有 ConversationView/Coordinator/History/AgentJob 模拟器+真机/真实 server 测试框架；无模式/头像 fixture |

---

## 4. 设计决策

### D1 两个独立模型：`AvatarCustomizationModel` + `ConversationModeModel`

- **AvatarCustomizationModel**（`@MainActor ObservableObject`，AppContainer 持有）：
  - `@Published avatarImage: CGImage?`、`avatarVisible: Bool`、`isSheetPresented`、`route: AvatarRoute?`、`cropSourceData: Data?`、`hasCustomAvatar`、`isSaving`、`errorMessage`；
  - **状态层不得使用 UIKit**（`docs/ios_dev_rules.md` §2；同时避免 Swift 6 跨 actor 传递 `UIImage`）：模型/Store 只传 `Data`/`CGImage`；`UIImage` 只允许出现在 `CameraPhotoPicker`（`UIImagePickerController` 适配层）内部，并在边界立即转成 JPEG `Data`；`AvatarView`/裁剪页用 `Image(decorative: cgImage, ...)` 渲染；
  - 持有 `AvatarStoring` + settings writer；启动异步加载本地头像；保存/删除；
  - 由 ConversationView 观察；不放进 ConversationViewModel（VM 已负责消息/相机/模式等）；`errorMessage` 由 ConversationView 顶部 banner/toast 展示（保存失败时面板已关，不能只写模型字段）；
  - `avatarImage` 解码失败/文件损坏时置 nil，但 `hasCustomAvatar` 由文件是否存在决定（仍允许「恢复默认头像」）；`cropSourceData` 在确认/取消后清空；`remove` 失败保留旧头像并报错。
  - **AvatarStore（actor，FileManager/ImageIO）**：
    - 正式目录 `Application Support/LivaChat/avatar/`（与 `HistoryService` 同一 app 数据根；UI test 使用独立临时目录）；
    - 文件 `custom_avatar_<epochMs>.jpg`，读取最新一份；保存先写唯一新文件，成功后再删旧文件；失败清理半截文件，不丢旧头像；`remove` 删除目录内全部；
    - 解码在后台、长边降采样到 2048、EXIF orientation 归一，返回 `CGImage`/`Data`（不返回 `UIImage`）；裁剪结果 JPEG 90；裁剪 source/临时文件放 `tmp` 并在确认/取消后清理；
    - 头像不上传、不写 History DB、不依赖 Keychain/权限。
- **ConversationModeModel**（`@MainActor ObservableObject`，AppContainer 持有）：
  - 四模式 flag、四模式 picker/dialog 状态、`transcribeSource`、`isSessionReady`、`pendingRequest`、`lastRequest`；
  - `sendRequest: (@MainActor (ModeChangeRequest) -> Void)?` 由 Coordinator 以 `[weak self]` 注入；Coordinator 读取 model 的 flag 决定消息 mode；
  - View 观察它渲染 badge/sheet/dialog；Coordinator 在 `.sessionChanged/.sessionReady/.error(1009)/stop` 更新它；
  - 这样模式状态在 View/Coordinator 间单一事实源，避免 ConversationViewModel 与 Coordinator 双写。
  - **生命周期**：Coordinator stop/settings apply 时 `sendRequest=nil` 并 `reset()`；新 runtime 创建后重新注入；否则旧的 Coordinator 会被闭包强持有并继续接收新 UI 的请求（S11）。
- 两者都按 AgentJobStatusModel 的注入模式：
  - AppContainer 创建 → RuntimeFactory/Coordinator 注入 → AppRootView → ConversationView；
  - 既有 Coordinator/VM 测试用可选参数（默认 nil），新测试显式注入。
  - AppContainer 在 `openHistory/openSettings` 前调用两个模型的 `dismissOverlays()`；settings apply 重建 runtime 时 `modeModel.reset()`，头像模型不清（本地持久化）。
- 备选：放进 ConversationViewModel（Android 方式）；已拍板不采用，见 §11 #3。

### D2 头像渲染（保持 iOS 96pt，无光环）

> 2026-10-21 拍板：**不移植 Android 120pt 外框与状态光环**，保持 iOS 现有 96pt 圆形头像；仅新增点击、自定义照片、模式徽章。

- 新增 `AvatarView`：
  - 单层 96pt 圆形：custom image `Image(decorative: cgImage)` `.scaledToFill()` + `clipShape(Circle())`；否则沿用现有渐变 + `~`（**现有 `.appFont(.headline)`，不改 Android 28pt/headlineLarge**）。
  - 可点击（`Button`/`onTapGesture`，无 ripple）；触控 96；`accessibilityIdentifier("conversation_avatar")`；按钮 label 用 `conv_avatar_picker_title`（动作语义），有模式时把模式名并入 label；**不要**因自定义照片把按钮 label 改为 `avatar_custom_image_desc`（用户会失去“点头像可更换”的提示）——照片本身 visual-only，desc 仅用于非按钮场景。
  - **无状态光环、无 Crossfade 状态动画**；连接/会话状态继续由 header 状态文案/颜色表达。`Reduce Motion` 只需覆盖面板/弹窗等新增动画，不涉及头像。
  - **徽章不参与圆形裁剪**：`.clipShape(Circle())` 只加在 96pt 照片/渐变上；ModeBadge 作为外层 `.overlay(alignment: .topTrailing)`（26pt）并 `.allowsHitTesting(false)`，否则会被裁掉或吞掉头像点击（S6）。
- 大头像区域**保持现有 iOS 布局**：无额外顶部 padding；外框 96；下方消息区自然上移/回落；隐藏时不渲染，消息上移 96pt（不是 Android 的 128pt）。
- **与 Agent Job 的兼容**：
  - 保持右侧 AgentJobStatusBadge 的 44pt 触控/64pt 槽；模式徽章叠在大头像右上（26pt）。
  - 头部 44 + 头像 96 = 140pt；Agent Job 面板顶部 180pt 不变，间距约 40pt；仍回归 Agent Job 面板布局/截图，但不因头像尺寸改锚点。
  - 大头像区结构：左 64 槽 + 8 间距 + 96 头像 + 8 间距 + 右 64 槽（总 240，居中；沿用现有 `HStack(spacing: AppSpacing.s)`）。
- 头像按钮在相机模式下不渲染（Android 当前）；隐藏态标题栏按钮仍可打开面板（见 D3）。

### D3 正常/缩小与标题栏头像簇

- `avatarVisible` 默认 true；持久化 `SettingsStore` key `settings.avatar_visible`（UserDefaults；对应 Android DataStore `avatar_visible`）。
- 「正常/缩小」pill 在头像面板「头像选择」标题右侧；视觉与 Android 一致（SurfaceVariant 容器 2pt padding，选中 BrandIndigo/TextPrimary，未选透明/TextSecondary，段 12/4 padding）；**视觉约 28pt，但 iOS 将两段触控扩展到 ≥44pt**（HIG；不与 Android 接受 28dp 偏离冲突）。注意 `.contentShape` 不能无限扩张命中区；若实现必须抬高标题行到 44pt，应接受该布局差异并用 UI 测试验证，不能只写“视觉不变”而实际热区不达标（S9）。
- 点击 pill：立即更新 `avatarVisible` 并持久化，不关闭面板；无动画（Android）。
- 隐藏态：
  - 大头像区整块不渲染；消息区上移 96pt（iOS 头像 96，无额外 padding）；
  - header 在 TopBar 内容上叠加**固定居中簇**：`AvatarTitleBarButton(视觉 36pt/触控 44pt) + 8pt + AgentJob 固定 44pt 槽`；簇宽 96pt；**整行居中后再偏移 `(44+8)/2 = 26pt`，使头像圆心而不是簇中心落在内容中线**（Android 对应 `(48+8)/2 = 28dp`，见 `ConversationScreen.kt:455-485`）；不随状态文案/job badge 出现移动；
  - 头像按钮外层不裁剪：可点击容器/视觉圆只裁 36pt 内圆；ModeBadge 20pt 放外层 bottom-end 并 `.allowsHitTesting(false)`，否则徽章会被裁切或抢走头像点击（S5/S6）；job badge 在右侧槽；
  - 点击头像按钮 → 头像面板；点击 job badge → Agent Job 面板（不变）。
- **header 44pt 的适配**：
  - Android TopAppBar 可容纳 48pt 触控；iOS header 44pt 固定，采用 44pt 触控/36pt 视觉，避免改导航栏高度（§11 #2 已拍板）。
  - 状态文案在隐藏态使用 `GeometryReader` 约束最大宽度：**偏移后的头像触控左缘 `clusterLeft = contentCenter - 22pt`**。**整个 status button** 最大宽度 = `clusterLeft - leading16 - gap8`（iPhone 393：174.5−16−8 = 150.5pt）；其中 Text 还要再减 Circle 8 + 间距 8 → **Text 最大 134.5pt**（不要只把 150.5 给 Text，否则会压到簇 8pt）。`lineLimit(1)`/尾部截断；状态无障碍 label 保持全文；iPad 600 宽自动放宽。正常态不约束。
  - 菜单按钮 44pt 在右侧；簇与菜单不重叠（iPhone 393：偏移后簇右 270.5，菜单左 333，间距 62.5）。
- **相机模式矩阵**（Android 代码事实 + 明确 iOS 保留项）：
  - `avatarVisible=true` + 相机：大头像不显示；**iOS 保留现有 header job badge**（Android 在 `avatarVisible=true` 相机态并不渲染 job badge；这是本次刻意保留的 iOS 既有能力，避免相机态丢失 Agent Job 入口，**已拍板保留**）；**不显示模式徽章/头像入口**（Android 无隐藏簇）；
  - `avatarVisible=false` + 相机：header 显示隐藏头像簇（头像/模式/job 都在簇内）；**抑制现有相机 header 的独立 job badge**，避免重复；
  - `avatarVisible=false` + 非相机：隐藏簇 + 无大头像；
  - 相机模式头像面板入口仅在隐藏簇存在时可用（Android 同）。
- 标题栏 job badge 的 `badgeFocused` 焦点：三处渲染（大头像右槽、相机 header、隐藏簇）只允许一个存在；Agent Job 面板关闭后焦点回到当前可见的那个 job badge。

### D4 头像面板（自定义 Bottom Sheet）

iOS 15 无 `presentationDetents`；实现自绘底部面板，匹配 Android ModalBottomSheet：
- 透明 scrim（tap → dismiss；Android ModalBottomSheet 有 scrim；使用 `Color.black.opacity(0.4)`），面板贴底、Surface、顶部圆角 24、水平 24/bottom 16 padding、安全区适配；
  - **覆盖范围**：ConversationView 位于 AppRootView 的 `maxWidth 600` 内容列内，自绘 scrim/面板默认只覆盖该内容列，不是物理全屏；本次采用“内容列内全屏”即可（与现有对话页一致）；若要求物理全屏，需把 overlay 挂到 AppRootView，另行评估。
- 内容可滚动（Dynamic Type/小屏）：`ScrollView`；面板最大高度 `rootHeight - 80`（或 `0.82 × height`），不小于内容；
- 结构：
```
「同声翻译」SectionHeader（BrandIndigo/label）
  switchRow ×4（title/desc + Toggle；minHeight 48pt，随字号增高）
divider（SurfaceVariant，8pt 垂直）
「头像选择」SectionHeader + 正常/缩小 pill
  avatarOption minHeight 48pt ×2/3（相册/拍照/恢复默认）
```
- 开关行：title 14 label/TextPrimary、subtitle 12 caption/TextSecondary；Toggle 使用 `.tint(brandIndigo)`；Switch 本身可点（行不整体可点，对齐 Android）；**不要固定 48pt 高度**，否则 AX 字号下两行文本会裁切；
- 开关互斥：**ready 时**同 Android（无激活模式时四个可开启；有激活模式时仅当前激活模式可关闭，其它禁用）；**未 ready 时**仅当前激活模式的开关可关闭，其余全部禁用（D5 本地退出规则）。
- 头像项：图标 24、16pt 间距、text 14、minHeight 48pt；随 Dynamic Type 增高；点击：
  - 相册/拍照：发起 picker/camera 并关闭面板；**必须等 picker/相机 cover 的 `onDismiss` 完成后再设置裁剪 route**，禁止在 sheet/cover 正在消失时呈现下一个全屏页；沿用现有 GalleryPreview 的 pending 状态模式（S10）；
  - 恢复默认：关闭面板并删除文件；删除失败保留旧图并展示错误；
- 模式确认：关闭面板；模式选择取消：保留面板；模式关闭：保留面板；
- 保存失败：面板已关，`errorMessage` 由 ConversationView 顶部 banner/toast 展示，不能静默；
- 面板打开时底层对话 `accessibilityHidden(true)` + 面板 `.accessibilityAddTraits(.isModal)`；关闭焦点回当前头像按钮；不覆盖/不打断 Agent Job 面板（用户路径上 Avatar 被 job scrim 遮挡，不会同时出现）。
- **显式关闭入口**：面板右上角 ×（44pt 触控）+ 面板级 accessibilityAction（Close）；不能只依赖 scrim tap/下滑手势，否则 VoiceOver 用户无法关闭（S19）。

### D5 模式模型与会话状态机

**`ConversationModeModel` 状态**
```swift
@Published private(set) var translateEnabled: Bool
@Published private(set) var lectureEnabled: Bool
@Published private(set) var transcribeEnabled: Bool
@Published private(set) var omniEnabled: Bool
@Published var isLanguagePickerPresented: Bool
@Published var languagePickerTarget: LanguageTarget? // .dialogue/.lecture
@Published var isTranscribeSourcePickerPresented: Bool
@Published var isOmniIntroPresented: Bool
@Published private(set) var transcribeSource: TranscribeAudioSource // .voice default
@Published private(set) var isSessionReady: Bool
private var pendingRequest: ModeChangeRequest?
private var lastRequest: ModeChangeRequest?
var sendRequest: (@MainActor (ModeChangeRequest) -> Void)?
```

- `ModeChangeRequest`：
  - `mode: SessionMode`；`myLanguage/localLanguage: String?`；`usecase: SessionUsecase?`；
  - transcribe 的 source 是本地字段，不进 request payload（与 Android 一致）。
- **用户操作**：
  - 对话/听众开关关闭 → `exitToChat()`：四 flag 清、**pendingRequest=nil**、lastRequest=nil、关闭所有 picker/dialog、采集源恢复 voice、不关面板；**ready 时** send `.chat`，**未 ready 时只本地退出、不发送**（SDK 会丢弃；避免重连被 `lastRequest` 强制恢复）；
  - 打开 → 只展示语言选择（不改 flag、不发送）；确认 → `requestTranslate(target, language)`：
    - `myLanguage = Locale.current.languageCode ?? "en"`；对话 `localLanguage = selected`（`auto` 原样）；听众 `localLanguage = selected=="auto" ? "en" : selected`；`usecase = .dialogue/.lecture`；**iOS 15 兼容：`Locale.current.language`/`Locale.Language` 是 iOS 16+，不能用；`Locale.current.languageCode` 虽在 iOS 16 deprecated 但 iOS 15 可用（S1）**；
    - optimistic flags；`pendingRequest=request`；`lastRequest=request`；关闭 picker + 关闭头像面板；`sendRequest(request)`；
  - 转录开关关闭 → `exitToChat()`；打开 → 源选择；确认 → `requestTranscribe(source)`：
    - 设 `transcribeSource`；optimistic `transcribeEnabled=true`；`pendingRequest`/`lastRequest` 都记 transcribe request；关闭 picker/panel；Coordinator 先按 source 配置本地采集源（D6），再 send `.transcribe`；
  - 全模态关闭 → `exitToChat()`；打开 → 说明弹窗；确认 → optimistic omni；`pendingRequest`/`lastRequest` 记 omni；关闭；send `.omni`；
- **音频源规则**：采集源跟随“生效模式”（echo/sessionReady/回滚结算后的模式，而不是只看请求意图）：
  - 生效模式 = transcribe → 应用 `transcribeSource`；其它（chat/dialogue/lecture/omni）→ voice。
  - 触发点：`requestTranscribe`、`requestTranslate/requestOmni`、`exitToChat()`、**1009 回滚**、**`.sessionChanged(chat)`**、**sessionReady 无 lastRequest 或恢复 translate/omni**、stop/settings、engine/route 重建。
  - transcribe source 应用失败：保持/回退 voice，并把 `transcribeSource` 设为 `.voice`、显示 banner；仍可发送 transcribe（实际 voice）——避免 UI 显示 RAW、实际 voice 的静默错配（S8/S16）。
- **模式与 flag 的关系**：`translateEnabled` 与 `lectureEnabled` 可同时为 true（lecture = translate + lecture）；对话 = `translate && !lecture`；转录/全模态各自单独。**任何时刻最多一个“模式”激活，不是“四个 flag 最多一个 true”**（S14）。
- **开关 on/enabled**：
  - on：对话 = translate && !lecture；听众 = lecture；转录 = transcribe；全模态 = omni（再加 picker/dialog 打开的 on 态，见 D6）。
  - enabled：ready 且无激活模式 → 四个都可开启；有激活模式 → 仅当前激活模式的开关可关闭，其它禁用；未 ready → 仅当前激活模式的开关可关闭（本地退出），OFF 的全部禁用。
- **服务端回声**：
  - **`isSessionReady=false` 时忽略 `.sessionChanged`**（不更新 flags/pendingRequest）；旧 session 的回声不可信，等 sessionReady 重置/恢复（S15）；
  - ready 时 `.sessionChanged(mode,usecase)` → `pendingRequest=nil`；flags 由 mode/usecase 设置（translate+lecture、transcribe、omni、chat；usecase nil/dialogue 视为对话，lecture 为听众）；按音频源规则应用 voice/transcribe source；**必须应用服务端改模式的 echo（如 1008 lecture 降级），不能按 pending mode 拒绝**；不关 picker（picker 已在确认时关）。
- **1009 被拒**：
  - 仅 `pendingRequest` 存在时：清 pending，回滚 `pendingRequest.mode` 对应 flag（translate+lecture/transcribe/omni）到 chat，清 `lastRequest`，采集源恢复 voice；现有 `.error` 仍展示 banner；
  - 其它错误码不碰模式状态/pending（对齐 Android）。
- **sessionReady / isSessionReady 切换**：
  - **进入未就绪**：`.connectionStateChanged` 非 `.connected`、`.connectionTerminated`、`.sessionEnded`、`start()`/`stop()`/`retry()`/`.connectionEpochAdvanced` 发起新 session 前、settings apply 均置 `isSessionReady=false` 并 **清 pendingRequest**（旧 session 的回声/1009 不再可信）；只有 `.sessionReady` 置 true（Coordinator 已有同名局部变量 `sessionReady`，见 `ConversationCoordinator.swift:311-317/361-375`，直接镜像到 model，禁止只设 true 不设 false）；
  - 服务端每次 session.start 将模式重置 chat；set `isSessionReady=true`，清 pendingRequest、四 flag chat（含 omni，见 S4）；
  - 若 `lastRequest`：乐观置 flags，`pendingRequest=lastRequest`，`sendRequest(lastRequest)`（重连恢复；由 Coordinator 在 `.sessionReady` 调用）；transcribe 的 request 不含 source，恢复时按 `modeModel.transcribeSource` 重新应用本地采集源后再 send；translate/omni → voice；
  - 若没有 lastRequest → chat + voice。
- **session 未就绪**：
  - `isSessionReady` 为 false 时：**OFF 的模式开关禁用、picker/dialog 的确认拒绝**（不能开启/切换）；**已激活模式的开关仍可关闭**，走本地 `exitToChat`（清四 flag/pendingRequest/lastRequest、恢复 voice、不发送 chat），避免断开/结束后无法取消恢复；
  - 等待 `.sessionReady` 恢复；重连 ready 按 `lastRequest` 重置/恢复；
  - 断开连接不清模式 flag（Android）；badge 可暂时保留；重连 ready 重置/恢复。
- **sender 生命周期**：`sendRequest` 由 Coordinator 以 `[weak self]` 注入；Coordinator stop/settings apply 前置 nil；新 runtime 创建后重新注入；避免旧 Coordinator 被闭包强持有并接收新 UI 的请求（S11）。
- **生命周期/清空**：
  - `stopRuntime`/settings apply → `reset()`：flags chat、清 pendingRequest/last、关闭所有 picker/dialog、isSessionReady=false；头像/自定义头像不清（本地持久化）。
  - 历史 `sessionSwitched`（删除当前/清空）→ **已拍板保留模式状态**：WS session 未重启，server 模式仍有效；清空 UI 会导致徽章/后缀与 server 不一致（与 Android clearSession 不同，见 §11 #6）。
  - **sessionEnded 语义覆盖两条路径**：SDK `.sessionEnded` 与 `.connectionTerminated(.serverClosed)`（Coordinator 两者都置 `sessionEnded=true`）。两条都 **清 UI flags + pendingRequest、关闭 picker/dialog、保留 lastRequest**；未 ready 时 UI 已清空、没有可关闭的开关，若不想恢复，重试进入 ready 后关闭模式即可。媒体已停止，下次 sessionReady 按“无 last→chat+voice / 有 last→恢复对应 source”处理（S18）。
  - App 重启：模式回 chat；lastRequest 丢失；头像/avatarVisible 保留。
- **与 Android 的已知差异**：sessionReady 的 omni 复位（S4）、相机态 job badge（D3）、isSessionReady 门控与未 ready 时允许本地关闭（§11 #8/§14）、进入未就绪清 pendingRequest + not-ready 忽略 `.sessionChanged`（S15）必须在实现记录中标注。ready 时 echo 仍按 Android 语义应用（含 1008 降级）；本地退出与旧 echo 的窄竞争作为残余风险（见 §13）。
- **发送 payload 表**：
  | 模式 | `mode` | `myLanguage` | `localLanguage` | `usecase` | 本地音频源 |
  |---|---|---|---|---|---|
  | 对话 | `.translate` | 设备语言（如 zh/en） | 所选（可 `auto`） | `.dialogue` | Voice |
  | 听众 | `.translate` | 设备语言 | 所选，auto→en | `.lecture` | Voice |
  | 转录 | `.transcribe` | nil | nil | nil | 所选 RAW/VOICE |
  | 全模态 | `.omni` | nil | nil | nil | Voice |
  | 对话/回 chat | `.chat` | nil | nil | nil | Voice |

### D6 语言/音源/全模态选择 UI

- **LanguagePickerDialog**（对齐 Android AlertDialog）：
  - 深色 Surface 圆角 16/20、居中卡片、scrim；标题居中：
    - 对话「选择对方语言」；听众「选择演讲者的语言」；
  - 一行下拉选择器（SurfaceVariant 圆角 12），显示当前语言名；点击展开候选；
  - 候选：`auto`（「自动识别」，仅置顶）+ `zh,en,ja,ko,fr,it,de,es,pt,ru,hi,id,th,vi,ar`（15 个），Android/iOS 已对齐；名称按 **App UI locale** 本地化（用 `Bundle.main.preferredLocalizations.first` 构造 Locale 后 `localizedString(forLanguageCode:)`；不要用裸 `Locale.current`，它可能是系统语言而非 App 语言；Android 用 `LocalConfiguration.current.locales[0]`）；默认 `en`；
  - 底部「取消」「确定」居中；
  - 对话 auto 原样传给 request；听众在 confirm 前归一为 `en`；
  - 取消 → 关闭 dialog、保留头像面板；确定 → 请求模式、关闭 dialog/面板；
  - 不持久化语言；每次打开默认 en（Android）。
- **TranscribeSourceDialog**：
  - 标题「选择现场录音模式」；两行 radio（原声/保留现场原始声音；语音/针对语音增强）；默认当前 `transcribeSource`（首次 voice）；
  - 取消/确定居中；确定 → 请求转录、关闭 dialog/面板；取消保留面板；
  - **iOS 音频源映射（高风险，需设备探针）**：
    - 事实：iOS 当前固定 `AVAudioSession.playAndRecord + .voiceChat` 并启用 input voice processing（`AudioSessionController.swift:41-58`）；`AudioCaptureController` 无 source 概念。
    - 设计：新增 `AudioCaptureSource { original, voice }`；voice = 当前 `.voiceChat` + voice processing；original = 关闭 input voice processing（必要时切 `.measurement`/`.default`）。
    - 切换需要在 capture/engine 层受控重建（stop tap/engine → 改 voice processing/mode → start），可能造成短暂音频中断；播放与采集共享 AVAudioEngine，需真机验证。
    - source 切换串行化并带 generation/epoch：exit/1009/`sessionEnded` 等新状态发生后，过期的切换完成不得覆盖最新生效模式（防止 RAW 重新泄漏到 chat）。
    - 进入转录前应用 source；离开转录恢复 voice；重配置失败保留 voice 并显示 banner，同时把 `modeModel.transcribeSource` 置为 `.voice`（UI/下次 picker/重连 restore 都反映实际生效值，避免显示 RAW 实际 voice，S16）。
    - **中断/重建后重新应用**：`onBackground`→`onForeground` 的 `stopMedia/startMedia`、audio interruption、route change、media services reset 后，按当前 `(transcribeEnabled, transcribeSource)` 重新应用 source（voice processing 可能被 `activate()` 重置）；不能只在模式切换时应用一次。
    - **不得猜测**：实施前用真机探针验证 `setVoiceProcessingEnabled(false/true)` 运行时切换、route/AEC 行为、engine 重建后的状态；若不可行则按 §11 #4 拍板分支（只提供语音/隐藏原声，实施时回报）。
    - **实施顺序**：探针必须在模式 UI（§9 第 7 步）之前完成；若 original 不可行，先拍板隐藏原声/只保留语音，再实现 TranscribeSourceDialog，避免 UI 返工（S8）。
- **OmniIntroDialog**：
  - 标题「全模态」；说明「使用全模态AI模型，可以降低交互延迟」；取消/开启；确认 → 请求 omni；取消保留面板。
- 模式 dialog 打开时，头像面板保留；dialog 的 scrim 覆盖面板；模式开关按 Android 的 `mode || picker/dialog` 显示 on。
- 所有 dialog：深色 Surface、TextPrimary/Secondary、BrandIndigo 主按钮，按钮居中，外部点击/取消关闭；`.accessibilityAddTraits(.isModal)` + 底层隐藏。
- 所有 dialog 内容在 AX 字号/小屏下可滚动或换行；语言候选列表可滚动；按钮不得因固定单行高度被裁切（S9）。

### D7 模式徽章与消息后缀

**徽章**
- `ModeBadgeView`：26pt（大头像）/20pt（隐藏簇）；BrandIndigo 0.7 圆、1.5pt Surface 描边、图标 14/11；模式优先级 translate → transcribe → omni；无模式不渲染；
- iOS SF Symbols 候选：`character.bubble`（翻译）、`captions.bubble`（转录）、`sparkles`（全模态）；lecture 共用翻译图标。符号可用性以 iOS 15 deployment target 编译/SF Symbols 校验为准，不在设计阶段假定（避免猜符号名）；
- 视觉 `.accessibilityHidden(true)`，模式名称合并进头像按钮的 accessibility label（如「头像，翻译模式」），避免重复读；或作为独立带 label 元素，二者取一（推荐合并）。
- 大头像：ModeBadge `.overlay(alignment: .topTrailing)`，且 `.allowsHitTesting(false)`、放在裁剪圆外；隐藏簇：bottomTrailing 20pt；相机 + avatarVisible=true 无徽章（Android）。
- 自定义照片不改变按钮 label；`avatar_custom_image_desc` 只用于非按钮场景，否则会替代“头像选择”的动作语义。

**消息模式数据**
- `ChatMessage` 增加 `mode: ChatMessageMode?`；`enum ChatMessageMode: String { case translate, transcribe }`（或直接 raw string；推荐 enum）。
- Coordinator 在消息 final 时写：
  - user final：transcribe > translate（含 lecture）；
  - assistant final：translate；
  - 流式 AI 不写 mode；final 才写；
- `ConversationViewModel.finishUserTranscription/finishAssistantMessage` 增加 `mode` 参数；`appendMessage` 接收；
- **历史**：
  - `PendingMessage`/`HistoryMessageRecord.mode`（DB 列已存在）；
  - `ConversationHistoryRecording.recordUserMessage/recordAssistantMessage` 增加 `mode`；
  - `HistoryService.processAppend` 写 `mode`；`chatMessages(from:)` 读 `record.mode` 到 `ChatMessage.mode`；
  - **无需 schema migration**：`HistoryDatabase` 自历史 v1 起 `messages` 表就含 `source`/`mode`，`append` 已 bind、`messages(sessionID:)` 已 SELECT/映射；本次只改 `HistoryService` 的传值/读值；
  - 旧行 `mode=nil` → 无后缀；不迁移、不拆旧文本；
- **展示**：
  - `MessageBubbleView` 计算 suffix：transcribe→"(转录)"/"(transcript)"；translate+user→"(原文)"/"(original)"；translate+AI→"(译文)"/"(translation)"；
  - 显示 `text + " " + suffix`；AI Markdown 把 suffix 拼到 markdown 文本末尾（Android 同）；用户 Text 同；**仅在 `message.text` 非空时拼接**（纯图片 AI 当前不存在，但不得生成只有后缀的文本）；
  - 折叠 agent 结果：在折叠前缀后加 suffix（与 Android 展示链一致）；全屏结果不拼 suffix；
  - accessibility label 使用带 suffix 的 displayText（用户）；AI Markdown 由 Markdown 可访问文本读取，suffix 作为正文末尾；
  - 历史详情复用 MessageBubble，自动显示。
- **听众第二行**：
  - 对齐 Android `TranscriptionBubble`：保留单个 BrandIndigo 0.88 圆角 12 外气泡，内部 VStack；当 `modeModel.lectureEnabled` 且 `streamingAIText` 非空，上方显示 AI 译文（TextPrimary/body 16pt，黑底 0.85 圆角 8，水平/垂直 8/4，底部 4pt 间距），下方原文 + dots；**不是两个独立背景的气泡**；仅当 `currentTranscription` 非空时显示（沿用现有 overlay 条件）；
  - 普通消息列表在 lecture 模式**不渲染流式 AI 气泡**，避免双显；final AI 消息进入列表并带 `(译文)`；
  - CameraModeView 现有 AI/transcription 覆盖层保持；模式徽章/后缀按全局模式（其文本不加后缀，保持现有相机视图行为；§11 #10 已拍板）。
- 模式消息示例：
  - 对话/听众：用户「こんにちは (原文)」、AI「Hello (译文)」；
  - 转录：用户「... (转录)」；AI（如有）无翻译后缀（Android AI 只在 translate 加）；
- 模式徽章和消息后缀使用当前模式（乐观 flag 或 echo）；1009 回滚只影响后续消息，不重写已落库消息。

### D8 音频源与页面行为

- 对话/听众/全模态：不改变采集源（voice）。
- 转录：应用所选源；进入前配置；离开/chat 恢复 voice。
- 页面：
  - 非 chat 模式不提供屏幕录制（iOS 无；无需 gating）。
  - 相机模式/头像/模式入口矩阵见 D3。
  - **相机模式 + 隐藏簇取头像**：相册/裁剪不占用相机硬件，可保持实时 `AVCaptureSession`；但「拍照」路径必须先 `stopCamera()`（系统相机与实时采集可能冲突），完成/取消后按设置恢复（若真机探针仍发现冲突，则退化为该状态禁用「拍照」，见 §11 #11）。若实现选择在打开头像面板时停止相机，则需在整个面板/裁剪结束后恢复。
  - 会话结束后模式按 D5 决策处理；重连按 Android 恢复。
  - 历史/设置：`AppContainer.openHistory/openSettings` 在设置 `router.showHistory/showSettings` 前调用 `avatarModel.dismissOverlays()` 与 `modeModel.dismissOverlays()`（模型由 AppContainer 持有；Agent Job 已先 `dismissPanel`）。仅写“应关闭”不够，必须给容器可调用的入口。
  - settings apply 重建 runtime：`modeModel.reset()` + `sendRequest` 重新注入；头像模型不清。自定义头像/avatarVisible 不被设置重建清除；模式/语言/源随 runtime stop 重置（source 回 voice），新 runtime 从 chat 开始。
  - UI test 隔离：默认将 `avatarVisible` 置 true（除非 `-uiTestAvatarHidden`），避免 UserDefaults 残留污染后续用例；自定义头像的“重启保留”用固定测试目录，不能用每次启动 UUID 的临时目录（S13）。
  - 后台：模式不清；头像不清；重连恢复。
  - App 重启：头像/avatarVisible 保留；模式 chat、语言/源默认。

---

## 5. 数据流

### 5.1 头像

```
启动：AvatarCustomizationModel.load() → AvatarStore.latestURL → 异步解码（CGImage）
  → AvatarView 显示自定义照片/默认渐变
avatarVisible ← SettingsStore (default true)

tap 大头像/标题栏头像
  → isSheetPresented = true
  → 面板：
      「正常/缩小」→ avatarVisible 更新 + 持久化（不关面板）
      「从相册选择」→ 发起 PHPicker → picker onDismiss → Data → 归一化/降采样 → 裁剪页
      「拍照」→ 权限检查 → UIImagePickerController(camera) → 适配层内部 UIImage → JPEG Data → onDismiss → 裁剪页
      「恢复默认头像」→ 关闭面板 → AvatarStore.remove → avatarImage=nil
  裁剪确认 → 生成正方形 JPEG 90 → AvatarStore.save（唯一文件名，成功后删旧）
    → avatarImage=新图；cropSourceData 清理
  裁剪取消 → 无变化

禁止在 picker/相机 sheet 消失过程中呈现裁剪页（用 pending 状态，见 D4/S10）。
```

### 5.2 模式

```
打开头像面板
  → toggle 对话/听众 → LanguagePickerDialog → 确定
      → modeModel.requestTranslate(...)
      → optimistic flag; pending; lastRequest; close sheet
      → Coordinator send session.change
  → toggle 转录 → TranscribeSourceDialog → 确定
      → modeModel.requestTranscribe(source)
      → optimistic flag; pending; lastRequest; close sheet
      → Coordinator apply capture source (voice fallback on failure)
      → send session.change(transcribe)
  → toggle 全模态 → OmniIntroDialog → 开启
      → requestOmni; restore capture voice; close sheet; send session.change(omni)
  → toggle 已激活模式 off → exitToChat; restore voice; panel stays
      → ready 时 send chat；未 ready 时只本地退出（不发送）

server session.changed → ready: applyEcho → badge/suffix/audio source 以 server 为准；
                        not ready: 忽略（旧 session 回声不可信）
server error 1009 → modeModel.rejectPending → 回 chat，清 lastRequest，source 回 voice; banner
disconnect/connectionTerminated → isSessionReady=false；清 pendingRequest；flags 保留
sessionEnded（SDK 事件 + connectionTerminated(.serverClosed)）→ isSessionReady=false；清 pendingRequest/UI flags；lastRequest 保留（重试恢复）
session.ready → reset flags; if lastRequest → optimistic restore + resend；audio source 按生效模式
stop/settings apply → reset（lastRequest 清）
history delete current → 已拍板保留（WS session 未重启）
```

---

## 6. UI 布局

### 6.1 正常头像（chat + avatarVisible=true）

```
header [status | ... | job badge(camera only) | menu]   高度 44
avatar row:
  [64 空槽] [AvatarView 96（96 图片/~） + ModeBadge 26 top-end] [64 槽: JobBadge 44 触控]
messageArea
toolbar
```

- 头像行无额外顶部 padding（保持现有 iOS 布局）；头像 96；模式徽章 26；iOS 继续用 HStack 右槽定位 job badge（Android 是 offset 叠放，两者视觉接近；仍 44 触控、视觉 28）；徽章不被 96 圆裁切且 `.allowsHitTesting(false)`；**无光环**。
- 大头像点击 → 头像面板。

### 6.2 隐藏态（avatarVisible=false）

```
header:
  [status (max width 约束)]   [ AvatarTitleBarButton 36/44 + ModeBadge 20 bottom-end ]  [JobBadge 槽 44]   [menu]
       (行整体居中后 offset +26，使头像圆心=内容中线；不随状态文案/徽章移动)
messageArea（上移 96）
toolbar
```

- 隐藏簇只在 `!avatarVisible`；头像 button 点击开面板；job badge 点击开 Agent Job 面板；徽章 `.allowsHitTesting(false)`、外层不裁剪。
- 相机 + avatarVisible=false：隐藏簇含 job badge；不渲染 header 独立 job badge。
- 相机 + avatarVisible=true：无隐藏簇；iOS 保留现有 header job badge（Android 无；**已拍板保留**）；无模式徽章/头像入口。

### 6.3 头像面板

```
scrim (black 0.4, tap dismiss)
 ┌────────────────────────────────────────┐
 │ 同声翻译                            [×] │
 │ 对话模式  实时翻译对话内容          [Toggle] │
 │ 听众模式  实时翻译演讲内容          [Toggle] │
 │ 转录模式  实时转录现场声音          [Toggle] │
 │ 全模态    使用全模态模型            [Toggle] │
 │ ────────────────────────────────────── │
 │ 头像选择                       [正常｜缩小] │
 │ [相册] 从相册选择                       │
 │ [相机] 拍照                             │
 │ [撤销] 恢复默认头像（有自定义时）         │
 └────────────────────────────────────────┘
```

- 内容可滚动、底部安全区、最大高度约 80%；圆角 24；Surface；switch/头像行用 minHeight 48，随 Dynamic Type 增高。

### 6.4 语言/音源/全模态 dialog

```
scrim
  深色卡片（圆角 16/20，Surface）
    标题（居中）
    语言：下拉字段（SurfaceVariant 圆角 12）→ 语言候选
    音源：两行 radio（原声/语音 + desc）
    说明文字（omni）
    [取消] [确定/开启]（居中）
```

- AX 字号/小屏下卡片内容可滚动；语言候选列表可滚动。

### 6.5 裁剪页

```
全屏 Background
  [取消]（左上，TextSecondary）
  「拖动图片调整位置」（顶部居中 caption）
  ZStack:
    图片（缩放/偏移，clip）
    圆外黑色 0.6 遮罩（EvenOdd）
    BrandIndigo 0.5 / 2pt 圆环（Ø192）
  [确认] minHeight 48pt BrandIndigo 圆角 12（底部，全宽，可随字号增高）
```

- 支持单指拖动 + 双指缩放；Reduce Motion 不影响手势；Dynamic Type 下 hint/按钮可换行/滚动。
- **VoiceOver**：裁剪图作为**可聚焦元素**（label「裁剪图片」+ hint「调整位置/缩放」），提供 Move Left/Right/Up/Down、Zoom In/Out 的 accessibility actions（如 5%/10% 步长），并保留 Cancel/Confirm 与提示；若图不可聚焦则 actions 不可达（S20）。

---

## 7. 字符串与本地化

iOS 当前 `Localizable.xcstrings` 支持 en + zh-Hans；新增键使用两侧文案（Android 同源）：

| 键 | en | zh-Hans |
|---|---|---|
| `conv_welcome_chip_translate` | Live translation | 同声翻译 |
| `conv_mode_dialogue` | Conversation mode | 对话模式 |
| `conv_mode_dialogue_desc` | Translate conversations live | 实时翻译对话内容 |
| `conv_mode_lecture` | Lecture mode | 听众模式 |
| `conv_mode_lecture_desc` | Translate speeches live | 实时翻译演讲内容 |
| `conv_mode_transcribe` | Transcribe mode | 转录模式 |
| `conv_mode_transcribe_desc` | Transcribe live audio | 实时转录现场声音 |
| `conv_omni` | Omni | 全模态 |
| `conv_omni_desc` | Use the omni model | 使用全模态模型 |
| `conv_omni_intro` | Use the omni AI model for lower interaction latency | 使用全模态AI模型，可以降低交互延迟 |
| `conv_enable` | Enable | 开启 |
| `conv_mode_translate` | Translate mode | 翻译模式 |
| `conv_mode_omni` | Omni mode | 全模态模式 |
| `conv_select_peer_language` | Their language | 选择对方语言 |
| `conv_select_speaker_language` | Speaker's language | 选择演讲者的语言 |
| `conv_language_auto` | Auto-detect | 自动识别 |
| `conv_picker_confirm` | OK | 确定 |
| `conv_transcribe_source_title` | Select recording source | 选择现场录音模式 |
| `conv_transcribe_source_original` | Original | 原声 |
| `conv_transcribe_source_original_desc` | Raw ambient audio | 保留现场原始声音 |
| `conv_transcribe_source_voice` | Speech | 语音 |
| `conv_transcribe_source_voice_desc` | Enhanced for speech | 针对语音增强 |
| `conv_msg_suffix_transcript` | (transcript) | (转录) |
| `conv_msg_suffix_original` | (original) | (原文) |
| `conv_msg_suffix_translation` | (translation) | (译文) |
| `conv_avatar_picker_title` | Avatar | 头像选择 |
| `conv_avatar_from_gallery`（已存在） | Choose from gallery | 从相册选择 |
| `conv_take_photo` | Take photo | 拍照 |
| `conv_avatar_reset` | Reset to default avatar | 恢复默认头像 |
| `conv_avatar_scale_normal` | Normal | 正常 |
| `conv_avatar_scale_shrink` | Shrink | 缩小 |
| `conv_avatar_drag_hint` | Drag to adjust position | 拖动图片调整位置 |
| `conv_confirm` | Confirm | 确认 |
| `conv_avatar_save_failed` | Failed to save avatar: %@ | 保存头像失败: %@ |
| `avatar_custom_image_desc` | Custom avatar | 自定义头像 |

- 语言候选显示名用 App UI locale 的系统本地化（`Bundle.main.preferredLocalizations`），不新增候选语言专用 key。
- 模式徽章 accessibility 模式名复用 `conv_mode_translate/transcribe/omni`。
- 头像/模式面板、dialog、裁剪页按 `appFont`/AppColor 主题；无新增颜色。
- 历史后缀从同一套 suffix key 取。

---

## 8. 测试计划

### 8.1 单元/集成

**头像**
- `AvatarStore`（临时目录）：
  - 保存 → 读取最新；连续保存两个 → 只保留最新、旧文件删除；
  - 保存写入失败 → 半截文件清理，旧头像不丢；
  - remove → 为空；损坏/缺失文件 → 加载返回 nil；
  - 临时文件/目录不残留。
- `AvatarCustomizationModel`：
  - 初始 avatarVisible 读取 SettingsStore 默认 true；set 持久化；
  - sheet 路由：gallery/photo/camera/crop；取消回退；picker/相机 onDismiss 后才进入 crop；
  - save/remove 更新 `avatarImage`/`hasCustomAvatar`；失败 `errorMessage` 且旧头像保留；不跨 launch 丢自定义头像（用临时目录 + 重新 init）；
  - 类型边界：模型/Store 不出现 `UIImage`（编译期检查）。
- 裁剪纯函数/视图：
  - 竖图/横图/EXIF 90/180/270；minScale/initialScale/clamp/圆内 crop rect 在边界；输出正方形、JPEG；
  - 屏幕/裁剪区尺寸变化重算；裁剪输入为 `Data`/`CGImage`，不依赖模型层 UIKit 类型；
- `SettingsStore` avatarVisible 默认/读写。
- Avatar title bar 状态派生（visible/hidden、camera 矩阵）纯函数。

**模式**
- `ConversationModeModel`（fake sender）：
  - dialogue payload：mode translate、myLanguage 设备语言、localLanguage auto 原样、usecase dialogue；
  - lecture：auto→en、usecase lecture；
  - transcribe：mode transcribe、languages/usecase nil、source 本地；omni/chat；
  - 模式互斥/flag：lecture 时 **translate+lecture 两 flag 均 true**；对话 = translate && !lecture；四个“模式”互斥；打开 picker 不置 active，confirm 才置；off 回 chat 且不关面板（未 ready 时只本地退出、不发送）；
  - 开关 enabled：ready 无激活→四个可开；有激活→仅当前激活开关可关；未 ready→仅当前激活开关可关；
  - echo：**not ready 忽略**；ready 时应用 server echo（含 1008 降级后的 mode 变化），清 pendingRequest；1009 回滚 + 清 last + source voice；其它错误不回滚；
  - sessionReady：无 last→chat+voice；有 last→乐观恢复 + resend；translate/omni→voice，transcribe→source；
  - **isSessionReady 状态机**：断开/connectionTerminated/sessionEnded/stop/settings apply/每次发起新 session（含 `.connectionEpochAdvanced`）→ false 且 **pendingRequest=nil**；非 ready 时禁止开启/切换、request 不发送；**允许关闭已激活模式（本地清 flags/pendingRequest/lastRequest，不发送）**；只有 sessionReady → true；
  - 音频源：生效模式非 transcribe（含 1009 回滚、echo chat、sessionReady chat、exit）→ voice；transcribe/restore 应用 remembered source；重配置失败回退 voice、`transcribeSource=.voice` 并提示；后台/中断/route 重建后按当前模式重新应用；**过期 source 切换完成不得覆盖新状态（generation）**；
  - disconnect/connectionTerminated（authFailed/reconnectExhausted）保留 flags；sessionEnded（SDK 事件 + serverClosed）清 UI 但保留 lastRequest；stop/settings reset；history switch 保留模式；
  - lastRequest 在 reconnect 保持；chat/本地关闭/1009/stop 清除；sendRequest 弱引用/清空。
- Coordinator：
  - `.sessionChanged` 转发 model（not ready 忽略/ready 按 pending 匹配）；`.sessionReady` 重置/恢复；`.error(1009)` 回滚；其它错误不动；
  - `.sessionEnded` 与 `.connectionTerminated(.serverClosed)` 都清模式 UI；authFailed/reconnectExhausted 保留 flags；
  - 用户/助手 final 消息 mode；user transcribe 优先 translate；AI translate；
  - stop/clear；
  - SDK provider 扩展后 FakeSDK 记录 sendSessionChange payload。
- 历史：
  - record user/AI with mode → DB；reload ChatMessage.mode；旧 nil 行不回拆/不加后缀；列表/详情；
  - summary 用无 suffix 原文。
- `MessageBubbleView` suffix 纯函数：mode+role → key/nil；拼空格；Markdown/用户/折叠 agent；历史详情一致。
- 语言列表/default/auto 归一；设备语言 fallback en；**iOS 15 编译通过（`Locale.current.languageCode`，不得使用 iOS 16 的 `Locale.current.language.languageCode`）**；badge 派生（lecture→translate）。
- 音频源纯映射/重配置失败回退（在设备探针确定后补）。

### 8.2 UI 测试（静态 fixture）

新增 launch options/fixtures：
- `-uiTestAvatar`（默认头像/自定义头像 fixture）、`-uiTestAvatarHidden`（avatarVisible=false）、`-uiTestAvatarCrop`（跳过系统 picker，直接展示裁剪页 + 生成图）；
- 模式 fixture：`-uiTestModeEcho` / fake sender；fixture 必须把 `isSessionReady` 置 true（否则无法开启模式），可种子 `-uiTestMode translate|lecture|transcribe|omni`、picker/dialog；
- 头像目录使用 UI test 临时目录（与 history 临时 DB 同策略），避免污染真实头像；默认 avatarVisible=true（除非 hidden fixture），避免 UserDefaults 残留；需要 relaunch 持久化的自定义头像用例用固定测试目录（不能每次 UUID）。

用例：
- 头像入口：大头像点击 → 面板；标题栏隐藏态按钮点击 → 面板；
- 面板：四项开关 + 头像项；item 间距 48；pill 正常/缩小；切换不关面板；**×/accessibilityAction 可关闭，VoiceOver 焦点回头像按钮**；
- 缩小：大头像消失、消息上移、隐藏簇出现；**头像圆心=内容中线（+26 offset）**、模式 badge 不被裁切且点击可打开面板、job 槽不推动头像；切回正常恢复；重启/relaunch 持久化（SettingsStore）；
- 自定义头像 fixture：显示照片；恢复默认；文件/视图更新；
- 裁剪：拖动/缩放/圆形遮罩/确认/取消；取消不改变头像；VoiceOver 的 Move/Zoom actions 可用；
- 相册/相机：系统 picker 无法稳定自动化；用 fixture 覆盖后续裁剪；相机权限拒绝/无相机引导手测；
- 模式：四开关互斥；dialog；auto/en；对话/听众确定 → 面板关闭、badge 出现；off → badge 消失、面板保留；transcribe source；omni intro；
- 1009：fake sender 返回/session error → badge 回 chat、banner；其它错误 flag 保留；
- 消息后缀：种子 translate/transcribe 用户/AI、历史详情；lecture 第二行；普通流式气泡在 lecture 不出现；
- 头像隐藏 + 模式 badge/job badge 同屏；相机矩阵（visible=true 保留 header job badge；hidden 抑制重复）；iPad；Dynamic Type（switch/option 不裁切、dialog 可滚动）；VoiceOver（面板 ×/action 可关、裁剪 Move/Zoom actions、模式 badge 合并不重复读）；Reduce Motion；断开时不可开启/切换、已激活模式可本地关闭。

### 8.3 真机 / 真实 server

- 真实 iPad：相册 → 裁剪 → 保存 → 重启；拍照 + 相机权限；恢复默认；正常/缩小持久化；小/大字号；
- **音频源探针先于模式 UI**：`setVoiceProcessingEnabled` 运行时切换、route/AEC/回声、与播放共享 engine 的中断；探针失败则隐藏原声；
- 真实 server：对话 auto、听众 auto→en、转录原声/语音、全模态；session.changed echo；1009（若可构造）；断网重连恢复；模式消息后缀/历史；离开模式后采集源回 voice；
- 相机模式 + 隐藏簇 + job badge；Agent Job 面板回归；
- VoiceOver/Reduce Motion。

---

## 9. 实施顺序

1. **模式核心**：`ConversationModeModel`、`ModeChangeRequest`、SDK provider `sendSessionChange`、Coordinator `.sessionChanged/.sessionReady/.error(1009)` + `isSessionReady` 全转换；单测。注意 iOS 15：设备语言用 `Locale.current.languageCode`。
2. **消息/历史模式**：`ChatMessageMode`、VM finish/append mode、Coordinator 取值、HistoryService/DB mode、MessageBubble suffix、听众第二行；单测/UI。
3. **头像核心**：`AvatarStore`（Data/CGImage）、`AvatarCustomizationModel`（无 UIKit）、SettingsStore avatarVisible、加载/保存/删除；单测。
4. **头像 UI**：AvatarView（96、无光环）、正常/隐藏 header（+26 offset）、头像面板、pill；与 Agent Job badge/panel 联调；UI 测试。
5. **选择/裁剪**：PHPicker 复用、UIImagePickerController 相机（UIKit 例外审批，适配层内转 Data）、裁剪页、picker→crop 时序；fixture/手测。
6. **音频源探针（先于模式 UI）**：真机确认 `setVoiceProcessingEnabled` 运行时切换、route/AEC、与播放共享 engine；决定是否提供「原声」。
7. **模式 UI**：语言/音源/omni dialog、徽章、面板开关、相机矩阵；按探针结论实现/隐藏原声；UI 测试。
8. **音频源实现/回退**：按探针结果实现 source 切换与失败回退；真机。
9. **全量**：模拟器单测/UI、iPad、真实 server、真机、文档记录。

---

## 10. 验收标准

- [ ] 大头像（保持 96pt、无光环）/标题栏头像按钮可打开面板；自定义照片与默认渐变 `~` 正确切换；
- [ ] 相册/拍照 → 裁剪（拖动/缩放/圆内无空白）→ 保存；取消不变；错误不崩溃；
- [ ] 自定义头像重启保留；恢复默认；仅本地存储，无服务器/SDK 改动；
- [ ] 正常/缩小：缩小后消息上移 96pt、标题栏头像圆心=内容中线（+26 offset）、模式/Job 徽章位置正确且不被裁切/不吞点击；重启保持；
- [ ] 四模式互斥（lecture = translate+lecture，两个 flag 同时 true）；对话框/选择器/全模态说明与 Android 行为一致；面板开关 on/enabled 语义一致；
- [ ] 对话 auto 原样、听众 auto→en、设备语言、语言列表/default；转录源本地切换；
- [ ] 服务端回声结算（not ready 时忽略；ready 时应用 echo，含 1008 降级）；1009 回滚且采集源回 voice；其它错误不回滚；重连恢复；sessionReady 重置；断开/结束（含 serverClosed）/stop 后 isSessionReady=false、pendingRequest 清空，不可开启/切换但已激活模式可本地关闭；
- [ ] 模式徽章 26/20；模式后缀/历史/听众第二行正确；旧历史无后缀；
- [ ] 头像/隐藏/相机/Agent Job 矩阵无重复 badge/布局遮挡；iPad/Dynamic Type/旁白（面板可关、裁剪 Move/Zoom actions）/Reduce Motion；
- [ ] 单测、UI、真机、真实 server 通过；无 server/SDK wire 改动。

---

## 11. 风险与已拍板决策（2026-10-21）

已拍板：除 #1 按要求改为保持 iOS 96、无光环外，其余全部按原推荐执行。#4 的最终分支仍需实施前真机探针确认。

| # | 决策/风险 | 拍板结果 |
|---|---|---|
| 1 | 是否移植 Android 120 外框 + 状态光环 | **保持 iOS 96、无光环**；仅新增点击/自定义照片/模式徽章 |
| 2 | 隐藏态标题栏尺寸/状态文案 | 视觉 36/触控 44、header 保持 44；状态文案按偏移后簇左缘截断 |
| 3 | 架构 | 独立 `AvatarCustomizationModel` + `ConversationModeModel`（与 AgentJobStatusModel 同模式） |
| 4 | 转录「原声」可行性 | 先真机探针；可行则按 voice=voiceChat+processing、original=关闭 processing（必要时 measurement/default）+受控重建；不可行则只提供语音并隐藏原声（实施时回报） |
| 5 | `UIImagePickerController` UIKit 例外 | **批准本次例外**；仅封装在 PlatformAdapters，业务层不依赖 UIKit |
| 6 | 历史删除当前/`sessionEnded` 的模式复位 | 历史切换保留模式；`sessionEnded` 清 UI 但保留 `lastRequest`（重试恢复） |
| 7 | 相机态头像/模式入口与 job badge | 相机 + `avatarVisible=true`：无头像/模式入口，**保留 iOS 现有 header job badge**；`avatarVisible=false`：隐藏簇含头像/模式/job，并抑制 header 重复 job badge |
| 8 | sessionReady 前的模式开关 | 未 ready：禁止开启/切换、确认请求被拒；**已激活模式允许本地关闭**（清 flags/pendingRequest/lastRequest、恢复 voice、不发送），避免重连被强制恢复 |
| 9 | 头像面板形态/覆盖范围 | 自绘 bottom sheet（iOS 15 无 detents）；scrim/面板覆盖 AppRootView 的 600pt 内容列，不追求物理全屏 |
| 10 | 相机覆盖层的模式后缀/听众第二行 | 仅在聊天页完整实现；`CameraModeView` 保持现有结构 |
| 11 | 实时相机与头像拍照 | 「拍照」前停止实时相机，完成/取消后恢复；相册/裁剪路径不停相机；若探针发现系统相机仍冲突，则退化为禁用相机态「拍照」 |
| 12 | `sessionReady` 时 omni 复位 | 四 flag 全复位（修正 Android 当前遗漏） |

残余风险：ready 时用户快速关闭模式，旧请求的 mode echo 可能晚于 chat echo 到达并短暂/错误点亮模式；与 Android 当前语义相同，协议无 request id 时不做严格匹配（见 §13 S17-撤回）。

---

## 12. 文件索引（计划）

| 主题 | 文件 |
|---|---|
| 本文 | `docs/ios_avator_and_mode_change.md` |
| Android 参考 | `docs/fd_avator_customization.md`、`docs/android_multilingual.md`（模式文案/消息后缀） |
| Android 头像 | `AiAvatar.kt`、`AvatarCustomizationSheet.kt`、`AvatarCropScreen.kt`、`AvatarRepository.kt` |
| Android 模式 | `ConversationViewModel.kt`、`ConversationListenerAdapter.kt`、`ConversationScreen.kt`、`LanguagePickerSheet.kt`、`TranscribeSourcePicker.kt`、`MessageBubble.kt` |
| iOS SDK | `LivaChatSDK.swift`、`LivaChatEvent.swift`、`CoreEnums.swift`、`Internal/Session/SessionManager.swift` |
| iOS 会话 | `ConversationViewModel.swift`、`ConversationCoordinator.swift`、`ConversationProtocols.swift`、`ConversationModels.swift` |
| iOS 音频 | `AudioSessionController.swift`、`AudioCaptureController.swift`、`AudioProtocols.swift` |
| iOS 历史 | `HistoryService.swift`、`HistoryModels.swift`、`HistoryDatabase.swift`、`MessageBubbleView.swift` |
| iOS 选择/裁剪 | `PHPickerView.swift`（相册）、`CameraPhotoPicker.swift`（UIImagePickerController）、`AvatarCropView.swift`、`AvatarCropMath.swift` |
| iOS 容器/设置 | `AppContainer.swift`、`AppRootView.swift`、`SettingsStore.swift`、`AppLaunchOptions.swift`、`ConversationRuntimeFactory.swift` |
| iOS 本地化 | `L10n.swift`、`Resources/Localizable.xcstrings` |
| iOS 头像/模式 | `AvatarCustomizationModel.swift`、`AvatarStore.swift`、`ConversationModeModel.swift`、`AvatarView.swift`、`AvatarCustomizationSheet.swift`、`ModeSelectionDialogs.swift` |
| iOS 测试 | `AvatarStoreTests.swift`、`AvatarCustomizationModelTests.swift`、`AvatarCropMathTests.swift`、`ConversationModeModelTests.swift`、Coordinator/History/MessageBubble 既有测试扩展、`AppShellUITests` 新增头像/模式用例、`AudioSpikeUITests` source 探针 |
| 已实现 Agent Job 兼容 | `AgentJobStatusModel.swift`、`AgentJobListPanel.swift`、`AgentJobStatusBadge.swift` |

---

## 13. 对抗式自审修订记录（2026-10-21）

本轮（含 2026-10-21 拍板后的二次全面 review）以 Android/iOS 当前源码逐条核对基线、iOS 15 API、平台规则与状态机；以下严重问题已落入正文（未改代码）。S17 为考虑后撤回的方案，保留记录以避免再次引入。

| # | 严重问题 | 修订落点 |
|---|---|---|
| S1 | 方案使用 `Locale.current.language.languageCode` 取设备语言；`Locale.Language` 是 **iOS 16+**，工程最低 iOS 15，直接编译失败/需 availability 分支 | D5/§8：改用 `Locale.current.languageCode ?? "en"`（iOS 16 deprecated 但 iOS 15 可用），并加 iOS 15 编译测试 |
| S2 | 原方案在 `AvatarCustomizationModel` 存 `UIImage`、`AvatarStore` 跨 actor 解码 `UIImage`：违反 `docs/ios_dev_rules.md` §2“业务模型/状态层不得依赖 UIKit”，并有 Swift 6 Sendable 风险 | D1/§5/§8：模型/Store 只用 `Data`/`CGImage`；`UIImage` 仅在 `CameraPhotoPicker`（UIImagePickerController 适配层）内部出现并立刻转 JPEG Data |
| S3 | `isSessionReady` 只定义了“sessionReady 置 true / stop 置 false”，漏掉断开、connectionTerminated、sessionEnded、retry 新 session 等 false 转换；开关会在 SDK 已丢弃 `session.change` 时仍可开启，乐观态挂死 | D5：列出全部 false 转换，镜像 Coordinator 现有 `sessionReady` 局部变量；未 ready 禁止开启、允许本地关闭；单测覆盖 |
| S4 | §2.7 声称 Android `onSessionReady` 复位“四 flag 全 false”，实际代码只复位 translate/lecture/transcribe，**omni 未复位**（`ConversationListenerAdapter.kt:260-263`） | §2.7 改为代码事实；D5 要求 iOS 四 flag 全复位并标注为修正 Android 遗漏（§11 #12 已拍板） |
| S5 | 隐藏簇只写“固定居中”，若整行居中则头像圆心会偏左 `(jobSlot+spacing)/2 = 26pt`，与 Android“头像锚定屏中线”不符 | D3/§6.2：明确整行居中后再 offset +26pt（Android 对应 +28dp）；UI 测试验证头像圆心 |
| S6 | 模式徽章若放在被 `clipShape(Circle())` 的按钮/头像内会被裁切；若作为独立可命中视图会吞掉头像点击；自定义照片替换按钮 label 会丢失“可更换”语义。（原条目中的 speaking 光环溢出 120 外框，因 §14 #1 拍板保持无光环而不再适用） | D2/D3/D7：徽章放外层、`.allowsHitTesting(false)`；按钮 label 保持 `conv_avatar_picker_title` + 模式名，照片 visual-only |
| S7 | 相机矩阵声称“与 Android 当前代码对齐”，但 Android 在 `avatarVisible=true` 相机态**没有** job badge；iOS 现有 header job badge 是保留项，不是 Android 对齐项 | D3/§6.2/§11 #7：明确 iOS 保留 job badge 是刻意偏离，已拍板保留；hidden 时用隐藏簇并抑制重复 |
| S8 | 音频源只写了“进入/离开转录”，未规定 translate/omni/chat/exit 时必须恢复 voice，也未处理 source 应用失败的 UI/实际值错配、后台/中断/route 重建后的重新应用；设备探针排在模式 UI 之后，可能返工 | D5/D6/§9：非 transcribe 一律恢复 voice；失败回退 voice 并提示；engine/route 重建后按当前模式重新应用；探针提前到模式 UI 之前 |
| S9 | 面板/dialog 的 switch/头像行固定 48pt、按钮固定单行：AX 字号下两行文本会裁切；“pill 触控 44 但视觉不变”未说明实现代价 | D3/D4/D6/§6：改为 minHeight + 自适应高度；dialog 可滚动；pill 热区若抬高行高需接受并用 UI 测试验证 |
| S10 | 相册/相机 → 裁剪的呈现顺序未定义；在 PHPicker/相机 cover 消失过程中再呈现 `.fullScreenCover` 会丢页或产生 presentation 警告 | D4/§5.1：必须等 `onDismiss`/pending 状态后再设 crop route，沿用 GalleryPreview 模式 |
| S11 | `sendRequest` 闭包生命周期未定义：AppContainer 持有 model，settings apply 会重建 Coordinator；旧闭包若强持有 self 会接收新 UI 请求/泄漏 | D1/D5/D8：weak 注入、stop/settings apply 置 nil、新 runtime 重新注入；openHistory/openSettings 调 `dismissOverlays()`；settings apply `modeModel.reset()` |
| S12 | §2.7 把“session 未建立时 SDK 丢弃 session.change”归因于 Android；实际 Android SDK 直接 `encodeAndSend`，只有 iOS SDK 有 `sessionStartedOnConnection` 门控 | §2.7：改为 iOS SDK 事实；iOS 显式门控的理由成立，但不能照搬 Android VM |
| S13 | UI 测试默认使用 UserDefaults 的 avatarVisible、头像目录用每次启动 UUID 的临时目录；前者会跨用例污染，后者无法验证“重启保留” | D8/§8.2：默认 avatarVisible=true（hidden fixture 除外）；relaunch 用例用固定测试目录 |
| S14 | D5 写“四 flag 始终只有一个 true”，但 lecture 必然 `translateEnabled` + `lectureEnabled` 同时 true；且“enabled 同 Android”与“未 ready 仅激活开关可关”互相矛盾 | D5/D4：明确 flag/模式派生与 on/enabled 规则；lecture = 两 flag true，但不违反“四模式互斥”；测试补 lecture/组合 |
| S15 | 本地关闭/未 ready 状态机未清 `pending`，且未 ready 时仍会应用 `.sessionChanged`：旧请求的迟到回声可把已关闭的模式重新点亮；`sessionEnded` 也未清 pending | D5/§5.2/§8：`exitToChat` 与进入未就绪/`sessionEnded` 清 `pendingRequest`；未 ready 忽略 `.sessionChanged`；测试覆盖 |
| S16 | 音频源只在“请求意图”上恢复 voice：1009 回滚、echo chat、sessionReady chat 后可能仍停留 RAW；source 应用失败也未回写 model，UI 与实际不一致；异步切换的过期完成也可能覆盖新状态 | D5/D6/§8/§10：source 跟随“生效模式”，列全触发点；失败回退 voice 且 `transcribeSource=.voice`；切换串行化 + generation；测试覆盖 |
| S17（撤回） | 曾计划 ready 时仅结算匹配 pending 的 echo；但该规则会拒绝 1008 lecture 降级等服务端改模式的合法 echo，且无 request id 无法区分迟到 echo | 最终保持 Android 语义：ready 应用 echo、not ready 忽略；本地退出与旧 echo 的窄竞争作为残余风险，不引入不成立的严格匹配 |
| S18 | “sessionEnded 清 UI”只覆盖 SDK `.sessionEnded`，漏掉 `.connectionTerminated(.serverClosed)`（该路径同样 `sessionEnded=true`），模式徽章会残留 | D5/§8：两条 sessionEnded 路径都清 flags/pendingRequest、保留 lastRequest；测试覆盖 serverClosed |
| S19 | 自绘头像面板只有 scrim tap/下滑关闭，VoiceOver 用户没有可达的关闭入口 | D4/§6.3/§8：面板右上 × + accessibilityAction（Close），焦点回头像按钮 |
| S20 | 裁剪页只有单指/双指手势，VoiceOver 无法调整图片；若图不聚焦，Move/Zoom actions 也不可达 | §6.5/§8：裁剪图设为可聚焦元素并提供 Move/Zoom accessibility actions，保留 Cancel/Confirm；手测/UI 测试 |
| S21 | 隐藏态状态文案宽度按“Text=150.5”计算会忽略状态条的 Circle 8 + 间距 8，实际右缘压到头像簇 8pt | D3/§6.2：区分 status button 最大 150.5 与 Text 最大 134.5；UI 测试断言不重叠 |

其它已修正的小问题：

- §2.3 `SheetOption` 图标间距从含糊的“8? 16pt”明确为 16pt；头像 option 的动作与 `onDismiss()` 顺序改为“发起动作并关闭面板”。
- §2.3/§7 字符串 `conv_avatar_save_failed` 英文改为 Android 同源 `Failed to save avatar: %@`。
- D7 听众第二行明确为 Android `TranscriptionBubble` 的单气泡内嵌结构（AI 译文在上、原文+dots 在下），不是两个独立背景气泡。
- D7 suffix 仅在正文非空时拼接，避免纯图片 AI 生成只有后缀的文本。
- D6/§7 语言候选显示名按 App UI locale（`Bundle.main.preferredLocalizations`）而非裸 `Locale.current`，对齐 Android `LocalConfiguration.locales[0]`。
- D8/§11 #11 精确化：只有「拍照」路径需要先停实时相机；相册/裁剪不占相机硬件，不必一概停止（已拍板）。
- §12 文件索引补充 `ConversationRuntimeFactory`、`L10n`/`Localizable.xcstrings`、本地化/测试文件。

同样核对并确认**无需修改**的关键事实：

- Android 大头像 120/内圆 96、IDLE 无光环、LISTENING/THINKING/SPEAKING 视觉与 `mapSessionToAvatarState`（`AiAvatar.kt:77-142/210-314`、`ConversationScreen.kt:1180-1189`）。
- Android 隐藏簇外层不裁剪、48×48 触控、job 固定槽、offset +28dp、模式徽章 20dp bottom-end（`ConversationScreen.kt:455-485`、`AiAvatar.kt:147-185`）。
- Android 头像存储唯一命名/旧文件清理/最新读取，裁剪半径 96dp、minScale 填圆、max=initial×3、JPEG 90（`AvatarRepository.kt`、`AvatarCropScreen.kt`）。
- Android 模式开关互斥、dialogue auto 原样/lecture auto→en、transcribe source 本地、omni 说明弹窗、echo/1009/sessionReady/lastModeRequest 主体语义（`ConversationViewModel.kt:499-659`、`ConversationListenerAdapter.kt:127-143/241-283/326-361`）。
- Android 用户 final transcribe>translate、AI final translate、消息后缀展示时拼接（`ConversationListenerAdapter.kt:155-239`、`MessageBubble.kt:79-82/206-213`）。
- iOS SDK `sendSessionChange` 已存在且带 `sessionStartedOnConnection` 门控，`.sessionChanged` 已解析；App 侧 `LivaChatSDKProviding` 缺方法、Coordinator 忽略事件（`LivaChatSDK.swift:373`、`SessionManager.swift:101-110/195-201`、`ConversationProtocols.swift:5`、`ConversationCoordinator.swift:464`）。
- iOS 现有大头像 96 静态、header 44、avatar row 64+8+96+8+64=240、Agent Job 面板 top 180、相机 header job badge（`ConversationView.swift:158-213/294-329/333-350`）。
- iOS History DB 已有 `mode` 列并读写接口（`append` bind、`messages` SELECT 映射，无需 schema migration），但 `processAppend` 写 nil、`chatMessages(from:)` 不映射；`ChatMessage` 无 mode（`HistoryDatabase.swift:97-148/250/328`、`HistoryService.swift:230-252/367-399`、`ConversationModels.swift:9-18`）。
- iOS 音频固定 `.voiceChat` + voice processing，无 capture source 抽象（`AudioSessionController.swift:38-57`、`AudioProtocols.swift:23-30`）。
- iOS 相机为实时 `AVCaptureSession` 视频流，无 still capture；PHPicker 只返回 Data（`CameraController.swift`、`PHPickerView.swift`）。
- iOS 历史/设置是 `fullScreenCover`，ConversationView 仍挂载；AppContainer 已负责在打开前 dismiss Agent Job 面板（`AppRootView.swift:25-49`、`AppContainer.swift:200-225`）。

结论：上述严重问题已修订进正文；§11 的决策已于同日拍板，见 §14。本轮**未改任何生产代码**。

---

## 14. 拍板记录（2026-10-21）

用户决定：

1. **头像保持 iOS 96pt、无光环**：不移植 Android 120pt 外框、`AvatarState` 光环/Crossfade；只新增点击、自定义照片、模式徽章。D2/§6 已按此重写；消息区隐藏/显示的位移为 **96pt**，头像行宽 **240pt**（64+8+96+8+64），Agent Job 面板顶部 **180pt** 不变。
2. **其余全部按 §11 推荐拍板**：
   - 隐藏态头像按钮视觉 36/触控 44，header 44，状态文案按偏移后的簇左缘截断；
   - 独立 `AvatarCustomizationModel` + `ConversationModeModel`；
   - 转录原声先真机探针；可行则实现，不可行则只保留语音并隐藏原声（实施时回报）；
   - `UIImagePickerController` 本次 UIKit 例外**批准**，仅限 PlatformAdapters；
   - 历史删除当前会话保留模式；`sessionEnded` 清 UI 但保留 `lastRequest`；
   - 相机 + 大头像态保留 iOS 现有 job badge；隐藏簇含 job 并抑制 header 重复；
   - 未 ready 禁止开启/切换，但允许关闭已激活模式（本地清状态，不发送）；
   - 自绘 bottom sheet，scrim 覆盖 600pt 内容列；
   - 相机覆盖层保持现状，模式后缀/听众第二行只在聊天页；
   - 头像拍照前停实时相机、结束恢复；相册/裁剪不停；
   - `sessionReady` 时四 flag 全复位（含 omni）。
3. **对自审记录的修订**：§13 S6 中 “speaking 光环溢出 120 外框” 因第 1 项拍板不再适用；S6 其余项（徽章裁剪/点击、按钮 label）仍适用。§13 S5 的隐藏簇 +26 offset 仍适用。
4. **二次 review（同日 S14-S21）**：不改变上述拍板结论，仅补齐 flag 语义、pending/echo、音频源回退、serverClosed、VoiceOver 与布局宽度细节；S17 严格 echo 匹配因会拒绝 1008 降级而撤回。
5. **实施状态**：已按 §9 实施完成，验证结果与剩余手工项见 §15。

---

## 15. 实施记录（2026-10-21）

> §13/§14 中“未改代码”指 review/拍板当时；本节记录实际实施与验证。

### 15.1 已实现

**头像**
- `AvatarStore`（actor）：Application Support/LivaChat/avatar 唯一命名、保存后删旧、失败不丢旧、JPEG/ImageIO 解码；UI test 全使用临时目录。
- `AvatarCustomizationModel`：CGImage 展示、avatarVisible、sheet、gallery/camera/crop route、picker onDismiss 后再进入 crop、保存/恢复默认、camera 权限与不可用错误；无 UIKit 类型进入状态层。
- `AvatarView`/`AvatarTitleBarButton`：96pt 大头像（无光环）、36/44 隐藏簇按钮、26/20 模式徽章；徽章不裁剪、不吞点击。
- 自绘头像面板：内容高度自适应、超过 82% 才滚动；正常/缩小 pill（AX 字号改为上下排）、四模式开关、相册/拍照/恢复默认、× 关闭与 accessibilityAction；模式 dialog 打开时面板 a11y 隐藏。
- 隐藏簇：头像圆心锚定内容中线（+26 offset）、状态文案按偏移后的簇左缘截断、job badge 固定 44 槽；相机 + 隐藏簇抑制 header 重复 job badge。
- `CameraPhotoPicker`（UIImagePickerController，适配层内 UIImage→JPEG Data）；`AvatarCropView`（Ø192 圆、拖动/缩放、EXIF、JPEG 90、Move/Zoom VoiceOver actions）；`AvatarCropMath` 纯函数。
- 拍照互斥（D8/§14 #11）：进入系统相机前由 Coordinator 暂停实时 `AVCaptureSession`（保持 `isCameraOn`，置 `cameraResumeIntent`），系统相机/裁剪结束或取消后恢复；相册路径不停相机；权限拒绝/无相机不暂停。

**模式**
- `ConversationModeModel`：四模式互斥/lecture=translate+lecture、完整 on/enabled 规则、不 ready 时只允许关闭已激活模式、pendingRequest/lastRequest、echo/1009/sessionReady/serverClosed/sessionEnded/reset、音频源回调。
- SDK provider 的 app-side `sendSessionChange`；Coordinator 接入 `.sessionChanged/.sessionReady/.error(1009)`、not-ready、serverClosed；消息 final mode 透传。
- 模式面板 dialog：语言（11 语言 + auto，App UI locale 显示）、转录源（原声/语音）、全模态说明；三张卡片统一使用内容自适应高度（上限 85% 可用高度，超出滚动），AX 字号/小屏下按钮可达（S9）；模式徽章；相机矩阵。
- 模式 ready 状态：`.connectionEpochAdvanced` 启动新 session 前同步 `sessionBecameNotReady()` + `sessionReady=false`，与 `retry()` 一致；新 session ready 后按 `lastRequest` 恢复。
- 消息/历史：`ChatMessageMode`、VM/Coordinator/HistoryService 写入读取、显示时 suffix、lecture 第二行与流式气泡抑制；旧历史 mode=nil 不加后缀；无 DB migration。
- 音频源：`AudioSessionController` 支持 voice（`.voiceChat` + voice processing）/original（`.measurement` + voice processing off）；Coordinator 在源变化时同步 stopMedia→setSource→startMedia，失败回退 voice 并提示；`AudioSpikeView` DEBUG 增加 source 探针。

### 15.2 验证结果

- **全量单元（iPhone 16 Pro 模拟器，iOS 18.6）**：`285 tests / 1 skipped / 0 failed`（含 review 修复新增 9 条）。命令：
  ```bash
  xcodebuild test -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
    -scheme LivaChatApp -destination 'platform=iOS Simulator,id=AD9096F3-27D8-4FDC-8571-D8D370D54651' \
    -only-testing:LivaChatAppTests
  ```
- **全量 UI（同上）**：`53 tests / 50 passed / 3 skipped / 0 failed`（含 review 修复新增的 AX dialog 用例；跳过的是手动 real-server Agent Job、real-server mode、device camera E2E）。命令：同上，`-only-testing:LivaChatAppUITests`。
- **iPad 模拟器（iPad Pro 11-inch M4，iOS 18.6）**：头像面板、隐藏簇居中、缩小/恢复、模式 dialog/badge、lecture 第二行、隐藏簇 mode+job、相机隐藏簇单 job badge 共 7 条通过；review 修复后复跑 dialog/隐藏簇 4 条通过。
- **Dynamic Type**：`testAvatarSheetAtAccessibilitySize` 与 `testModeDialogAtAccessibilitySize`（Accessibility XXXL）通过；面板滚动、pill 上下排、Take photo 可达、dialog 确定按钮可达。
- **真机音频源探针（iPad Pro 10.5，iPadOS 17.7.5）**：`AudioSpikeUITests/testAudioCaptureSourceSwitches` 通过；App 日志（UTC 10:34:56–10:35:03）显示 `voice processing enabled → disabled → enabled`，输入格式 `1 ch, 44100 Hz, Float32`，capture start/stop 正常；voice 时 AEC enabled、original 时 disabled（测试断言）。
- **真实 server 模式 E2E（同 iPad，server `<lan-host>:8080`）**：`testRealServerModeSwitchE2E` 通过；App 日志确认：
  - dialogue `request translate usecase=dialogue local=auto` → `echo mode=translate usecase=dialogue`；
  - lecture `request ... local=en` → echo lecture；
  - transcribe original：`audio source=original restart=true` → `voice processing disabled` → `request transcribe source=original` → `echo mode=transcribe`；
  - off：`audio source=voice restart=true` → `voice processing enabled` → chat echo；
  - omni request → echo omni；所有模式未触发 1009。
- **签名真机 App**：iPad 安装/启动/正常 server 连接正常；音频探针与模式 E2E 均通过 GUI Terminal 签名执行。

### 15.3 与设计的偏差 / 待手工验收

- **音频源实现**：采用同步 `stopMedia → setCaptureSource → startMedia`（而非引擎内原地切换）；因此在单线程上天然串行，不需要异步 generation。真机探针已验证 voice/original 切换与 AEC 状态；未做客观回声/AEC 指标测量。
- **头像面板高度**：实现为内容高度自适应 + 82% 上限滚动；AX 字号下 pill 改为上下排，均优于原设计“最大高度”描述。
- **真实 server 模式 E2E 为手动用例**（默认 skip，`LIVA_REAL_MODE_E2E=1`）；本次通过 GUI Terminal 临时放行执行后已恢复 guard。
- **仍待手工**：
  - 真机系统相机/相册 → 裁剪 → 保存完整链路（自动化只覆盖 crop fixture 与模型/store 单测；系统 picker/camera 不可稳定自动化）；
  - 真实语音下的模式消息后缀/历史详情（fixture/单测已覆盖展示与持久化，真实 server 未造语音消息）；
  - 1009 被拒与真实断网重连恢复（单测已覆盖；真实 server 未构造拒绝/断网）；
  - VoiceOver 实际朗读、Reduce Motion 真机走查（元素/action 已实现并通过标识断言）；
  - 相机模式 + 隐藏簇打开头像面板后「拍照」：已实现系统相机期间暂停/恢复实时采集（单测覆盖），仍待真机手测确认系统相机与 `AVCaptureSession` 无冲突、取消/完成后画面恢复。
- **无 server/BFF/WS protocol/SDK wire 改动**；仅 App 侧扩展 `LivaChatSDKProviding` 并调用既有 SDK `sendSessionChange`。

### 15.4 实施后 Review 修复（2026-10-21）

- **拍照与实时相机互斥**：`AvatarCustomizationModel` 增加 photoFlow 状态与暂停/恢复 hook，`AppContainer` 接入 Coordinator 的 `pauseCameraForPhoto`/`resumeCameraAfterPhoto`；系统相机与裁剪完成/取消后恢复，后台→前台期间保持暂停（避免系统相机仍在前台时重启 `AVCaptureSession`）；相册路径不暂停。补 model/coordinator 单测。
- **模式 dialog 可滚动**：语言/转录源/全模态三张 dialog 统一为内容自适应 + 85% 可用高度上限滚动（S9）；模式开关补 `accessibilityLabel`/`accessibilityHint`；新增 AX 字号 UI 用例验证确定按钮可达。
- **新 session 的模式 ready 复位**：`.connectionEpochAdvanced` 在 `startSession` 前同步 `sessionBecameNotReady()` + `sessionReady=false`（与 `retry()` 一致），避免旧 ready 状态使新会话 ready 时跳过 `lastRequest` 恢复；补单测。
