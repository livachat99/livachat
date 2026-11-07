# Android App 多语言（16 locales）

> **状态**：已实施（2026-10-27，待 review）。**2026-10-28 变更**：移除土耳其语 `tr`（App UI 语言、`values-tr/`、`locales_config`、translate 选择器），当前 **16 locale**；`session.start` 对 tr 设备回落上报 `en`。
> **2026-10-27 已实施变更**：发图指令（屏幕抓取 + 图库）本地化、`LanguagePickerSheet` 加入 `it`、`tr`、`hi`、`id` / 移除 `th`，见 §2.3.4 与 §2.3.9；**新增泰语 `th`、越南语 `vi` 至 17 locale**，见 §21。
> **基线**：2026-10-24，`versionCode 137`；2026-10-27 th/vi 轮 bump 至 `versionCode 138`；2026-10-28 移除 tr 轮 bump 至 **`versionCode 140`**（`versionName 1.0.0` 不变）
> **范围**：`LivaChatApp`。`LivaChatAppMock`、`LivaChatSDK`、服务端内容不在本地化范围。
> **支持语言**：English、简体中文、繁體中文、日本語、한국어、Русский、Deutsch、Français、Italiano、Español、Português、हिन्दी、Bahasa Indonesia、**ไทย（Thai）**、**Tiếng Việt（Vietnamese）**、العربية。
> **说明**：本文记录当前最终状态（16 locale）。

---

## 1. 范围与边界

### 1.1 支持语言与资源目录

| locale | 资源目录 | `locales_config` 条目 | plurals 数量形 | 备注 |
|---|---|---|---|---|
| English | `values/` | `en` | one / other | 默认目录与兜底 |
| 简体中文 | `values-b+zh+Hans/` | `zh-CN` | other | 脚本限定符覆盖 Hans 全族 |
| 繁體中文 | `values-b+zh+Hant/` | `zh-TW` | other | 台灣正體＋台灣慣用詞；TW/HK/MO 共用 |
| 日本語 | `values-ja/` | `ja` | other | です・ます調 |
| 한국어 | `values-ko/` | `ko` | other | 해요체 |
| Русский | `values-ru/` | `ru` | one / few / many / other | 西里尔；月名有格变 |
| Deutsch | `values-de/` | `de` | one / other | 文本最长语言 |
| Français | `values-fr/` | `fr` | one / other | 法式排版（NBSP、« »） |
| Italiano | `values-it/` | `it` | one / other | `mar` 星期/月份同形需消歧 |
| Español | `values-es/` | `es` | one / other | `mar` 同形需消歧；¿…? |
| Português | `values-pt/` | `pt` | one / other | 单套 **pt-BR** 文案 |
| हिन्दी | `values-hi/` | `hi` | one / other | 天城文；数词后名词同形 |
| Bahasa Indonesia | `values-in/` | `id` | other | 资源限定符为遗留码 `in` |
| العربية | `values-ar/` | `ar` | zero / one / two / few / many / other | RTL；阿拉伯-印度数字 |
| ไทย | `values-th/` | `th` | other | 泰文；Gregorian 公历（不使用佛历）；见 §21 |
| Tiếng Việt | `values-vi/` | `vi` | other | 越南文声调符号；见 §21 |

- `res/xml/locales_config.xml` 共 **16 项**：`en`、`zh-CN`、`zh-TW`、`ja`、`ko`、`ru`、`de`、`fr`、`it`、`es`、`pt`、`hi`、`id`、`th`、`vi`、`ar`。
- 每个资源目录的键集合一致：**243 条 `<string>` + 11 组 `<plurals>` = 254 个键**（2026-10-27 新增 `conv_image_action_text`，见 §2.3.9）。
- 默认目录 `values/` 为英文；未匹配的语言落英文。`th`/`vi` 已于 2026-10-27 重新加入/新增（§21）；`tr` 已于 2026-10-28 移除，`values-tr/` 删除。

### 1.2 边界

| 类别 | 在范围内 | 说明 |
|---|---|---|
| App 本地 UI 文案（按钮/弹窗/空态/错误/提示） | ✅ | `res/values*/strings.xml` |
| 无障碍描述 `contentDescription` | ✅ | 与普通文案同资源 |
| 通知 channel 名 / 标题 / 正文 | ✅ | `MicCaptureService`、`ScreenCaptureService` |
| 设备标签（麦克风/扬声器枚举显示名） | ✅ | `DeviceLabelSpec` + 资源 |
| 服务器选项显示名 | ✅ | `ServerOption.labelRes` |
| 日期/星期/周期/计数格式 | ✅ | `MylifeDateLabels`、`PeriodAggregator`、`HistoryScreen` 等 |
| 「对方语言」选择器显示名 | ✅ | `Locale.getDisplayLanguage(uiLocale)` |
| MyLife 客户端气泡后缀「(转录)/(原文)/(译文)」 | ✅ | 展示时按资源拼接 |
| 对话落库的模式后缀 | ✅ | 只存正文+`mode`，展示时本地化（D4=B） |
| App→AI 发图指令（屏幕抓取 + 图库） | ✅ | 16 locale 资源 `conv_image_action_text`，随 App 语言，英文兜底；见 §2.3.9/§21 |
| 服务端内容（AI 文本/转录原文/MyLife 摘要/Agent 结果） | ❌ | 服务端产物不翻译 |
| logcat / SDK 日志 | ❌ | 非用户可见 |
| `@Preview` 样例文案 | ❌ | 仅 IDE 预览 |
| `LivaChatAppMock`、`LivaChatSDK`、`sdk-kotlin` | ❌ | Mock/SDK 不含用户文案 |
| 协议常量（`chat`/`translate`/`dialogue` 等 code） | ❌ | 仅显示名翻译 |

### 1.3 运行时与构建基线

- `minSdk 26`、`compileSdk = targetSdk = 34`、AGP 8.5.0、Kotlin 2.0.0、Gradle 8.7。
- `AndroidManifest.xml`：
  - `android:localeConfig="@xml/locales_config"`（Android 13+ 系统「应用语言」页）；
  - `android:supportsRtl="true"`；
  - `configChanges` **不含 locale**——切换语言会重建 Activity，Compose 资源随之刷新。
- 无 `resConfigs`，全部 16 个 locale 均打包。
- **无 App 内 UI 语言切换入口**：App UI 语言只跟随系统语言；Android 13+ 可额外使用系统「应用语言」页（由 `localeConfig` 提供 16 个条目）。`LanguagePickerSheet` 是 translate/lecture 的「对方语言」选择器，不是 App UI 语言切换。
- `session.start.config.language` 每次以 `resources.configuration.locales[0]` 归一上报（`zh-CN → zh-Hans`、`zh-TW → zh-Hant`、`in → id`，不支持语言回退 `en`）；App 通过 `LivaChatConfig.languageProvider` 在每次 `session.start` 现取。该字段与 translate 的 `my_language` 解耦，详见 `docs/session_start_improvments.md`。
- Android 无 CI 门禁（`.github/workflows/ci-mobile.yml` 仅覆盖 iOS）；i18n 门禁为本地 JVM 测试 + `androidTest` 编译 + `assembleDebug`。

---

## 2. 资源与运行时现状

### 2.1 资源目录与解析

- `values/` = 英文默认/兜底；`values-b+zh+Hans/` = 简体；`values-b+zh+Hant/` = 繁體。
- `zh-CN` 命中 Hans；`zh-TW` 命中 Hant；`zh-HK`/`zh-MO` 按脚本匹配同样命中 Hant（TW/HK/MO 共用一套台灣正體文案）。
- `values-in/` 是印度尼西亚语的 Android 资源限定符（历史遗留码）；`locales_config` 与 BCP-47 使用 `id`。日期纯函数与月段分支同时匹配 `"id"` 与 `"in"`（双键防御，见 §19）。
- `values-pt/` 为 **pt-BR 单套**；pt-PT 设备同样命中该目录，显示巴葡文案。
- `values-ar/` 为 RTL 语言；`supportsRtl=true`，Compose 的 start/end 布局自动镜像。
- `values-th/`、`values-vi/` 使用语言限定符（无遗留码）；`locales_config` 条目分别为 `th`、`vi`。
- 解析位置：
  - Compose：`stringResource(R.string.…)` / `pluralStringResource(...)`，locale 取 `LocalConfiguration.current.locales[0]`；
  - Service/通知：`getString(...)`；
  - ViewModel 用户可见状态：Hilt `@ApplicationContext` 注入后 `getString(...)`；
  - 纯函数（日期/标签规格）：显式接收 `Locale` 或返回资源 id/`DeviceLabelSpec`，不直接取全局 locale。

### 2.2 字符串与 plurals 清单

主源集按 key 前缀分布（254 键）：

| 前缀 | 数量 | 典型内容 |
|---|---:|---|
| `conv_` | 89 | 对话页、模式、Agent、后缀、picker、发图指令 |
| `mylife_` | 49 | 时光机时间线/相册/音频/搜索 |
| `settings_` | 36 | 设置页与服务器配置 |
| `common_` | 17 | 取消/确定/关闭/返回/重试等 |
| `device_` | 17 | 音频设备标签与连接符 |
| `history_` | 13 | 对话历史列表/详情 |
| `perm_` | 10 | 权限页/权限弹窗 |
| `notif_` | 6 | 麦克风/屏幕抓取 FGS 通知 |
| `webview_` | 4 | WebView 页 |
| `cap_` / `capture_` | 7 | 屏幕抓取浮窗提示/失败 |
| `splash_` | 2 | 启动页 |
| `app_` / `avatar_` / `chat_` / `player_` | 4 | 单键 |

11 组 plurals（每组在所有 locale 中均存在）：

`conv_agent_jobs_title`、`conv_agent_jobs_running`、`conv_job_remaining_chars`、`history_item_meta`、`mylife_audio_entry_desc`、`mylife_frames_count`、`mylife_analyzing_frames`、`mylife_delete_confirm`、`mylife_segments_count`、`mylife_search_total`、`mylife_photos_deleted`。

**数量形按 locale**（`I18nResourceFilesTest.pluralQuantitiesMatchCldrPerLocale` 固化）：

| locale | 数量形 |
|---|---|
| en | one / other |
| zh-Hans / zh-Hant / ja / ko | other |
| ru | one / few / many / other |
| de / fr / it / es / pt | one / other |
| hi | one / other（名词短语同形，动词短语按数分形） |
| id | other |
| th / vi | other |
| ar | zero / one / two / few / many / other |

- 带参文案使用位置参数（`%1$s`、`%2$d` 等），允许语序重排。
- 月份标题模板：`mylife_overview_month`；设备标签连接：`device_label_join`；模式后缀：`conv_msg_suffix_*`。
- 当前无 `translatable="false"` 键。

### 2.3 非普通 string 专项

#### 2.3.1 日期/时间/周期格式

日期/周期标签纯函数（`MylifeDateLabels`、`PeriodAggregator`、`HotSlotRows` 等）显式接收 `Locale`；Compose 侧从 `LocalConfiguration.current.locales[0]` 取当前 UI locale。`formatTimecode`/`formatRemainingChars` 使用默认 `.format`，见下。

**MyLife 日期标签与周期摘要（`MylifeDateLabels.kt` / `PeriodAggregator.kt`）**

| locale | 年 | 月年 | 月日 | 星期 | 周范围 | 周徽标 | 摘要兜底（N=5） |
|---|---|---|---|---|---|---|---|
| en | 2026 | Aug 2026 | Aug 21 | Fri | 8/17 – 8/23 | W34 | · 5 days |
| zh-Hans | 2026年 | 2026年8月 | 8月21日 | 周五 | 8/17 – 8/23 | 34周 | 共 5 天 |
| zh-Hant | 2026年 | 2026年8月 | 8月21日 | 週五 | 8/17 – 8/23 | 34週 | 共 5 天 |
| ja | 2026年 | 2026年8月 | 8月21日 | 金曜日 | 8/17 – 8/23 | 第34週 | ・計 5 日 |
| ko | 2026년 | 2026년 8월 | 8월 21일 | 금요일 | 8/17 – 8/23 | 34주차 | ・총 5일 |
| ru | 2026 | август 2026 | 21 авг. | пт | 17.08 – 23.08 | Неделя 34 | · всего 5 дней |
| de | 2026 | August 2026 | 21. Aug. | Fr. | 17.08. – 23.08. | KW 34 | · 5 Tage |
| fr | 2026 | août 2026 | 21 août | ven. | 17/08 – 23/08 | S34 | · 5 jours |
| it | 2026 | agosto 2026 | 21 ago | venerdì | 17/08 – 23/08 | S34 | · 5 giorni |
| es | 2026 | agosto de 2026 | 21 ago | viernes | 17/08 – 23/08 | S34 | · 5 días |
| pt | 2026 | agosto de 2026 | 21 ago. | sex. | 17/08 – 23/08 | S34 | · 5 dias |
| tr | 2026 | Ağustos 2026 | 21 Ağu | Cum | 17.08 – 23.08 | 34. Hafta | · 5 gün |
| hi | 2026 | अगस्त 2026 | 21 अग॰ | शुक्र | 17/08 – 23/08 | सप्ताह 34 | · कुल 5 दिन |
| id | 2026 | Agustus 2026 | 21 Agu | Jum | 17/08 – 23/08 | Pekan 34 | · total 5 hari |
| ar | ٢٠٢٦ | أغسطس ٢٠٢٦ | ٢١ أغسطس | الجمعة | ١٧/٠٨ – ٢٣/٠٨ | الأسبوع ٣٤ | · إجمالي ٥ أيام |
| th | 2026 | สิงหาคม 2026 | 21 ส.ค. | ศ. | 17/08 – 23/08 | สัปดาห์ที่ 34 | · 5 วัน |
| vi | 2026 | Tháng 8 năm 2026 | 21 thg 8 | Thứ Sáu | 17/08 – 23/08 | Tuần 34 | · 5 ngày |

说明：

- `weekLabel`（周范围）与 `mylife_week_label`（周期卡周徽标）是两条独立路径；上表分别列出。
- en/zh/ja/ko 的周范围是 `M/D – M/D`（不补零）；其他语言按本地习惯使用 `dd.MM` / `dd.MM.` / `dd/MM`。
- ru 的 `monthYearText` 使用 `TextStyle.FULL_STANDALONE`（主格），`monthDayText` 使用 `SHORT`（属格缩写，五月本身为「мая」）。
- de 的日卡日数字带尾点（`21. Aug.`）；tr 的周范围点分无尾点（`17.08 – 23.08`），与 de 的 `17.08.` 区分。
- es/pt 的月年含 `de`；month header 资源模板分别为 `%2$s de %1$d`。
- th/vi（§21）：th 使用 Gregorian 公历，月年全称 `สิงหาคม 2026`；vi 月年 `Tháng 8 năm 2026`（`FULL_STANDALONE` + `năm`），月日 `SHORT` 缩写 `thg 8`，星期 FULL `Thứ Sáu`。
- ar 的插值数字统一经 `localizeDigits` 转阿拉伯-印度数字；资源侧 `%d` 本身也会输出阿数。

**时间线月段标题（`OverviewSection.MonthHeader`）**

| locale | 资源模板 | 月名 |
|---|---|---|
| zh-Hans / zh-Hant / ja | `%1$d年 · %2$s` | `8月` |
| ko | `%1$d년 · %2$s` | `8월` |
| es / pt | `%2$s de %1$d` | FULL |
| th | `%2$s %1$d` | FULL（`สิงหาคม`） |
| vi | `%2$s năm %1$d` | FULL_STANDALONE（`Tháng 8`） |
| 其他 | `%2$s %1$d` | en/ar SHORT；ru FULL_STANDALONE；de/fr/it/tr/hi/id FULL |

`yyyy-MM` 解析失败时回退中文 `"${y}年 · ${m}月"`（正常数据不可达，见 §2.4/§6）。

**其它格式化站点（时间/页码/剩余字数）**

| 站点 | 当前实现 |
|---|---|
| `HistoryScreen` 会话副标题 | `DateTimeFormatter.ofLocalizedDate(LONG).withLocale(locale)` + `dayOfWeek.getDisplayName(FULL, locale)` + `history_item_meta` plural；数字经 `localizeDigits` |
| `OverviewSection.MonthHeader` | `mylife_overview_month` 资源模板；月名按语言取 `FULL_STANDALONE` / `FULL` / `SHORT`（见上方 MonthHeader 表） |
| `HotSlotRows` 行时间 / 标题 | `HH:mm`；标题 = `monthDayText(...) + " " + HH:mm`；数字经 `localizeDigits` |
| `HotSlotChips` | `HH:mm`；数字经 `localizeDigits` |
| `SearchResultList` 日头 / 时间 | 日头 `monthDayText(...)`；时间 `HH:mm`；数字经 `localizeDigits` |
| `AudioGalleryUiState.segmentTimeLabels` | `HH:mm`；数字经 `localizeDigits` |
| `AudioGalleryUiState.formatTimecode` | `M:SS`，`"%d:%02d".format(...)`（默认 locale，ar 设备天然阿数） |
| `AgentJobListPanel` | 开始时间 `HH:mm:ss`、耗时 `Xh Ym`/`Xm Ys`/`Xs`；数字经 `localizeDigits`，单位 h/m/s 保持拉丁 |
| `AgentJobResultOverlay` | `HH:mm`；数字经 `localizeDigits` |
| `MessageBubble` | `HH:mm`；数字经 `localizeDigits` |
| `ImageViewerOverlay` / `FrameViewerOverlay` | 页码 `n / N`；数字经 `localizeDigits` |
| `AgentJobResultText.formatRemainingChars` | `"%,d".format(...)`（默认 locale，ar 设备天然阿数） |

- 全 UI 采用 **24 小时制** `HH:mm` / `HH:mm:ss`，不切换 AM/PM。
- `DateTimeFormatter` 本身不本地化数字（`DecimalStyle.STANDARD`）；显式 `DateTimeFormatter`/纯模板日期时间站点在 ar 下必须经 `localizeDigits`，否则会与资源侧 `%d` 的阿数混排。资源侧 `%d` 与默认 `.format` 站点（`formatTimecode`、`formatRemainingChars`）在 ar 下天然输出阿数。

#### 2.3.2 计数文案

计数统一走 11 组 `<plurals>`（清单见 §2.2）。典型调用点：

- `history_item_meta`：历史列表「日期 · 星期 · N 条消息」；
- `mylife_frames_count` / `mylife_analyzing_frames` / `mylife_delete_confirm` / `mylife_photos_deleted`：相册张数/删除确认/删除提示；
- `mylife_segments_count` / `mylife_audio_entry_desc`：音频段数/语音记录描述；
- `mylife_search_total`：搜索结果总数；
- `conv_agent_jobs_title` / `conv_agent_jobs_running` / `conv_job_remaining_chars`：Agent job 标题/运行中数量/剩余字数。

zh/ja/ko/id/th/vi 仅 `other`；en/de/fr/it/es/pt/hi 为 `one/other` 两形；ru 四形、ar 六形由资源分别提供。

#### 2.3.3 模式后缀（D4=B）

- Room `messages` 表当前 **version 9**；`mode` 列由 `MIGRATION_8_9` 加入。
- 写入：
  - 用户转录 final：`text` 只存正文，`mode = "transcribe"`；
  - 用户翻译 final：`text` 只存正文，`mode = "translate"`；
  - AI 翻译 final：`text` 只存正文，`mode = "translate"`；
  - chat 模式：`mode = null`。
- 展示（`MessageBubble.modeSuffixResFor`）：
  - `transcribe` → `conv_msg_suffix_transcript`；
  - `translate` + user → `conv_msg_suffix_original`；
  - `translate` + AI → `conv_msg_suffix_translation`；
  - `null` → 无后缀。
- 会话实时气泡与 History 详情共用 `MessageBubble`，后缀同源。
- 迁移前历史行 `mode = null`，其后缀已写入 `text`，原样渲染、不回拆；因此旧历史可能保留写入时的语言。
- MyLife 时光机事件没有落库后缀：`HotSlotRows` 展示时按 `EventType.TRANSCRIBE/TRANSLATE` + role 用 `conv_msg_suffix_*` 资源拼接。

#### 2.3.4 「对方语言」选择器

`LanguagePickerSheet` 是 translate/lecture 的业务语言选择器，候选 code：

`zh`、`en`、`ja`、`ko`、`fr`、`it`、`de`、`es`、`pt`、`ru`、`hi`、`id`、`th`、`vi`、`ar`

- **2026-10-28 已实施**：移除 `tr`（土耳其语），具体语言候选由 16 个降为 **15 个**；`auto` 仍置顶，默认选中 `en` 不变。
- **2026-10-27 已实施**：加入 `it`（意大利语）、`tr`（土耳其语）、`hi`（印地语）、`id`（印尼语），随后重新加入 `th`（泰语）、新增 `vi`（越南语）；候选共 16 个，默认 `en` 不变。
- 显示名 = `Locale.forLanguageTag(code).getDisplayLanguage(uiLocale)`，随 App UI locale 自动本地化；`th`/`vi` 显示名无需新增文案 key。
- 「自动识别」走 `conv_language_auto` 资源；`auto` 协议语义等同英语；
- 该列表不是 App UI 语言列表；App UI 语言由系统/per-app 设置决定。
- iOS 使用同一列表（`auto` + 15 个 code）。

#### 2.3.5 设备标签与服务器选项

- `DeviceLabelSpec`（`media/DeviceLabel.kt`）把展示拆成：
  - `dynamicName`（产品名，不翻译）或 `nameRes`（类型兜底资源）；
  - `suffixText`（address/序号）或 `suffixRes`（类型后缀）；
  - 有后缀时用 `device_label_join`（`%1$s (%2$s)`）包裹。
- `AudioInputDevice` / `AudioOutputDevice` 的纯函数只产出 `DeviceLabelSpec`，UI 层 `deviceLabelText()` 用 `stringResource` 组合；同 type 多条用 address/稳定序号区分，输出侧跨 type 同名用 `device_tag_bt_voice` / `device_tag_bt_music` 区分。
- `ServerOption.labelRes` 指向 `settings_server_public` / `settings_server_lan`；`ServerOptionPicker` 用 `stringResource(item.labelRes)` 渲染，地址本身不翻译。

#### 2.3.6 通知

| 服务 | channel id | notification id | 文案资源 |
|---|---|---|---|
| `MicCaptureService` | `microphone` | `1002` | `notif_mic_channel/title/text` |
| `ScreenCaptureService` | `screen_capture` | `1001` | `notif_screen_channel/title/text` |

- 两个 Service 都在 `buildNotification()` 中用当前 Context 的 `getString(...)` 建通知，并调用 `createNotificationChannel`。
- channel 名/描述在 Service 启动时按当前 locale 创建；同 id 重建可更新名称。若 Service 持续运行期间切换语言，channel 名要等 Service 下次启动才会刷新。
- 通知标题/正文随每次 Service 启动重新取资源。

#### 2.3.7 其它动态文案

以下用户可见文案均走资源，不在 Kotlin 中硬编码：

- `StatusIndicator` 状态词（随时可以说/正在连接/我在听/让我想想…）；
- 对话页模式标签（翻译/转录/全模态/听众模式）；
- `WelcomeGuide` 欢迎页；
- `PermissionScreen` / `PermissionDialog` 权限页；
- `SplashScreen`；
- `ScreenCaptureBubble` 四种反馈（已发送/前台拒绝/锁屏拒绝/抓取失败）；
- `ScreenCaptureController` 启动/停止错误（经 `startErrors` → `ErrorBanner`）；
- 各类删除/确认/错误弹窗。

#### 2.3.8 App→AI 指令文本（2026-10-27 已本地化）

两处发图指令随帧上行给服务端起 LLM 轮，均使用同一个 16 locale 资源：

| 文件 | 位置 | 发送值 |
|---|---|---|
| `media/ScreenCaptureController.kt` | `grabAndPush` | `context.getString(R.string.conv_image_action_text)` |
| `media/GalleryPhotoSender.kt` | `send` | `context.getString(R.string.conv_image_action_text)` |

- 两处均在 omni 模式下传 `null`；
- **2026-10-27 已实施**：资源随 App 当前语言选择，英文兜底；详见 §2.3.9。

#### 2.3.9 已实施变更：发图指令本地化（2026-10-27）

> 状态：**已实施**。`TAP_CAPTURE_TEXT` / `GALLERY_ACTION_TEXT` 已删除；屏幕抓取/图库均解析 `conv_image_action_text`。单测 956/0 failures（4 skipped）、`assembleDebug` 与 `compileDebugAndroidTestKotlin` 通过。

两个入口共用同一资源，避免屏幕抓取与图库文案漂移：

| 入口 | 代码位置 | 发送值 |
|---|---|---|
| 屏幕抓取 | `media/ScreenCaptureController.grabAndPush` | `context.getString(R.string.conv_image_action_text)`；omni 传 `null` |
| 图库 | `media/GalleryPhotoSender.send` | `context.getString(R.string.conv_image_action_text)`；omni 传 `null` |

- 资源 key：`conv_image_action_text`，16 个 `values*/strings.xml` 全量补齐；`values/` 为英文默认，未匹配 locale 自动回退英文。
- 删除 `ScreenCaptureController.TAP_CAPTURE_TEXT` 与 `GalleryPhotoSender.GALLERY_ACTION_TEXT` 常量；发送时解析资源，不缓存常量值。
- Android XML 中法语/意大利语撇号写 `\'`（如 `Décrivez le contenu de l\'image`、`Descrivi il contenuto dell\'immagine`）。
- `I18nResourceFilesTest` 的 16 目录 key 齐平、`values/` 无汉字、法语 NBSP、阿语数字守卫覆盖新 key。
- 资源数：**243 条 `<string>` + 11 组 `<plurals>` = 254 个键**（新增 1 个）。
- 测试：`GalleryPhotoSenderTest` 的期望值已改为 `context.getString(R.string.conv_image_action_text)`；已补“两个入口使用同一资源”的守卫（`GalleryPhotoSenderTest.gallery and screen capture share the localized image action resource`），并新增 `LanguagePickerSheetTest`。
- 实现结果（2026-10-27）：
  1. `res/values*/strings.xml` ×15 已新增 `conv_image_action_text`（fr/it 撇号写 `\'`）。
  2. `media/GalleryPhotoSender.kt` 已删除 `GALLERY_ACTION_TEXT`，`send` 使用 `context.getString(R.string.conv_image_action_text)`。
  3. `media/ScreenCaptureController.kt` 已删除 `TAP_CAPTURE_TEXT`，`grabAndPush` 使用 `context.getString(R.string.conv_image_action_text)`；omni 仍传 `null`。
  4. `ui/screens/conversation/LanguagePickerSheet.kt` 的 `languageCodes` 已加入 `it`、`tr`、`hi`、`id`，移除 `th`。
  5. 已更新 `GalleryPhotoSenderTest`，新增 `LanguagePickerSheetTest` 与共享资源守卫。
- 服务端语义不变：`payload.text` 非空且过 `min_meaningful_chars` 时起文本回合；omni 仍不发送 text。
- 16 locale 文案（与 iOS `conv_gallery_action_text` 一致）：

| locale | 文案 |
|---|---|
| en | Describe the image content |
| zh-Hans | 描述图片内容 |
| zh-Hant | 描述圖片內容 |
| ja | 画像の内容を説明してください |
| ko | 이미지 내용을 설명해 주세요 |
| ru | Опишите содержимое изображения |
| de | Beschreibe den Inhalt des Bildes |
| fr | Décrivez le contenu de l'image |
| it | Descrivi il contenuto dell'immagine |
| es | Describe el contenido de la imagen |
| pt | Descreva o conteúdo da imagem |
| hi | छवि की सामग्री का वर्णन करें |
| id | Jelaskan isi gambar |
| th | อธิบายเนื้อหาในภาพ |
| vi | Mô tả nội dung hình ảnh |
| ar | صف محتوى الصورة |

> Android XML 实际值需对 `'` 转义：fr 为 `Décrivez le contenu de l\'image`，it 为 `Descrivi il contenuto dell\'immagine`。

### 2.4 工程事实与有意保留的中文

- 主源集 `src/main/kotlin` 去注释后仍有 **21 处中文字面量**，均为有意保留（2026-10-27 移除 2 处 App→AI 指令）：

| 类别 | 数量 | 说明 |
|---|---:|---|
| `@Preview` 样例 | 13 | `AgentJobListPanel`、`EmptyState`、`ErrorBanner`、`MessageBubble`、`TranscriptionBubble`；仅 IDE |
| MyLife 日期/摘要模板 | 7 | `MylifeDateLabels` 的 zh/ja 模板、`PeriodAggregator` 的 zh/ja 摘要、`OverviewSection` 的 zh 月段与畸形数据兜底 |
| App→AI 指令 | 0（2026-10-27 已移除） | 改为 `conv_image_action_text` 资源；见 §2.3.9 |
| DI 崩溃文案 | 1 | `MylifeModule` 的 `serverUrl 无 host`（开发者路径，非用户可见） |

- `values/strings.xml` 去 XML 注释后不含汉字（`I18nResourceFilesTest.defaultStringsContainNoHan`）；简体/繁體中文只在对应目录。
- `OverviewSection` 在 `yyyy-MM` 解析失败时仍有中文兜底 `"${y}年 · ${m}月"`；正常数据不会触发，畸形数据下属于已知残留（见 §6）。
- `@Preview` 与 DI 崩溃文案不参与用户可见文案审查。

---

## 3. 实现规则

### 3.1 资源目录与回退

- 默认目录 `values/` 为英文，同时承担所有未匹配语言的兜底。
- 简体/繁體使用脚本限定符：`values-b+zh+Hans/`、`values-b+zh+Hant/`；其余语言使用语言限定符 `values-xx/`。
- `values-in/` 是印尼语资源目录（Android 遗留限定符），`locales_config` 用 `id`；代码分支同时兼容 `"id"`/`"in"`。
- `values-pt/` 单套巴葡文案，覆盖 pt-BR/pt-PT。
- 新增语言的最小步骤：新增 `values-xx/strings.xml`（254 键齐平）→ `locales_config.xml` 加条目 → 在 `MylifeDateLabels`/`PeriodAggregator`/`OverviewSection` 等纯函数中补语言分支（如该语言需要特殊日期/复数规则）→ 补测试与守卫。`values-th`/`values-vi` 已按此流程落地（§21）。
- 未匹配 locale 落英文；不存在 `values-zh` 或 `values-id`。

### 3.2 资源命名与参数

- key 使用 `snake_case`，按模块前缀分组：`conv_*`、`mylife_*`、`settings_*`、`history_*`、`perm_*`、`notif_*`、`device_*`、`common_*` 等。
- 跨屏复用文案进 `common_*`；同语义不同原文分键。
- 带参文案使用位置参数（`%1$s`/`%2$d`），允许不同语言重排。
- 复数一律 `<plurals>`；同一 key 在所有 locale 都必须存在。
- 当前无 `translatable="false"` 键；不翻译的内容不放进资源（见 §3.8）。

### 3.3 解析位置

| 场景 | 当前做法 |
|---|---|
| Compose 可组合 | `stringResource(...)` / `pluralStringResource(...)` |
| Service / 通知 | `getString(...)`（Service 自带 Context） |
| ViewModel 用户可见状态 | Hilt `@ApplicationContext` 注入后 `context.getString(...)` |
| 纯函数标签（设备/ServerOption） | 返回 `DeviceLabelSpec` / 资源 id，UI 层解析 |
| 日期/周期纯函数 | 显式传入 `Locale`；生产由 Compose 取 `LocalConfiguration.current.locales[0]`（`formatTimecode`/`formatRemainingChars` 默认 `.format` 例外） |
| 测试 | 优先 `ApplicationProvider.getApplicationContext().getString(...)` / `getQuantityString(...)`；纯函数用显式 `Locale` |

### 3.4 模式后缀（D4=B）

- 写侧只存正文 + `mode`；展示侧按当前 App locale 拼后缀。
- 会话实时气泡、History 详情、MyLife 时光机三条展示路径均使用 `conv_msg_suffix_*` 资源。
- 迁移前历史行 `mode=null`，后缀已并入正文；不自动拆分，避免误改历史内容。
- 相关测试：`ModeSuffixMappingTest`、`MessageBubble` 相关 UI 测试。

### 3.5 日期、数字与复数规则

- 不硬编码中文日期格式；所有日期/周期标签由 `Locale` 驱动。
- 24 小时制固定；不按 locale 切 12 小时制。
- 复数数量形按 CLDR/平台规则：en/de/fr/it/es/pt/hi 两形；zh/ja/ko/id/th/vi 仅 other；ru 四形；ar 六形。
- 数字：
  - 资源侧 `%d`/`%,d` 随 locale 数字体系（ar 天然阿数）；
  - 纯函数/`DateTimeFormatter` 侧 ar 统一经 `ui/util/LocaleDigits.localizeDigits`；
  - 技术标识（版本号、IP/域名、协议 code）保持默认格式。
- 排版：
  - fr：`?`/`!`/`:` 前与 `«`/`»` 内侧使用 U+00A0；技术格式串（含 `port`）豁免；
  - fr/it/th/vi：资源中的撇号必须写 `\'`（Android 编译强制）；
  - es：疑问句全句 `¿…?`；
  - ar：RTL，`supportsRtl=true`，Compose start/end 自动镜像。
- 代码/资源注释中的「缺口①…⑤」是注释里的适配点编号，分别对应：① 日先月后、② 月年形式、③ 摘要兜底、④ 周范围格式、⑤ 星期消歧/数字本地化（按语言不同）。本文档以各语言章节的当前规则为准。

### 3.6 语言切换

- App 不提供 UI 语言设置行；只跟随系统语言。
- Android 13+：`locales_config.xml` 提供 16 个条目，用户可在系统「设置 → 应用 → LivaChat → 语言」中单独选择；选择结果由平台注入 App 资源配置。
- Android 12 及以下：只跟随系统语言。
- `MainActivity` 的 `configChanges` 不含 locale，语言变化会重建 Activity；Compose 资源与 `LocalConfiguration` 随之更新。
- 切换语言后，正在运行的 FGS 通知 channel 名不会立即刷新；Service 下次启动时才用新 locale 重建 channel（见 §2.3.6）。

### 3.7 translate/lecture 源语言

- `ConversationViewModel` 在确认翻译/听众语言时取 `java.util.Locale.getDefault().language` 作为 `deviceLanguage`，随 `sendSessionChange("translate", deviceLanguage, peerLanguage, ...)` 下发。
- 期望语义 = **说话者/系统语言**，与 `LanguagePickerSheet` 选的对方语言解耦；当前取的是进程默认语言 `Locale.getDefault()`。
- Android 13+ per-app locale 可能使进程默认语言随 App locale 变化，因此不能保证始终等于系统语言；当前未显式读取系统 locale（见 §6）。
- 注意：Android 13+ per-app locale 可能影响进程默认 locale；当前实现未显式区分「系统语言」与「App 语言」。如需严格固定为系统语言，应改为显式读取系统 locale（见 §6）。

### 3.8 不翻译清单

- 服务端内容：AI 回复、转录原文、翻译结果、MyLife 摘要、Agent 任务正文/结果；
- 协议 code：`chat`/`translate`/`dialogue`/`lecture`/`auto` 等；
- 日志/logcat/SDK 日志；
- `@Preview` 样例；
- `LivaChatAppMock`、`LivaChatSDK`、生成 DTO；
- 版本号/技术标识（设置页 `versionName.versionCode`、IP/域名、端口）。

### 3.9 测试与门禁

- `I18nResourceFilesTest`（JVM）是资源主守卫，规则：
  1. **16 目录 key 齐平**：默认 + Hans/Hant/ja/ko/ru/de/fr/it/es/pt/hi/id(`values-in`)/th/vi/ar，逐一双向比较 `string`/`plurals`/`string-array` 键名；
  2. **plurals 数量形**：按 locale 断言 11 组 plurals 的 quantity 集（en one/other；Hans/Hant/ja/ko/id/th/vi other；ru one/few/many/other；de/fr/it/es/pt one/other；hi one/other；ar 六形）；
  3. **默认目录无汉字**：`values/strings.xml` 去 XML 注释后不得含 `[一-鿿]`；
  4. **法语排版**：`?`/`!` 前必须 U+00A0；`:` 仅文案句检查，含 `port` 的技术格式串豁免；兼容 ` ` 转义与字面 NBSP；
  5. **阿语字面数字**：`values-ar` 去掉占位符后不得残留 ASCII 数字，必须写阿拉伯-印度数字。
- 语言格式单元测试：
  - `MylifeDateLabelsTest`：zh/en 回归锁 + ja/ko/ru/de/fr/it/es/pt/tr/hi/id/ar/th/vi 的日期/月年/星期规则；id 双键（`id`/`in`）回归；
  - `PeriodAggregatorTest`：各语言周范围、摘要兜底（ru 三形、ar 六形）、日卡星期消歧（it/es `mar`）、id 双键、th/vi；
  - `LocaleDigitsTest`：ar 映射、区域变体、幂等、非 ar 恒等；
  - `ModeSuffixMappingTest`：mode+role → 后缀资源；
  - `AudioInputDeviceLabelTest` / `AudioOutputDeviceLabelTest`：`DeviceLabelSpec` 规格（不断言文案）；
  - `AgentJobTimeFormatTest`、`SegmentTimeLabelsTest`、`HotSlotChipLabelTest`、`HotSlotRowsTest` 等：ar 时间/页码/耗时数字本地化。
- 仪器测试与 UI 测试：断言优先从资源解析（`getString`/`getQuantityString`），避免写死中文；`ScreenCaptureBubbleRenderTest` 等固定 `qualifiers = "b+zh+Hans"` 的用例只用于中文渲染回归。
- 本地门禁：`:LivaChatApp:testDebugUnitTest`、`:LivaChatApp:assembleDebug`、`androidTest` 编译（真机 UI 走查另行执行）。Android 无 CI。

---

## 4. 布局、脚本与排版约束

| 语言/脚本 | 文本长度/方向 | 重点检查 |
|---|---|---|
| de | 最长（多复合词） | 设置行标题、按钮、chip、工具栏、通知长文案 |
| ru | 西里尔，较长 | 设置/按钮/chip 截断；月名变格 |
| fr/es/pt | 拉丁，较长 | 设置、picker、通知；fr 的 NBSP/« » |
| it/hi/id | 拉丁/天城文，中等 | hi 的连字/依附符/行高 |
| th | 泰文，无词间空格 | 组合符/断行、Dynamic Type、按钮/标签宽度；不要手动插空格 |
| vi | 拉丁 + 声调符号，较长 | 声调符号行高、截断、窄按钮；不要用 `uppercase()` 破坏声调 |
| ja/ko/zh | 东亚文字，通常较紧凑 | 与 en 混排时的行高、标点 |
| ar | RTL，阿文连写 | 全屏镜像、bidi 组合串、数字混排、固定 offset |

- **RTL**：`supportsRtl=true`；Compose 的 `Arrangement.Start/End`、`Alignment.CenterStart/End` 等方向 token 自动镜像。
- **3 处固定 `offset(x=)` 不镜像**（当前代码位置）：
  - `ui/screens/mylife/timeline/components/PeriodCard.kt:141`（角标）；
  - `ui/screens/conversation/ConversationScreen.kt:459`（头像栏 job 槽）；
  - `ui/screens/conversation/ConversationScreen.kt:1074`（头像栏）。
  这些是装饰性/位置性偏移，RTL 下保持 LTR 方向；修改布局时需真机确认。
- **天城文（hi）**：连字/依附符在 Canvas 气泡与窄按钮中的渲染/测量需真机确认。
- **法语排版**：`?`/`!`/`:` 前与 `«`/`»` 内侧 U+00A0；技术格式串（`ip:port`/`domain:port`）豁免。
- **阿拉伯数字**：资源侧 `%d` 天然阿数；纯函数/DateTimeFormatter 侧经 `localizeDigits`；资源字面数字必须直接写阿数。
- **24 小时制**：所有时间站点固定 `HH:mm` / `HH:mm:ss`，不随 locale 切 12 小时制。
- **布局敏感点**：设置页服务器选择卡片 `260.dp` 定宽、`BottomToolbar` 单行 caption、`GranularitySelector` 四格、`LanguagePickerSheet` 下拉、两条 FGS 通知长文案、`StatusIndicator`/模式标签/`WelcomeGuide`。

---

## 5. 维护与验收清单

- [ ] 新增/修改 key：16 个 `strings.xml` 全部补齐，`I18nResourceFilesTest` 双向 key 齐平通过。
- [ ] 新增/修改 plurals：按 locale 提供正确 quantity 集（ru 四形、ar 六形；zh/ja/ko/id/th/vi 仅 other）。
- [ ] 默认目录 `values/` 不含汉字；中文只在 Hans/Hant。
- [ ] 法语 `?`/`!`/`:` 的 U+00A0、« » 内侧空格、撇号 `\'` 转义。
- [ ] 阿语资源字面数字写阿数；纯函数/DateTimeFormatter 数字站点经 `localizeDigits`。
- [ ] 新语言补 `locales_config.xml` 条目；资源目录名遵循脚本/语言限定符规则（id 用 `values-in`）。
- [ ] 日期/周期：检查 `MylifeDateLabels`、`PeriodAggregator`、`OverviewSection`、`HistoryScreen` 的语言分支与回归锁。
- [ ] 后缀：`MessageBubble` 的 mode+role 映射、History 与 MyLife 同源、历史 `mode=null` 原样渲染。
- [ ] 通知：两条 FGS 的 channel/title/text 资源；切换语言后 Service 重启时刷新 channel 名。
- [x] 发图指令 `conv_image_action_text` 在 16 目录存在，屏幕抓取/图库两入口均使用资源（2026-10-27 已实施）。
- [ ] 跑 `:LivaChatApp:testDebugUnitTest`、`assembleDebug`、androidTest 编译；真机走查关键屏。

---

## 6. 已知限制

1. **服务端内容不翻译**：AI 回复、转录原文、MyLife 摘要、Agent 结果保持服务端语言。
2. **历史后缀可能混语言**：D4=B 之前的历史行 `mode=null`，后缀已写入正文；迁移后不拆分，旧行保留写入时语言。
3. **无 App 内 UI 语言切换**：只能跟随系统或 Android 13+ 系统「应用语言」页；App 设置页没有语言行。
4. **FGS channel 名刷新时点**：Service 运行期间切换语言，通知 channel 名要等下次 Service 启动才更新。
5. **`Locale.getDefault()` 语义**：translate/lecture 的 `deviceLanguage` 取进程默认语言；Android 13+ per-app locale 下可能随 App locale 变化，不能保证等于系统语言；当前未显式读取系统 locale。
6. **RTL 固定 offset**：3 处 `offset(x=)` 不随 RTL 镜像（§4），修改这些布局时需真机复核。
7. **24 小时制固定**：不提供 12 小时制/AM-PM 变体。
8. **技术标识与默认格式化**：版本号、IP/域名、端口、协议 code 保持原样；`formatTimecode`/`formatRemainingChars` 走默认 `.format`（ar 下天然阿数，但不受显式 `localizeDigits` 控制）。
9. **pt 单套巴葡**：`values-pt` 为 pt-BR；pt-PT 用户看到巴葡用词（tela/usuário 等）。
10. **zh-Hant 单套台灣用詞**：TW/HK/MO 共用；港式/澳门用词差异不单独覆盖。
11. **id 遗留码**：资源目录 `values-in`、localeConfig `id`；所有 id 日期分支用 `"id","in"` 双键防御。
12. **阿拉伯数字覆盖**：显式日期/时间/页码/耗时站点已接入 `localizeDigits`；新增站点若直接使用 `DateTimeFormatter` 或字面数字，需自行接入，否则会与资源侧阿数混排。
13. **畸形月份兜底**：`OverviewSection` 在 `yyyy-MM` 解析失败时回退中文 `"${y}年 · ${m}月"`（正常数据不可达）。
14. **残留中文字面量**：21 处均为有意保留（13 `@Preview` + 7 MyLife 模板/兜底 + 1 DI 崩溃文案）；原 2 处 App→AI 指令已于 2026-10-27 移除（见 §2.3.9/§2.4）。
15. **Android 无 CI**：i18n 守卫依赖本地 JVM 测试；提交前需手动跑全量门禁。
16. **泰语公历**：`th` 使用 Gregorian 公历（2026），不使用佛历（2569）；`java.time` 保持 ISO chronology，禁止引入 `ThaiBuddhistDate`。History LONG 由平台输出 `21 สิงหาคม ค.ศ. 2026`，属公历口径。

---

## 7. 定稿决策

| # | 决策 | 当前结果 |
|---|---|---|
| D1 | 资源目录 | 默认 `values/`=en；`values-b+zh+Hans`、`values-b+zh+Hant`；其余 `values-xx`；id 用 `values-in` |
| D2 | 资源载体 | 标准 `strings.xml` + `<plurals>`；无自研双语 Map |
| D3 | 解析位置 | Compose `stringResource`；Service/VM `getString`；纯函数返回资源 id/`DeviceLabelSpec` |
| D4 | 模式后缀 | 写侧存正文+`mode`（Room v9），展示侧按资源拼后缀；历史不回拆 |
| D5 | 日期/计数 | `Locale` 驱动日期；11 组 plurals；ar 数字助手 |
| D6 | 语言切换 | 仅系统/per-app（`localeConfig` 16 项）；无 App 内语言行 |
| D7 | translate 源语言 | 取 `Locale.getDefault().language`；期望为说话者/系统语言，与对方语言解耦；per-app locale 风险见 §6 |
| D8 | 不翻译边界 | 服务端内容、日志、预览、Mock/SDK、协议 code、其他 App→AI 指令（发图指令 `conv_image_action_text` 除外，见 §2.3.9） |
| D9 | 测试 | JVM 资源守卫 + 语言纯函数单测 + 资源解析式 UI 测试；本地门禁 |
| D10 | 发图指令与语言候选 | 屏幕抓取/图库共用 `conv_image_action_text`（16 locale，随 App 语言，英文兜底）；`LanguagePickerSheet` 当前 15 个具体语言候选（含 `it`、`hi`、`id`、`th`、`vi`；`auto` 另置顶）；2026-10-28 移除 `tr`。与 iOS §2.9 一致。 |
| D11 | th/vi | 已新增 `values-th/`、`values-vi/`（各 254 键）；`locales_config` 16 项；泰语 Gregorian；共享翻译以 Android 资源为事实源；服务端无白名单改动 |

**语言口径定稿**：

- 繁體：台灣正體＋台灣慣用詞；TW/HK/MO 共用；`週`。
- ja：です・ます調；星期 FULL；`第%1$d週`。
- ko：해요체；星期 FULL；`%1$d주차`。
- ru：中性礼貌（隐含 вы）；四形复数；月年 `FULL_STANDALONE`；周范围 `dd.MM`。
- de：中性、去主语、不定式优先；必要称呼用 Sie；最长文本。
- fr：中性、不定式优先、vouvoiement；NBSP/« »；0 归单数（CLDR）。
- it：中性、不定式优先、必要时 tu；0 归复数；星期 FULL 消 `mar` 歧义。
- es：中性、不定式优先、必要时 tú；`mar` 星期 FULL；`¿…?`；月年含 `de`。
- pt：pt-BR 单套；中性、不定式优先、必要时 você；月年含 `de`；星期 SHORT。
- hi：中性礼貌 `-ें`（आप 隐含）；one/other 名词同形、两处动词短语按数分形；天城文。
- id：中性祈使（动词裸形）；仅 other 复数；`values-in` + `"id","in"` 双键。
- ar：MSA 阳性单数祈使；六形复数；RTL；阿拉伯-印度数字全 UI 统一。
- th：中性礼貌（ครับ/ค่ะ 不代指用户；文案避免第一/第二人称）；仅 other 复数；Gregorian 公历；泰文无词间空格，不手动插空格。
- vi：中性、礼貌、无代词优先；仅 other 复数；月年用 `FULL_STANDALONE` + `năm`；声调符号保留原形。

---

## 8. 繁體中文（zh-Hant）

### 8.1 资源与格式

- `values-b+zh+Hant/`；`locales_config` 条目 `zh-TW`；plurals 仅 `other`。
- TW/HK/MO 共用同一套台灣正體＋台灣慣用詞。
- 日期：`yearText` `2026年`、`monthYearText` `2026年8月`、`monthDayText` `8月21日`；星期 SHORT（`週五`）；周范围 `8/17 – 8/23`；周徽标 `34週`；摘要 `共 N 天`。
- History 使用 CLDR LONG（`2026年8月21日`）+ 星期 FULL（`星期五`）。

### 8.2 特殊约束

- 与简体共享 `"zh"` 代码分支（年月日模板同形）；CLDR/资源输出繁体字形与「週」。
- 无 NBSP/撇号特殊面；`supportsRtl` 不适用。
- 后缀资源：`(轉錄)` / `(原文)` / `(譯文)`。

### 8.3 关键术语

| 简体 | 繁體（台灣正體＋慣用詞） |
|---|---|
| 时光机 | 時光機 |
| 全模态 | 全模態 |
| 转录 | 轉錄 |
| 同声翻译 | 即時翻譯 |
| 对方语言 | 對方語言 |
| 设置 / 服务器 / 认证密钥 | 設定 / 伺服器 / 驗證金鑰 |
| 屏幕 / 悬浮窗 / 屏幕交互 | 螢幕 / 懸浮視窗 / 螢幕互動 |
| 麦克风 / 扬声器 / 摄像头 | 麥克風 / 喇叭 / 鏡頭 |
| 对话历史 | 對話紀錄 |

### 8.4 测试与维护

- `MylifeDateLabelsTest` 的 `zh-TW (Hant) behavior unchanged` 回归锁；`I18nResourceFilesTest` 的 Hant key/plural 守卫。
- 修改简体日期模板时，必须同时确认 Hant 输出（共用 `"zh"` 分支）。

---

## 9. 日本語（ja）

### 9.1 资源与格式

- `values-ja/`；`locales_config` 条目 `ja`；plurals 仅 `other`。
- 日期：`2026年` / `2026年8月` / `8月21日`；星期 **FULL**（`金曜日`）；周范围 `8/17 – 8/23`；周徽标 `第34週`；摘要 `・計 N 日`。
- History：CLDR LONG + 星期 FULL。

### 9.2 文体与特殊约束

- 文体：**です・ます調**。
- 星期用 FULL 是为了避免 SHORT 单字（「月」）与月份歧义。
- 后缀资源：`(文字起こし)` / `(原文)` / `(翻訳)`。
- 无 NBSP/撇号特殊面。

### 9.3 关键术语

| 中文 | 日本語 |
|---|---|
| 时光机 | タイムマシン |
| 全模态 | オムニ |
| 转录 / 转录模式 | 文字起こし / 文字起こしモード |
| 同声翻译 | リアルタイム翻訳 |
| 对方语言 | 相手の言語 |
| 演讲者（人）/ 扬声器（设备） | 話者 / スピーカー |
| 设置 / 服务器 / 认证密钥 | 設定 / サーバー / 認証キー |
| 屏幕 / 悬浮窗 / 屏幕交互 | 画面 / フローティングバブル / 画面インタラクション |
| 麦克风 / 扬声器 / 摄像头 | マイク / スピーカー / カメラ |
| 对话历史 | 会話履歴 |

### 9.4 测试与维护

- `MylifeDateLabelsTest` ja 用例（CJK 模板 + FULL 星期）；`PeriodAggregatorTest` ja 摘要。
- 修改 zh 日期模板时同步检查 ja（共用 `"zh","ja"` 分支）。

---

## 10. 한국어（ko）

### 10.1 资源与格式

- `values-ko/`；`locales_config` 条目 `ko`；plurals 仅 `other`。
- 日期：`2026년` / `2026년 8월` / `8월 21일`；星期 **FULL**（`금요일`）；周范围 `8/17 – 8/23`；周徽标 `34주차`；摘要 `・총 N일`。
- History：CLDR LONG + 星期 FULL。

### 10.2 文体与特殊约束

- 文体：**해요체（친근한 존댓말）**。
- 星期 FULL 避免 SHORT 单字（「월」）与月份歧义。
- 后缀资源：`(전사)` / `(원문)` / `(번역)`。
- 无 NBSP/撇号特殊面。

### 10.3 关键术语

| 中文 | 한국어 |
|---|---|
| 时光机 | 타임머신 |
| 全模态 | 옴니 |
| 转录 / 转录模式 | 전사 / 전사 모드 |
| 同声翻译 | 실시간 번역 |
| 对方语言 | 상대방 언어 |
| 演讲者（人）/ 扬声器（设备） | 화자 / 스피커 |
| 设置 / 服务器 / 认证密钥 | 설정 / 서버 / 인증 키 |
| 屏幕 / 悬浮窗 / 屏幕交互 | 화면 / 플로팅 버블 / 화면 인터랙션 |
| 麦克风 / 扬声器 / 摄像头 | 마이크 / 스피커 / 카메라 |
| 对话历史 | 대화 기록 |

### 10.4 测试与维护

- `MylifeDateLabelsTest` ko 用例；`PeriodAggregatorTest` ko 摘要。
- 修改日期模板时保持 `"ko"` 独立分支。

---

## 11. Русский（ru）

### 11.1 资源与格式

- `values-ru/`；`locales_config` 条目 `ru`；plurals `one/few/many/other`。
- 日期：日先月后 `21 авг.`；月年 `август 2026`（`FULL_STANDALONE` 主格，避免属格「августа」）；星期 SHORT（`пт`）；周范围 `17.08 – 23.08`；周徽标 `Неделя 34`；摘要 `· всего N день/дня/дней`。
- History：CLDR LONG（`21 августа 2026 г.`）+ 星期 FULL。

### 11.2 文体与特殊约束

- 语域：中性礼貌（隐含「вы」）。
- 月名有格变：日卡用 SHORT（属格/缩写），月年用 `FULL_STANDALONE` 主格。
- 复数四形；`ruDayWord` 纯函数处理 `день/дня/дней`（n%10==1 且 n%100!=11；n%10∈2..4 且 n%100∉12..14；其余）。
- 后缀资源：`(расшифровка)` / `(оригинал)` / `(перевод)`。
- 文本较长，注意西里尔布局。

### 11.3 关键术语

| 中文 | Русский |
|---|---|
| 时光机 | Машина времени |
| 全模态 | Омни |
| 转录 / 转录模式 | расшифровка / Режим расшифровки |
| 同声翻译 | синхронный перевод |
| 对方语言 | язык собеседника |
| 演讲者（人）/ 扬声器（设备） | спикер / динамик |
| 设置 / 服务器 / 认证密钥 | Настройки / Сервер / ключ авторизации |
| 屏幕 / 悬浮窗 / 屏幕交互 | экран / плавающий пузырь / взаимодействие с экраном |
| 麦克风 / 扬声器 / 摄像头 | микрофон / динамик / камера |
| 对话历史 | История чатов |

### 11.4 复数与测试（Q4）

- 11 组 plurals 均为 one/few/many/other；`I18nResourceFilesTest` 固化 `ru={one,few,many,other}`。
- `MylifeDateLabelsTest` ru 三用例（日先月后 / 主格月年 / 星期 SHORT）；`PeriodAggregatorTest` ru 三形摘要与周范围。

---

## 12. Deutsch（de）

### 12.1 资源与格式

- `values-de/`；`locales_config` 条目 `de`；plurals one/other。
- 日期：日先月后 `21. Aug.`（日带尾点）；月年 `August 2026`；星期 SHORT（`Fr.`）；周范围 `17.08. – 23.08.`（带尾点）；周徽标 `KW 34`；摘要 `· N Tag/Tage`。
- History：CLDR LONG（`21. August 2026`）+ 星期 FULL。

### 12.2 文体与特殊约束

- 语域：中性、去主语优先、不定式短句；必要称呼用 Sie。
- 月名无变格；SHORT/FULL 格式与独立语境同形。
- 德语为当前最长文本语言（多复合词），布局优先短词短句。
- 后缀资源：`(Transkript)` / `(Original)` / `(Übersetzung)`。

### 12.3 关键术语

| 中文 | Deutsch |
|---|---|
| 时光机 | Zeitmaschine |
| 全模态 | Omni |
| 转录 / 转录模式 | Transkript / Transkriptionsmodus |
| 同声翻译 | Live-Übersetzung |
| 对方语言 | Sprache des Gesprächspartners |
| 演讲者（人）/ 扬声器（设备） | Sprecher / Lautsprecher |
| 设置 / 服务器 / 认证密钥 | Einstellungen / Server / Authentifizierungsschlüssel |
| 屏幕 / 悬浮窗 / 屏幕交互 | Bildschirm / schwebende Blase / Bildschirm-Interaktion |
| 麦克风 / 扬声器 / 摄像头 | Mikrofon / Lautsprecher / Kamera |
| 对话历史 | Chat-Verlauf |

### 12.4 测试与维护

- `MylifeDateLabelsTest` de 用例（日先月后 + 全称月年 + SHORT 星期）；`PeriodAggregatorTest` de Tag/Tage 与 `dd.MM.` 周范围。
- 布局走查优先设置/按钮/chip/工具栏。

---

## 13. Français（fr）

### 13.1 资源与格式

- `values-fr/`；`locales_config` 条目 `fr`；plurals one/other（CLDR one={0,1}，0 用单数）。
- 日期：日先月后 `21 août`；月年 `août 2026`（FULL 小写）；星期 SHORT（`ven.`）；周范围 `17/08 – 23/08`；周徽标 `S34`；摘要 `· N jour/jours`。
- History：CLDR LONG（`21 août 2026`）+ 星期 FULL。

### 13.2 文体与特殊约束

- 语域：中性、不定式优先、vouvoiement（vous，必要称呼处）。
- 法语排版：`?`/`!`/`:` 前与 `«`/`»` 内侧使用 U+00A0；技术格式串（含 `port`）豁免；撇号全量 `\'` 转义。
- 文本中等偏长。
- 后缀资源：`(transcription)` / `(original)` / `(traduction)`。

### 13.3 关键术语

| 中文 | Français |
|---|---|
| 时光机 | Machine à remonter le temps |
| 全模态 | Omni |
| 转录 / 转录模式 | transcription / Mode transcription |
| 同声翻译 | Traduction en direct |
| 对方语言 | Langue de l'interlocuteur |
| 演讲者（人）/ 扬声器（设备） | locuteur / haut-parleur |
| 设置 / 服务器 / 认证密钥 | Paramètres / Serveur / clé d'authentification |
| 屏幕 / 悬浮窗 / 屏幕交互 | écran / bulle flottante / interaction avec l'écran |
| 麦克风 / 扬声器 / 摄像头 | micro / haut-parleur / caméra |
| 对话历史 | Historique des conversations |

### 13.4 排版与测试（Q4）

- `I18nResourceFilesTest.frenchTypographyUsesNbsp`：`?`/`!` 必查；`:` 文案句检查、含 `port` 的格式串豁免；兼容 ` ` 与字面 NBSP。
- `MylifeDateLabelsTest` fr 用例；`PeriodAggregatorTest` fr jour/jours 与 `dd/MM` 周范围。
- 撇号转义由 aapt 编译兜底；新增文案需检查 `'`。

---

## 14. Italiano（it）

### 14.1 资源与格式

- `values-it/`；`locales_config` 条目 `it`；plurals one/other（CLDR one={1}，0 用复数）。
- 日期：日先月后 `21 ago`；月年 `agosto 2026`（FULL 小写）；星期 **FULL**（`venerdì`，消 `mar` 歧义）；周范围 `17/08 – 23/08`；周徽标 `S34`；摘要 `· N giorno/giorni`。
- History：CLDR LONG（`21 agosto 2026`）+ 星期 FULL。

### 14.2 文体与特殊约束

- 语域：中性、不定式优先、必要时 tu。
- `mar`（martedì）与 `mar`（marzo）SHORT 同形；星期走 FULL。其余星期无碰撞。
- 撇号全量 `\'` 转义；`« »` 意式引号（普通空格，无 NBSP 规则）。
- 文本中等。
- 后缀资源：`(trascrizione)` / `(originale)` / `(traduzione)`。

### 14.3 关键术语

| 中文 | Italiano |
|---|---|
| 时光机 | Macchina del tempo |
| 全模态 | Omni |
| 转录 / 转录模式 | trascrizione / Modalità trascrizione |
| 同声翻译 | Traduzione in tempo reale |
| 对方语言 | Lingua dell'interlocutore |
| 演讲者（人）/ 扬声器（设备） | relatore / altoparlante |
| 设置 / 服务器 / 认证密钥 | Impostazioni / Server / chiave di autenticazione |
| 屏幕 / 悬浮窗 / 屏幕交互 | schermo / bolla flottante / interazione con lo schermo |
| 麦克风 / 扬声器 / 摄像头 | microfono / altoparlante / fotocamera |
| 对话历史 | Cronologia chat |

### 14.4 测试与维护

- `MylifeDateLabelsTest` it 用例（FULL 星期消歧）；`PeriodAggregatorTest` it giorno/giorni、周范围、`21 mar · martedì`。
- 撇号转义由 aapt 兜底；新增文案检查 `'`。

---

## 15. Español（es）

### 15.1 资源与格式

- `values-es/`；`locales_config` 条目 `es`；plurals one/other（0 用复数）。
- 日期：日先月后 `21 ago`；月年 `agosto de 2026`（含 `de`）；星期 **FULL**（`viernes`，消 `mar` 歧义）；周范围 `17/08 – 23/08`；周徽标 `S34`；摘要 `· N día/días`。
- History：CLDR LONG（`21 de agosto de 2026`）+ 星期 FULL。

### 15.2 文体与特殊约束

- 语域：中性、不定式优先、必要时 tú。
- 疑问句全句 `¿…?`；`« »` 西式引号（普通空格）。
- 无撇号转义面、无 NBSP 面。
- 文本中等。
- 后缀资源：`(transcripción)` / `(original)` / `(traducción)`。

### 15.3 关键术语

| 中文 | Español |
|---|---|
| 时光机 | Máquina del tiempo |
| 全模态 | Omni |
| 转录 / 转录模式 | transcripción / Modo transcripción |
| 同声翻译 | Traducción en tiempo real |
| 对方语言 | Idioma del interlocutor |
| 演讲者（人）/ 扬声器（设备） | orador / altavoz |
| 设置 / 服务器 / 认证密钥 | Ajustes / Servidor / clave de autenticación |
| 屏幕 / 悬浮窗 / 屏幕交互 | pantalla / burbuja flotante / interacción con la pantalla |
| 麦克风 / 扬声器 / 摄像头 | micrófono / altavoz / cámara |
| 对话历史 | Historial de chats |

### 15.4 测试与维护

- `MylifeDateLabelsTest` es 用例（`de` 月年 + FULL 星期）；`PeriodAggregatorTest` es día/días、周范围、`17 mar · martes`。
- 新增问句必须检查 `¿` 是否存在。

---

## 16. Português（pt）

### 16.1 资源与格式

- `values-pt/`；`locales_config` 条目 `pt`；plurals one/other（0 用复数）。
- **pt-BR 单套**：pt-PT 设备同样显示巴葡用词（tela/usuário/câmera 等）。
- 日期：日先月后 `21 ago.`（SHORT 自带点）；月年 `agosto de 2026`（含 `de`）；星期 SHORT（`sex.`）；周范围 `17/08 – 23/08`；周徽标 `S34`；摘要 `· N dia/dias`。
- History：CLDR LONG（`21 de agosto de 2026`）+ 星期 FULL。

### 16.2 文体与特殊约束

- 语域：中性、不定式优先、必要时 você（巴葡）。
- 无 `mar` 星期/月份碰撞（周二 `ter` ≠ 三月 `mar.`）；星期保持 SHORT。
- 无撇号/NBSP 面；`« »` 引号（普通空格）。
- 文本中等。
- 后缀资源：`(transcrição)` / `(original)` / `(tradução)`。

### 16.3 关键术语

| 中文 | Português（pt-BR） |
|---|---|
| 时光机 | Máquina do tempo |
| 全模态 | Omni |
| 转录 / 转录模式 | transcrição / Modo transcrição |
| 同声翻译 | Tradução em tempo real |
| 对方语言 | Idioma do interlocutor |
| 演讲者（人）/ 扬声器（设备） | orador / alto-falante |
| 设置 / 服务器 / 认证密钥 | Configurações / Servidor / chave de autenticação |
| 屏幕 / 悬浮窗 / 屏幕交互 | tela / bolha flutuante / interação com a tela |
| 麦克风 / 扬声器 / 摄像头 | microfone / alto-falante / câmera |
| 对话历史 | Histórico de conversas |

### 16.4 测试与维护

- `MylifeDateLabelsTest` pt 用例（`de` 月年 + 星期 SHORT 回归锁）；`PeriodAggregatorTest` pt dia/dias、周范围。
- pt-PT 用词差异不单独建目录；如需欧葡另加 `values-pt-rPT`。

---

## 17. Türkçe（tr）

> **2026-10-28：已移除。** `values-tr/` 与 `locales_config` 条目已删除，App UI 对 tr 系统语言回落英文，translate 候选不再包含 `tr`。以下 17.x 内容仅作历史记录；`MylifeDateLabels`/`PeriodAggregator` 中的 tr 格式化分支因仍可能被设备 locale 命中而保留。

### 17.1 资源与格式

- ~~`values-tr/`；`locales_config` 条目 `tr`~~（已删除）；plurals **仅 other**。
- 日期：日先月后 `21 Ağu`；月年 `Ağustos 2026`（首字母大写）；星期 SHORT（`Cum`）；周范围 `17.08 – 23.08`（点分无尾点）；周徽标 `34. Hafta`；摘要 `· N gün`（恒同形）。
- History：CLDR LONG（`21 Ağustos 2026`）+ 星期 FULL。

### 17.2 文体与特殊约束

- 语域：中性祈使式（动词裸形，无称呼）。
- 数字后名词不带复数标记（`2 mesaj`、`5 gün`）；plurals 仅 other。
- 月名/星期/语言名首字母大写。
- 专有名词格后缀撇号需转义（`LivaChat\'e`）。
- 无 NBSP 面。
- 后缀资源：`(transkript)` / `(orijinal)` / `(çeviri)`。

### 17.3 关键术语

| 中文 | Türkçe |
|---|---|
| 时光机 | Zaman Makinesi |
| 全模态 | Omni |
| 转录 / 转录模式 | transkript / Transkript modu |
| 同声翻译 | Gerçek zamanlı çeviri |
| 对方语言 | Karşı tarafın dili |
| 演讲者（人）/ 扬声器（设备） | konuşmacı / hoparlör |
| 设置 / 服务器 / 认证密钥 | Ayarlar / Sunucu / kimlik doğrulama anahtarı |
| 屏幕 / 悬浮窗 / 屏幕交互 | ekran / yüzen balon / ekran etkileşimi |
| 麦克风 / 扬声器 / 摄像头 | mikrofon / hoparlör / kamera |
| 对话历史 | Sohbet geçmişi |

### 17.4 测试与维护

- `MylifeDateLabelsTest` tr 用例（大写月年 + SHORT 星期）；`PeriodAggregatorTest` tr gün 恒同形、`dd.MM` 周范围。
- 新增专有名词/格后缀文案时检查撇号转义。

---

## 18. हिन्दी（hi）

### 18.1 资源与格式

- `values-hi/`；`locales_config` 条目 `hi`；plurals one/other（名词短语同形）。
- 日期：日先月后 `21 अग॰`（SHORT 含天城文缩写符 `॰`）；月年 `अगस्त 2026`（FULL）；星期 SHORT（`शुक्र`）；周范围 `17/08 – 23/08`；周徽标 `सप्ताह 34`；摘要 `· कुल N दिन`（恒同形）。
- History：CLDR LONG（`21 अगस्त 2026`）+ 星期 FULL。

### 18.2 文体与特殊约束

- 语域：中性礼貌 `-ें` 祈使式（आप 隐含）。
- 数词后名词不变格：one/other 名词短语同形；两处动词短语按数分形（`mylife_photos_deleted`、`mylife_delete_confirm`）。
- 天城文：连字/依附符在 Canvas 气泡与窄按钮中需真机确认渲染/测量。
- 标点用西式 `?`/`.`；无撇号/NBSP 面；拉丁数字。
- 后缀资源：`(प्रतिलेख)` / `(मूल)` / `(अनुवाद)`。

### 18.3 关键术语

| 中文 | हिन्दी |
|---|---|
| 时光机 | टाइम मशीन |
| 全模态 | ओमनी |
| 转录 / 转录模式 | प्रतिलेख / प्रतिलेख मोड |
| 同声翻译 | लाइव अनुवाद |
| 对方语言 | सामने वाले की भाषा |
| 演讲者（人）/ 扬声器（设备） | वक्ता / स्पीकर |
| 设置 / 服务器 / 认证密钥 | सेटिंग्स / सर्वर / प्रमाणीकरण कुंजी |
| 屏幕 / 悬浮窗 / 屏幕交互 | स्क्रीन / फ़्लोटिंग बबल / स्क्रीन इंटरैक्शन |
| 麦克风 / 扬声器 / 摄像头 | माइक्रोफ़ोन / स्पीकर / कैमरा |
| 对话历史 | चैट इतिहास |

### 18.4 测试与维护

- `MylifeDateLabelsTest` hi 用例（日先月后 + 全称月年 + SHORT 星期）；`PeriodAggregatorTest` hi `कुल N दिन`、周范围。
- 新增天城文文案时真机检查连字/行高/窄按钮测量。

---

## 19. Bahasa Indonesia（id）

### 19.1 资源与格式

- **资源目录 `values-in/`**（Android 遗留限定符）；`locales_config` 条目 `id`；plurals 仅 other。
- 代码中所有 id 日期分支同时匹配 `"id"` 与 `"in"`；测试同时覆盖 `Locale("id")`/`Locale("in")`。
- 日期：日先月后 `21 Agu`；月年 `Agustus 2026`（首字母大写）；星期 SHORT（`Jum`）；周范围 `17/08 – 23/08`；周徽标 `Pekan 34`（避 `Minggu`=周日）；摘要 `· total N hari`（恒同形）。
- History：CLDR LONG（`21 Agustus 2026`）+ 星期 FULL。

### 19.2 遗留码实现

- Android 资源限定符官方表使用 `in`；BCP-47/localeConfig 使用 `id`。当前 `values-in` 已在真机/探针确认被 `id`/`in`/`id-rID` 命中。
- 日期/月段代码分支统一写 `"id", "in" ->`；新增 id 相关分支必须保持双键。
- 无 `values-id` 目录。
- 后缀资源：`(transkrip)` / `(asli)` / `(terjemahan)`。

### 19.3 关键术语

| 中文 | Bahasa Indonesia |
|---|---|
| 时光机 | Mesin Waktu |
| 全模态 | Omni |
| 转录 / 转录模式 | transkrip / Mode transkrip |
| 同声翻译 | Terjemahan langsung |
| 对方语言 | Bahasa lawan bicara |
| 演讲者（人）/ 扬声器（设备） | pembicara / pengeras suara |
| 设置 / 服务器 / 认证密钥 | Setelan / Server / kunci autentikasi |
| 屏幕 / 悬浮窗 / 屏幕交互 | layar / balon mengambang / interaksi layar |
| 麦克风 / 扬声器 / 摄像头 | mikrofon / pengeras suara / kamera |
| 对话历史 | Riwayat chat |

### 19.4 测试与维护

- `MylifeDateLabelsTest` id 用例含双键归一锁；`PeriodAggregatorTest` id `total N hari`、周范围、`Locale("in")` 孪生。
- 新增 id 日期分支必须写 `"id", "in"`；资源目录保持 `values-in`。

---

## 20. العربية（ar）

### 20.1 资源、日期、复数与数字

- `values-ar/`；`locales_config` 条目 `ar`；plurals 六形：`zero/one/two/few/many/other`。
- RTL：`supportsRtl=true`；Compose start/end 自动镜像。
- 日期：日先月后 `٢١ أغسطس`；月年 `أغسطس ٢٠٢٦`（SHORT==FULL，无需结构分支）；星期 SHORT（`الجمعة`）；周范围 `١٧/٠٨ – ٢٣/٠٨`；周徽标 `الأسبوع ٣٤`；摘要 `· إجمالي N يوم/يومان/أيام/يومًا/يوم`。
- History：CLDR LONG（`٢١ أغسطس ٢٠٢٦`）+ 星期 FULL；日期数字经 `localizeDigits`。
- 六形规则（`arDayWord`）：
  - n == 2 → `يومان`；
  - n % 100 ∈ 3..10 → `أيام`（few）；
  - n % 100 ∈ 11..99 → `يومًا`（many）；
  - 其余（0/1/100/101/102…）→ `يوم`。
- 资源字面数字必须写阿拉伯-印度数字（`I18nResourceFilesTest.arabicLiteralDigitsUseArabicIndic`）。

### 20.2 RTL 与数字本地化实现

- `ui/util/LocaleDigits.localizeDigits(text, locale)`：仅 ar 将 ASCII `0-9` 映射为 U+0660-0669；其余 locale 恒等、幂等。
- 当前接入点：
  - `MylifeDateLabels` 的 `yearText`/`monthYearText`/`monthDayText`；
  - `PeriodAggregator` 的 ar 周范围与摘要；
  - `HistoryScreen` 日期；
  - `HotSlotRows` 行时间/标题、`HotSlotChips`；
  - `SearchResultList` 时间；
  - `AudioGalleryUiState.segmentTimeLabels`；
  - `AgentJobListPanel` 开始时间/耗时；
  - `AgentJobResultOverlay` 时间；
  - `MessageBubble` 时间；
  - `ImageViewerOverlay`/`FrameViewerOverlay` 页码。
- 默认 `String.format` 类站点（`formatTimecode` 的 `%d:%02d`、`AgentJobResultText` 的 `%,d`）在 ar 设备上天然输出阿数，无需显式助手；技术标识（版本号/IP/域名）保留拉丁。
- 固定 `offset(x=)` 3 处不镜像：`PeriodCard.kt:141`、`ConversationScreen.kt:459`、`ConversationScreen.kt:1074`；修改时需真机复核 RTL。
- bidi 组合串（日卡「٢١ أغسطس · الجمعة」、周范围）按逻辑串渲染；改动日期/数字拼接时需真机确认视觉顺序。

### 20.3 关键术语

| 中文 | العربية（MSA） |
|---|---|
| 时光机 | آلة الزمن |
| 全模态 | أومني |
| 转录 / 转录模式 | نسخ / وضع النسخ |
| 同声翻译 | الترجمة الفورية |
| 对方语言 | لغة المحاور |
| 演讲者（人）/ 扬声器（设备） | المتحدث / مكبر الصوت |
| 设置 / 服务器 / 认证密钥 | الإعدادات / الخادم / مفتاح المصادقة |
| 屏幕 / 悬浮窗 / 屏幕交互 | الشاشة / الفقاعة العائمة / التفاعل مع الشاشة |
| 麦克风 / 扬声器 / 摄像头 | الميكروفون / مكبر الصوت / الكاميرا |
| 对话历史 | سجل المحادثات |

### 20.4 文体与决策

- 语域：MSA 中性祈使；阳性单数祈使为默认中性形。
- 数字口径：阿拉伯-印度数字全 UI 统一；资源侧 `%d` 天然阿数，纯函数/DateTimeFormatter 侧经助手。
- RTL 口径：Compose 方向 token 自动镜像；3 处固定 offset 保持现状，改动时按实况决定是否 direction-aware 改写。
- 后缀资源：`(نسخ)` / `(الأصل)` / `(ترجمة)`。
- 标点：西式 `?`/`،`；无撇号/NBSP 面。

### 20.5 测试与维护

- `LocaleDigitsTest` 5 例（映射/区域变体/幂等/混排/非 ar 恒等）。
- `MylifeDateLabelsTest` ar 用例（日先月后 + 阿数 + SHORT 星期）。
- `PeriodAggregatorTest` ar 六形边界（0/1/2/3/11/99/100/103/111）+ 周范围/日卡。
- 时间站点断言：`AgentJobTimeFormatTest`、`SegmentTimeLabelsTest`、`HotSlotChipLabelTest`、`HotSlotRowsTest` 等覆盖 `٠٩:١٢` 等阿数输出。
- `I18nResourceFilesTest` 六形 quantity 集 + 阿语资源字面数字守卫。
- 真机走查重点：RTL 全屏镜像、bidi 组合串、数字混排、3 处固定 offset、阿文 shaping、六形复数边界。

---

## 21. ไทย（th）与 Tiếng Việt（vi）语言轮（2026-10-27 已实施，待 review）

> 状态：**已实施（2026-10-27），待 review**。当轮 **17 locale**（App UI + translate）；泰语使用 **Gregorian 公历**。代码、资源、单测与构建已完成；真机切语言与 translate/TTS 联调待 review 后执行。
>
> **后续（2026-10-28）**：`tr` 已移除，当前 **16 locale / 15 个具体语言候选（`auto` 另置顶）**；本节其余数字为当轮历史记录。

### 21.1 范围与决策

| 项 | 结论 |
|---|---|
| App UI | 15 → 17：新增 `th`、`vi` |
| translate 候选 | 14 → 16 个具体语言：`zh,en,ja,ko,fr,it,de,es,pt,ru,tr,hi,id,th,vi,ar`（`auto` 另置顶） |
| `locales_config` | 15 → 17 项 |
| 新增 key | 无；仍为 243 条 `<string>` + 11 组 `<plurals>` |
| 泰语日历 | **Gregorian**（公历；不使用佛历） |
| `th` | **重新加入** App UI 与 translate，覆盖 2026-10-27 早前“移除 th”的决定 |
| `vi` | 新增 App UI 与 translate |
| 翻译来源 | 专业翻译 + 母语 review；同一批次覆盖两端共享 key |
| 跨端事实源 | Android `values-th`/`values-vi` 作为 184 个共享 key 的事实源；iOS 的 51 个 iOS-only + forced `conv_avatar_save_failed`（52 个 authored 条目）另行翻译 |
| 服务端 | 无白名单改动（`_normalize_lang` 已接受 2–3 位 ISO 码）；补 prompt 示例并验证 LLM/TTS |

- 本轮已完成 Android；iOS 已有同语言计划（`docs/ios_multilingual.md` §2.9），两端目标一致。
- 已按 Android 发布流程 bump `versionCode` **137 → 138**；`versionName` 保持 `1.0.0`。

### 21.2 工作量

- 每个新 locale：**243 条 `<string>` + 11 组 `<plurals>` = 254 个资源值**；th/vi 的 plurals 均只有 `other`。
- 两个 locale 合计 **508 个新资源值**。
- 原 15 个 `values*/strings.xml` 未改；新增 `values-th/strings.xml`、`values-vi/strings.xml`。
- 配置：`locales_config.xml` +2；`LanguagePickerSheet.languageCodes` +2。
- 代码：`MylifeDateLabels`、`PeriodAggregator`、`OverviewSection` 的 th/vi 分支；无布局重构。
- 测试：资源守卫、日期/周期单测、picker 测试、History 日期与真机走查。

### 21.3 翻译来源与跨端协作

- `values-th`/`values-vi` 已在 Android 落地；同一批次覆盖两端 key 并集：**184 shared + 70 Android-only + 51 iOS-only = 305 key/locale**；Android 落其中 254 个，iOS 落 235 个。
- Android 的资源已可作为事实源；iOS 可从 `values-th`/`values-vi` 导入 184 个 shared key，iOS 的 51 个 iOS-only + forced `conv_avatar_save_failed` 单独翻译，避免两端同一 key 文案漂移。iOS 文档 §2.9 已按此更新工作量（Android 先落地）。
- 术语沿用现有规范：产品名 `LivaChat`、`Agent`、`Omni`、`MyLife` 等不翻译；日期/错误/模式后缀按本地语言表达。
- `conv_image_action_text` 已落值（母语 review 仍待执行）：
  - `th`：`อธิบายเนื้อหาในภาพ`
  - `vi`：`Mô tả nội dung hình ảnh`

### 21.4 日期 / 数字 / 复数设计

| 项 | `th` | `vi` |
|---|---|---|
| 资源目录 | `values-th/` | `values-vi/` |
| plurals | only `other` | only `other` |
| 年 | 2026（Gregorian） | 2026（Gregorian） |
| 月年 | `สิงหาคม 2026` | `Tháng 8 năm 2026` |
| 月日 | `21 ส.ค.`（SHORT） | `21 thg 8`（SHORT） |
| 星期 | SHORT：`ศ.`（周五） | FULL：`Thứ Sáu`（周五） |
| 周范围 | `17/08 – 23/08` | `17/08 – 23/08` |
| 周徽标 | `สัปดาห์ที่ 34` | `Tuần 34` |
| 摘要兜底 | `· 5 วัน` | `· 5 ngày` |
| History LONG（示例） | `21 สิงหาคม ค.ศ. 2026` | `21 tháng 8, 2026` |
| History 星期 | `วันศุกร์`（FULL） | `Thứ Sáu`（FULL） |
| 数字 | 拉丁数字 | 拉丁数字 |
| 方向 | LTR | LTR |

> History LONG 为 JDK 17 CLDR 实测示例；Android ICU 版本可能对 `ค.ศ.`、逗号等细节有差异，验收以**年份为 Gregorian（2026，非 2569）**且日期/星期本地化为准。

与 iOS 的差异：iOS 的日期模板主要在 `Localizable.xcstrings`，Android 的月年/月日/星期/周范围/摘要是 `MylifeDateLabels`、`PeriodAggregator`、`OverviewSection` 的 Kotlin 分支；新增 th/vi 不能只加资源，必须同时改代码并加纯函数单测。

实现分支：

- `MylifeDateLabels.kt`
  - `monthYearText`：`"th"` → `"${FULL} $year"`；`"vi"` → `"${FULL_STANDALONE} năm $year"`。
  - `monthDayText`：`"th"`/`"vi"` → `"$day ${SHORT}"`。
  - `weekdayText`：把 `"vi"` 加入 FULL 列表（`Thứ Sáu`）；`th` 保持默认 SHORT（`ศ.`）。
  - `yearText`：走默认 `"$year"`，无需分支。
- `PeriodAggregator.kt`
  - `weekLabel`：把 `"th"`、`"vi"` 加入 `dd/MM – dd/MM` 斜线分支。
  - `summarize` 兜底：`th` → `"$rangeLabel · $n วัน"`；`vi` → `"$rangeLabel · $n ngày"`。
- `OverviewSection.MonthHeader`
  - `"th"` 加入 FULL 分支；`"vi"` 使用 `FULL_STANDALONE`（`Tháng 8`）。
  - 资源模板：`values-th` 的 `mylife_overview_month`=`%2$s %1$d`；`values-vi` 的 `mylife_overview_month`=`%2$s năm %1$d`。
- `LocaleDigits`：不新增映射；th/vi 使用拉丁数字。
- 时间：继续固定 24 小时制 `HH:mm`/`HH:mm:ss`。

**泰历口径**：全程使用 `java.time` 的 ISO chronology（`LocalDate`），禁止 `ThaiBuddhistDate`/佛历转换；`yearText` 输出 2026，而不是 2569。`HistoryScreen` 的 `DateTimeFormatter.ofLocalizedDate(LONG)` 在 JDK 17 的 th 下输出 `21 สิงหาคม ค.ศ. 2026`，年份仍是公历；`ค.ศ.` 是泰语对公元的标注，符合本决策。Android ICU 版本可能省略/保留 `ค.ศ.`，但年份不得变为 2569。

### 21.5 资源、picker 与配置

- 已新增 `values-th/strings.xml`、`values-vi/strings.xml`：254 键齐平；11 组 plurals 仅 `other`。
- 周徽标资源：
  - `values-th`：`mylife_week_label` = `สัปดาห์ที่ %1$d`
  - `values-vi`：`mylife_week_label` = `Tuần %1$d`
- `locales_config.xml` 已加入 `<locale android:name="th" />`、`<locale android:name="vi" />`，共 17 项。
- `LanguagePickerSheet.kt` 的 `languageCodes` 已加入 `th`、`vi`，顺序与 iOS 计划一致：`zh,en,ja,ko,fr,it,de,es,pt,ru,tr,hi,id,th,vi,ar`；显示名继续由 `Locale.forLanguageTag(code).getDisplayLanguage(uiLocale)` 生成，不新增文案 key。
- `conv_image_action_text` 两处入口无需改代码，只补 th/vi 资源；屏幕抓取与图库继续共用同一 key。
- `I18nResourceFilesTest` 已加入 `values-th`/`values-vi` 的文件与 quantity 断言；`values/` 无汉字、法语 NBSP、阿语数字等既有守卫保留。

### 21.6 测试与验收

- `I18nResourceFilesTest`：17 目录 key 双向齐平；th/vi plurals 仅 `other`；`values/` 无汉字；现有 fr/ar 守卫不回归。
- `MylifeDateLabelsTest`：
  - th：`yearText=2026`、`monthYearText=สิงหาคม 2026`、`monthDayText=21 ส.ค.`、周一 `จ.`、周五 `ศ.`。
  - vi：`monthYearText=Tháng 8 năm 2026`、`monthDayText=21 thg 8`、周一 `Thứ Hai`、周五 `Thứ Sáu`。
- `PeriodAggregatorTest`：
  - th/vi 周范围 `17/08 – 23/08`；
  - th 摘要 `· 5 วัน`、vi `· 5 ngày`；
  - 日卡标签含本地化星期；月/年卡标签与 `MylifeDateLabels` 一致。
- `LanguagePickerSheetTest`：精确断言 16 个 code，包含 `th`/`vi`，顺序与设计一致。
- `OverviewSection` 月标题：建议抽 `monthHeaderText(year, month, locale)` 纯函数后加单测；若不抽，至少加 Compose UI 测试或真机截图。
- History 日期：确认 th 年份为 2026（非 2569）；如抽纯函数则加单测。
- 真机：
  - Android 13+ 系统「应用语言」列出 17 项，切换 th/vi 后 Activity 重建、UI/日期/通知正确；
  - Android 12 及以下跟随系统语言；
  - 泰文断行/组合符、越南文声调/行高、Dynamic Type、窄按钮/设置页；
  - translate picker 选择 `th`/`vi` 后下发正确 code，服务端 LLM 翻译与 TTS 发音可用。
- 门禁：`:LivaChatApp:testDebugUnitTest`、`:LivaChatApp:assembleDebug`、`androidTest` 编译；真机走查另行执行。

**实施结果（2026-10-27）**：`testDebugUnitTest` **962 tests / 0 failures / 4 skipped**；`assembleDebug` 与 `compileDebugAndroidTestKotlin` 通过；debug APK 已产出。真机 per-app language、translate/TTS 联调与母语翻译 review 尚未执行，待 review 后补。

### 21.7 风险与边界

1. **泰历混淆**：已确认 Gregorian；禁止佛历转换。History 的 `ค.ศ.` 属公历标注，不是佛历。
2. **泰文排版**：无词间空格、组合符/断行、Dynamic Type；不要手动插空格或换行，需真机截图。
3. **越南文排版**：声调符号增大行高/宽度；避免 `uppercase()` 破坏声调，检查截断与窄按钮。
4. **翻译质量**：th/vi 必须母语 review；LLM 初稿不得直接上线。
5. **跨端漂移**：Android 作为 shared key 事实源；iOS 导入后需跑占位符/复数守卫，51 个 iOS-only + forced `conv_avatar_save_failed` 单独 review。
6. **服务端能力**：`_normalize_lang` 无白名单限制，但 LLM 翻译质量与 TTS 对 th/vi 的覆盖需真实 provider 验证。
7. **per-app locale 语义**：`Locale.getDefault()` 在 Android 13+ 可能随 App locale 变化；现有已知限制（§6）不因新增语言改变。
8. **Key 齐平**：17 目录必须全量补齐；缺失 key 会静默回退英文，必须由 `I18nResourceFilesTest` 拦截。
9. **商店元数据**：Google Play 的商店文案/截图不随 App 资源自动更新，需单独维护。
10. **`th` 回归**：此前已移除，本次已重新加入；文档/测试中的“已移除”表述已同步清理。

### 21.8 实施记录

1. 已完成：翻译批次与术语草案（覆盖 Android 254 key；两端并集 305 key/locale 待 iOS 轮补齐）。
2. 已完成：`values-th`/`values-vi` 资源与 `locales_config` 17 项。
3. 已完成：`MylifeDateLabels`、`PeriodAggregator`、`OverviewSection`、`LanguagePickerSheet` 分支。
4. 已完成：`I18nResourceFilesTest`、日期/周期/picker 测试。
5. 已完成：单测、`assembleDebug`、androidTest 编译。
6. 已完成 versionCode bump（137 → 138，versionName 1.0.0 不变）；待 review 后：真机切语言与 translate/TTS 联调、母语翻译 review。
7. iOS 已实施：从 Android 导入 183 shared key，另译 51 个 iOS-only + forced `conv_avatar_save_failed`（52 个 authored 条目）。

### 21.9 ไทย（th）关键术语（草案，待母语 review）

| 中文 | ไทย |
|---|---|
| 时光机 | เครื่องย้อนเวลา |
| 全模态 | Omni |
| 转录 / 转录模式 | ถอดเสียง / โหมดถอดเสียง |
| 同声翻译 | แปลสด |
| 对方语言 | ภาษาของคู่สนทนา |
| 演讲者（人）/ 扬声器（设备） | ผู้พูด / ลำโพง |
| 设置 / 服务器 / 认证密钥 | การตั้งค่า / เซิร์ฟเวอร์ / คีย์ยืนยันตัวตน |
| 屏幕 / 悬浮窗 / 屏幕交互 | หน้าจอ / ฟองลอย / การโต้ตอบบนหน้าจอ |
| 麦克风 / 扬声器 / 摄像头 | ไมโครโฟน / ลำโพง / กล้อง |
| 对话历史 | ประวัติการสนทนา |

### 21.10 Tiếng Việt（vi）关键术语（草案，待母语 review）

| 中文 | Tiếng Việt |
|---|---|
| 时光机 | Cỗ máy thời gian |
| 全模态 | Omni |
| 转录 / 转录模式 | phiên âm / Chế độ phiên âm |
| 同声翻译 | Dịch trực tiếp |
| 对方语言 | Ngôn ngữ của người đối thoại |
| 演讲者（人）/ 扬声器（设备） | người nói / loa |
| 设置 / 服务器 / 认证密钥 | Cài đặt / Máy chủ / khóa xác thực |
| 屏幕 / 悬浮窗 / 屏幕交互 | màn hình / bong bóng nổi / tương tác màn hình |
| 麦克风 / 扬声器 / 摄像头 | micrô / loa / camera |
| 对话历史 | Lịch sử trò chuyện |

---

## 附录：关键文件索引

| 层 | 文件 | 职责 |
|---|---|---|
| 资源 | `res/values/strings.xml` | 英文默认；243 strings + 11 plurals |
| 资源 | `res/values-b+zh+Hans/strings.xml` | 简体中文 |
| 资源 | `res/values-b+zh+Hant/strings.xml` | 繁體中文 |
| 资源 | `res/values-xx/strings.xml` | ja/ko/ru/de/fr/it/es/pt/hi/ar/th/vi；id 为 `values-in` |
| 配置 | `res/xml/locales_config.xml` | 16 个 per-app locale 条目 |
| 配置 | `AndroidManifest.xml` | `localeConfig`、`supportsRtl`、`configChanges` |
| 日期 | `ui/screens/mylife/timeline/MylifeDateLabels.kt` | 年/月年/月日/星期纯函数 |
| 日期 | `ui/screens/mylife/timeline/PeriodAggregator.kt` | 周范围、摘要兜底、周/月/年标签 |
| 日期 | `ui/screens/mylife/timeline/components/OverviewSection.kt` | 月份标题模板 |
| 日期 | `ui/screens/history/HistoryScreen.kt` | 历史 LONG 日期 + FULL 星期 |
| 数字 | `ui/util/LocaleDigits.kt` | ar 阿拉伯-印度数字助手 |
| 后缀 | `ui/components/MessageBubble.kt` | mode+role → 后缀资源；时间数字本地化 |
| 后缀 | `ui/screens/conversation/ConversationListenerAdapter.kt` | 写入正文+mode |
| 后缀 | `data/local/entity/MessageEntity.kt` / `LivaChatDatabase.kt` | `mode` 列；Room 8→9 |
| 后缀 | `ui/screens/mylife/hotslot/HotSlotRows.kt` | MyLife 后缀展示时拼接 |
| 标签 | `media/DeviceLabel.kt` / `AudioInputDevice.kt` / `AudioOutputDevice.kt` | 设备标签规格 |
| 标签 | `domain/model/ServerOption.kt` | 服务器选项 labelRes |
| 选择器 | `ui/screens/conversation/LanguagePickerSheet.kt` | 对方语言显示名 |
| 通知 | `media/MicCaptureService.kt` / `ScreenCaptureService.kt` | FGS channel/标题/正文 |
| 指令 | `media/ScreenCaptureController.kt` / `GalleryPhotoSender.kt` | `conv_image_action_text`（16 locale，已实施） |
| 守卫 | `src/test/.../I18nResourceFilesTest.kt` | key/plural/汉字/NBSP/阿数守卫 |
| 单测 | `MylifeDateLabelsTest` / `PeriodAggregatorTest` / `LocaleDigitsTest` / `ModeSuffixMappingTest` / `Audio*DeviceLabelTest` | 语言规则与标签规格 |
