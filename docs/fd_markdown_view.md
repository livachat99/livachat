# Feature Design: AI 消息 Markdown 渲染与应用内 WebView（最终 v1）

> **Status**: 已定稿（d4/k3 两稿评审合并；评审记录见两稿原文）
> **Date**: 2026-08-11
> **Scope**: Android 正式 App（`apps/android/LivaChatApp`，**不含** `LivaChatAppMock`）—— `ai_text` 可能是纯文本或 Markdown 内容，需要以 Markdown 视图渲染；消息中的链接点击后在**应用内 WebView** 打开。
> **决策**: 采用 **Markwon 4.6.2 自封装**（d4 方案 A）+ k3 文档的组件/导航/WebView/测试体系。Toolchain 升级（Kotlin/Compose 大版本）及 mikepenz 迁移为**未来选项**，见 §9。
> **约束**: 本文所有第三方库版本/兼容性结论均经联网核实（Maven Central POM、GitHub 源码、官方文档），来源见 §10。

---

## 1. 功能说明

### 1.1 背景

服务端 `ai_text` 消息的文本内容为纯文本**或 Markdown 格式**。当前 `MessageBubble`（`MessageBubble.kt:92`）用 Compose `Text` 原样渲染，`**加粗**`、`[链接](url)` 等标记以字面量显示，链接不可点击。

### 1.2 目标

1. AI 气泡（`MessageRole.AI`）按 Markdown 渲染：标题/加粗/斜体/列表/引用/代码块/**GFM 表格**/任务列表/链接。
2. 链接（`[text](url)`、裸 URL 自动成链、HTML `<a>`）可点击，在**应用内 WebView 屏幕**打开。
3. 不改变 WS 协议、`ChatMessage` 模型、Room 持久化与会话数据流；**不改任何构建链版本**（Kotlin 2.0.0 / Compose BOM 2024.06.00 / AGP 8.5.0 全部不动）。
4. 历史详情页（复用 `MessageBubble`）同步获得能力。

### 1.3 范围与非目标

**本期范围**

- `LivaChatApp` 新增 Markdown 渲染组件（Markwon 自封装）并接入 `MessageBubble`（AI 消息）。
- 新增应用内 WebView 屏幕 + 导航路由。
- 单元测试与 Compose UI 测试。

**非目标**

- `LivaChatAppMock`（纯 UI 演示 module，不接入）。
- 用户气泡（ASR 转写文本，保持纯文本，避免 `*`/`_`/`#` 误渲染）。
- 流式（token 级）Markdown 渲染（overlay 保持纯文本；数据流下非 final 消息不进列表，见 §2.1）。
- Markdown 内嵌网络图片（`ChatMessage.image` 走现有 `ChatImageView`，互不影响；Markwon 有 coil 插件可后续叠加）。
- 代码块语法高亮、表格横向滚动等增强交互（表格按内容列宽压缩显示，POC 验收可接受度）。
- 相机视频模式浮层 `VideoPreview.lastAiText`、lecture 模式 `TranscriptionBubble` 的 Markdown 化（保持纯文本）。

---

## 2. 现状分析（代码事实，已核实）

### 2.1 ai_text 数据流

```
服务端 ai_text delta (index 0..N, is_final)
  → LivaChatSDK 回调 LivaChatListener.onAiText()
  → ConversationListenerAdapter.onAiText()
      ├─ 按 messageId 累积 delta 到 messageCache
      ├─ 【注意】normalized = text.replace(Regex("\\n{2,}"), "\n")   // L128 空行折叠，见 §3.3 POC-4
      ├─ _streamingAiText.value = 累积文本           // 仅 lecture 模式浮层使用
      └─ is_final=true 时才 chatRepository.appendMessage()  // Room upsert (L145-150)
  → ChatRepository.observeMessages(sessionId)        // Room Flow
  → ConversationViewModel 合并进 ConversationUiState.messages
  → ConversationScreen LazyColumn → MessageBubble(Text)
```

关键事实：

- **列表中只有 final 消息**（`ConversationViewModel` 仅观察 Room，Room 仅 final 落库）→ 每条 AI 消息 Markdown **只解析一次**，无逐 token 解析压力，无未闭合标记闪烁问题。
- `ChatMessage` 模型无需改动；`MessageBubble` 有 **2 处调用**：`ConversationScreen.kt:503`、`HistoryDetailScreen.kt:74`。
- **落库文本已被 adapter 折叠空行**（L128）：Markdown 段落分隔退化为 soft break，渲染器的 soft break 行为直接影响版式（见 §3.3 POC-4）。

### 2.2 渲染挂载点

| 位置 | 内容 | 本期处理 |
|------|------|---------|
| `MessageBubble.kt:92` | AI 消息（对话列表） | ✅ 替换为 MarkdownContent |
| `HistoryDetailScreen.kt:74` | 复用 `MessageBubble` | ✅ 随上项自动生效 |
| `ConversationScreen` `cameraAiText` → `VideoPreview.lastAiText` overlay | 视频模式最末 AI 文本 | ❌ 保持纯文本 |
| `TranscriptionBubble`（lecture 模式 `streamingAiText`） | 实时流式译文 | ❌ 保持纯文本 |

### 2.3 依赖基线（硬约束，本期不动）

Kotlin **2.0.0**、AGP 8.5.0 / Gradle 8.7、Compose BOM **2024.06.00**（Compose 1.6.8）、compileSdk 34 / minSdk 26 / JVM 17、navigation-compose 2.7.7、Hilt 2.51、Room 2.6.1；`settings.gradle.kts` 为 `FAIL_ON_PROJECT_REPOS`（仅 google + mavenCentral）。

---

## 3. 方案调研与决策

### 3.1 候选结论（评审合并后）

| # | 方案 | 结论 | 理由（均已核实） |
|---|------|------|------|
| **A** | **Markwon 4.6.2 自封装 `AndroidView`** | ✅ **本期采用** | 零依赖升级（Maven Central 纯 Java 库）；表格/任务列表/删除线/linkify 插件开箱即用；`LinkResolver` 全量接管链接点击（**含表格单元格内链接**）；Spannable 单次绘制适合列表；`TextView` 天然支持长按选择复制 |
| B | mikepenz/multiplatform-markdown-renderer | ⏸ 未来选项 | 0.21.0（与基线对齐）**无表格**；0.28.0 起有表格但需 Compose 1.7+；0.43.0 需 Kotlin 2.4 + Compose 1.10 + compileSdk 36（toolchain 升级评估见 §9） |
| C | jeziellago/compose-markdown 0.7.2 | ❌ | 仅 JitPack 分发（违反当前仓库策略）、强制带入 Coil3+OkHttp、表格内链接点击失效（Issue #153，2025-06 报告至今 open） |
| D | WebView + JS 渲染 | ❌ | LazyColumn 内性能/安全面不可接受 |
| E | 自研解析+渲染 | ❌ | 工作量与质量风险高 |

**决策要点**：A 是唯一同时满足"零工具链升级 + Maven Central 供应链 + 表格开箱 + 链接全可控 + 文本选择"的选项。代价是 View 体系（`AndroidView` interop）与 Markwon 已停更（**最后提交 2021-03**，非两稿所写的 2023；功能极成熟，CommonMark 覆盖完整，无已知高危问题）。停更风险由隔离层对冲（§4.1）。

### 3.2 链接打开方式

**应用内 WebView 屏幕**（`AndroidView` 包 `android.webkit.WebView`；Accompanist WebView 已官方废弃，不引入）。Chrome Custom Tabs 仅作为用户在 WebView 内选择"在浏览器打开"时的实现选项。

### 3.3 POC 验证清单（动手前 0.5 天）

1. 引入 4 个 Markwon 依赖 → 构建通过；`./gradlew :LivaChatApp:dependencies` 确认**无 Compose/Kotlin 版本牵引、无新增传递依赖意外**；确认无 `META-INF` 打包冲突。
2. `MarkdownContent` 渲染样例（标题/粗斜体/列表/引用/代码块/表格/任务列表/链接），暗色气泡背景下可读。
3. `LinkResolver` 对 `[t](u)`、裸 URL（linkify）、**表格单元格内链接**均触发回调；非 http/https scheme 不触发导航。
4. **soft break × 空行折叠**（§2.1）：验证 Markwon 对单 `\n` 的渲染（Markwon 默认 soft break 渲染为换行；如不符，通过 `MarkwonConfiguration.softBreakAddsNewLine` 调整或评估渲染层预处理）。**结论决定是否需要改 adapter**——原则上不动 adapter（其折叠逻辑同时服务 overlay 展示）。
5. `<a href>` 标签：加 `html` 插件后渲染可点（POC 确认后决定是否保留该插件）。
6. `LazyColumn` 50+ 条 Markdown 气泡滚动无掉帧（`remember` 缓存 Markwon 实例 + `setMarkdown` 仅在内容变化时调用）。
7. `setTextIsSelectable(true)` 与链接点击的交互（点按开链、长按选择）表现可接受。
8. 宽表格（多列）在 75% 屏宽气泡内的显示效果验收。

---

## 4. 方案设计

### 4.1 总体结构

```
ui/components/MarkdownContent.kt        ← 新增：库隔离层（唯一 import markwon 的文件）
ui/components/MessageBubble.kt          ← 修改：AI 且 isFinal → MarkdownContent；新增 onLinkClick
ui/screens/webview/WebViewScreen.kt     ← 新增：应用内 WebView
ui/navigation/Routes.kt / NavGraph.kt   ← 修改：新增 webview 路由
ui/screens/conversation/ConversationScreen.kt  ← 修改：透传 onOpenLink
ui/screens/history/HistoryDetailScreen.kt      ← 修改：透传 onOpenLink
LivaChatApp/build.gradle.kts            ← 修改：+4 个 Markwon 依赖（Maven Central，settings 不动）
```

**隔离原则**：全 App 仅 `MarkdownContent.kt` 依赖 Markwon；未来切换 mikepenz（§9）只动这一个文件 + build 依赖。

### 4.2 依赖（`LivaChatApp/build.gradle.kts`）

```kotlin
implementation("io.noties.markwon:core:4.6.2")
implementation("io.noties.markwon:linkify:4.6.2")        // 裸 URL 自动成链
implementation("io.noties.markwon:ext-tables:4.6.2")     // GFM 表格
implementation("io.noties.markwon:ext-tasklist:4.6.2")   // 任务列表
// 可选（POC-5 后决定）：implementation("io.noties.markwon:html:4.6.2")              // <a> 等行内 HTML
// 可选：implementation("io.noties.markwon:ext-strikethrough:4.6.2")                // ~~删除线~~
```

### 4.3 MarkdownContent 组件（新增）

```kotlin
// ui/components/MarkdownContent.kt（签名示意，具体参数以实现为准）
@Composable
fun MarkdownContent(
    markdown: String,
    onLinkClick: (String) -> Unit,
    modifier: Modifier = Modifier,
)
```

实现要点：

- `AndroidView` 包装 `TextView`；Markwon 实例用 `remember` 缓存（避免 LazyColumn 每项重建 builder）；
  `update { }` 块中 `markwon.setMarkdown(textView, markdown)`（`update` 仅在 `markdown` 变化时执行）。
- Markwon 构建（插件类名/API 以 4.6.2 实际为准，实现时验证）：

  ```kotlin
  Markwon.builder(context)
      .usePlugin(LinkifyPlugin.create())        // 裸 URL → 链接
      .usePlugin(TablePlugin.create(context))   // GFM 表格
      .usePlugin(TaskListPlugin.create(context))
      // .usePlugin(HtmlPlugin.create())        // 可选，POC-5
      .linkResolver { _, link ->                // 全量链接出口（含表格内）
          if (link.isHttpUrl()) onLinkClick(link) // scheme 白名单，见 §4.6
      }
      .build()
  ```
- **主题映射**（集中一个函数，随 theme 重组）：正文色 = `MaterialTheme.colorScheme.onSurface`；
  链接色 = `BrandIndigo`（`textView.setLinkTextColor`）；代码块底色/引用边线/标题字号经
  `MarkwonTheme`（builder `usePlugin` 后 `configureTheme`）映射 `LivaChatTheme` 配色与
  `MaterialTheme.typography.bodyLarge` 字号。
- 文本选择：`setTextIsSelectable(true)`（POC-7 验收后启用）。
- 图片语法 `![..](url)`：无图片插件时按文本/链接降级显示（本期可接受）。

### 4.4 MessageBubble 改造

```kotlin
@Composable
fun MessageBubble(
    message: ChatMessage,
    showCursor: Boolean = false,
    modifier: Modifier = Modifier,
    onLinkClick: ((String) -> Unit)? = null,   // 新增，默认 null 保持现有调用兼容
)
```

- `isUser == true`：保持纯文本 `Text`。
- AI 且 `isFinal == true` 且 `onLinkClick != null`：`MarkdownContent(message.text, onLinkClick)`。
- AI 但非 final：纯文本 + `BlinkingCursor`（现状；数据流下实际不进列表，防御保留）。
- `message.image != null` 分支（`ChatImageView`）不变，位于 Markdown 内容上方。

### 4.5 链接点击 → WebView 导航链路

```
MarkdownContent(onLinkClick)
  ← MessageBubble(onLinkClick)
  ← ConversationScreen(onOpenLink) / HistoryDetailScreen(onOpenLink)   // 新增参数，与现有 onNavigateToHistory 同模式
  ← NavGraph: navController.navigate(Routes.webview(url))
```

- `Routes.kt` 新增：
  ```kotlin
  const val WEBVIEW = "webview/{url}"
  fun webview(url: String) = "webview/${android.net.Uri.encode(url)}"
  ```
- `NavGraph.kt` 新增 `composable(Routes.WEBVIEW)`，取 `url`（自动 decode）传给 `WebViewScreen`。
- ViewModel **零改动**。

### 4.6 WebViewScreen（新增）与安全清单

LLM 生成内容不可信，按以下基线实现：

| 能力 | 设计 |
|---|---|
| 加载 | `loadUrl(url)`；`WebChromeClient.onProgressChanged` 驱动顶部 `LinearProgressIndicator` |
| TopAppBar | 页面标题（`onReceivedTitle`，空则回退域名）+ 关闭按钮 + 溢出菜单"在浏览器打开"（`Intent.ACTION_VIEW`） |
| 返回键 | `BackHandler`：`webView.canGoBack()` 则 `goBack()`，否则 `popBackStack` |
| 安全加固 | `javaScriptEnabled = false`（一期纯阅读场景）；`domStorageEnabled = false`；`allowFileAccess / allowContentAccess = false`；`setSupportMultipleWindows(false)`；不注册任何 JS bridge |
| 跳转控制 | `shouldOverrideUrlLoading`：仅 http/https 内部继续加载（防重定向到非法 scheme）；其余丢弃或交系统 Intent（try/catch） |
| 入参校验 | `isHttpUrl()`（`Uri.parse(url).scheme in {http, https}`）双重校验：MarkdownContent 拦截处 + WebView 入口；非法 URL 显示错误占位 |
| 错误处理 | `onReceivedError` / `onReceivedHttpError`（main frame）→ 错误占位 + "重试/浏览器打开"；`onPageStarted` 重置错误态（重试可恢复） |
| SSL 错误 | **忽略并继续访问**（`onReceivedSslError` → `handler.proceed()`，产品决策 2026-08，支持自签名/过期证书页面）。风险边界：JS 关闭、无 JS bridge、不注入凭据、纯阅读场景；主要风险为中间人可替换页面内容。收紧路径：仅放行主框架或按 host 白名单 |
| 生命周期 | `DisposableEffect` 中先脱离视图树再 `webView.destroy()`（防泄漏）；错误态用覆盖层而非移除 WebView（保持重试/返回栈可用） |

### 4.7 错误与边界处理

| 场景 | 行为 |
|---|---|
| 非 http/https 链接（mailto、自定义 scheme） | 拦截层忽略，不导航 |
| Markdown 语法畸形 | 解析器容错，按纯文本降级，不崩溃 |
| 链接 URL 编码 | 导航前 `Uri.encode`，取值自动 decode |
| 空行折叠后版式黏连 | 见 POC-4；首选渲染层解决，不动 adapter |
| 极长 Markdown | 单条消息 KB 级，Markwon 同步解析可接受；POC-6 实测 |

---

## 5. 改动清单（实施映射）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `LivaChatApp/build.gradle.kts` | +4（至多 6）个 Markwon 依赖 |
| 2 | `ui/components/MarkdownContent.kt` | 新增（隔离层 + LinkResolver 白名单 + 主题映射） |
| 3 | `ui/components/MessageBubble.kt` | AI/final 分支换 MarkdownContent；新增 `onLinkClick`；补 `@Preview` |
| 4 | `ui/screens/webview/WebViewScreen.kt` | 新增（§4.6 全项） |
| 5 | `ui/navigation/Routes.kt` | +`WEBVIEW` 常量与 `webview(url)` helper |
| 6 | `ui/navigation/NavGraph.kt` | +webview destination；两屏幕接线 `onOpenLink` |
| 7 | `ui/screens/conversation/ConversationScreen.kt` | +`onOpenLink` 参数并透传 |
| 8 | `ui/screens/history/HistoryDetailScreen.kt` | +`onOpenLink` 参数并透传 |
| 9 | 测试 | 见 §6 |

预计新增/修改代码 ≈ 350~450 行。

## 6. 测试计划

| 层级 | 用例 |
|---|---|
| 单元测试（local） | `isHttpUrl` 白名单（http/https 放行；file/javascript/data/intent 拒绝）；主题映射函数纯逻辑 |
| Compose UI 测试（androidTest） | 含链接的 AI Markdown → 断言点击回调 URL 正确；**表格渲染 + 表格内链接可点**；用户气泡保持纯文本；`WebViewScreen` 用 mockwebserver（已有依赖）起本地页冒烟 |
| 回归 | 现有 `ConversationViewModelTest`、`ConversationListenerAdapterTest`、`MessageBubbleTest` 不受影响（无 VM/Adapter 改动），CI 全量 |
| E2E | 扩展 `ConversationE2ETest`：Mock 后端下发含链接/表格的 ai_text → 渲染 → 点击 → WebView 显示 |
| 手工 | 深色/浅色、历史详情页、长文本、链接内重定向、断网错误页、长按选择复制 |

## 7. 风险评估与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| Markwon 已停更（2021-03 起） | 低-中 | 功能成熟稳定、无已知高危问题；隔离层单文件封装，未来可平移 mikepenz（§9） |
| AndroidView 与 Compose 组合/测量/无障碍 | 低 | 成熟 interop 模式；TextView 文本天然可被 TalkBack 读取 |
| 空行折叠 × soft break 版式问题（§2.1） | 中 | POC-4 先行；Markwon 默认 soft break 换行，预计可直接接受 |
| 宽表格在窄气泡内挤压 | 中 | POC-8 验收；超宽时容器横滚兜底 |
| 深色模式配色偏差 | 低 | 主题映射集中一个函数，随 theme 重组 |
| LLM 输出恶意链接 | 中 | §4.6 安全清单全项落地（scheme 白名单双重校验 + JS 关闭 + 无 bridge） |
| 大文本列表性能 | 低 | 消息 KB 级；Markwon 实例缓存 + LazyColumn 复用；POC-6 |

## 8. 工作量评估

| 阶段 | 预估 |
|---|---|
| POC（§3.3 全项） | 0.5 天 |
| 实施（§5 全部改动 + 主题适配） | 1–1.5 天 |
| 测试（单测 + UI 测试 + E2E 扩展 + 手工） | 0.5–1 天 |
| **合计** | **2–3 天** |

## 9. 未来选项：toolchain 升级与 mikepenz 迁移（本期不做）

若未来产品需要 Compose 原生渲染、流式 Markdown（`StreamingMarkdownState`）或单元格内富内联表格，可整体升级工具链后迁移 mikepenz（已调研核实）：

| 路线 | 要求 | 代价 |
|---|---|---|
| mikepenz **0.28.0**（首个含表格版本） | 仅 Compose BOM → 2024.12.01（Compose 1.7.8）；Kotlin 2.0.0 可读其 metadata（Kotlin 2.0.21 构建）；AGP/compileSdk 不动 | BOM 单点升级 + POC（注意 0.31.0 才修复的链接导航 bug #292） |
| mikepenz **0.43.0**（最新） | Kotlin ≥ 2.4.0 + Compose ≥ 1.10 + compileSdk 36 + AGP 8.13/Gradle 8.13 + Hilt/Room/navigation 联动 | 全工具链升级，评估 3.5–5 人日，应独立立项 |

迁移时 `MarkdownContent` 对外签名（`markdown, onLinkClick, modifier`）保持不变，仅替换内部实现与 build 依赖；`MessageBubble`/WebView/导航/测试全部复用。

## 10. 调研来源（核实依据）

| 事实 | 来源 |
|---|---|
| Markwon 4.6.2 为最后版本、最后提交 2021-03 | github.com/noties/Markwon（tags/commits） |
| Markwon LinkResolver / 插件体系（linkify/ext-tables/ext-tasklist/html） | github.com/noties/Markwon（README/docs） |
| jeziellago 0.7.2 构建基线、JitPack-only、Coil3 `api` 导出、fork 依赖 | github.com/jeziellago/compose-markdown（`markdowntext/build.gradle` @0.7.2）、jitpack.io、Maven Central 404 确认 |
| Issue #153 表格内链接不可点（open 至今） | github.com/jeziellago/compose-markdown/issues/153 |
| mikepenz 0.21.0 无表格；0.28.0 首现 `MarkdownTable.kt`；0.41.0 单元格富内联 | mikepenz/multiplatform-markdown-renderer 各 tag `compose/elements/` 源码目录 |
| mikepenz 版本要求阶梯（0.31→Compose 1.7；0.33→Kotlin 2.1.20；0.34→Compose 1.8；0.36→Kotlin 2.2/Compose 1.8.3；0.39→Kotlin 2.3/Compose 1.10；0.43→kotlin-stdlib 2.4.0） | 仓库 release notes、`...-android-0.43.0.pom`（Maven Central） |
| Accompanist WebView 官方废弃 | google.github.io/accompanist/web |
| 数据流/挂载点/基线版本 | 本仓库源码：`ConversationListenerAdapter.kt`（L115-150）、`ConversationViewModel.kt`、`MessageBubble.kt:92`、`ConversationScreen.kt:503`、`HistoryDetailScreen.kt:74`、各 `build.gradle.kts` |
