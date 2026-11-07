# LivaChat iOS App UI/UX 设计（M1）

**状态**：已实施（M1.4；5 个 AppShell UI 测试通过）
**日期**：2026-10-20
**基线**：Android 产品 App 现行实现（`ConversationScreen.kt`、`SplashScreen.kt`、`PermissionScreen.kt`、`SettingsScreen.kt`、`ui/components/*`、`ui/theme/*`）
**上游文档**：`docs/ios_app_m1.md` §7–§8

M1 只实现前台 chat 语音闭环所需的页面与组件；未实现的 Android 功能不显示死控件，不显示空占位。

---

## 1. 视觉基础

### 1.1 颜色（Dark-only）

| Token | Hex | 用途 |
|---|---|---|
| `brandIndigo` | `#6366F1` | 主按钮、用户气泡、激活工具、转写浮层 |
| `brandViolet` | `#8B5CF6` | 渐变终点 |
| `success` | `#10B981` | IDLE/SPEAKING、可用 |
| `warning` | `#F59E0B` | CONNECTING/THINKING、可恢复错误 |
| `error` | `#EF4444` | DISCONNECTED、致命错误、认证失败 |
| `info` | `#3B82F6` | LISTENING |
| `background` | `#0F172A` | 页面背景 |
| `surface` | `#1E293B` | 卡片、AI 气泡、设置区块 |
| `surfaceVariant` | `#334155` | 描边、次要面、未聚焦输入框 |
| `textPrimary` | `#F8FAFC` | 主文字 |
| `textSecondary` | `#94A3B8` | 次要文字 |
| `textTertiary` | `#64748B` | 占位、禁用 |
| `white` | `#FFFFFF` | 气泡/浮层正文 |

- 不使用系统语义色代替品牌色；错误/成功等语义色只在对应语义场景使用。
- 不在本批新增未在 Android token 中的颜色。

### 1.2 字体与动态字体

基线字号/行高/字重：

| Style | Size/Line | Weight | SwiftUI 映射 |
|---|---|---|---|
| `headline` | 28 / 36 | Bold | 28 bold，相对 `.title` 缩放 |
| `title` | 22 / 28 | Semibold | 22 semibold，相对 `.title2` |
| `body` | 16 / 24 | Regular | 16 regular，相对 `.callout` |
| `label` | 14 / 20 | Medium | 14 medium，相对 `.subheadline` |
| `caption` | 12 / 16 | Regular | 12 regular，相对 `.caption` |

- 使用 `@ScaledMetric` 包装每个 style 的 size/line，保留 Android 基线，同时响应 Dynamic Type。
- 所有文本默认 `lineLimit(nil)`；头像、工具栏按钮、状态点等固定尺寸不随字体缩放。
- 正文行距 = `行高 - 字号`（body 8pt、label 6pt、caption 4pt、title 6pt、headline 8pt）。

### 1.3 间距与圆角

- Spacing: `4 / 8 / 16 / 24 / 32 / 48`。
- Radius: `4 / 8 / 12 / 16 / 24`。
- 页面水平边距：16pt（对话页工具栏可 24pt）。
- 消息气泡：16pt，尾角 8pt；AI 气泡 1pt `surfaceVariant` 描边。
- 按钮/卡片：16pt；工具栏按钮圆形。
- iPhone 内容宽度不设硬上限；iPad 兼容模式内容最大宽度 600pt 居中。

---

## 2. 导航与 Shell

```
Splash（≥1s）
  ├─ 首次启动 / 麦克风未授权 → Permission
  └─ 已授权且完成首启   → Conversation
Conversation ── 右上“更多”(⋯) / 配置缺失 ──→ Settings（fullScreenCover）
Settings ── 返回 ──→ Conversation
```

- `AppRouter` 用枚举而非 `NavigationStack`（iOS 15 无 NavigationStack）。
- Settings 用 `.fullScreenCover`，保留深色背景与独立返回按钮。
- M1 无 History、MyLife、WebView、模式选择、录屏入口。

---

## 3. Splash

布局（垂直居中）：

- 80pt 渐变圆（`brandIndigo` → `brandViolet`），中心 `~` 28 Bold 白色；
- 标题 `LivaChat` 28 Bold；
- tagline 14 Secondary（`splash_tagline`）；
- 底部 `splash_waking` 14 Secondary，距安全区底部 48pt；
- 渐变圆脉冲 `scaleEffect 1.0 ↔ 1.05`，600ms `easeInOut` 循环；
- 背景 `background`，全屏；
- 最短展示 1s；期间并行读取首启/麦克风权限，1s 后路由；
- 无障碍：`splash_root`，标题/文案可读；动画尊重 `reduceMotion`（静态圆）。

---

## 4. Permission（仅麦克风）

```
┌─────────────────────────────────────┐
│       开始之前，需要你的许可          │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ 🎤 麦克风权限          [授权] │  │
│  │    让我听见你的声音…          │  │
│  └───────────────────────────────┘  │
│                                     │
│  ┌───────────────────────────────┐  │
│  │         开始聊天              │  │
│  └───────────────────────────────┘  │
│         前往系统设置（文字按钮）     │
└─────────────────────────────────────┘
```

- 卡片 `surfaceVariant` 背景、16pt 圆角、16pt 内边距；麦克风图标 `brandIndigo`；已授权显示 ✓。
- 主按钮 56pt 高、`brandIndigo`、16pt 圆角、body/加粗白字。
- 未授权/拒绝时显示 `perm_mic_required_hint`（14/20 `error` 或 `textSecondary`）。
- 拒绝/永久拒绝显示 `perm_go_to_settings` 文字按钮，打开 `UIApplication.openSettingsURLString`。
- 首次进入该页时先展示说明，用户点“授权”或“开始聊天”再触发系统权限弹窗。
- 授权成功 → 标记首启完成 → AppRouter 切 Conversation。
- 不显示摄像头卡片；无相机权限申请。
- 标识：`permission_root`、`permission_mic_card`、`permission_start`、`permission_settings`。

---

## 5. Conversation

### 5.1 结构

```
VStack(spacing: 0)
 ├─ 顶部栏（状态指示器 + 更多按钮）                 // 44pt 高
 ├─ ScrollView / LazyVStack                          // 消息区
 │   ├─ 欢迎空态（无消息）
 │   ├─ MessageBubble(user/assistant)
 │   └─ streaming assistant bubble（可选）
 ├─ 实时转写浮层（非 final，覆盖消息区底部）          // 不占列表布局
 ├─ 底部工具栏（麦克风 / 扬声器 / 相机 + 图库）        // 48pt 按钮 + 说明文字 + 安全区
 └─ 错误 banner（顶部 overlay，滑入）
```

- 背景 `background`；顶部栏与工具栏无分割线（用间距区分）。
- 状态指示器：8pt 圆点 + label 14/20；整块可点（断线时重试）；VoiceOver 合并为一句。
- 更多按钮：`ellipsis` 图标，44×44 触控；菜单只有 Settings（无 Chats/时光机）。
- 消息区默认自动滚底；用户上滑后暂停自动滚动，滚回底部恢复（使用 `ScrollViewReader` + `ScrollOffset` 判断）。
- 输入框无（M1 纯语音）。

### 5.2 状态矩阵

| 条件 | 点颜色 | 文案 | 头像 | 其他 |
|---|---|---|---|---|
| 无配置 | — | 阻断卡片：`conv_server_config_required` + `conv_server_config_hint` + “去设置” | 无 | 不启动连接 |
| DISCONNECTED | error | `conv_status_disconnected` | idle | 状态可点重试；ErrorBanner（若存在原因） |
| CONNECTING/RECONNECTING | warning | `conv_status_connecting` | idle | 头像静态 |
| CONNECTED + IDLE | success | `conv_status_idle` | idle | 欢迎空态（首次且无消息） |
| CONNECTED + LISTENING | info | `conv_status_listening` | listening | 状态光环/轻脉冲 |
| CONNECTED + THINKING | warning | `conv_status_thinking` | thinking | 流式 AI 文本 |
| CONNECTED + SPEAKING | success | 无文案（保留状态点） | speaking | TTS 播放 |
| sessionEnded | warning/error | `conv_session_ended` | idle | 常驻 banner + Retry |
| 麦克风权限未授权/丢失 | error | `conv_mic_unavailable` | idle | 打开系统设置（`.openSystemSettings`） |

- 状态颜色同时伴随文案/图标，不只依赖颜色。
- 头像光环：IDLE 不显示；非 IDLE 显示当前状态色；M1 实现基础呼吸/脉冲，Android 的涟漪/粒子/音波作为 P1。

### 5.3 头像

- 外框 120pt；中心渐变圆 96pt（`brandIndigo` → `brandViolet`），默认 `~` 28 Bold 白字；
- 光环 120pt（描边 2pt，状态色 60% 透明度）+ 非 IDLE 呼吸 1.0→1.08，1.2s 循环；
- 无自定义头像、无 job/模式徽标；
- 无障碍：头像为按钮，label 为 `conv_avatar_picker_title` + 当前模式（`LocalizedList` 组合）。

### 5.4 消息气泡

- 用户：右对齐、`brandIndigo` 背景、`textPrimary`、右下 8pt 其余 16pt；
- AI：左对齐、`surface` 背景 + 1pt `surfaceVariant` 描边、左下 8pt 其余 16pt；
- 最大宽度 `screenWidth * 0.75`（iPad 兼容模式用内容宽度 75%），**含气泡内边距**；会话、History 详情、时间线 Hot Slot 统一使用 `AppLayout.messageBubbleMaxWidth`；
- AI 多行消息撑满统一最大宽度（右边缘对齐）；一行放得下的 AI 消息按文字长度自适应；用户消息始终贴右；
- 正文 body 16/24，纯文本，不解析 Markdown；
- AI 流式：`isFinal=false` 时末尾显示闪烁光标（0.6s 透明切换）；final 后定型；
- 不显示时间戳、图片、模式后缀、agent job；
- 标识：`message_user` / `message_ai`（按 id 可用于测试）。
- 空消息不渲染；最终消息去重按 id（`user-<transcriptionID>` / `ai-<messageID>`）。

### 5.5 实时转写浮层

- 固定在工具栏上方，覆盖消息区底部，不挤占列表高度；
- `brandIndigo` 88% 不透明背景、12pt 圆角、水平 16pt 边距、垂直 12pt；
- 文本 16/24 白色；非 final 时右侧三点跳动动画；
- final 后转为用户消息并淡出消失（0.2s）；
- 断连/会话结束时立即清空，不保留残影；
- 标识：`conversation_transcription`。

### 5.6 底部工具栏

- 三项：麦克风、扬声器、相机（相机下方含图库入口），`HStack` 固定间距；
- 按钮 48pt 圆形：开启透明背景、无描边，图标 `brandIndigo`；关闭透明背景 + 1pt `surfaceVariant` 描边 + `textSecondary` 图标；caption 恒为 `textSecondary`（Android `BottomToolbar` parity）；
- caption 12/16 在按钮下方 4pt；
- 按压缩放 0.92（`ButtonStyle`），触控目标 ≥44pt；
- **Mic 开启**：显示 24pt `MicVolumeGlyph`，4 条横线自下而上点亮；点亮 `brandIndigo`、未点亮 `surfaceVariant`，外圈/话筒下半/竖线 `brandIndigo`；bar 颜色动画 120ms ease-out（reduceMotion 时无动画）。电平更新只重绘 glyph（`MicLevelModel` 叶子），不触发 `ConversationView` 整页重绘；
- **Mic 关闭**：`mic.slash.fill` + `textSecondary`，不渲染 glyph；mute 时电平归零；
- glyph 对 VoiceOver 隐藏，电平不播报；Button 的 accessibilityLabel 仍为完整 `conv_toolbar_mic_on/off`；
- 文字：`conv_toolbar_mic_on/off`、`conv_toolbar_speaker_on/off`；
- 标识：`conversation_mic`、`conversation_speaker`、`conversation_camera`、`conversation_gallery`；
- 不显示设备选择器。

### 5.7 错误 banner

- 顶部 overlay，`safeAreaTop + 8pt`，水平 16pt；
- Warning 背景 `warning`、Error 背景 `error`；14/20 白色文本；
- recoverable：右侧 Retry（若可重试）+ 关闭，5s 自动消失；
- fatal：常驻，右侧 Close，可带 Retry / Settings（服务端配置，`.openSettings`）/
  Open system settings（麦克风或相机权限，`.openSystemSettings`）；相机非权限类失败只提示，
  用户可再次点相机重试；
- 断线/认证失败/重连耗尽/会话结束文案区分；
- 动画：从顶部滑入/滑出 0.2s；
- 标识：`conversation_banner`、`conversation_banner_action`。

### 5.8 欢迎/空态

- 首次会话且无消息：卡片居中，宽 75%；
- 标题 `conv_welcome_title`（22 Semibold）；
- 副标题 `conv_welcome_subtitle`（14/20 Secondary）；
- 无 chips（M1 无文本输入/本地文本上行）；
- 空态状态文案直接复用 `conv_status_idle`，不新造；
- 有消息后不再显示。

### 5.9 滚动/动画

- 新消息/流式文本更新时，仅在用户位于底部附近（<80pt）自动滚底；
- 状态变化动画 0.2s ease-in-out；头像光环循环；转写三点循环；
- `accessibilityReduceMotion` 时关闭呼吸/跳动，只保留文本更新。

---

## 6. Settings

```
←   设置
服务
  服务器
  [ ip:port 或 域名:port        ]
  认证密钥
  [ ••••••••••••             👁 ]
  检测服务              [可用]
  允许自签名证书               [OFF]
  [            保存             ]      // 仅 dirty
通用
  触觉反馈                    [ON]
关于
  LivaChat               1.0.0.1
```

- 页面背景 `background`；区块标题 14 Medium `textSecondary`；卡片/输入框 `surface` 或 `surfaceVariant`。
- 输入框 44pt 高、16pt 圆角、聚焦 1pt `brandIndigo` 边框、未聚焦 1pt `surfaceVariant`；
- 服务器占位 `settings_server_placeholder`；密钥占位 `settings_auth_token_placeholder`；
- 密钥 SecureField + 显示/隐藏按钮（44×44）；
- “检测服务”行：按钮 + 状态文本（可用 success / 认证失败 error / 不可用 error）；探测中 spinner；
- “允许自签名证书”Toggle：默认关闭；描述“仅用于可信的本地服务器，会关闭证书校验”；属于运行时配置，保存后重建 runtime；
- 保存按钮 56pt、`brandIndigo`，仅 dirty 时出现；点击先校验，再弹确认“应用并重新连接 / 取消”；
- 触觉开关用 `Toggle`（tint `brandIndigo`），即时生效，不重建；
- 关于：版本 `CFBundleShortVersionString.CFBundleVersion`；
- 不显示 Video 区块、隐私政策、用户协议；
- 标识：`settings_server`、`settings_secret`、`settings_reveal_secret`、`settings_probe`、`settings_probe_status`、`settings_tls_allow_self_signed`、`settings_save`、`settings_haptic`、`settings_back`。

---

## 7. 文案与本地化

- iOS 使用 String Catalog `Resources/Localizable.xcstrings`，source language `en`；当前 16 个 locale（含 `th`/`vi`，不含 `tr`），详见 `docs/ios_multilingual.md` §2.9。
- key 与 Android 对齐；iOS-only/new key 在 catalog 中维护；`make ios-i18n-check` 守卫 key/locale/placeholder/plural/typography。
- 以下是 M1 设计期 key 清单，仅作语义参考；当前 key 以
  `Localizable.xcstrings` 与 `make ios-i18n-check` 为准（`app_name` 由
  `CFBundleDisplayName`/品牌提供，`conv_permission_lost` 已移除，错误文案改用
  `AppErrorMessage` 的 key）。

| Key | English | 简体中文 |
|---|---|---|
| `app_name` | LivaChat | LivaChat |
| `common_retry` | Retry | 重试 |
| `common_close` | Close | 关闭 |
| `common_back` | Back | 返回 |
| `common_cancel` | Cancel | 取消 |
| `splash_tagline` | Your AI companion | 你的AI伙伴 |
| `splash_waking` | Waking up your companion... | 正在唤醒你的伙伴... |
| `perm_dialog_title` | Before we start, we need your permission | 开始之前，需要你的许可 |
| `perm_mic_title` | Microphone permission | 麦克风权限 |
| `perm_mic_desc` | So I can hear your voice and be here for you | 让我听见你的声音，更好地陪伴和倾听 |
| `perm_start_chat` | Start chatting | 开始聊天 |
| `perm_mic_required_hint` | Microphone permission is required to start chatting | 请先授权麦克风权限才能开始聊天 |
| `perm_go_to_settings` | Open system settings | 前往系统设置 |
| `perm_grant_action` | Allow | 授权 |
| `conv_status_idle` | Say something anytime | 随时可以说 |
| `conv_status_connecting` | Connecting... | 正在连接... |
| `conv_status_listening` | I'm listening... | 我在听... |
| `conv_status_thinking` | Let me think... | 让我想想... |
| `conv_status_disconnected` | Disconnected | 连接断开 |
| `conv_welcome_title` | Welcome to LivaChat | 欢迎来到 LivaChat |
| `conv_welcome_subtitle` | I'm your AI companion — chat with me anytime. | 我是你的 AI 伙伴，随时可以和我聊天 |
| `conv_toolbar_mic_on` | Mic on | 麦克风 已开启 |
| `conv_toolbar_mic_off` | Mic off | 麦克风 已关闭 |
| `conv_toolbar_speaker_on` | Speaker on | 扬声器 已开启 |
| `conv_toolbar_speaker_off` | Speaker off | 扬声器 已关闭 |
| `conv_menu_more` | More options | 更多选项 |
| `conv_menu_settings` | Settings | 设置 |
| `conv_server_config_required` | Server setup required | 需要先配置服务器 |
| `conv_server_config_hint` | Please enter the server address and auth key in Settings first. | 请先在设置中填写服务器地址与认证密钥。 |
| `conv_session_ended` | Session ended | 会话已结束 |
| `conv_connection_failed` | Can't connect to the server | 无法连接到服务器 |
| `conv_auth_failed` | Authentication failed. Check your server settings. | 认证失败，请检查服务器配置 |
| `conv_connection_closed` | Connection closed | 连接已关闭 |
| `conv_mic_unavailable` | Microphone unavailable | 麦克风不可用 |
| `conv_audio_unavailable` | Audio unavailable | 音频不可用 |
| `conv_permission_lost` | Microphone permission lost | 麦克风权限已丢失 |
| `settings_title` | Settings | 设置 |
| `settings_section_service` | Service | 服务 |
| `settings_section_general` | General | 通用 |
| `settings_section_about` | About | 关于 |
| `settings_server_label` | Server | 服务器 |
| `settings_server_placeholder` | ip:port or domain:port | ip:port 或 域名:port |
| `settings_auth_token` | Auth key | 认证密钥 |
| `settings_auth_token_placeholder` | See the server config file | 查看 server 配置文件获取 |
| `settings_show_token` | Show key | 显示密钥 |
| `settings_hide_token` | Hide key | 隐藏密钥 |
| `settings_check_service` | Check service | 检测服务 |
| `settings_tls_allow_self_signed` | Allow self-signed certificate | 允许自签名证书 |
| `settings_tls_allow_self_signed_desc` | Only for trusted local servers. Disables certificate verification. | 仅用于可信的本地服务器，会关闭证书校验。 |
| `settings_status_available` | Available | 可用 |
| `settings_status_auth_failed` | Auth failed | 认证失败 |
| `settings_status_unavailable` | Unavailable | 不可用 |
| `settings_save` | Save | 保存 |
| `settings_apply_confirm_title` | Apply and reconnect? | 应用并重新连接？ |
| `settings_apply_confirm_message` | The current conversation will end and the app will reconnect with the new server settings. | 当前会话将结束，并使用新的服务器配置重新连接。 |
| `settings_apply` | Apply and reconnect | 应用并重新连接 |
| `settings_haptic` | Haptic feedback | 触觉反馈 |
| `settings_haptic_desc` | Subtle vibration on state changes | 状态变化时产生轻微震动 |
| `settings_error_address` | Enter a valid server address (ip:port or domain:port). | 请填写正确的服务器地址（ip:port 或 域名:port）。 |
| `settings_error_secret` | Enter the auth key. | 请填写认证密钥。 |
| `settings_save_failed` | Can't save settings | 无法保存设置 |

- 系统权限弹窗文案通过 Info.plist 本地化；`NSMicrophoneUsageDescription` = `perm_mic_desc` 语义，`NSLocalNetworkUsageDescription` 说明“连接局域网内的 LivaChat 服务器”。
- 文案不硬编码在 View 中；通过 `L10n` 访问。复合错误/a11y 使用 `common_title_with_detail` 与 `LocalizedList`；技术 detail 只写日志。
- 工具栏 caption 只显示本地化名词（如“麦克风”“扬声器”“摄像头”，key 为 `*_short`），开关状态由图标表达；三个 caption 行共享 24pt 最小高度并顶部对齐，保证文字同一基线（换行时首行仍对齐）；Accessibility 字号下隐藏 caption，a11y label 仍包含完整状态。
- 服务器地址/密钥输入框、Markdown 代码块、页码/时间码固定 LTR；其余布局跟随 `layoutDirection`。

---

## 8. 无障碍

- 状态指示器合并为一个 accessibility element，label 使用 `conv_status_*` 本地化文案；
- 头像为可点击按钮，label 由标题 + 当前模式经 `LocalizedList` 组合；
- 消息按 `conv_speaker_user`/`conv_speaker_ai` + 正文经 `titleWithDetail` 读屏；
- 工具栏按钮 label 为完整本地化 caption（含开关状态）；AX 字号下 caption 视觉隐藏但 a11y label 保留；
- History/Search/Agent job 的复合 label 使用 `ListFormatter`（本地化逗号/连接词），空片段会被过滤；
- 错误 banner 使用 `.accessibilityAddTraits(.isStaticText)`，出现时 VoiceOver 播报（`.accessibilityFocused`）；
- 最小触控 44×44pt；
- 支持 Dynamic Type；文本换行，工具栏/头像固定；
- 颜色对比：正文 `textPrimary` 对 `background`/`surface` ≥ 7:1；次要文字 ≥ 4.5:1；
- 触觉可在 Settings 关闭，关闭后不产生轻触觉。

---

## 9. iPad 兼容

- target 保持 `TARGETED_DEVICE_FAMILY=1`、默认竖屏；仅 Agent Job 结果全屏查看期间临时解锁横竖屏；iPad 以 iPhone 兼容模式运行；
- 内容容器 `maxWidth: 600` 居中；对话页消息区 75% 最大宽度按内容容器计算；Agent Job 结果覆盖层例外，由窗口级宿主全宽呈现；
- 不实现 iPad 分栏、iPad 横屏专项、Split View、键盘快捷键；
- M2 再决定是否切 Universal。

---

## 10. UI 测试注入

Debug 启动参数（`#if DEBUG`）：

| 参数 | 值 | 作用 |
|---|---|---|
| `-uiTestScreen` | `splash` / `permission` / `conversation` / `settings` | 直接进入页面（跳过 1s splash 与权限） |
| `-uiTestConnection` | `idle` / `listening` / `thinking` / `speaking` / `disconnected` | 预置 Conversation 状态 |
| `-uiTestPermission` | `granted` / `denied` / `undetermined` | Permission 页状态 |
| `-uiTestMessages` | `empty` / `sample` | 空态或两条示例消息 |
| `-uiTestAutoDismissBanner` | `0/1` | 固定 banner 是否自动消失 |

- UI 测试模式使用 `FakeConversationRuntime`，不联网、不开麦克风、不播放音频。
- 所有页面根视图与关键控件暴露 §3–§6 的 accessibility identifiers。
- UI 测试末尾用 `XCUIScreen.main.screenshot()` 附加截图；不依赖外部 `screenshotr`。

---

## 11. 与 Android 的明确差异

| 项 | M1 iOS |
|---|---|
| 录屏 | 不显示 |
| 模式切换/translate/lecture/transcribe/omni | 不显示 |
| Agent job 列表/取消卡片 | 不显示 |
| 历史/时光机/MyLife | 不显示 |
| 设备选择器 | 不显示 |
| 消息时间戳 | 不显示（与 Android 现行气泡一致） |
| Markdown | 不渲染（纯文本） |
| 文本输入/chips | 无（纯语音） |
| 设置页 Video 区块 | 不显示 |
| 隐私政策/用户协议 | 不显示（URL 未定） |
