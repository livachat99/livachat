# iOS 时光机开发与测试计划

**状态**：Phase 0–6 已完成，Phase 7 自动化部分已完成（2026-10-22）
**依据**：`docs/ios_timeline_arch.md`、`ios_timeline_homepage.md`、`ios_timeline_topics.md`、`ios_timeline_hotslot.md`、`ios_timeline_image_gallery.md`、`ios_timeline_audio_gallery.md`。
**约束**：iOS 15+、Swift 6、SwiftUI、仅竖屏、en/zh-Hans；不新增远程依赖（复用 vendored `NetworkImage`）；服务端不改。

---

## 1. 交付范围

按设计文档实现完整时光机：

- 会话页「时光机」入口；
- 首页四种粒度浏览、下钻、分页、分析中轮询、搜索；
- topic 聚合与点击搜索、搜索结果、命中进入 EventList；
- 日卡活跃时段、hotSlotView、eventListViewer；
- 卡片缩略图、Gallery、全屏查看器、保存、批量删除；
- 日卡语音入口、AudioGallery、m4a 播放器、能量包络、时间头跳转。

---

## 2. 实施阶段

### Phase 0 — 工程接线与文案

**代码**
- Xcode 工程加入 `Core/Mylife/`、`Features/Timeline/`、测试文件与 `NetworkImage` 本地包。
- `AppRouter.showTimeline`、`AppRootView` fullScreenCover、`ConversationView` 菜单入口、`AppContainer.openTimeline`。
- `MylifeStore` 装配点与 UI 测试 fake 入口。
- `Localizable.xcstrings` 新增 `mylife_*` / `common_play|pause|clear` / `player_play_failed`。

**验证**
- `xcodebuild build` 通过。
- 菜单能打开空时光机宿主（占位内容），返回不停止实时会话。

### Phase 1 — 数据层

**文件**
- `Core/Mylife/MylifeDTOs.swift`
- `Core/Mylife/MylifeModels.swift`
- `Core/Mylife/MylifeErrors.swift`
- `Core/Mylife/MylifeAPIClient.swift`
- `Core/Mylife/MylifeStore.swift`（含 `MylifeServicing`）
- `Core/Mylife/MylifeDateLabels.swift`
- `Core/Mylife/MylifeAggregator.swift`

**实现**
- DTO/API：overview/timeline/search/remove/media；token、TLS、错误信封、30s remove、60s media。
- Store：overview 窗口合并、时区重建、deeper cursor、60s 轮询、400 天 pending、删除延迟刷新、媒体下载。
- 日期标签：en/zh、24h、ISO 周、UTC 日期键解析。
- 聚合：四粒度、scope、summary 折叠、图片轮转、topics 权重、audioCount、未闭合组过滤。
- 所有稳定排序带原始下标 tie-breaker。

**测试**
- `MylifeAPIClientTests`：URL/token/format、错误信封、取消、自签 host/port。
- `MylifeStoreTests`：合并、时区、游标、轮询、pending、flush。
- `MylifeDateLabelsTests`：日/周/月/年/分组头/标题/搜索日头。
- `MylifeAggregatorTests`：粒度/scope/summary/图片/topics/audioCount/未闭合组。

**门禁**：`xcodebuild test -only-testing:LivaChatAppTests` 全绿。

### Phase 2 — 首页与导航

**文件**
- `Features/Timeline/Components/MylifeTheme.swift`
- `Features/Timeline/Components/MylifeFlowLayout.swift`
- `Features/Timeline/Components/MylifeRemoteImage.swift`
- `Features/Timeline/Components/GranularitySelector.swift`
- `Features/Timeline/Components/MylifeSearchBar.swift`
- `Features/Timeline/Components/PeriodCard.swift`
- `Features/Timeline/Components/HotSlotChips.swift`
- `Features/Timeline/Components/TopicChipRow.swift`
- `Features/Timeline/Components/AudioEntryButton.swift`
- `Features/Timeline/Components/ScopePill.swift`
- `Features/Timeline/TimelineRoute.swift`
- `Features/Timeline/TimelineFlowModel.swift`
- `Features/Timeline/TimelineFlowView.swift`
- `Features/Timeline/Home/TimelineHomeViewModel.swift`
- `Features/Timeline/Home/TimelineHomeView.swift`
- `Features/Timeline/Home/SearchResultsView.swift`

**实现**
- Abyss + 星点背景、色板/字体/间距。
- 首页浏览、分组头、卡片、下钻、footer 四态、滚动保持。
- 搜索模式、350ms 防抖、结果列表（Phase 3 数据细节同期）。
- 子页栈与首页常驻。

**测试**
- 首页状态/卡片/搜索 VM 单测。
- UI 冒烟：入口、粒度切换、下钻、footer、搜索状态。

### Phase 3 — Topic 与搜索

**实现**
- topic 聚合接入卡片；chip 点击置 query。
- 搜索结果分组、类型图标、时间、snippet 高亮、分页、命中跳转参数。
- `localDateOf` pending 归属。

**测试**
- topic 聚合/权重/稳定随机；搜索防抖/取消/分页/高亮；命中 pending。
- UI：topic 点击、结果/无结果/过短查询、命中打开 EventList。

### Phase 4 — Hot Slot 与 EventList

**文件**
- `Features/Timeline/HotSlot/HotSlotRows.swift`
- `Features/Timeline/HotSlot/HotSlotViewModel.swift`
- `Features/Timeline/HotSlot/HotSlotScreen.swift`
- `Features/Timeline/HotSlot/HotSlotView.swift`
- `Features/Timeline/HotSlot/EventListView.swift`
- `Features/Timeline/HotSlot/TimelineBubbleView.swift`
- `Features/Timeline/HotSlot/FrameRowView.swift`
- `Features/Timeline/HotSlot/FrameViewerOverlay.swift`
- `AgentJobResultOverlay` 解耦为 text/timestamp/背景参数。

**实现**
- 取数窗口/过滤/映射/分页/去重。
- 行模型、分钟分隔、帧 chunk、气泡、Agent Markdown/折叠/全屏。
- 覆盖层互斥、保存、轮询重取保持覆盖层。
- EventList 锚点追击/定位/高亮。

**测试**
- 行模型/锚点/VM 单测；覆盖层/保存/追击 UI 测试。

### Phase 5 — 图片 Gallery

**文件**
- `Features/Timeline/Gallery/FrameGalleryViewModel.swift`
- `Features/Timeline/Gallery/FrameGalleryView.swift`
- `Features/Timeline/Gallery/GalleryViewerOverlay.swift`

**实现**
- 3 列网格、分页去重、focus、查看器翻页/缩放/caption/保存/预取。
- 编辑选择、批量删除、确认弹窗、删除中全拦截、退出刷新。

**测试**
- VM/轮转/dateRange/保存/删除单测；Gallery/Viewer/删除 UI 测试。

### Phase 6 — 语音 Gallery

**文件**
- `Core/Audio/MylifeAudioPlayer.swift`
- `Features/Timeline/Audio/AudioGalleryViewModel.swift`
- `Features/Timeline/Audio/AudioGalleryView.swift`
- `Features/Timeline/Audio/AudioSegmentCard.swift`
- `Features/Timeline/Audio/SegmentProgressBar.swift`

**实现**
- m4a 下载 + 会话引擎/AVAudioPlayer 双后端单播、startAtMs/pendingSeekMs、实时会话单向暂停、后台暂停。
- 时间头、正文回退、逐段标签、能量包络、拖动/点击、非当前段切段定位。
- 分页、pending 收敛、播放中不重取、弹栈停止。

**测试**
- 玩家状态机/能量/正文/标签单测；播放/能量/时间头 UI 测试。

### Phase 7 — 真机与收尾

- mock server 18080 联调：自签 TLS、图片、m4a、删除、分页。
- iPad 兼容模式 600pt。
- `AppShellUITests` 新增 timeline 场景与截图。
- 文档回填最终行为与已知限制。

---

## 3. 测试矩阵

| 层 | 入口 | 覆盖 |
|---|---|---|
| 单元 | `xcodebuild test -only-testing:LivaChatAppTests` | DTO/URL/Store/聚合/日期/行模型/VM/音频数学 |
| UI | `xcodebuild test -only-testing:LivaChatAppUITests` | 入口/首页/搜索/HotSlot/Gallery/Audio 主路径 |
| 服务端回归 | 现有 BFF/lvdata pytest | 不新增改动，跑相关既有用例 |
| 真机 | `./scripts/build-ios.sh app` + mock server 18080 | 自签 TLS/相册/音频路由/iPad |

---

## 4. 状态

| Phase | 状态 | 备注 |
|---|---|---|
| 0 工程接线 | 完成 | 菜单入口、fullScreenCover、UI 测试 fake 与文案已接线 |
| 1 数据层 | 完成 | 单测覆盖 DTO/URL/Store/聚合/日期 |
| 2 首页 | 完成 | 四粒度、下钻、分页、滚动保持 |
| 3 搜索 | 完成 | 防抖、分页、高亮、命中跳转 |
| 4 HotSlot | 完成 | 行模型、气泡、覆盖层、EventList 锚点追击 |
| 5 Gallery | 完成 | 网格、查看器、编辑删除、保存 |
| 6 Audio | 完成 | m4a 播放器、包络、时间头跳转、单向暂停 |
| 7 收尾 | 部分完成 | 333 单测 + 61 UI 测试通过；mock server 18080 / 真机 / iPad 待人工验证 |
