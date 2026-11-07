# iOS App 多语言支持（16 locales）分析与设计

> **状态**：16 locale 资源、日期/数字/复数、RTL、错误/a11y 组合、Dynamic Type 与校验/CI 已实施（2026-10-25 建立 15 locale 基线；2026-10-27 增加相册指令本地化并新增 `th`/`vi`；2026-10-28 移除 `tr`）。最终 235 key / 12 plural / 0 unused。iOS App 单测见 `make ios-app-test`；build **13**。剩余：真机 per-app language 全量矩阵与截图归档、AX 下 transcription overlay 遮挡观察、th/vi 母语翻译 review。
> **基线**：2026-10-25，iOS `1.0.0 (1)`；2026-10-28 当前 `1.0.0 (13)`，最低 iOS 15.0，Swift 6
> **范围**：`LivaChatApp`（iOS App）。`LivaChatSDK`、Mock、服务端内容不在本地化范围。
> **目标语言**：当前 16 个 App UI locale：English、简体中文、繁體中文、日本語、한국어、Русский、Deutsch、Français、Italiano、Español、Português、हिन्दी、Bahasa Indonesia、**ไทย（Thai）**、**Tiếng Việt（Vietnamese）**、العربية。
> **上游参考**：`docs/android_multilingual.md`（语言口径、术语、日期/复数/数字规则、翻译资源）。
>
> **阅读说明**：正文中未标注当前状态的“15 locale”描述可能指 2026-10-25 基线，“17 locale”描述可能指 2026-10-27 当轮历史；当前实现与验证以 §2.9、§4 和 §10 为准（16 locale，2026-10-28 移除 `tr`）。

---

## 1. 目标与边界

### 1.1 目标

1. App 本地 UI、无障碍文案、`Info.plist` 权限说明、客户端生成的日期/时间/数字/复数文案在 16 个 locale 下正确显示。
2. App UI 语言跟随系统；iOS 13+ 通过系统「设置 → App → LivaChat → 语言」提供 per-app language，不新增 App 内语言切换入口（与 Android D6 一致）。
3. 日期/数字使用**App 语言**而不是系统语言；内部数据 key 保持 POSIX/ASCII，不被本地化数字污染。
4. RTL（العربية）下布局、导航方向、滑动操作、bidi 组合串可用；德语/俄语/印地语/阿拉伯语文本膨胀与字形渲染通过验收。
5. 服务端内容、日志、SDK、协议 code 保持不翻译；App→AI 相册指令改为随 App 语言本地化（其他 App→AI 指令如有则保持不翻译）；Android 已同步，见 §3.8。

### 1.2 支持语言（iOS 标识）

iOS 使用 script/language 标识，与 Android 资源目录的映射如下。`CFBundleLocalizations`、`knownRegions`、`.lproj` 名称和 `Localizable.xcstrings` localization code 均使用同一组值。

| UI locale | iOS 标识 | Android 对应 | 复数数量形 | 备注 |
|---|---|---|---|---|
| English | `en` | `values/` | one / other | 源语言与兜底 |
| 简体中文 | `zh-Hans` | `values-b+zh+Hans/`、`zh-CN` | other | 覆盖 zh-CN / zh-SG |
| 繁體中文 | `zh-Hant` | `values-b+zh+Hant/`、`zh-TW` | other | TW/HK/MO 共用台灣正體＋台灣慣用詞 |
| 日本語 | `ja` | `values-ja/` | other | です・ます調 |
| 한국어 | `ko` | `values-ko/` | other | 해요체 |
| Русский | `ru` | `values-ru/` | one / few / many / other | 月名有格变 |
| Deutsch | `de` | `values-de/` | one / other | 文本最长 |
| Français | `fr` | `values-fr/` | one / other | NBSP、« » |
| Italiano | `it` | `values-it/` | one / other | `mar` 星期/月同形需 FULL 星期 |
| Español | `es` | `values-es/` | one / other | `mar` 同形、¿…?、月年含 de |
| Português | `pt` | `values-pt/` | one / other | 单套 pt-BR 文案 |
| हिन्दी | `hi` | `values-hi/` | one / other | 天城文；名词短语同形 |
| Bahasa Indonesia | `id` | `values-in/` | other | iOS 无 `in` 遗留码问题 |
| العربية | `ar` | `values-ar/` | zero / one / two / few / many / other | RTL；阿拉伯-印度数字 |
| **ไทย** | `th` | `values-th/`（Android 已实施） | other | 泰文；Gregorian + 泰文月/星期名；见 §2.9 |
| **Tiếng Việt** | `vi` | `values-vi/`（Android 已实施） | other | 越南文声调符号；见 §2.9 |

- `CFBundleLocalizations` 需要显式包含以上 16 项；仅靠 Xcode `knownRegions` 不一定能让 per-app language 与 App Store 语言列表完整暴露。
- `zh-Hans` / `zh-Hant` 使用 script 标识；系统「应用语言」会显示中文（简体）/中文（繁體）。
- `pt` 为通用标识，文案按 pt-BR 单套；pt-PT 设备同样命中该目录，显示巴葡用词（与 Android 一致）。若 Xcode 在 string catalog 中将 Portuguese 归一为 `pt-BR`/`pt-PT`，以 Xcode 实际写入的 code 为准，但 `.lproj`、`CFBundleLocalizations`、`Bundle.main.localizations` 必须保持一致，文案仍只维护一套 pt-BR。
- 泰语 `th` **已于 2026-10-27 重新加入**（App UI + translate），并新增 `vi`，替代此前“移除 `th`”的决定；详见 §2.9。**Android 已于 2026-10-27 实施 17 locale（`values-th/`、`values-vi/`，见 `docs/android_multilingual.md` §21）**；iOS 可复用 184 个同名共享 key 中的 183 个（`conv_avatar_save_failed` 走 authored）。`tr` 已于 2026-10-28 移除。

### 1.3 在范围内 / 不在范围内

| 类别 | 范围 | 说明 |
|---|---|---|
| App UI 文案（按钮/弹窗/空态/错误/提示） | ✅ | `Resources/Localizable.xcstrings` |
| 无障碍描述 | ✅ | `L10n` / SwiftUI accessibility |
| `Info.plist` 权限用途说明 | ✅ | `InfoPlist.strings`（4 个 key） |
| 客户端日期/时间/周期/计数 | ✅ | `MylifeDateLabels`、`HistoryDateFormatting`、`AgentJobFormatting` 等 |
| 客户端错误与 banner 标题 | ✅ | App 生成的错误需 key 化；服务端 detail 见边界 |
| App 内「对方语言」显示名 | ✅ | `Locale.localizedString(forLanguageCode:)` |
| 模式后缀（转录/原文/译文） | ✅ | 展示时按当前 UI locale 拼接 |
| 服务端内容（AI 文本/转录/MyLife 摘要/Agent 结果） | ❌ | 服务端产物不翻译 |
| SDK / Mock / 日志 | ❌ | 非 App 用户文案 |
| App→AI 相册指令（`conv_gallery_action_text`） | ✅ | 16 locale 本地化，随 App 语言选择，英文兜底；见 §3.8 |
| 协议 code（`chat`/`translate`/`omni` 等） | ❌ | 仅显示名翻译 |
| 调试页 `AudioSpikeView` | ❌ | DEBUG-only 诊断页 |
| UI 测试 fixture / 预览文案 | ❌ | 测试数据，不是产品文案 |
| App Store 截图/商店元数据 | ❌ | 不在代码仓库范围 |

### 1.4 与 Android 的功能差异

iOS 没有以下 Android 功能，因此对应的 Android-only key 不迁移：

- 音频输入/输出设备选择器（Android `device_*`、`conv_select_mic/speaker_device`）。
- 麦克风/录屏前台服务通知（Android `notif_*`）。
- 屏幕抓取浮窗与相关权限/错误（Android `cap_*`、`capture_*`、`conv_screen_capture_*`）。
- 公共/局域网服务器选项选择器（iOS 使用服务器地址输入框）。

iOS 独有、需要单独翻译的 UI：头像定制、历史、MyLife 时间线/相册/音频、TLS 自签设置、设置页 Apply/TLS 等；见 §5.1。

---

## 2. 现状分析（codebase）

### 2.1 资源与工程配置

| 项 | 当前事实 | 位置 |
|---|---|---|
| 字符串目录 | `Localizable.xcstrings`，source language `en` | `apps/ios/LivaChatApp/Resources/Localizable.xcstrings` |
| key 数量 | **235** 个 | 同上 |
| 已有 locale | **16** 个；全部 key 无缺失、全部 `translated` | 同上 |
| 复数组 | **12** 组；ru 四形、ar 六形；fr/it/es/pt `many` 走 `other` 回退（app 计数范围内文案相同） | 同上 |
| 未引用 key | **0** 个（由 `check_ios_localization.py` 守卫） | 同上 |
| 与 Android key 关系 | 184 个同名共享；51 个 iOS-authored；70 个 Android-only（2026-10-27 新增 `conv_image_action_text`） | 与 `apps/android/LivaChatApp/src/main/res/values/strings.xml` 对比 |
| L10n 使用 | 47+ 个 Swift 文件引用 `L10n`；复合 a11y 使用 `LocalizedList` | `Core/UI/L10n.swift`、`LocalizedList.swift` 及 Features |
| InfoPlist 文案 | 4 个权限说明；16 个 `.lproj` | `Resources/InfoPlist.strings`、`Resources/Info.plist` |
| 工程 region | `developmentRegion = en`；`knownRegions = 16 locale + Base` | `LivaChatApp.xcodeproj/project.pbxproj` |
| `CFBundleLocalizations` | 16 项 | `Resources/Info.plist` |
| 最低系统 / Swift | iOS 15.0 / Swift 6 | `docs/ios_dev_rules.md`、pbxproj |
| 系统字体 | `Font.system`（AppFont / MylifeFont），无自定义字体 | `Core/UI/AppTheme.swift:55-68`、`Features/Timeline/Components/MylifeTheme.swift` |
| 本地化测试 | catalog 守卫 + 16 locale 单测 + 定向 RTL/AX UI 测试 | `LivaChatAppTests`、`LivaChatAppUITests` |
| CI | `ci-mobile.yml` 含 `ios-app` job：本地化守卫 + App 单测 | `.github/workflows/ci-mobile.yml` |

> 注：§2.3 的问题表是开工前基线快照；其中日期、数字、kindName、计数等在 M1–M4 已修复。

### 2.2 `L10n` 与运行时解析

`Core/UI/L10n.swift`：

```swift
enum L10n {
    static func t(_ key: String) -> String {
        NSLocalizedString(key, comment: "")
    }

    static func plural(_ key: String, _ count: Int) -> String {
        String.localizedStringWithFormat(t(key), count)
    }
}
```

问题：

- `t()` 使用 App bundle 的 preferred localization，语言选择正确；但**没有显式 locale 参数**，测试和跨 locale 校验只能依赖 App 语言。
- `plural()` 使用 `String.localizedStringWithFormat`，按**当前系统 locale** 选择复数形/格式化数字；当 App per-app language 与系统语言不同（例如系统 en、App ru/ar），会出现俄语/阿语复数形错误或阿拉伯数字混排。必须改为 `String(format:locale:arguments:)` 或等价方式。
- `L10n.bannerTitle` 直接拼接 `title: detail`，detail 可能是未本地化的系统/SDK 文本；需要明确错误文案边界。

### 2.3 日期、数字、复数的现状

| 位置 | 当前行为 | 问题 |
|---|---|---|
| `Core/Mylife/MylifeDateLabels.swift:9-18` | `effectiveLocale` 将所有 `zh*` 强制成 `zh-Hans`，其他语言全部 `en` | 繁體日期被强制简体；ja/ko/ru/de/fr/it/es/pt/tr/hi/id/ar 全部显示英文日期 |
| `MylifeDateLabels` 日期方法 | 只有 `zh` / `en` 两个分支 | 无 15 locale 的 year/month/year/month-day/weekday 规则 |
| `weekRangeLabel` | 固定 `M/D – M/D`，忽略 locale | de/ru/tr/fr/…/ar 的日期顺序与分隔符错误 |
| `weekNumberLabel` | 仅 `Wn` / `n周` | 无 `第n週`、`n주차`、`Неделя n`、`KW n`、`Sn`、`n. Hafta`、`सप्ताह n`、`Pekan n`、`الأسبوع n` |
| `timeLabel` | `en_US_POSIX` + `HH:mm` | ar 仍显示拉丁数字；与日期部分的阿数混排 |
| `HotSlotRows.swift:136-143`、`SearchResultsView.swift:70-87` | `formatRowTime`/搜索结果时间调用 `timeLabel` 时没有 locale 参数 | 改造 `timeLabel(locale:)` 后必须把 App locale 传入这些显示路径 |
| `monthDayText(date:)` | `M月d日` / `MMM d` | 其他语言退回英文格式；de/ru/tr/hi/id/ar 顺序与尾点不对 |
| `MylifeAggregator.swift:130-135` | 无 summary 时仅 `zh` / `en` | Hant 输出简体摘要；其他语言输出英文 `N days` |
| `Features/Timeline/Home/TimelineHomeViewModel.swift:54` | `private let locale = MylifeDateLabels.effectiveLocale` 在 init 缓存 | 语言变化后 scopeLabel/日期 stale；应改为 AppLocale 计算值或注入 |
| `Core/History/HistoryDateFormatting.swift:4-27` | 静态 `DateFormatter.locale = .autoupdatingCurrent`；`NSLocalizedString` + `localizedStringWithFormat` | App 语言与系统语言不一致时日期/星期/复数混语言 |
| `Core/Conversation/AgentJobFormatting.swift:5-55` | `startedTime`/`elapsed` 默认 `.autoupdatingCurrent`；`badgeCount` 直接 `String(count)` | ar 不显示阿数；App 语言与系统不一致时数字体系错误 |
| `Features/Conversation/AgentJobResultOverlay.swift:143-154` | 静态 `DateFormatter.locale = .current` + `HH:mm` | 静态 formatter 缓存系统语言；语言切换后不刷新 |
| `Features/Timeline/Audio/AudioSegmentCard.swift:8-10` | `String(format: "%d:%02d")` 默认 locale | ar 数字、App 语言与系统语言不一致时不可控 |
| 多处 `Text("\(pageIndex + 1) / \(count)")` | 原始整数插值 | ar 拉丁数字；RTL bidi 未验证 |
| `SearchResultsView.swift:113-121` | `kindName` 硬编码 `Chat/Transcript/Translation/Agent/Photo/Event` | VoiceOver 在 15 locale 下始终英文 |
| `Features/Conversation/AgentJobListPanel.swift:245` | a11y label `summary, started, elapsed` 硬编码 `", "` | 15 locale 下标点/顺序错误 |
| `Features/History/HistoryComponents.swift:67` | a11y label `title, subtitle` 硬编码 `", "` | 同上 |
| `Features/Timeline/Home/SearchResultsView.swift:87` | a11y label `kind, time, snippet` 硬编码 `", "` | 同上 |
| `Features/Conversation/AvatarView.swift:40/103` | a11y label `title, mode` 硬编码 `", "` | 同上 |
| `Features/Timeline/Components/PeriodCard.swift:155` | a11y label `"\(imageTotal)"` 使用原始数字 | ar 拉丁数字；应为 `LocalizedNumber` |
| `Features/Timeline/Components/AudioEntryButton.swift:22` | 可见 `Text("\(count)")` 使用原始数字 | ar 拉丁数字；应为 `LocalizedNumber` |

**数据 key 与显示格式混用**：

- `MylifeDateLabels.dateKey`、`isoWeekKey`、`localDateKey`、`MylifeModels`/`TimelineHomeViewModel` 的日期范围、`HistoryService` 的 `yyyy-MM-dd` 是**服务端/数据库 key**，必须始终使用 ASCII/Latin 数字与 POSIX 格式。
- `MylifeDateLabels.timeLabel` 是**显示**时间，不应使用 `en_US_POSIX`；两者需要拆开。
- 当前代码中 `String(format:)` 未显式 locale 的 data-key 站点在系统 locale 为 ar/en 时存在潜在分组/数字风险；设计必须把所有 data-key 格式统一到 `en_US_POSIX`。

### 2.4 硬编码中文与未翻译内容

主源集 Swift 中用户可见的硬编码中文：

| 位置 | 内容 | 处理 |
|---|---|---|
| `Core/Mylife/MylifeDateLabels.swift` | `年/月/日/周` 日期模板 | 改为资源模板 + 语言样式表 |
| `Core/Mylife/MylifeAggregator.swift` | `共 N 天` summary 兜底 | 改为 `mylife_period_summary` plural |
| `Core/Video/GalleryPhotoSender.swift`（原 :21） | `描述图片内容` | 已移除硬编码；`GalleryPhotoEncoder.actionText` 改为 `L10n.t("conv_gallery_action_text")`，17 locale 本地化；见 §3.8 |
| `Core/Mylife/UITestMylifeStore.swift`、`App/AppContainer.swift` | UI test fixture | 测试数据，不在产品本地化范围 |

`Localizable.xcstrings` 的 `en` 值已检查无汉字；`zh-Hans` 之外没有中文资源。`InfoPlist.strings` 英文无汉字。

### 2.5 错误与 banner

- banner 标题通过 `L10n.t(titleKey)` 本地化；detail 来自：
  - 服务端 SDK `message`（内容边界，不翻译）；
  - `CameraCaptureError.errorDescription`（英文硬编码，显示在相机失败 banner）；
  - `AudioCaptureController.CaptureError.errorDescription` / `onFailure` 组合英文（显示在采集失败 banner）；
  - `SettingsViewModel.saveError`（`error.localizedDescription`，直接拼进 `settings_save_failed`）；
  - `AvatarCustomizationModel` 把 `error.localizedDescription` 拼进 `conv_avatar_save_failed`。
- 结果：App 标题是目标语言，detail 可能是英文/系统语言，出现混排。

### 2.6 RTL 与布局

- SwiftUI 的 `.leading` / `.trailing`、`HStack` 顺序、`.move(edge:)` 大多自动镜像。
- 当前有 **9 处方向性 SF Symbol** 硬编码为 LTR：
  - `SettingsView.swift:44`、`WebPageView.swift:90`、`HistoryDetailView.swift:117`、`HistoryListView.swift:41`、`TimelineHomeView.swift:81`、`FrameGalleryView.swift:94`、`HotSlotScreen.swift:98`、`AudioGalleryView.swift:55`（`chevron.left`）；`GalleryPreviewView.swift:41`（`arrow.left`）。
- `HistoryComponents.swift:83-100` 的删除滑动写死 `translation.width < 0` / 负 offset；RTL 下 trailing 在左侧，手势方向与 reveal 方向会错。
- `ImageViewerOverlay.swift:202-227` 的自定义横向翻页写死 `translation.width < 0` / `> 0` 与 `pageDrag` 方向；RTL 下需反转。`GalleryViewerOverlay` 用系统 `TabView(.page)`，仍需截图确认系统镜像行为。
- `MylifeFlowLayout.swift:61-82` 固定从左到右排列；RTL 下 chip 流方向不镜像。
- 固定 offset：`ConversationView.swift:408`、`PeriodCard.swift:154`；RTL 下需方向感知或保持装饰性 LTR 并截图确认。
- `AudioSegmentCard.swift:145-148` 的活动时间码是 `position / duration` 组合串；ar 阿数下需 LTR 容器/截图，避免 bidi 重排。
- `ConversationView`/`TimelineBubbleView` 的气泡 `.leading/.trailing` 已方向感知；`MarkdownUI` 表格/代码块可能保持 LTR，需单独验收。
- 系统字体覆盖全部目标脚本；`AppFont`/`MylifeFont` 的固定 `lineSpacing` 对天城文/阿拉伯文可能偏紧，需真机/大字号检查。

### 2.7 测试与门禁

- 没有 `Localizable.xcstrings` 的资源守卫测试。
- `MylifeDateLabelsTests` 只有 `en` / `zh` 用例（2026-08-21、W34）。
- `MylifeAggregatorTests`、`AgentJobStatusModelTests`、`AudioGalleryMathTests` 只断言英文。
- `AppShellUITests` 中存在硬编码中文断言（例如 `AppShellUITests.swift:958、1243、1258`），且部分测试显式设置 `-AppleLanguages (zh-Hans)`；扩展到 15 locale 后不能继续依赖系统语言。
- `.github/workflows/ci-mobile.yml` 只跑 SDK；App 本地化资源/格式化测试没有 CI 门禁（已实施，见 §10）。

### 2.8 缺口汇总

| 严重度 | 缺口 | 影响 |
|---|---|---|
| 高 | 日期/周期/摘要只有 en/zh，且 Hant 被强制 Hans | 13 个 locale 的 MyLife 页面出现英文/简体混排 |
| 高 | `L10n.plural` 使用系统 locale | ru/ar 复数形错误；ar 数字体系错误 |
| 高 | ar 需要 `ar_SA` + Gregorian；`Locale("ar")` 在 Foundation 下可能给拉丁数字 | 日期/时间/数字可能全拉丁或回历 |
| 高 | InfoPlist/knownRegions/CFBundleLocalizations 只有 2 个 locale | per-app language 列表、App Store 语言、权限说明不完整 |
| 中 | Banner detail / 设置保存错误混入英文/系统语言 | 用户看到混排错误 |
| 中 | 计数/页码/时间码使用原始插值或默认 locale | ar 数字不一致 |
| 中 | RTL 方向性符号、滑动、flow layout | 阿拉伯语下导航/删除操作错向 |
| 中 | 无资源/占位符/复数守卫 | 缺失 key、占位符错位、复数形缺失无法在构建时发现 |
| 低 | 单 catalog 体积与翻译 review 成本 | 已清理 0 个未引用 key；235 key 仍在单表维护 |

### 2.9 泰语（`th`）与越南语（`vi`）语言轮（2026-10-27 已实施，待 review）

> 状态：**已实施（2026-10-27），待 review**。当轮 iOS **17 locale**（App UI + translate）；build **11**；单测 519 / 0 failures / 1 skipped。
>
> **后续（2026-10-28）**：`tr` 已移除（App UI、`tr.lproj`、`CFBundleLocalizations`、`knownRegions`、translate picker），当前 **16 locale / 15 个具体语言候选（`auto` 另置顶）**、build **13**；本节其余数字为当轮历史记录。

**已确认决策**：

| 决策 | 结论 |
|---|---|
| 泰语日历 | **Gregorian**（泰文月/星期名，年份为公历；不使用佛历） |
| `th` | **重新加入** App UI 与 translate（覆盖 2026-10-27 早前“移除 th”的决定） |
| `vi` | 新增 App UI 与 translate |
| 翻译来源 | 专业翻译 + 母语 review；Android 已落 `values-th`/`values-vi`，183 shared key 从 Android 导入，51 iOS-only + forced `conv_avatar_save_failed` 另译 |
| 本轮范围 | Android 已实施并提交 17 locale（versionCode 138）；iOS 已实施 17 locale（待 review），复用 Android shared 资源作为事实源 |
| 服务端 | 不改白名单；更新翻译 prompt 示例并验证 LLM/TTS |

#### 2.9.1 范围与工作量

- **App UI**：从 15 locale 扩展到 17 locale，新增 `th`（泰语）、`vi`（越南语）。
- **translate**：iOS「对方语言」候选新增 `th`、`vi`；候选变为 `auto` + 16 个具体语言。
- **不新增 key**：仍为 235 key；Android 已落地，iOS 翻译量为：
  - 184 个同名共享 key 中，**183 个 ×2 = 366 条从 Android `values-th`/`values-vi` 导入复用**（`conv_avatar_save_failed` 因 iOS 无占位符走 authored）；
  - 52 个 authored 条目（51 个 iOS-only + forced `conv_avatar_save_failed`）×2 = **104 条新译**；
  - `InfoPlist.strings` 4 ×2 = **8 条**；
  - 合计 **112 条新翻译**（470 条 strings + 8 条权限说明）。
- 12 组 plural 不变；泰语/越南语均只用 `other`（CLDR）。
- 未支持 locale 仍回退英文（iOS source language / development language 为 `en`）。

#### 2.9.2 翻译来源与流程

- Android 已于 2026-10-27 落地并提交 th/vi（`versionCode 138`；`docs/android_multilingual.md` §21）：`values-th`/`values-vi` 与 iOS 有 184 个同名共享 key。`import_android_to_ios.py` 已加入 `LOCALE_DIRS["th"]="values-th"`、`["vi"]="values-vi"`。
- 已执行流程：
  1. 专业翻译 / 母语 reviewer 用**同一批次**产出两端 key 并集（184 shared + 70 Android-only + 51 iOS-only = 305 key/locale）的 th/vi 译稿； Android 已落其 254 个，iOS 补齐 51 个 iOS-only + forced `conv_avatar_save_failed`（共 52 个 authored 条目）。
  2. iOS 用 `import_android_to_ios.py` 从 Android 导入 **183 个 shared key**（`conv_avatar_save_failed` 走 authored）；将 **52 个 authored 条目**（51 iOS-only + forced `conv_avatar_save_failed`）写入 `scripts/i18n/authored_translations.json` 的 `th` / `vi` 节（含 `mylife_period_summary` 等 plural 的 `{"other": "..."}`）。
  3. 所有 `th` / `vi` 条目均为 `state = translated`。
  4. 已新增 `Resources/th.lproj/InfoPlist.strings`、`Resources/vi.lproj/InfoPlist.strings`（4 个权限 key）。
- 术语沿用现有规范：产品名 `LivaChat`、`Agent`、`Omni`、`MyLife` 等不翻译；日期/错误/模式后缀按本地语言表达。Android 文档 §21.9/§21.10 维护共享术语表，iOS 以同一表 review。
- th/vi 翻译仍待母语 review；发布前必须与 Android 同表校验，避免同一 key 文案漂移。

#### 2.9.3 日期 / 数字 / 复数设计

| locale | plural | 日历 | monthYear | monthDay | weekday | 周范围 | 备注 |
|---|---|---|---|---|---|---|---|
| `th` | other | **Gregorian** | full | short | short | `dd/MM` | 泰文；已确认 Gregorian，佛历不在本次范围 |
| `vi` | other | Gregorian | fullStandalone | short | full | `dd/MM` | 越南文声调符号；行高需实测 |

- **泰历决策（已确认）**：CLDR 的 `th` 默认日历是 Buddhist，但本次确认使用 **Gregorian 年份 + 泰文月/星期名**，避免 MyLife 数据 key 与显示年份不一致。佛历不在本次范围；如后续需要，需单独设计年份转换与测试。
- `MylifeDateLabels.style(for:)` 新增：
  - `case "th"`: `monthYear: .full`, `monthHeader: .full`, `monthDay: .short`, `weekday: .short`, `weekdayLowercase: false`, `weekRange: .dayMonthSlash`。
  - `case "vi"`: `monthYear: .fullStandalone`, `monthHeader: .fullStandalone`, `monthDay: .short`, `weekday: .full`, `weekdayLowercase: false`, `weekRange: .dayMonthSlash`。
  - 具体短月/短星期缩写以 `DateFormatter` 的 `th`/`vi` symbols 为准，并加入快照测试。
- `AppLocale.formattingLocale` 对 `th` / `vi` 使用默认 `Locale(identifier:)`；`isRTL` 为 false；`LocalizedNumber` 沿用 NumberFormatter；`LocalizedDate.time` 继续 24 小时制。
- 泰文：无词间空格、组合符/断行与 Dynamic Type 需重点验收；越南文：声调符号、行高、截断需重点验收。

**Android 已实现输出（iOS 对齐基准）**

以 2026-08-21（ISO 2026-W34）为例：

| 项 | `th` | `vi` |
|---|---|---|
| 月年 | `สิงหาคม 2026` | `Tháng 8 năm 2026` |
| 月日 | `21 ส.ค.` | `21 thg 8` |
| 星期 | `ศ.`（SHORT） | `Thứ Sáu`（FULL） |
| 周范围 | `17/08 – 23/08` | `17/08 – 23/08` |
| 周徽标 | `สัปดาห์ที่ 34` | `Tuần 34` |
| 摘要 | `… · 5 วัน` | `… · 5 ngày` |
| History LONG | `21 สิงหาคม ค.ศ. 2026` | `21 tháng 8, 2026` |
| History 星期 | `วันศุกร์`（FULL） | `Thứ Sáu`（FULL） |
| 相册指令 | `อธิบายเนื้อหาในภาพ` | `Mô tả nội dung hình ảnh` |

对齐 Android 的 iOS 模板建议：

| key | `th` | `vi` |
|---|---|---|
| `mylife_month_year_format` | `%1$@ %2$@` | `%1$@ năm %2$@` |
| `mylife_overview_month` | `%2$@ %1$@` | `%2$@ năm %1$@` |
| `mylife_month_day_format` | `%2$@ %1$@` | `%2$@ %1$@` |
| `mylife_week_range_format` | `%1$@ – %2$@` | `%1$@ – %2$@` |
| `mylife_week_label` | `สัปดาห์ที่ %1$@` | `Tuần %1$@` |
| `mylife_period_summary` | `%1$@ · %2$lld วัน` | `%1$@ · %2$lld ngày` |
| `history_item_meta` | `%1$@, %2$@, %3$lld ข้อความ` | `%1$@, %2$@, %3$lld tin nhắn` |
| `conv_gallery_action_text` | `อธิบายเนื้อหาในภาพ` | `Mô tả nội dung hình ảnh` |

- 参数顺序按现有调用：`mylife_month_year_format(monthText, yearText)`、`mylife_month_day_format(monthText, dayText)`、`mylife_overview_month(yearText, monthText)`；因此月日模板必须用 `%2$@ %1$@` 才能输出日先。
- `mylife_period_summary` 与 `history_item_meta` 的 th/vi 只有 `other` 类别；`history_item_meta` 的 `%3$lld` 为消息数。
- `LocalizedDate.longDate`/`symbols` 已显式 `calendar = Calendar(identifier: .gregorian)`；实现 th 时必须保留，禁止佛历 `DateFormatter`。Android 已在 `HistoryScreen` 验证输出年份为 2026（非 2569）。iOS Foundation 的 LONG 可能在 `ค.ศ.`/逗号上略有差异：iOS `vi` 的 LONG 为 `ngày 21 tháng 8, 2026`（比 Android 多 `ngày`），验收以 Gregorian 年份与非佛历为准。

#### 2.9.4 translate 候选设计

iOS `LanguagePickerDialog.languageCodes` 增加 `th`、`vi`，候选顺序：

```text
auto, zh, en, ja, ko, fr, it, de, es, pt, ru, tr, hi, id, th, vi, ar
```

- App UI：17 locale；translate：`auto` + 16 个具体语言。
- 与 App UI 的唯一语言级差异仍是 `zh-Hans` / `zh-Hant` 合并为 `zh`；`th` / `vi` 同时进入 UI 与 translate。
- `th` 是此前 2026-10-27 从 iOS picker 移除后的**重新加入（已确认）**；需要同步更新相关文档的“已移除”描述。
- Android 已于 2026-10-27 实施并提交 17 locale / 16 translate（`versionCode 138`；`docs/android_multilingual.md` §21，待 review）；iOS 复用 shared key。服务端 `_normalize_lang` 已接受任意 2–3 位 ISO 码，`th`/`vi` 无需新增白名单；需更新翻译 prompt 示例并验证 LLM/TTS。

#### 2.9.5 工程改动清单

| 层 | 文件 | 改动 |
|---|---|---|
| App 语言 | `Core/UI/AppLocale.swift` | `supportedCodes` 加 `th`/`vi`；新增 `AppLocaleTests` resolve/format/RTL 用例 |
| 字符串 | `Resources/Localizable.xcstrings` | 235 key × 2 locale；plural 用 `other` |
| 权限 | `Resources/th.lproj/InfoPlist.strings`、`Resources/vi.lproj/InfoPlist.strings` | 4 个权限说明各 2 份 |
| 工程配置 | `Resources/Info.plist` | `CFBundleLocalizations` 15 → 17 |
| 工程配置 | `LivaChatApp.xcodeproj/project.pbxproj` | `knownRegions` 加 `th`/`vi`；InfoPlist variant group / 文件引用 |
| 日期 | `Core/Mylife/MylifeDateLabels.swift` | 新增 `th`/`vi` style 分支 |
| 选择器 | `Features/Conversation/ModeSelectionDialogs.swift` | `languageCodes` 加 `th`/`vi` |
| 守卫 | `scripts/i18n/check_ios_localization.py` | `LOCALES` 加 `th`/`vi`；`REQUIRED_PLURAL_CATEGORIES` 加 `other` |
| 翻译源 | `scripts/i18n/authored_translations.json` | 新增 52 个 authored 条目（51 iOS-only + forced `conv_avatar_save_failed`，含 12 个 plural 的 `other`） |
| 导入脚本 | `scripts/i18n/import_android_to_ios.py` | `LOCALE_DIRS` 加 `th`/`vi`，直接导入 `values-th`/`values-vi` 的 183 shared key；forced `conv_avatar_save_failed` 走 authored |
| 测试 | `LivaChatAppTests/*` | `AppLocaleTests`、`L10nFormattingTests`、`MylifeDateLabelsTests`、`HistoryDateFormattingTests`、`AgentJobStatusModelTests`、`MylifeAggregatorTests` |
| 服务端 | `services/session/orchestrator.py` | 翻译 prompt 示例补 `th`/`vi`；无逻辑白名单改动 |
| 文档 | `docs/ios_*.md`、`docs/android_multilingual.md` | 同步 17 locale / cross-platform 差异 |

#### 2.9.6 测试与验收

- `check_ios_localization.py`：235 key × 17 locale；4 InfoPlist × 17；`knownRegions` / `CFBundleLocalizations` 17；全部 `translated`；th/vi plural 只含 `other`。
- `AppLocaleTests`：`resolvePreferred(["th"]) == "th"`、`resolvePreferred(["vi"]) == "vi"`、非 RTL、formatting locale。
- `L10nFormattingTests`：th/vi 的常用 key、plural、日期/数字样本。
- `MylifeDateLabelsTests` / `HistoryDateFormattingTests` / `AgentJobStatusModelTests` / `MylifeAggregatorTests`：17 locale 矩阵；`HistoryDateFormattingTests` 必须断言 th 年份为 2026（非佛历 2569）。
- UI/真机：per-app language 17 项；泰文断行/组合符、越南文声调/Dynamic Type 截图；translate picker 的 `th`/`vi` 可选并下发正确 code。
- 服务端：`th`/`vi` 作为 `local_language` / `my_language` 的真实会话验证（LLM 翻译 + TTS 发音）。

**实施结果（2026-10-27）**：`check_ios_localization.py` 通过（235 key / 12 plural / 17 locale / 4 InfoPlist）；`make ios-app-test IOS_SIMULATOR="iPhone 16"` **519 tests / 0 failures / 1 skipped**。真机 per-app language、translate/TTS 联调与母语翻译 review 尚未执行。

#### 2.9.7 风险与边界

1. **泰历**：已确认 Gregorian；佛历不在本次范围，后续如需佛历另立设计与测试。
2. **翻译质量**：Thai/Vietnamese 需母语 review；不可直接依赖机器翻译上线。
3. **泰文排版**：无空格语言、组合符、断行、Dynamic Type；需真机截图。
4. **越南文排版**：声调符号行高、截断、按钮宽度。
5. **Android 协作**：Android 已于 2026-10-27 实施 17 locale（待 review）；iOS 直接导入 183 shared key，另译 51 个 iOS-only + forced `conv_avatar_save_failed`（52 个 authored 条目；184 同名 key 中的 forced 例外）。
6. **服务端 TTS/LLM**：`th`/`vi` 的翻译与语音输出需真实 provider 验证。
7. **App Store 元数据**：17 语言的商店截图/描述需单独维护。
8. **`th` 回归**：已确认重新加入；实施时需同步所有文档与测试中的“已移除”表述。

#### 2.9.8 实施记录

1. 已完成：与 Android 对齐翻译批次与术语表（Android 254 key/locale）。
2. 已完成：52 个 authored 条目 ×2 + InfoPlist 4×2 = 112 条；183 shared key 已从 Android 导入，`conv_avatar_save_failed` 走 authored。
3. 已完成：xcstrings、InfoPlist、CFBundleLocalizations、knownRegions、PBX。
4. 已完成：`AppLocale` + `MylifeDateLabels` + picker。
5. 已完成：`check_ios_localization.py` / `authored_translations.json` / 导入脚本 `LOCALE_DIRS`。
6. 已完成：build 11，单测 519 / 0 failures / 1 skipped；真机 per-app language + translate 联调待 review 后执行。
7. 待 review 后：母语翻译 review、真机走查。

---

## 3. 设计

### 3.1 `AppLocale`：App 语言的唯一事实源

**决策**：UI 语言以 `Bundle.main.preferredLocalizations.first` 为准，不使用 `Locale.current` 判断 App 语言。Apple 明确 `Locale.current` 不保证等于 App 当前运行的 bundle localization（见附录 B）。

建议新增 `Core/UI/AppLocale.swift`：

```swift
enum AppLocale {
    static var current: Locale {
        let id = Bundle.main.preferredLocalizations.first ?? "en"
        return formattingLocale(for: id)
    }

    static var languageCode: String {
        current.languageCode ?? "en"
    }

    static var isRTL: Bool {
        Locale.characterDirection(forLanguage: languageCode) == .rightToLeft
    }

    static var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.locale = current
        calendar.timeZone = .autoupdatingCurrent
        return calendar
    }

    /// 资源语言与格式化 locale 的映射。
    static func formattingLocale(for id: String) -> Locale {
        switch id {
        case "ar": return Locale(identifier: "ar_SA") // Arabic-Indic digits + MSA month names
        case "pt": return Locale(identifier: "pt-BR") // 单套 pt-BR 文案
        default: return Locale(identifier: id)
        }
    }

    /// 对方语言选择器显示名使用 App UI locale。
    static var displayLocale: Locale {
        Locale(identifier: Bundle.main.preferredLocalizations.first ?? "en")
    }
}
```

关键点：

- `AppLocale.supportedCodes` 当前为 16 项（含 `th` / `vi`，不含 `tr`）；已补 `resolvePreferred` / `isRTL` / formatting locale 测试。
- `zh-Hant` 必须映射到 `Locale("zh-Hant")`，不能强制 `zh-Hans`。
- `ar` 的**字符串资源**使用 `ar`；**格式化 locale** 使用 `ar_SA`，否则 Foundation 的 `ar` 可能给拉丁数字；`ar_SA` 默认日历是伊斯兰历，所有日期 formatter 必须显式 `calendar = Calendar(identifier: .gregorian)`。
- `pt` 的字符串 localization 使用 `pt`；格式化 locale 使用 `pt-BR`。
- `AppLocale.current` 不缓存，或在 `NSLocale.currentLocaleDidChangeNotification`（C 名 `NSCurrentLocaleDidChangeNotification`）/前台切换时清缓存；App 语言变化通常由系统重启 App 生效。
- 测试可注入 `Locale`，不要依赖进程系统语言。

### 3.2 `L10n`：locale-aware 格式化 API

建议将 `L10n` 扩展为：

```swift
enum L10n {
#if DEBUG
    /// 默认 App bundle；DEBUG 单测可指向 <locale>.lproj，tearDown 恢复 .main。
    static var resourceBundle: Bundle = .main
#else
    static let resourceBundle: Bundle = .main
#endif

    static func t(_ key: String) -> String {
        NSLocalizedString(key, bundle: resourceBundle, comment: "")
    }

    /// 通用格式化（非 plural 与多参数 plural 都用它）。模板来自 App 当前语言；
    /// locale 用于数字/复数规则与数字体系。
    static func format(
        _ key: String,
        locale: Locale = AppLocale.current,
        _ args: CVarArg...
    ) -> String {
        String(format: t(key), locale: locale, arguments: args)
    }

    /// 单 count 复数；多参数 plural 用 `format`。
    static func plural(
        _ key: String,
        _ count: Int,
        locale: Locale = AppLocale.current
    ) -> String {
        String(format: t(key), locale: locale, count)
    }
}
```

约束：

- **不要**再用 `String.localizedStringWithFormat` 作为 UI 格式化路径；它按系统 locale 选择复数形/数字。
- `String(format: locale:)` 已验证会按传入 locale 选择 stringsdict/xcstrings 的 plural 类别；单元测试必须锁定 en/ru/ar/fr/tr/id 的行为。
- 模板来自 `NSLocalizedString`（App bundle 的当前语言）；`locale` 必须与 App 语言同族，且 **`locale` 参数只控制数字/复数规则与数字体系，不改变模板语言**。生产默认 `AppLocale.current`；测试若要在进程内断言其他语言，必须使用 locale-specific bundle 或 `String(localized:locale:)` 解析模板，不能只给 `format` 传一个不同 locale。
- 多参数复数（如 `history_item_meta` `%1$@, %2$@, %3$lld`）使用 `L10n.format`；Foundation 用模板中的 numeric 变量选择 plural 类别，调用方必须按模板顺序传参。
- 非格式字符串用 `t`；格式字符串必须走 `format`/`plural`，不要 `t` 后直接 `String(format:)`。
- 对 data key/技术标识，**禁止**调用 `L10n.format`；使用 `PosixFormat`（§3.3）。

- **进程内 15 locale 测试**：`L10n` 增加 DEBUG-only `resourceBundle` 测试缝（默认 `.main`；测试用 `Bundle.main.path(forResource: code, ofType: "lproj")` + `Bundle(path:)` 指向目标语言，并给被测函数传同一 locale，`tearDown` 恢复 `.main`）。否则 `NSLocalizedString` 始终取 App 当前语言，给 `format` 传 ru/ar 只会改变数字/复数规则，模板仍是 App 语言。生产路径不设置该缝；如 Swift 6 并发检查报错，用 `NSLock` 保护该静态值或把测试标记为串行。

### 3.3 显示格式化 vs 数据 key

明确两类 API：

| 类型 | 用途 | Locale | 示例 |
|---|---|---|---|
| 显示格式化 | 用户可见日期/时间/数字/复数 | `AppLocale.current` | `Aug 21`, `21 авг.`, `٢١ أغسطس` |
| 数据 key / 技术标识 | API 参数、数据库 key、URL、UUID、版本号、日志 | `en_US_POSIX` / Latin | `2026-08-21`, `2026-W34`, `1.0.0 (1)` |

- `MylifeDateLabels.dateKey`、`isoWeekKey`、`localDateKey`、`MylifeModels`/`TimelineHomeViewModel` 日期范围、`HistoryService` 日期 key 统一使用 `en_US_POSIX`。
- `MylifeDateLabels.timeLabel` 是显示，改为 `AppLocale.current`；不要再用 `en_US_POSIX`。
- 技术字段（服务器地址/IP/域名/端口、版本号、UUID、协议 code）在 RTL 下保持 LTR；必要时对 TextField/Text 设置 `.environment(\.layoutDirection, .leftToRight)` 或使用独立 LTR 容器。
- `String(format:"%04d", locale: en)` 在 Foundation 下可能产生 `2,026`；**data key 不允许传 App locale**，必须传 `en_US_POSIX`。

### 3.4 日期/星期/周/周期

#### 3.4.1 实现策略

不直接依赖 `DateFormatter` 的默认 `dateFormat`（平台顺序与产品要求不一致），采用：

1. **月名/星期名**：`DateFormatter` + `AppLocale.current` + Gregorian calendar，按语言选择 `monthSymbols` / `standaloneMonthSymbols` / `shortMonthSymbols`，以及 `weekdaySymbols` / `shortWeekdaySymbols`。
2. **数字**：`NumberFormatter`（`locale = AppLocale.current`，`usesGroupingSeparator = false`）或 `DateFormatter` 的数字 pattern；ar 用 `ar_SA` + Gregorian。
3. **结构/顺序/后缀**：`Localizable.xcstrings` 中的格式模板，由翻译决定。
4. **缓存**：`DateFormatter`/`NumberFormatter` 不是线程安全的；`MylifeDateLabels` 可能在列表/纯函数中调用。使用带 `NSLock` 的 formatter cache（按 locale/timezone/pattern），或在 MainActor 上使用值类型 `Date.FormatStyle`；不得共享可变静态 formatter。
5. **24 小时制**：所有时间固定 `HH:mm` / `HH:mm:ss`，不随 locale 切 12 小时制（与 Android 一致）。

#### 3.4.2 新增/复用格式 key

建议新增或改造以下 key（参数均使用位置参数）：

| key | 参数 | 用途 |
|---|---|---|
| `mylife_year_format` | `%1$@` = 已格式化年份 | 年；zh/ja/ko 加后缀 |
| `mylife_month_year_format` | `%1$@` = 月（名或数字），`%2$@` = 年 | 月年 |
| `mylife_overview_month`（复用） | `%1$@` = 年，`%2$@` = 月 | 月段标题 |
| `mylife_month_day_format` | `%1$@` = 月，`%2$@` = 日 | 日卡/搜索结果 |
| `mylife_week_range_format` | `%1$@`、`%2$@` = 两个日期 | 周范围 |
| `mylife_week_label`（复用） | `%1$@` = 周数 | 周徽标 |
| `mylife_period_summary`（新增 plural） | `%1$@` = rangeLabel，`%2$lld` = 天数 | 无日摘要时的兜底 |
| `common_page_indicator` | `%1$@ / %2$@` = 两个已预格式化数字 | 图片页码 |

- 现有 `mylife_overview_month` 的 `%1$lld`（年）改为 `%1$@`，`%2$@` 仍是月份；`mylife_week_label` 的 `%lld` 改为 `%1$@`。两者都由代码用 `NumberFormatter` 预格式化数字，避免年份/周数被分组或使用错误数字体系。
- **`mylife_overview_month` 的 zh/ja/ko 值必须补上 `月/월`**（例如 zh `%1$@年 · %2$@月`、ko `%1$@년 · %2$@월`），因为代码传给 `%2$@` 的是数字月份，不是「8月」字符串；en/es/pt/ru/de/fr/it/tr/hi/id/ar 仍传月名。现有该 key 未被引用，可以安全改值。
- zh/ja/ko 的月/日使用数字；其余语言使用月名（见 §4）。所有 zh/ja/ko 的**年/月/日类**模板（`mylife_year_format`、`mylife_month_year_format`、`mylife_month_day_format`、`mylife_overview_month`）由资源包含 `年/月/日`（ko 为 `년/월/일`）后缀，代码只传数字/月名，不得在 Swift 里再拼这些后缀；周范围/周徽标不含年/月/日后缀。
- `mylife_month_year_format`、`mylife_month_day_format`、`mylife_week_range_format`、`mylife_period_summary` 需要新增 17 locale 翻译（th/vi 见 §2.9）。

**结构模板骨架（`%1$@`/`%2$@` 均为已格式化字符串）：**

| key | 语言 | 模板 |
|---|---|---|
| `mylife_year_format` | en、ru、de、fr、it、es、pt、tr、hi、id、th、vi、ar | `%1$@` |
| | zh-Hans、zh-Hant、ja | `%1$@年` |
| | ko | `%1$@년` |
| `mylife_month_year_format` | en、ru、de、fr、it、tr、hi、id、th、ar | `%1$@ %2$@` |
| | vi | `%1$@ năm %2$@` |
| | es、pt | `%1$@ de %2$@` |
| | zh-Hans、zh-Hant、ja | `%2$@年%1$@月` |
| | ko | `%2$@년 %1$@월` |
| `mylife_month_day_format` | en | `%1$@ %2$@` |
| | de | `%2$@. %1$@` |
| | ru、fr、it、es、pt、tr、hi、id、th、vi、ar | `%2$@ %1$@` |
| | zh-Hans、zh-Hant、ja | `%1$@月%2$@日` |
| | ko | `%1$@월 %2$@일` |
| `mylife_week_range_format` | 全部 | `%1$@ – %2$@` |
| `mylife_overview_month` | en、ru、de、fr、it、tr、hi、id、th、ar | `%2$@ %1$@` |
| | vi | `%2$@ năm %1$@` |
| | es、pt | `%2$@ de %1$@` |
| | zh-Hans、zh-Hant、ja | `%1$@年 · %2$@月` |
| | ko | `%1$@년 · %2$@월` |
| `mylife_week_label` | en | `W%1$@` |
| | zh-Hans | `%1$@周` |
| | zh-Hant | `%1$@週` |
| | ja | `第%1$@週` |
| | ko | `%1$@주차` |
| | ru | `Неделя %1$@` |
| | de | `KW %1$@` |
| | fr、it、es、pt | `S%1$@` |
| | tr | `%1$@. Hafta` |
| | hi | `सप्ताह %1$@` |
| | id | `Pekan %1$@` |
| | th | `สัปดาห์ที่ %1$@` |
| | vi | `Tuần %1$@` |
| | ar | `الأسبوع %1$@` |

> `mylife_month_year_format` 的参数顺序是「月、年」；`mylife_overview_month` 是「年、月」；`mylife_month_day_format` 是「月、日」。实现时不要按列名猜顺序。`mylife_overview_month` 的目标输出示例：en `Aug 2026`、zh-Hans/zh-Hant/ja `2026年 · 8月`、ko `2026년 · 8월`、ru `август 2026`、es/pt `agosto de 2026`、th `สิงหาคม 2026`、vi `Tháng 8 năm 2026`、ar `أغسطس ٢٠٢٦`。

#### 3.4.3 语言样式表

`MylifeDateLabels` 增加一个 `MylifeDateStyle` 表（仅决定结构，不存翻译）：

| locale | monthYear 月样式 | monthHeader 月样式 | monthDay 月样式 | 星期 | 周范围 pattern |
|---|---|---|---|---|---|
| en | short | short | short | short | `M/d` |
| zh-Hans | numeric | numeric | numeric | short | `M/d` |
| zh-Hant | numeric | numeric | numeric | short | `M/d` |
| ja | numeric | numeric | numeric | full | `M/d` |
| ko | numeric | numeric | numeric | full | `M/d` |
| ru | fullStandalone | fullStandalone | short | short，小写 | `dd.MM` |
| de | full | full | short | short | `dd.MM.` |
| fr | full | full | short | short | `dd/MM` |
| it | full | full | short | full | `dd/MM` |
| es | full | full | short | full | `dd/MM` |
| pt | full | full | short | short | `dd/MM` |
| tr | full | full | short | short | `dd.MM` |
| hi | full | full | short | short | `dd/MM` |
| id | full | full | short | short | `dd/MM` |
| th | full | full | short | short | `dd/MM` |
| vi | fullStandalone | fullStandalone | short | full | `dd/MM` |
| ar | full | full | short | full | `dd/MM` |

- `fullStandalone` 用 `standaloneMonthSymbols`（ru 主格 `август`，避免 `августа`）。
- ar 用 `ar_SA` + Gregorian；`weekday` 用 full 以得到 `الجمعة`（short 可能缺 `ال`）。
- ru 的 short weekday 需要 `lowercased(with: locale)`，从 `Пт` → `пт`。
- 所有范围/日期数字用 AppLocale formatter；ar 为阿数。

#### 3.4.4 月段/日期样本

以 2026-08-21（ISO 2026-W34）为例，产品目标输出：

| locale | 年 | 月年 | 月日 | 星期 | 周范围 | 周徽标 |
|---|---|---|---|---|---|---|
| en | 2026 | Aug 2026 | Aug 21 | Fri | 8/17 – 8/23 | W34 |
| zh-Hans | 2026年 | 2026年8月 | 8月21日 | 周五 | 8/17 – 8/23 | 34周 |
| zh-Hant | 2026年 | 2026年8月 | 8月21日 | 週五 | 8/17 – 8/23 | 34週 |
| ja | 2026年 | 2026年8月 | 8月21日 | 金曜日 | 8/17 – 8/23 | 第34週 |
| ko | 2026년 | 2026년 8월 | 8월 21일 | 금요일 | 8/17 – 8/23 | 34주차 |
| ru | 2026 | август 2026 | 21 авг. | пт | 17.08 – 23.08 | Неделя 34 |
| de | 2026 | August 2026 | 21. Aug. | Fr. | 17.08. – 23.08. | KW 34 |
| fr | 2026 | août 2026 | 21 août | ven. | 17/08 – 23/08 | S34 |
| it | 2026 | agosto 2026 | 21 ago | venerdì | 17/08 – 23/08 | S34 |
| es | 2026 | agosto de 2026 | 21 ago | viernes | 17/08 – 23/08 | S34 |
| pt | 2026 | agosto de 2026 | 21 ago. | sex. | 17/08 – 23/08 | S34 |
| tr | 2026 | Ağustos 2026 | 21 Ağu | Cum | 17.08 – 23.08 | 34. Hafta |
| hi | 2026 | अगस्त 2026 | 21 अग॰ | शुक्र | 17/08 – 23/08 | सप्ताह 34 |
| id | 2026 | Agustus 2026 | 21 Agu | Jum | 17/08 – 23/08 | Pekan 34 |
| th | 2026 | สิงหาคม 2026 | 21 ส.ค. | ศ. | 17/08 – 23/08 | สัปดาห์ที่ 34 |
| vi | 2026 | Tháng 8 năm 2026 | 21 thg 8 | Thứ Sáu | 17/08 – 23/08 | Tuần 34 |
| ar | ٢٠٢٦ | أغسطس ٢٠٢٦ | ٢١ أغسطس | الجمعة | ١٧/٠٨ – ٢٣/٠٨ | الأسبوع ٣٤ |

> 注：表中「月年」指 `monthYearText`（`mylife_month_year_format`），不含 MonthHeader 的 ` · `；MonthHeader 使用 `mylife_overview_month`，zh-Hans/zh-Hant/ja 为 `2026年 · 8月`，ko 为 `2026년 · 8월`（见 §3.4.2）。
>
> 注：示例日期 2026-08-21 的 ISO 周为 **34**；Android/iOS 文档示例已统一为 W34（iOS 既有测试断言 W34）。`docs/android_multilingual.md` 的对应示例已同步修正。

#### 3.4.5 摘要兜底

`MylifeAggregator` 无日摘要时，不再写 `zh`/`en` 分支，改为：

```swift
return [L10n.format(
    "mylife_period_summary",
    locale: locale,
    rangeLabel, n
)]
```

`mylife_period_summary` 是 **多参数 plural**：`%1$@` = rangeLabel，`%2$lld` = 天数。Xcode 会把该 key 编译为 `%2$#@value@` 的 positional plural 格式；`String(format:locale:arguments:)` 按第 2 个数值参数选择类别并输出阿数。多参数 plural 必须由 catalog 编译器生成，不能手写 `%#@…@`（本机手写格式在额外参数下会崩溃/错配；编译产物结构见附录 A）。模板示例：

- en: `%1$@ · %2$lld day` / `days`
- zh-Hans: `%1$@共 %2$lld 天`
- ja: `%1$@・計 %2$lld 日`
- ru: `%1$@ · всего %2$lld день/дня/дней`
- th: `%1$@ · %2$lld วัน`（only `other`）
- vi: `%1$@ · %2$lld ngày`（only `other`）
- ar: `%1$@ · إجمالي %2$lld يوم/يومان/أيام/يومًا/…`

plural 类别按 §4.1 的 CLDR 表提供；count < 1000，避免分组问题。`String(format:locale:)` 用 `%2$lld` 选择类别并输出阿数。

### 3.5 数字、阿拉伯数字与计数

- 统一入口 `LocalizedNumber`（建议新增 `Core/UI/LocalizedNumber.swift`）：
  ```swift
  static func integer(_ value: Int, locale: Locale, minimumDigits: Int = 1) -> String
  ```
  使用 `NumberFormatter`，`usesGroupingSeparator = false`，`minimumIntegerDigits`，`maximumFractionDigits = 0`；ar 用 `ar_SA`。
- 日期中的年/月/日/周数使用 `LocalizedNumber` 或 DateFormatter（Gregorian）；**不要**用 `String(format: "%lld", locale: appLocale)` 生成年份（会分组为 `2٬026`）。
- 计数/页码/徽标使用 `LocalizedNumber`：`AgentJobFormatting.badgeCount`、`PeriodCard` imageTotal、`AudioEntryButton` count、`GalleryViewerOverlay`/`ImageViewerOverlay` page。
- 时间码 `M:SS` 使用 `LocalizedNumber` 分别格式化分钟/秒（秒最小 2 位），拼接 `:`；ar 输出 `٠:٠٥`。
- `AgentJobFormatting.startedTime`/`elapsed` 保持 NumberFormatter 方案，默认 locale 改 `AppLocale.current`；单位 h/m/s 保持 Latin（与 Android 一致）。
- 小数/分组：UI 计数默认不分组；`conv_job_remaining_chars` 等大数可按翻译/产品要求使用分组，但必须由 formatter 控制，不得依赖 `String(format:locale:)` 的隐式行为。
- 复合 a11y 标签（`AgentJobListPanel` 行、`HistoryComponents` 行、`SearchResultsView` 命中、`AvatarView` 头像）用 `ListFormatter`（`locale = AppLocale.current`，过滤空段）或资源模板连接，不拼 `", "`；`PeriodCard` 图片 badge 与 `AudioEntryButton` 可见计数用 `LocalizedNumber`。
- `L10n.plural`/多参数 plural 中的 `%lld` 由 Foundation 按 locale 格式化，可能带分组；页码/徽标等必须无分组的站点使用 `LocalizedNumber`，不要把 `%lld` 直接嵌入这些文本。复数句子中的计数可按产品接受 locale 分组（Android 的 `%d` 通常不分组，需要逐站点确认）。

### 3.6 复数

- 所有会随数量变化的文案使用 xcstrings `variations.plural`，禁止手写 `n == 1 ? ... : ...`。
- 补齐 `conv_job_remaining_chars` 的 plural 变化，替换当前非 plural 的 `%d` 单值。
- Android 的 11 组 plural key 语义全部对齐：iOS 现有 10 组 + 补 `conv_job_remaining_chars` = 11 组；另新增 iOS-only `mylife_period_summary`，总计 **12 组** plural。
- 按 locale 提供 CLDR 类别（§4.1）：zh/ja/ko/id 仅 `other`；en/de/fr/it/es/pt/hi `one/other`；ru `one/few/many/other`；ar 六形。
- 用 `L10n.plural`/`L10n.format` + `String(format:locale:)`，按 App locale 选择类别；以单测锁定 ru 1/2/5/11/21、ar 0/1/2/3/11/100、fr 0/1/2、id 恒定。

### 3.7 模式后缀与历史

- 保持 D4=B：数据库只存正文 + `mode`；展示时按当前 App locale 拼 `conv_msg_suffix_*`。
- 会话实时气泡、History 详情、MyLife 时光机共用同一组后缀 key（当前 `MessageModeSuffix` / `HotSlotScreen` 已如此）。
- 迁移前 `mode=nil` 的历史行，后缀已写入正文；不回拆、不改写。
- 16 locale 后缀 key 必须齐平；`MessageModeSuffix.appended` 的空格需按语言验证（CJK/ar bidi）。

### 3.8 错误、banner 与不翻译边界

**App 生成错误**（相机/采集/设置/头像）建议引入可本地化错误描述：

```swift
struct AppErrorMessage {
    let key: String
    let arguments: [CVarArg]
}
```

- `CameraCaptureError`、`AudioCaptureController.CaptureError` 保留英文 `errorDescription` 供日志/诊断；新增 `userMessage(locale:)` 返回 `L10n.format(key, ...)`。
- `ConversationBannerMessage.captureFailed/cameraStartFailed` 携带 `AppErrorMessage`/key，而不是已拼好的英文 String；`L10n.bannerTitle` 用目标语言拼接。
- `SettingsViewModel.saveError` 不直接把 `error.localizedDescription` 拼进用户句子；显示本地化通用错误，技术 detail 写日志或放入独立的 detail 区。
- `AvatarCustomizationModel` 当前把 `error.localizedDescription` 作为 `conv_avatar_save_failed` 的 `%@`；同样改为无占位符通用文案，技术 detail 写日志/独立 detail 区。`ConversationCoordinator` 的 `conv_audio_source_failed` 也不再用 `.server(..., detail: sourceError.localizedDescription)` 把 App 本地错误 detail 拼进用户句子。
- 所有 UI 组合串（`MessageBubbleView` 的 agent header + 时间、speaker + 正文，`L10n.bannerTitle` 的 title + detail）不要用 Swift 插值硬编码 `": "`；使用 `common_title_with_detail` 或专用格式 key，由翻译决定冒号/空格/顺序。Settings 错误改用通用文案，不套 detail。
- 服务端 `SDK error message` 属于内容边界，保持原样；标题本地化。若 detail 与标题语言不同，明确这是服务端内容，不做自动翻译。
- 不翻译清单（与 Android D8 保持一致的部分）：服务端内容、协议 code、日志、Mock/SDK、`@Preview`、版本号/IP/域名/端口。App→AI 相册指令是已本地化的例外，见下一条；Android 已同步使用 `conv_image_action_text`。
- `GalleryPhotoEncoder.actionText` 已改为 `L10n.t("conv_gallery_action_text")`，17 locale 本地化并随 App 语言选择；英文为 source language 兜底。Android 已同步使用 `conv_image_action_text`（屏幕抓取/图库共用，17 locale；2026-10-27，见 `docs/android_multilingual.md` §2.3.9/§21）。

### 3.9 语言切换与运行时刷新

- 不新增 App 内语言选择器；支持系统语言与 iOS Settings 的 per-app language。
- `CFBundleLocalizations` + 17 个 `.lproj`/xcstrings localization 让系统显示语言选项。
- per-app language 变更通常由系统重启 App；若系统不重启：
  - 在 `AppRootView` 使用 `.environment(\.locale, AppLocale.current)`（并视需要显式设置 `.environment(\.layoutDirection, ...)`）。
  - 监听 `NSLocale.currentLocaleDidChangeNotification`/前台回到 active：清 formatter cache、刷新 root；若 `Bundle` 仍未更新，按系统行为要求重启。
- 不在运行时手动替换 `AppleLanguages`；不实现语言切换动画。
- `AppLocale.current` 每次从 bundle 读取；formatter cache 以 locale 为 key，语言变化自然失效。
- `session.start.config.language` 使用 `AppLocale.resourceCode`（`zh-Hans`/`zh-Hant` 等），由 `LivaChatConfig.languageProvider` 在每次 `session.start` 现取；与 translate 的 `my_language` 解耦，详见 `docs/session_start_improvments.md`。

### 3.10 设备语言与「对方语言」

- `ConversationModeModel.deviceLanguage` 当前为 `Locale.current.languageCode ?? "en"`（`ConversationModeModel.swift:56`）。设计保持“设备/说话者语言”和 App UI 语言解耦：
  - 默认继续使用 `Locale.current.languageCode`（系统 locale），不改为 `AppLocale.current`；per-app language 下该值可能不等于系统语言，风险与 Android `Locale.getDefault()` 相同，必须写入已知限制。
  - 如产品要求严格系统语言，改用 `Locale.preferredLanguages.first` 的主语言并增加测试；不在此设计中静默改变协议语义。
- 当前候选列表为 `auto, zh, en, ja, ko, fr, it, de, es, pt, ru, tr, hi, id, ar`；已加入 `it`（意大利语）、`tr`（土耳其语）、`hi`（印地语）、`id`（印尼语），移除 `th`（泰语）。Android 已与 iOS 对齐（2026-10-27，见 `docs/android_multilingual.md` §2.3.4）。
- **2026-10-27 已实施**：加入 `th`、`vi`，候选 `auto, zh, en, ja, ko, fr, it, de, es, pt, ru, tr, hi, id, th, vi, ar`（`auto` + 16 个具体语言）；详见 §2.9。`th` 为重新加入，`vi` 为新增；Android 已于同日实施同一候选（versionCode 138）。
- 语言显示名用 `AppLocale.displayLocale.localizedString(forLanguageCode:)`，随 App UI 语言本地化。

### 3.11 RTL 设计

- 继续使用 SwiftUI 语义化 `.leading/.trailing`、`HStack` 顺序和 `.move(edge:)`；不要新增硬编码 x 坐标。
- 方向性符号改为语义名：
  - `chevron.left` → `chevron.backward`；`arrow.left` → `arrow.backward`；或使用 `.flipsForRightToLeftLayoutDirection(true)`。
  - 覆盖 9 处（§2.6）；逐个确认图标语义（返回、上一页、展开）。
- `HistoryComponents` 滑动删除：
  - 根据 `@Environment(\.layoutDirection)` 计算手势方向与 offset；trailing 在 RTL 为左侧，允许向 trailing 方向滑动并 reveal。
  - 不能只反转 offset，需同时反转触发阈值。
- `ImageViewerOverlay` 横向翻页：按 `layoutDirection` 反转手势阈值、`pageDrag` 与 settle 方向；`GalleryViewerOverlay` 的系统 `TabView(.page)` 需截图确认镜像行为。
- `MylifeFlowLayout`：读取 `layoutDirection`，RTL 时按 `width - x - itemWidth` 镜像每行起点；行内顺序仍按数据顺序从右到左；宽度变化/方向变化重算。
- 固定 offset（`ConversationView.swift:408`、`PeriodCard.swift:154`）：改为对齐/overlay + 方向感知 offset；装饰性角标如保持 LTR 需截图确认。
- 技术文本（服务器地址、IP、版本号）保持 LTR：必要时 `.environment(\.layoutDirection, .leftToRight)` 或 LTR 容器。页码/时间仍按 App locale 输出数字（ar 为阿数），但整体排列保持 LTR，避免 bidi 把 `n / N`、`HH:mm`、音频 `position / duration` 重排。
- Markdown 表格/代码块、WKWebView 内容：内容方向由 Markdown/HTTP 决定；App 只保证 chrome 与标签镜像，文档化限制。
- 验收：ar 全屏截图 + VoiceOver 顺序 + 滑动删除 + 页码/时间/日期 bidi。

### 3.12 布局与文本膨胀

- 系统字体覆盖全部脚本；保留 `@ScaledMetric` 与 Dynamic Type。
- 固定 `lineSpacing` 对 hi/ar 可能偏紧；在真机/大字号下检查，必要时按 script 调整或移除固定 lineSpacing。
- 德语/俄语优先：设置页、权限页、工具栏 caption、Agent job 面板、GranularitySelector、chip、WebView 错误页、通知式长文案。
- 不对文本使用固定宽度；现有固定宽度基本是图标/按钮（44/48/64/200），需要检查 200pt 的 WebView 错误按钮、64pt 工具栏 caption、四格 Granularity。
- `lineLimit` 站点（历史行、Agent job、PeriodCard、WebView 标题、Audio 段）：17 locale + 最大 Dynamic Type 下不得截断关键语义；必要时放宽行数或使用 `minimumScaleFactor`。
- 日语/韩语/中文与 en 混排的行高、标点；印地语连字/依附符；阿拉伯语 shaping；全部加入截图矩阵。

---

## 4. 16 locale 格式规范

### 4.1 locale / 复数 / 样式表

| locale | iOS code | plural 类别 | monthYear 月 | monthHeader 月 | monthDay 月 | weekday | 周范围 |
|---|---|---|---|---|---|---|---|
| en | en | one/other | short | short | short | short | M/d |
| zh-Hans | zh-Hans | other | numeric | numeric | numeric | short | M/d |
| zh-Hant | zh-Hant | other | numeric | numeric | numeric | short | M/d |
| ja | ja | other | numeric | numeric | numeric | full | M/d |
| ko | ko | other | numeric | numeric | numeric | full | M/d |
| ru | ru | one/few/many/other | fullStandalone | fullStandalone | short | short（小写） | dd.MM |
| de | de | one/other | full | full | short | short | dd.MM. |
| fr | fr | one/other | full | full | short | short | dd/MM |
| it | it | one/other | full | full | short | full | dd/MM |
| es | es | one/other | full | full | short | full | dd/MM |
| pt | pt | one/other | full | full | short | short | dd/MM |
| tr | tr | other | full | full | short | short | dd.MM |
| hi | hi | one/other | full | full | short | short | dd/MM |
| id | id | other | full | full | short | short | dd/MM |
| th | th | other | full | full | short | short | dd/MM |
| vi | vi | other | fullStandalone | fullStandalone | short | full | dd/MM |
| ar | ar | zero/one/two/few/many/other | full | full | short | full | dd/MM |

- 月名/星期名由 `DateFormatter` 的 `monthSymbols`/`standaloneMonthSymbols`/`shortMonthSymbols` 与 `weekdaySymbols`/`shortWeekdaySymbols` 提供，locale 用 AppLocale，calendar 用 Gregorian。
- 结构/后缀由 §3.4.2 的模板提供；`%1$@`/`%2$@` 都是已格式化字符串，`mylife_period_summary` 的 count 是 `%2$lld`。
- `en` 的 monthYear 用 short（`Aug 2026`），与 Android 表和 iOS 现有测试一致。
- `es`/`pt` 的 monthYear 使用 `de`；`ru` 使用 standalone 主格。
- `de` 的 monthDay 模板保留日尾点（`21. Aug.`）；`ru` 的 short 月自带尾点。
- `ar` 的 monthYear/monthHeader/monthDay 数字全部 `ar_SA` 阿数；`DateFormatter.calendar` 必须 Gregorian。
- `fr` 的 UI 文案保留 NBSP/« » 规则；日期模板中的空格按翻译资源。

### 4.2 时间/计数

| 站点 | 目标 | 备注 |
|---|---|---|
| MyLife 行时间、HotSlot、AudioGallery、Search | `HH:mm`，App locale 数字 | ar 阿数；24h |
| Agent job started | `HH:mm:ss`，App locale 数字 | 单位/分隔符不变 |
| Agent job elapsed | `Xh Ym` / `Xm Ys` / `Xs`，数字本地化 | h/m/s 保持 Latin（Android 口径） |
| Agent job result/Message agent header | `HH:mm`，App locale 数字 | 移除静态 `Locale.current` |
| Audio segment timecode | `M:SS`，数字本地化 | ar `٠:٠٥`；活动态 `position / duration` 整串保持 LTR |
| 图片页码 | `n / N`，数字本地化 | RTL bidi 需截图 |
| Agent badge | `99+`，数字本地化 | 99+ 阈值不变；RTL bidi 需截图 |
| History meta | `date · weekday · N messages` | date LONG + weekday FULL；plural count |
| Period summary | `rangeLabel · N days` | `mylife_period_summary` plural |

- 不切换 12 小时制/AM-PM。
- 计数默认不分组；`conv_job_remaining_chars` 等大数可按产品决定分组，但由 formatter 控制。
- 负数/0 边界：`elapsed` 负值按 0；`AudioSegmentCard` 负值按 0；plural 0 类别按 CLDR（fr 0 单数、it/es/pt 0 复数、ar zero）。

### 4.3 术语口径

直接沿用 `docs/android_multilingual.md` §8–§21 的术语表（th/vi 见 §21.9/§21.10），不在 iOS 另造译名：

- Time Machine / 时光机 / 時光機 / タイムマシン / 타임머신 / Машина времени / Zeitmaschine / Machine à remonter le temps / Macchina del tempo / Máquina del tiempo / Máquina do tempo / Zaman Makinesi / टाइम मशीन / Mesin Waktu / آلة الزمن。
- Omni / 全模态 / 全模態 / オムニ / 옴니 / Омни / Omni / Omni / Omni / Omni / Omni / Omni / ओमनी / Omni / أومني。
- Transcript / 转录 / 轉錄 / 文字起こし / 전사 / расшифровка / Transkript / transcription / trascrizione / transcripción / transcrição / transkript / प्रतिलेख / transkrip / نسخ。
- Live translation / 同声翻译 / 即時翻譯 / リアルタイム翻訳 / 실시간 번역 / синхронный перевод / Live-Übersetzung / Traduction en direct / Traduzione in tempo reale / Traducción en tiempo real / Tradução em tempo real / Gerçek zamanlı çeviri / लाइव अनुवाद / Terjemahan langsung / الترجمة الفورية。
- 设备「扬声器」与人物「演讲者」在 de/ru/ja 等语言中分开译；见 Android 表。
- 品牌 `LivaChat` 保持 Latin；`Text(verbatim:)` 不改。
- 繁體采用台灣正體＋台灣慣用詞（週、設定、伺服器、麥克風、鏡頭、對話紀錄）。
- th：`เครื่องย้อนเวลา`（时光机）、`ถอดเสียง`（转录）、`แปลสด`（同声翻译）、`คู่สนทนา`（对方）；完整表见 Android §21.9。
- vi：`Cỗ máy thời gian`（时光机）、`phiên âm`（转录）、`Dịch trực tiếp`（同声翻译）、`người đối thoại`（对方）；完整表见 Android §21.10。
- 语域/文体沿用 Android：ja です・ます；ko 해요체；ru 中性礼貌；de 中性/必要 Sie；fr vouvoiement；it/es 中性/必要时 tu/tú；tr/id 祈使裸形；hi 中性礼貌；ar MSA 阳性单数祈使；th 中性礼貌（避免ครับ/ค่ะ 指代用户）；vi 中性/礼貌、无代词优先。

---

## 5. 资源迁移与 key 规划

### 5.1 Android ↔ iOS key 对齐

| 类别 | 数量 | 处理 |
|---|---:|---|
| 同名共享 key | 184 | 直接复用 Android 16 locale 翻译（th/vi 已落地）；按 iOS 占位符规则转换后 review |
| iOS-authored key | 51 | 从英文翻译；含搜索 kind、`mylife_period_summary`、通用错误、工具栏短 caption、相册指令 `conv_gallery_action_text` 等 |
| Android-only key | 70 | 按下表决策，不盲目导入；2026-10-27 新增 `conv_image_action_text` |
| iOS 未引用 key | 0 | `check_ios_localization.py` 守卫 |

Android-only key 决策：

| Android key/类别 | iOS 现状 | 决策 |
|---|---|---|
| `device_*`、`conv_select_mic/speaker_device`、`conv_system_default` | 无设备选择器 | 不导入 |
| `notif_*` | 无本地通知 | 不导入 |
| `cap_*`、`capture_*`、`conv_screen_capture_*`、`conv_overlay_permission_required` | 无屏幕抓取 | 不导入 |
| `settings_server_public/lan`、`settings_select_server`、`settings_server_updated`、`settings_restart_to_apply` | iOS 服务器地址输入 + Apply | 不导入；用 iOS `settings_*` |
| `settings_privacy_policy/terms_of_service/exit/got_it` | iOS 设置页无对应区块 | 不导入；如后续加功能再补 |
| `conv_welcome_chip_daily/speaking/video/voice` | iOS 无对应 chip | 不导入；`conv_welcome_chip_translate` 已共享 |
| `mylife_api_empty_response/json_parse_failed` | iOS 使用通用 load failed | 不导入；技术错误保持日志 |
| `common_go_to_settings` | iOS 使用 `perm_go_to_settings` | 映射复用 |
| `common_save_failed_storage` | iOS `settings_save_failed` | 映射/新增通用错误 |
| `conv_conn_timeout`、`conv_continue` | iOS 使用 `conv_connection_failed/closed` | 映射/按需新增 |
| `conv_permission_denied_fmt`、`perm_screen_camera_title` | iOS 使用 `perm_*`/capture errors | 映射/按需新增 |
| `mylife_*` 其余 | 同名共享或 iOS 新 key | 逐一映射 |

iOS-authored key（51）需要翻译；重点：

- `conv_avatar_*`（头像相机/裁剪/移动/缩放提示）
- `settings_apply*`、`settings_tls_*`、`settings_error_*`
- `conv_service_error/warning`、`conv_connection_closed`、`conv_capture_failed`、`conv_camera_start_failed`
- `conv_audio_source_failed`、`conv_audio_unavailable`
- `conv_agent_header`、`conv_speaker_ai/user`、`conv_status_speaking`
- `mylife_hot_slot_desc`、`perm_granted`
- `common_coming_soon`（未引用，删除或补用）

同名 key 的英文占位符只有 2 处需要人工处理：`conv_job_remaining_chars`（Android 为 plural `%1$s`，iOS 当前为单值 `%d`，需改为 plural `%lld`）与 `mylife_photos_deleted_partial`（`%1$d/%2$d` → `%1$lld/%2$lld`）。其余 185 个共享 key 的占位符数量/位置/类型一致（按 §5.3 规则转换）。

### 5.2 新增/修正 key（建议）

| key | 类型 | 参数 | 说明 |
|---|---|---|---|
| `mylife_year_format` | string | `%1$@` | 年格式；zh/ja/ko 加后缀 |
| `mylife_month_year_format` | string | `%1$@` 月、`%2$@` 年 | 月年 |
| `mylife_month_day_format` | string | `%1$@` 月、`%2$@` 日 | 月日 |
| `mylife_week_range_format` | string | `%1$@`、`%2$@` | 周范围 |
| `mylife_period_summary` | **plural** | `%1$@` range、`%2$lld` days | 周期摘要兜底 |
| `common_page_indicator` | string | `%1$@ / %2$@` | 图片页码（`LocalizedNumber` 预格式化） |
| `mylife_search_kind_chat` | string | — | 搜索结果 a11y |
| `mylife_search_kind_transcribe` | string | — | 同上 |
| `mylife_search_kind_translate` | string | — | 同上 |
| `mylife_search_kind_agent_job` | string | — | 同上 |
| `mylife_search_kind_photo` | string | — | 同上 |
| `mylife_search_kind_event` | string | — | 同上 |
| `conv_capture_permission_denied` | string | — | 采集错误 |
| `conv_capture_invalid_input` | string | — | 采集错误 |
| `conv_capture_converter_unavailable` | string | — | 采集错误 |
| `conv_capture_engine_failed` | string | — | 采集引擎启动失败（detail 只写日志） |
| `conv_capture_unavailable` | string | — | 重试耗尽 |
| `conv_camera_permission_denied` | string | — | 相机错误 |
| `conv_camera_input_unavailable` | string | — | 相机错误 |
| `conv_camera_configuration_failed` | string | — | 相机配置失败（detail 只写日志） |
| `settings_save_failed_message` | string | — | 设置保存通用错误 |
| `common_title_with_detail` | string | `%1$@` title、`%2$@` detail | banner/agent header/speaker 等组合串（RTL 标点） |
| `conv_toolbar_camera_short` | string | — | 工具栏/相册行相机名词（与 mic/speaker 短 caption 对齐） |

- 修正 `conv_job_remaining_chars`：改为 plural，`%lld`（或 `%1$lld`），补齐 17 locale 的 one/other/few/many 等类别；`MessageBubbleView`/`TimelineBubbleView` 统一走 `L10n.plural`/`L10n.format`。
- 复用 `mylife_overview_month`（月段标题）与 `mylife_week_label`（周徽标），把 `mylife_overview_month` 的年份 `%1$lld` 与 `mylife_week_label` 的 `%lld` 改成 `%@`，由 `LocalizedNumber` 预格式化；`mylife_overview_month` 参数顺序为「年、月」。
- `conv_avatar_save_failed` 去掉 `%@`，改为通用文案；`AvatarCustomizationModel` 不再把 `error.localizedDescription` 拼进句子。
- 删除或补用未引用 key：`app_name`、`avatar_custom_image_desc`、`common_coming_soon`、`conv_permission_lost`、`webview_unsupported_link`。

### 5.3 占位符 / 转义 / 排版规则

从 Android `strings.xml` 导入时：

| Android | iOS xcstrings |
|---|---|
| `%s` / `%1$s` | `%@` / `%1$@` |
| `%d` / `%1$d` | `%lld` / `%1$lld`（Swift `Int`） |
| `%f` / `%.1f` | 避免用于 UI 数字；用 NumberFormatter |
| `%%` | `%%` |
| `\'` | `'`（xcstrings JSON 不需要 aapt 转义） |
| `&amp;`/`&lt;`/`&gt;` | 解码为 `&`/`<`/`>` |
| `\n` | 保留转义或 JSON 换行，按文案 |

- 位置参数必须保留；不同语言可重排。
- 预格式化数字 key（`mylife_overview_month`、`mylife_week_label`、`mylife_year_format`、`mylife_month_year_format`、`mylife_month_day_format`、`mylife_week_range_format`、`common_page_indicator`）的参数是 `%@`/`%1$@`/`%2$@`，**不要**按 `%d`→`%lld` 机械转换；`mylife_period_summary` 的天数仍是 `%2$lld`，由 plural 规则选择类别。
- 不支持 `%s`/`%d` 直接进入 iOS 资源（validator 拒绝）。
- 复数 key 的所有 locale 必须有 `other`，并按 §4.1 提供完整类别。
- 法语：`?`/`!`/`:` 前、`«`/`»` 内侧使用 U+00A0；含 `port` 的技术格式串豁免；iOS 不需要 `\'`。
- 阿拉伯语：静态资源中的数字使用阿拉伯-印度数字；动态数字由 formatter 输出；技术标识（版本/IP/域名）豁免。
- 中文只在 zh-Hans/zh-Hant；en 源不得含汉字。
- 土耳其语专有名词格后缀撇号按 iOS 普通 `'`，不需要 aapt 转义。
- `pt` 使用 pt-BR 文案；`zh-Hant` 使用台灣用詞。
- **泰语（`th`）**：无词间空格，组合符/断行依赖系统 shaping；不要手动插入空格或换行；需真机验证按钮/标签在 Dynamic Type 下的断行。
- **越南语（`vi`）**：声调符号会增大行高/文本宽度；不要用 `uppercased()` 破坏声调；需验证窄按钮和 caption 截断。

### 5.4 翻译导入与 review 流程

1. 建立 Android→iOS key 映射（shared 184 + iOS-authored 51 + Android-only 70 的决策）。
2. 脚本 `scripts/i18n/import_android_to_ios.py`：
   - 解析 Android `values*/strings.xml` 与 `<plurals>`；
   - 按映射表生成 draft xcstrings entries；
   - 规范化占位符/转义/XML entity；
   - 保留 iOS-only key 的英文 draft，标记 `needs_review`；
   - 不覆盖人工已 review 的 iOS 特有文案（按 key/locale 合并）。
3. 人工 review：重点 iOS-only、布局敏感、复数、ar/ru/fr、模式后缀、错误文案；新增 `th`/`vi` 需母语 reviewer。
4. 设置 xcstrings state：`translated` 仅用于已 review；开发期允许 `needs_review`，发布门禁要求全部 `translated`。
5. 交叉检查 Android 文档 §8–§20 的术语；如 iOS 文案因长度需要改写，同步评估 Android，避免平台漂移。
6. **`th` / `vi` 的导入**：Android 已于 2026-10-27 落地并提交（versionCode 138；§21，待 review）。iOS 直接导入 183 shared key，仅把 52 个 authored 条目（51 iOS-only + forced `conv_avatar_save_failed`）译入 `authored_translations.json`；细节见 §2.9。

### 5.5 资源守卫（建议新增）

`scripts/i18n/check_ios_localization.py`（本地 + 可选 CI）检查：

1. `Localizable.xcstrings` 的 17 个 localization 与 `knownRegions`/`CFBundleLocalizations` 一致。
2. 每个 key 在 17 locale 都有非空值；发布模式不允许 `new`/`needs_review`。
3. 占位符签名（位置、类型、数量）跨 locale 一致；拒绝 `%s`/`%d`。
4. plural key 的 quantity set 符合 §4.1；必须包含 `other`。
5. en 源无汉字（排除技术/品牌 allowlist）。
6. 法语 NBSP/« » 规则；ar 字面数字规则（技术 key allowlist）。
7. `InfoPlist.strings` 17 个 locale 的 4 个 key 齐平，无占位符。
8. 未引用 key 报告（允许动态 key allowlist：`titleKey`、`validationMessageKey`、`L10n.t(condition ? ...)`）。
9. `mylife_overview_month`/`mylife_week_label`/日期格式 key/`common_page_indicator` 的参数类型与代码调用一致（尤其 `mylife_overview_month` 是「年、月」，`mylife_month_year_format` 是「月、年」）。

建议 Makefile target：`make ios-i18n-check`；如 CI 扩展 App job，则加入。

---

## 6. 实施步骤

### Phase 0：基线审计

- 冻结当前 235 key/12 plural/0 unused 的清单。
- 建立 Android→iOS key 映射表（可作为 `scripts/i18n/android_to_ios_keys.json`）。
- 保持 en/zh-Hans 现有输出不变作为回归锁。

### Phase 1：基础设施（不改语言列表）

1. 新增 `Core/UI/AppLocale.swift`；`Core/UI/LocalizedNumber.swift`；日期 formatter cache/helper。项目使用显式 PBX 文件引用（无 synchronized group），需为新增 app/test 文件补 `PBXFileReference`、`PBXBuildFile`、group child 与 Sources phase。
2. 改造 `L10n.t/format/plural`；先让 en/zh-Hans 行为不变（现有测试全绿）。
3. 新增 `AppLocaleTests`、`L10nFormattingTests`（en/zh 回归 + 显式 locale 的 ru/ar 预览）。
4. 把 data-key 的 `String(format:)` 站点统一到 `en_US_POSIX`（`MylifeDateLabels`、`MylifeModels`、`TimelineHomeViewModel`、`HistoryService`、技术 hex）。
5. 跑全量单测。

### Phase 2：17 locale 资源与工程配置

1. `Localizable.xcstrings` 增加 13 个 localization（zh-Hant、ja、ko、ru、de、fr、it、es、pt、tr、hi、id、ar）。
2. 新增 13 个 `Resources/<locale>.lproj/InfoPlist.strings`，补 4 个权限说明。
3. `project.pbxproj`：
   - `knownRegions` 补齐到 17 个语言（保留 Base）；
   - `PBXVariantGroup` 加 13 个 file ref；
   - 不新增 build file（variant group 已有）。
4. `Resources/Info.plist` 加 `CFBundleLocalizations` 17 项；`CFBundleDevelopmentRegion` 保持 en；`CFBundleDisplayName` 品牌不译。
5. 导入 Android 翻译（shared 184）并翻译 iOS-authored 51；补 Android 对齐的 11 组 plural 的 17 locale 类别。**`th`/`vi` 已按 §2.9 落地；翻译仍需母语 review。**
6. 新增 `mylife_*` 日期格式 key、`common_page_indicator`、搜索 kind keys、错误 keys；修正 `conv_job_remaining_chars`；新增 iOS-only `mylife_period_summary` plural（总计 12 组）。
7. 资源守卫脚本 + `make ios-i18n-check`。
8. 构建/启动，切换 en/zh-Hans/zh-Hant/ja/ar 抽查；确认 `Bundle.main.localizations` 与 Settings 语言列表。

### Phase 3：日期/数字/复数/计数接线

1. 重写 `MylifeDateLabels`：`AppLocale`、样式表、资源模板、formatter cache；移除 en/zh 分支；修复 Hant。
2. `MylifeAggregator` 摘要改 `mylife_period_summary`；`locale` 默认 `AppLocale.current`。
3. `HistoryDateFormatting` 注入 AppLocale/Gregorian；`history_item_meta` 用 `L10n.format`。
4. `AgentJobFormatting`/`AgentTimeLabel`/`AgentJobResultOverlay` 改 AppLocale；badge count 用 `LocalizedNumber`。
5. `AudioSegmentCard.formatTimecode` 改 `LocalizedNumber`；`SearchResultsView.kindName` 改 key。
6. 所有 `String(format: L10n...)`、裸 `Text("\(count)")`、页码替换为 `L10n.format`/`LocalizedNumber`。
7. 扩展 `MylifeDateLabelsTests`、`MylifeAggregatorTests`、`AgentJobStatusModelTests`、`AudioGalleryMathTests` 到 17 locale；新增 `HistoryDateFormattingTests`。
8. 全量单测；ar/ru/fr 重点断言。

### Phase 4：RTL、布局、错误与清理

1. 9 处方向性符号改 `.backward`/flip；History swipe、MylifeFlowLayout、固定 offset 修复。
2. 技术文本 LTR 处理；页码/时间/日期 bidi 截图。
3. 错误 key 接入 camera/capture/settings/avatar/audio source；banner detail 策略；`conv_avatar_save_failed` 去 `%@`。所有 `title: detail` / `speaker: text` 组合串改用 `common_title_with_detail` 或专用 key；复合 a11y 标签改 `ListFormatter`/模板；`PeriodCard`/`AudioEntryButton` 计数改 `LocalizedNumber`。
4. 删除/补用未引用 key（已完成，实际 0 个）；补 key 注释（可选）。
5. 文本膨胀/最大 Dynamic Type 检查；调整 lineSpacing/lineLimit/固定宽度。
6. ar RTL、de/ru、hi、ja/zh-Hant 截图矩阵；UI 测试语言 helper。

### Phase 5：门禁与设备验收

1. `xcodebuild test` 全量；`make ios-i18n-check`。
2. 模拟器 per-app language 切换 17 locale；`CFBundleLocalizations`/Settings 列表。
3. iPhone 11 真机：权限弹窗、Settings 语言、ar RTL、de/ru 文本膨胀、hi/ar 字形。
4. 录屏/截图归档；更新 `docs/ios_app_ui.md` 的本地化约束（如需要）。
5. 可选：把 App 单测/本地化检查加入 `.github/workflows/ci-mobile.yml`（当前只覆盖 SDK）。

---

## 7. 测试与验收

### 7.1 单元测试

- `LocalizationCatalogTests` / `check_ios_localization.py`：16 locale key/plural/占位符/状态/排版守卫。
- `AppLocaleTests`：bundle preferred → Locale 映射（zh-Hant、pt-BR、ar_SA）、fallback en、isRTL、calendar Gregorian。
- `L10nFormattingTests`：
  - `String(format:locale:)` 的 plural 选择：en/ru/ar/fr/id；
  - ar 数字体系；data-key POSIX 反例（`%04d` 分组）。
- `MylifeDateLabelsTests`：16 locale 的 year/monthYear/monthHeader/monthDay/weekday/weekRange/weekBadge；2026-08-21 样本；Hant 不再 Hans；ru standalone；ar 阿数。
- `MylifeAggregatorTests`：16 locale 的 `mylife_period_summary`，ru 1/2/5/11，ar 0/1/2/3/11/100。
- `HistoryDateFormattingTests`：LONG 日期 + FULL 星期 + plural；ar/ru/zh-Hant。
- `AgentJobStatusModelTests`：started/elapsed/badge 的 ar/ru；99+ 阈值。
- `AudioGalleryMathTests`：timecode 0/65s/负值；ar 阿数。
- `MylifeDateLabels` data-key 回归：ar 环境下 dateKey/isoWeekKey 仍 ASCII。
- 复合 a11y 标签：`AgentJobListPanel`/`HistoryComponents`/`SearchResultsView`/`AvatarView` 在 ar/fr 下使用 `ListFormatter` 的目标 locale 标点与顺序；`PeriodCard`/`AudioEntryButton` 计数为阿数。

### 7.2 UI/截图矩阵

- 语言：en、zh-Hans、zh-Hant、ja、ko、ru、de、fr、it、es、pt、tr、hi、id、ar。
- 关键屏：Splash、Permission、Conversation（含工具栏/模式/气泡/Agent job）、Settings、History、MyLife Timeline/Gallery/Audio/Search、WebView 错误、图片查看器、音频段。
- 重点组合：
  - ar：全屏 RTL、滑动删除、页码/时间/日期 bidi、阿数；
  - de/ru：最大 Dynamic Type 文本膨胀；
  - hi：天城文连字/行高/窄按钮；
  - ja/zh-Hant：CJK 行高与标点；
  - fr：NBSP/« »；
  - ru：四形复数、月名主格；
  - ar：六形复数。
- UI 测试改为语言参数化 launch（`-AppleLanguages`/`-AppleLocale`），不再依赖系统语言或硬编码中文；断言优先用 accessibility identifier，必要时用目标语言的资源解析。
- 系统权限弹窗的按钮文案由系统语言决定；语言矩阵测试要同时处理系统 alert 的 Allow/OK/允许 等本地化标签（现有 `M2DeviceE2ETests` 已有列表，需扩展）。

### 7.3 验收清单

- [x] `CFBundleLocalizations`、`knownRegions`、`Localizable.xcstrings`、`InfoPlist.strings` 17 locale 完全一致（`check_ios_localization.py`）。
- [ ] Settings → App → Language 列出 15 项；切换后 UI/日期/权限说明一致（真机手工验收待完成）。
- [x] 17 locale 的 235 key 无缺失、无 placeholder 错位；发布状态无 `new`/`needs_review`。
- [x] 12 组 plural 覆盖 app 计数范围；ru 1/2/5/11、ar 1/2/3/11 已测（ar 0/100 仍建议补测）；tr `one` 与 fr/it/es/pt `many` 回退 `other`（文案相同）。
- [x] en 源无汉字；法语标点 NBSP；ar 静态数字为阿数（脚本守卫）。
- [x] MyLife 日期/周范围/周徽标/摘要单测覆盖；Hant 不显示简体。
- [x] ar 日期/时间/页码/计数为阿数；Gregorian 日期。
- [x] ar RTL：返回箭头、History 滑动、图片分页、flow chips、气泡对齐、隐藏头像居中、Settings 技术字段有定向 UI 测试/截图；气泡尾巴按 `isRTL` 显式交换底部圆角（见 §10.3）。
- [x] de/ru/hi/ar 最大 Dynamic Type 冒烟通过；工具栏 caption 在 AX 下隐藏，保留完整 a11y label。
- [x] 权限弹窗 4 条 usage description 资源在 17 locale 下存在；系统弹窗显示仍需真机抽查。
- [x] Banner/错误：App 生成错误只显示本地化标题，技术 detail 仅写日志；服务端 detail 使用 `common_title_with_detail`。
- [x] 模式后缀在 17 locale 正确；旧历史 `mode=nil` 原样（既有测试）。
- [x] 相册 App→AI 指令 `conv_gallery_action_text` 已按 17 locale 本地化；其余 App→AI 指令与服务端内容未翻译。
- [x] UI 语言矩阵按定向方案通过：ar RTL + de/ru/hi/ar AX + 现有 14 locale 权限/Agent；完整 15×全屏截图矩阵仍为手工项。
- [ ] 真机 iPhone 11 全流程；per-app language 17 locale 切换；ar/de/ru/hi/th/vi 截图归档。

---

## 8. 风险与已知限制

1. **服务端内容不翻译**：AI 回复、转录原文、翻译结果、MyLife 摘要、Agent 结果保持服务端语言；客户端只翻译 chrome/labels。
2. **系统 UI 语言**：权限弹窗按钮、照片选择器、Safari、分享 sheet 由系统语言决定，可能与应用语言不同；usage description 本身的本地化需实测。
3. **pt 单套巴葡**：pt-PT 用户看到 pt-BR 用词；如需欧葡另加 `pt-PT`。
4. **zh-Hant 单套台灣用詞**：TW/HK/MO 共用；港式/澳门差异不单独覆盖。
5. **24 小时制固定**：不提供 12 小时制/AM-PM。
6. **技术错误/日志**：App 生成的错误应 key 化；系统/SDK 技术 detail 可能保持原语言；不得把未翻译的 detail 拼进必须完全本地化的句子。
7. **ar 数字与日历**：必须用 `ar_SA` + Gregorian；`Locale("ar")` 可能给拉丁数字，`ar_SA` 默认伊斯兰历。任何新增 formatter 若不走 helper，会出现混排/回历。
8. **运行时语言刷新**：per-app language 通常触发 App 重启；不实现 App 内语言切换。若系统不重启，bundle/format cache 需在 locale 通知/前台刷新，最坏情况提示重启。
9. **`deviceLanguage` 语义**：继续取 `Locale.current.languageCode`；per-app language 下不保证等于系统语言，与 Android `Locale.getDefault()` 同类限制；协议语义不在本设计改变。
10. **RTL 内容**：App chrome 镜像；Markdown 表格/代码块、WKWebView 内容方向由内容/HTML 决定，需单独验收。
11. **固定 offset/手势**：`HistoryComponents`、`MylifeFlowLayout`、`ConversationView`/`PeriodCard` offset 必须逐项真机截图；不能只靠 SwiftUI 自动镜像。
12. **文本膨胀**：de/ru 最长；hi 连字/行高；ar shaping/bidi；最大 Dynamic Type 下固定 lineLimit/宽度可能截断。
13. **翻译维护**：235 key × 17 locale 规模大；单 catalog review 成本高。先保持单表 + key 前缀；若后续超过约 300 key 或多团队并行，再评估按模块拆表。
14. **InfoPlist/catalog 同步**：权限说明在 `InfoPlist.strings`，其余在 xcstrings；新增文案必须两处判断，资源守卫需覆盖。
15. **CI**：已新增 `ios-app` job（`make ios-i18n-check` + `make ios-app-test`）与 `workflow_dispatch` 的 `ios-app-ui` job（`make ios-app-uitest`）；模拟器 runtime 由 `scripts/ios_simulator_os.py` 自动固定。
16. **App Store 元数据**：截图、描述、关键词不在代码仓库；语言列表依赖 bundle localizations/CFBundleLocalizations，商店元数据另行维护。
17. **泰语**：2026-10-27 曾按产品决定从 iOS「对方语言」候选移除。**Android（versionCode 138）与 iOS（build 11）均已于 2026-10-27 重新加入 `th` 并新增 `vi`（17 locale）。** 已确认使用 Gregorian；iOS 仍需重点验证泰文排版。
18. **翻译术语漂移**：必须沿用 `docs/android_multilingual.md` §8–§21；如 iOS 为长度改写，需同步评估 Android。

---

## 9. 文件索引

| 层 | 文件 | 当前/规划职责 |
|---|---|---|
| 资源 | `Resources/Localizable.xcstrings` | 235 key；16 locale |
| 资源 | `Resources/<locale>.lproj/InfoPlist.strings` | 4 个权限说明 × 16 locale |
| 配置 | `Resources/Info.plist` | `CFBundleLocalizations` 16 项 |
| 配置 | `LivaChatApp.xcodeproj/project.pbxproj` | `knownRegions`、InfoPlist variant group |
| API | `Core/UI/L10n.swift` | `t/format/plural/titleWithDetail` locale-aware |
| API | `Core/UI/AppLocale.swift` | bundle preferred → 格式化 locale/calendar/RTL；`appLocalizationEnvironment()` |
| API | `Core/UI/LocalizedNumber.swift` | NumberFormatter、阿拉伯数字、无分组 |
| API | `Core/UI/LocalizedList.swift`（新增） | ListFormatter 复合 a11y 标签 |
| API | `Core/UI/AppErrorMessage.swift`（新增） | 本地化错误 key + 仅日志技术 detail |
| 日期 | `Core/Mylife/MylifeDateLabels.swift` | 16 locale 日期/周/样式表；data key POSIX |
| 日期 | `Core/Mylife/MylifeAggregator.swift` | `mylife_period_summary` plural |
| 日期 | `Core/History/HistoryDateFormatting.swift` | AppLocale LONG + FULL 星期 + plural |
| 日期 | `Core/Conversation/AgentJobFormatting.swift` | started/elapsed/badge 本地化 |
| 日期 | `Features/Conversation/AgentJobResultOverlay.swift` | `AgentTimeLabel` AppLocale |
| 日期 | `Features/Timeline/Audio/AudioSegmentCard.swift` | timecode 本地化 |
| 搜索 | `Features/Timeline/Home/SearchResultsView.swift` | kindName key 化 |
| 选择器 | `Features/Conversation/ModeSelectionDialogs.swift` | 语言名随 AppLocale |
| 模式 | `Core/Conversation/ConversationModeModel.swift` | `deviceLanguage` 语义/测试缝 |
| RTL | `Features/History/HistoryComponents.swift` | 滑动删除方向感知 |
| RTL | `Features/Timeline/Components/MylifeFlowLayout.swift` | RTL flow |
| RTL | `ConversationView`/`PeriodCard`/各 `chevron.left` | 方向性符号/固定 offset |
| 布局 | `Core/UI/AppTheme.swift`、`MylifeTheme.swift` | 字体/lineSpacing/Dynamic Type |
| 测试 | `LivaChatAppTests/MylifeDateLabelsTests.swift` 等 | 16 locale 格式断言 |
| 测试 | `LivaChatAppTests/L10nFormattingTests.swift`（新增） | plural/数字/locale 测试 |
| 测试 | `LivaChatAppUITests/AppShellUITests.swift` | 语言参数化、去中文硬编码 |
| 工具 | `scripts/i18n/check_ios_localization.py`（新增） | 资源/占位符/复数/工程守卫 |
| 工具 | `scripts/i18n/import_android_to_ios.py`（新增，可选） | Android 翻译导入 draft |
| 文档 | `docs/android_multilingual.md` | 语言口径/术语/格式事实源 |
| 文档 | `docs/ios_app_ui.md` | 布局/字号/图标约束 |
| 文档 | `docs/ios_dev_rules.md` | iOS 15/SwiftUI 约束 |

---

## 10. 实施结果（2026-10-25）

### 10.1 资源与门禁

- 235 key / 12 plural / 17 locale；184 shared + 51 iOS-authored；0 unused；全部 `translated`；ru/ar 复数类别完整，tr `one` 与 fr/it/es/pt `many` 回退 `other`（文案相同）；th/vi 仅 `other`。2026-10-27 新增 iOS-authored key `conv_gallery_action_text`，并完成 th/vi 17 locale。
- `scripts/i18n/check_ios_localization.py` 覆盖 key/locale/placeholder、state、en 汉字、fr NBSP、ar 静态数字、plural 类别、未使用 key、**源码 key 反向校验**、**authored JSON 与 catalog 漂移**、`.lproj`/`knownRegions`/variant group 一致性。
- `make ios-i18n-check`、`make ios-app-test`、`make ios-app-uitest`；`ci-mobile.yml` 新增 `ios-app` job（资源守卫 + App 单测）与 `workflow_dispatch` 触发的 `ios-app-ui` job（UI 套件）。

### 10.2 错误与 a11y

- 新增 `common_title_with_detail`（CJK 全角冒号、fr NBSP）与 `AppErrorMessage`；App 生成的 camera/audio/settings/avatar detail 只写 `AppLog`，不再进入用户句子；server/SDK detail 保留并使用 locale-aware 模板。
- `conv_avatar_save_failed` 去掉 `%@`；importer 增加 `FORCE_AUTHORED_KEYS` 防止回归。
- `LocalizedList` 接入 History row、Search hit、Agent job row、Avatar 的复合 a11y label；`MessageBubbleView` 的 Agent 头与 speaker 文案改用 `titleWithDetail`。
- 相机权限横幅使用短标题 `conv_camera_permission_denied`，长引导文案 `conv_camera_permission_guide` 只留在权限弹窗正文。
- 工具栏 caption 只显示本地化名词（`conv_toolbar_mic_short` / `conv_toolbar_speaker_short` / `conv_toolbar_camera_short`），开关状态由图标与完整 a11y label 表达；AX 尺寸下隐藏 caption。

### 10.3 RTL

- 根视图与 cover 显式 `layoutDirection`。
- 9 个返回符号改 `chevron.backward`/`arrow.backward`（Timeline 搜索模式 `xmark`）；`▸`/`››` 改 SF Symbols。
- History 滑动、ImageViewer 分页、MylifeFlowLayout、SegmentProgressBar、隐藏头像簇、气泡尾巴按 RTL 处理。
- 气泡尾巴**必须显式处理**：`BubbleShape(isUser:isRTL:)` 按方向交换底部两角（LTR：user 右下 / AI 左下；RTL：user 左下 / AI 右下）。自定义 `Shape` 的镜像行为依赖渲染上下文：裸 SwiftUI 探针/`ImageRenderer` 会镜像，但本 App 的实时层级（主会话与 History cover 均验证）不会，因此不能依赖自动镜像。
- **SwiftUI RTL 实测规则**（iOS 17.4 / 18.0 / 27.0 模拟器实时截图逐像素验证）：`.offset(x:)` 自动镜像；`Shape` / `.clipShape` **上下文相关**（本 App 不镜像）；`Canvas` 绘制不镜像；`HStack`/`.leading`/`.trailing` 与 SF Symbols 自动镜像。自定义 `Shape` 与 `Canvas` 必须显式镜像，并用实时截图验证。
- Settings 服务器/密钥输入框、Markdown 代码块、页码/时间码固定 LTR。
- 定向 ar UI 测试：图片分页、History 右滑删除、气泡对齐、隐藏头像居中、返回按钮位置、Timeline chips 不越界、Gallery 分页、Settings 技术字段。

### 10.4 Dynamic Type

- 文字型 header/按钮固定高度改 `minHeight`；History/WebView/MyLife/Audio/Conversation 标题与状态放宽到 2 行，Agent summary 3 行。
- 新增 Settings/History/MyLife/Conversation AX 测试与 de/ru/hi/ar AX 冒烟；工具栏 caption 在 AX 下隐藏。
- 已知观察项：AX XXXL 下 transcription overlay 会遮挡消息气泡底部，属于浮动 overlay 的既有行为，后续可考虑并入滚动内容。

### 10.5 验证记录

- 全量单元测试：494 passed / 1 skipped / 0 failed。
- ar 气泡尾巴模拟器实时截图逐像素复核（iOS 17.4 / 18.0 / 27.0，主会话与 History cover）：用户气泡靠左、尾巴在左下；AI 气泡靠右、尾巴在右下（显式 `isRTL`；本 App 实时层级不自动镜像 Shape）。
- 定向 RTL UI：7 passed；LTR 回归：6 passed；AX UI：5 passed；caption/banner：2 passed；最终合并 UI 套件 15 passed。
- `make ios-i18n-check`、`xcstringstool compile`、`plutil -lint` 通过。
- 真机：iPhone 11 已安装；per-app language 17 locale 全量切换与 ar/de/ru/hi/th/vi 截图归档仍为手工验收项。

## 附录 A：本机 Foundation 行为验证

以下是设计依据，需在 iOS 单测中锁定（macOS 与 iOS 共享 Foundation 行为，但以 iOS 模拟器结果为准）：

1. **`Locale("ar")` vs `ar_SA`**
   - `Locale("ar")` 在本机给拉丁数字；`ar_SA`/`ar_EG` 给阿拉伯-印度数字。
   - `ar_SA` 的默认日历是 Islamic；必须显式 `Calendar(identifier: .gregorian)`，否则日期会变成回历。
   - `DateFormatter(locale: ar_SA, calendar: gregorian, dateFormat: "yyyy-MM-dd HH:mm")` → `١٩٧٠-٠١-٠١ ٠٠:٠٠`。

2. **`String(format: locale:)` 的复数选择**
   - 用 stringsdict/plural format 测试：`String(format: pluralFormat, locale: Locale("ru"/"ar"), count)` 会按传入 locale 选择 one/few/many/zero/two 等类别，而不是系统 locale。
   - 因此 `L10n.plural` 用 `String(format: t(key), locale: locale, count)` 是正确的；`String.localizedStringWithFormat` 会按系统 locale，不能用于 per-app language。
   - Xcode 编译多参数 plural 时会生成 positional 格式，例如 `history_item_meta` 为 `%3$#@value@`，variant 内为 `%1$@, %2$@, %3$lld message(s)`；`mylife_period_summary` 对应 `%2$#@value@` + `%1$@ · %2$lld ...`。
   - 本机用上述 positional stringsdict 验证：`String(format: format, locale: ru, range, n)` 在 n=1/2/5/11 时正确选择 one/few/many，三参数 history 同样正确。**手写**非 positional `%#@value@` 再附加额外参数会在 Foundation 下崩溃/错配；多参数 plural 必须由 xcstrings 编译器生成。

3. **数字分组陷阱**
   - `String(format: "%04d", locale: Locale("en"), 2026)` → `2,026`；`String(format: "%lld", locale: Locale("ar_SA"), 2026)` → `٢٬٠٢٦`。
   - data key 必须用 `en_US_POSIX`；显示年份/周数用 NumberFormatter（`usesGroupingSeparator = false`）或 DateFormatter，不要用 `%lld` + App locale。

4. **DateFormatter 默认 pattern 与产品目标的差异**
   - ru `MMMM y` → `августа 2026`（属格），产品要 `август 2026`（standalone 主格）。
   - de `MMM d` → `Aug. 21`，产品要 `21. Aug.`。
   - zh/ja/ko `y`/`MMM d` → `2026`/`8月 21`，产品要 `2026年`/`8月21日`。
   - it `EEE` → `ven`，产品要 `venerdì`（full）。
   - 因此使用资源模板 + 明确 month/weekday 样式，不直接依赖默认格式。

5. **`Bundle.main.preferredLocalizations.first`**
   - 是 App 实际运行的 bundle localization；`Locale.current`/`Locale.preferredLanguages` 不能替代 App 语言判断。
   - 参考 Apple 论坛/CFBundleLocalizations 文档（附录 B）。

---

## 附录 B：参考

- Android 多语言事实源：`docs/android_multilingual.md`（当前 17 locale，th/vi 已实施；iOS 已实施 17 locale，见 §2.9 与 Android §21）。
- Apple `CFBundleLocalizations`：https://developer.apple.com/documentation/bundleresources/information-property-list/cfbundlelocalizations
- Apple 论坛：`Bundle.main.preferredLocalizations` vs `Locale.preferredLanguages` / App 语言：https://developer.apple.com/forums/thread/769769
- Apple 论坛：如何获取真实 App 语言：https://developer.apple.com/forums/thread/772532
- Apple 论坛：App 显示语言与设备语言不一致：https://developer.apple.com/forums/thread/706739
- 现有 iOS UI/架构文档：`docs/ios_app_ui.md`、`docs/ios_app_conversation.md`、`docs/ios_app_audio.md`、`docs/ios_dev_rules.md`
