# LivaChat iOS App M3 分析与设计：AI Markdown 与 Agent Job 结果查看

**状态**：已实现并通过模拟器全量测试与 iPad 真机 UI 测试（2026-10-20；折叠采用 A）；2026-10-22 追加 Agent Job 全屏横竖屏（§6.6）与三种入口共用的窗口级宿主（History/Timeline 也获得整窗宽度），模拟器与 iPhone 11 真机 UI 测试通过。
**日期**：2026-10-20（2026-10-22 追加 §6.6）
**上游文档**：
- `docs/android_agent_job_view.md`（Android Agent Job 结果查看、折叠、Markdown 交互、WebView 入口）
- `docs/fd_markdown_view.md`（Android AI 消息 Markdown 渲染与应用内 WebView）
- `docs/ios_app_m1.md`、`docs/ios_app_m2.md`（iOS 会话/UI/媒体现状）
- `docs/ios_sdk_design.md`（`aiText` 事件、`source` 字段）
- `docs/ios_dev_rules.md`（iOS 15 / SwiftUI / SPM / UIKit 例外）

**UI/UX 基线（以 Android codebase 为准）**：
- `apps/android/LivaChatApp/.../ui/components/MarkdownContent.kt`
- `.../ui/components/AgentJobResultText.kt`
- `.../ui/components/MessageBubble.kt`
- `.../ui/screens/conversation/AgentJobResultOverlay.kt`
- `.../ui/screens/webview/WebViewScreen.kt`
- `docs/android_agent_job_view.md` §2.1（id 契约）、§4.3（全屏覆盖层）、§11（折叠）、§12.3.1（共享折叠文本块）

> 本文覆盖：AI final 消息的 Markdown 渲染与链接交互；Agent 来源标识；Agent Job 结果全屏查看。Agent Job 列表/取消属于 `android_agent_job_status.md` 范围，不在本文。

---

## 1. 目标与范围

### 1.1 目标

```
AI final message（Markdown 原文）
  → 气泡内按 GFM 渲染（标题/粗斜体/列表/任务列表/引用/代码/表格/链接）
  → 链接点击 → 应用内 Safari 打开（仅 http/https）

Agent Job 结果（message_id = msg_agent_job_*）
  → 气泡头行显示 Agent 图标 + `Agent: HH:mm` + 展开提示
  → 点击气泡 → 全屏 Markdown 查看器（整宽，长文本可滚动）
  → 长结果折叠 + “还有约 N 字”（P0，源边界截断 A，见 §6.4）
```

### 1.2 范围分级

| 级别 | 内容 |
|---|---|
| **P0** | AI **final** 消息 Markdown 渲染；链接（显式链接 + GFM 自动链接）可点，应用内 Safari 打开；仅放行 http/https；Agent 来源头行；Agent Job 结果识别与全屏查看；**Agent Job 长结果折叠（源边界截断 A）+ “还有约 N 字”入口**；iOS 15 表格内容不丢失的降级；Markdown 图片不发起网络请求；**`livachat_url://webroot/...` 渲染期转换与应用内 WKWebView（见 `docs/ios_webroot_support.md`）** |
| **P1** | 文本选择/复制体验打磨；自定义 WKWebView（进度条/重试/自签证书放行） |
| **不做** | 用户消息 Markdown；流式 partial Markdown；相机模式浮层 Markdown；Markdown 内嵌网络图片；代码高亮；Agent Job 列表/取消；History（iOS 尚无）；其他页面的横竖屏切换（仅 Agent Job 全屏查看期间临时解锁）；WebView 内 JS bridge/登录态 |

### 1.3 “完成”的判定

- AI final 气泡中的 Markdown 正确渲染，且用户气泡/流式气泡保持纯文本；
- 标题、粗体、斜体、删除线、列表、任务列表、引用、代码块、表格（iOS 16+）、链接可见；
- 点击 http/https 链接在应用内 Safari 打开；`javascript:`/`file:`/`data:`/`intent:` 等 scheme 被拒绝；
- iOS 15 上 GFM 表格不会消失：降级为可读的等宽文本块；
- Agent 消息气泡头行显示 Agent 图标 + `Agent: HH:mm`；Agent Job 结果气泡显示“查看完整结果”提示；
- 点击 Agent Job 结果气泡进入全屏查看器，整宽 Markdown 可滚动、可关闭，链接可打开；
- 长 Agent Job 结果在气泡内按源安全边界折叠，显示“查看全文 · 还有约 N 字”；点开全屏后为完整正文；
- Markdown 内图片不会触发网络请求；现有 AI 图片气泡/图片查看器不受影响；
- 无网络/服务端/协议改动；单测、UI 测试、iPad 真机冒烟通过。

---

## 2. 现状分析

### 2.1 Android 现行行为（对齐基线）

| 能力 | Android 事实 |
|---|---|
| 渲染范围 | 仅 **AI 且 `isFinal` 且提供 `onLinkClick`** 的消息走 `MarkdownContent`；用户消息、流式消息、相机浮层保持纯文本（`MessageBubble.kt`） |
| Markdown 引擎 | Markwon 4.6.2 自封装：tables、tasklist、linkify（仅 `WEB_URLS`，关闭邮箱/电话误识别）、`SoftBreakAddsNewLinePlugin` |
| 样式 | 正文 16/24；标题全部按 base 字号（`headingTextSizeMultipliers=1`，只保留加粗）；链接 BrandIndigo；代码/引用底色 SurfaceVariant；段落空行保留 |
| 链接 | `[text](url)` 与裸 URL 自动成链；仅放行 http/https 且 host 非空；点击进入应用内 `WebViewScreen`（自签证书按现状放行） |
| 表格 | 气泡仍受 75% 最大宽度约束（对齐 Android `MessageBubble` 的 `widthIn(max)`）；表格在气泡内占满可用宽度；Markwon TablePlugin |
| 长文本 | 仅 Agent Job 结果可折叠：长度 ≥800 字符且渲染超过 24 行；按行边界裁剪，底部“查看全文 · 还有 N 字” |
| Agent 标识 | `source=="agent"` 显示 AgentRobot（SmartToy 机器人头）+ `Agent: HH:mm`；**全屏入口判定用 id 前缀** `ai-msg_agent_job_`，不用 source（source 会误伤 job 启动提示/nudge） |
| Agent 全屏 | 点击整卡进入覆盖层：顶部 Agent 图标 + 时间 + 关闭；正文整宽 Markdown 可滚动；进入时临时解锁横竖屏（Android FULL_SENSOR 等价），横屏去掉顶栏、关闭钮浮于右上角 |
| WebView | 顶部关闭/标题/浏览器打开；进度条；加载失败重试；非 http(s) 导航拦截；自签 SSL 继续访问 |

### 2.2 iOS 当前状态

| 项目 | 当前事实 |
|---|---|
| AI 消息 | `ChatMessage.text` 直接用 SwiftUI `Text` 渲染；Markdown 标记原样显示，链接不可点 |
| 消息模型 | `ChatMessage` 只有 id/role/text/isFinal/timestamp/image；**没有 `source`**；SDK `LivaChatEvent.aiText` 已提供 `AITextSource?`（`.llm`/`.agent`），Coordinator 当前丢弃 |
| Agent 头行 | 无 Agent 图标/时间/展开提示；不存在 Agent Job 全屏入口 |
| 渲染范围 | 列表只收 final 消息；`streamingAIText` 只用于流式气泡；相机模式浮层使用最后一条 final AI 文本，均为纯文本 |
| 图片 | `ChatMessage.image` 在气泡上方用 `AIImageView` 显示；与 Markdown 正文独立 |
| 全屏模式 | 已有图片查看器/Gallery 预览（`fullScreenCover`）、Settings（`fullScreenCover`）；尚无文本阅读覆盖层 |
| 依赖 | 本地 SPM 仅 `LivaChatSDK`；未引入 Markdown 库 |
| 平台 | iOS 15 target、iPhone-only；默认竖屏，Agent Job 全屏查看期间临时解锁横竖屏；iPad 兼容模式；UIKit 例外已有 PHPicker/相机预览层 |

### 2.3 MarkdownUI 依赖分析（已核实）

`gonzalezreal/swift-markdown-ui` **2.4.1**（MIT）：

- `Package.swift` 最低支持 **iOS 15.0**，版本 `swift-tools-version:5.6`；
- 依赖 `swift-cmark`（GFM cmark）与 `NetworkImage`（远程图片）；SPM 引入会带入这两个传递依赖；
- 支持 headings/lists/task lists/blockquote/code blocks/tables/links；
- **表格与多图段落要求 iOS 16.0+**：parser 在 iOS16+ 才注册 cmark `table` 扩展，`TableView` 也使用 iOS16 的 `Grid`；iOS15 上不会生成 table 节点，表格源码退化为普通文本/管道符显示（**不是原生表格**）；
- 链接通过 SwiftUI `openURL` 环境值处理，可用 `OpenURLAction` 全量接管；
- soft break 默认渲染为空格，可用 `.markdownSoftBreakMode(.lineBreak)` 对齐 Android 的单换行换行；
- 图片通过 `InlineImageProvider` 注入，可替换为“不发起网络请求”的实现。

结论：**采用 MarkdownUI 2.4.1**，但必须为 iOS 15 表格做降级，且必须禁用默认网络图片。

### 2.4 差距清单

| 能力 | Android | iOS 当前 | 本文动作 |
|---|---|---|---|
| AI Markdown | Markwon | 纯文本 | 引入 MarkdownUI 封装 |
| 链接 | 应用内 WebView | 不可点 | OpenURLAction + Safari 适配 |
| Agent 标识 | source + id 契约 | source 未保存 | ChatMessage 增加 source |
| Agent Job 全屏 | 覆盖层 | 无 | 新增覆盖层 |
| 长结果折叠 | 24 行/800 字 | 无 | P0，源边界截断 A（§6.4） |
| 横屏阅读 | 全屏解锁旋转 | target 默认竖屏 | 已实现：Agent Job 全屏期间解锁，关闭恢复竖屏 |
| 表格 | GFM | 无 | iOS16+ 原生；iOS15 内容降级 |

---

## 3. 设计决策

| 编号 | 决策 | 理由 |
|---|---|---|
| D1 | 使用 `swift-markdown-ui` 2.4.1（vendored 本地包，见 `apps/ios/deps/README.md`）；只有 Markdown 隔离层的 `MarkdownMessageView.swift` 与 `MarkdownTheme.swift` 允许 import `MarkdownUI` | 满足 iOS 15、SPM、GFM；主题必须使用 MarkdownUI 类型，不能强行塞进单文件；其余业务文件不依赖库，仍便于替换/升级 |
| D2 | 只有 **assistant final** 消息渲染 Markdown；用户消息、流式气泡、相机浮层继续纯 Text | 与 Android 一致；流式逐 token 解析会闪烁且成本高 |
| D3 | Markdown 主题映射 Android token：正文 16/24、标题同字号加粗、链接 BrandIndigo、代码/引用 SurfaceVariant、段落软换行 = lineBreak | 视觉与 Android 对齐 |
| D4 | 链接仅允许 http/https 且 host 非空；通过 `OpenURLAction` 拦截，应用内 `SFSafariViewController` 打开（**已拍板**） | 安全白名单；iOS 原生应用内浏览器；避免任意 scheme |
| D5 | iOS 15 表格降级：渲染前把 GFM 表格块转换为 fenced code block（内容保留、可滚动/等宽）；iOS 16+ 使用 MarkdownUI 原生表格（**已接受降级**） | iOS15 无 table 扩展、表格会退化为管道符文本；代码块保留列关系与可读性；不改 target |
| D6 | 禁用 Markdown 内嵌网络图片：自定义 `InlineImageProvider` 不发起请求（**已拍板**） | Android 不渲染 Markdown 图片；避免模型输出触发任意网络请求 |
| D7 | `ChatMessage` 增加 `source: AITextSource?`；Agent 头行用 `source == .agent`，全屏入口用 id 前缀 `ai-msg_agent_job_` | 与 Android 契约一致；source 不能唯一判定 job 结果 |
| D8 | Agent Job 全屏使用覆盖层（ZStack），不是导航路由；会话/History/Timeline 统一由窗口级 `AgentJobResultWindowCenter` + `AgentJobResultWindowHost` 呈现，各屏只发布当前结果；整宽 Markdown、可滚动、顶部关闭 | 与 Android 共享宿主 `AgentJobResultHost` 对齐；三种入口都避开 600pt 内容上限；不打断会话/音频；横屏可获得整窗宽度 |
| D9 | Agent Job 全屏期间临时解锁横竖屏（AppDelegate 动态 mask + geometry update），关闭恢复竖屏；其余页面保持竖屏 | 对齐 Android `FULL_SENSOR` while composed；Info.plist 列出 portrait + landscape，默认 mask 仍为 portrait |
| D10 | 气泡整卡点击（Agent Job）与链接点击需要仲裁：优先链接；实现时用 `OpenURLAction` 记录 + 延迟 120ms 打开全屏，若期间发生链接打开则取消 | 避免点链接同时打开全屏；Android D13 的 iOS 等价实现 |
| D11 | 链接展示使用全局 `WebLinkRouter` + `SFSafariViewController` 适配层；不使用第三方 WebView | 系统浏览器安全/可访问性更好；避免自建 JS/证书策略 |
| D12 | 折叠纳入 P0，采用 **A：源安全边界截断**（≥400 源字符，截在空行/段落边界；显示“还有约 N 字”，N 按源字符） | MarkdownUI 无公开行布局 API；A 不依赖布局、不破坏 Markdown、可精确计算源剩余字符，且性能/无障碍更稳（§6.4） |
| D13 | 文本选择（`.textSelection`）作为 P1 评估，不与 P0 链接点击绑定 | 选择/链接手势可能冲突，需要真机验证 |

---

## 4. 架构设计

### 4.1 新增/修改文件

```
apps/ios/LivaChatApp/
  Features/Conversation/
    MarkdownMessageView.swift       // Markdown 隔离层（允许 import MarkdownUI）
    MarkdownTheme.swift             // 暗色主题 token 映射
    MarkdownTableFallback.swift     // iOS15 GFM 表格降级（纯函数可测）
    AgentJobCollapse.swift          // 源边界折叠/剩余字数（纯函数可测）
    AgentJobResultOverlay.swift     // 全屏结果阅读器
  Core/PlatformAdapters/
    SafariView.swift                // SFSafariViewController 的 Representable
  Core/Conversation/
    ConversationModels.swift        // ChatMessage + source
    ConversationCoordinator.swift   // aiText source 透传
    ConversationViewModel.swift     // finishAssistantMessage(source:)、agent job 状态（如放 VM）
  Features/Conversation/ConversationView.swift
                                    // 气泡 Markdown/Agent 头行/全屏覆盖层/链接入口
  App/AppContainer.swift            // WebLinkRouter 装配
  App/AppRootView.swift             // 全局 Safari sheet
  Resources/Info.plist              // 无新增权限（Safari 不需要）
```

### 4.2 数据流

```
SDK .aiText(text, messageID, index, isFinal, image, source)
  → ConversationCoordinator：累积 text/image/source
  → final：ChatMessage(id: "ai-\(messageID)", source: source, ...)
  → ConversationViewModel.messages
  → ConversationView.messageBubble
       ├─ source == .agent → Agent 头行
       ├─ MarkdownMessageView(markdown: text, onOpenLink: linkRouter.open)
       └─ id.hasPrefix("ai-msg_agent_job_") → 气泡点击 → AgentJobResultOverlay
```

- Agent 结果没有图片（服务端 relay 白名单无 image）；如未来携带，沿用 `AIImageView` 在 Markdown 上方。
- `source` 只影响头行，不影响 Markdown 渲染条件。

### 4.3 依赖隔离

- 全 App 只有 Markdown 隔离层的 `MarkdownMessageView.swift` 与 `MarkdownTheme.swift` `import MarkdownUI`；`MarkdownTableFallback`、`AgentJobCollapse`、Agent UI 不直接依赖库。
- 三个包 vendored 在 `apps/ios/deps/`（MarkdownUI 2.4.1 / NetworkImage 6.0.1 / swift-cmark 0.9.0），工程以本地 SPM 引用接入；无远程包、无需 `Package.resolved`，完全离线构建（见 `apps/ios/deps/README.md`）。
- 升级 MarkdownUI 视为依赖升级，需要单独 review（iOS15 表格行为、openURL API）。

### 4.4 测试缝

| 协议/类型 | 生产 | 测试 |
|---|---|---|
| `WebLinkHandling` | `WebLinkRouter`（校验 URL + 呈现 Safari） | 记录 URL 的 fake |
| `MarkdownTableFallback` | 纯函数 | 单测 |
| `AgentJobDetecting` | `ChatMessage.isAgentJobResult` | 单测 |
| `MarkdownMessageView` | 真实 MarkdownUI | UI 测试注入静态消息 |

---

## 5. Markdown 渲染设计

### 5.1 气泡条件与布局

- 用户消息：`Text`（纯文本），不变。
- Assistant final：`MarkdownMessageView`。
- 流式气泡（`streamingAIText`）：纯 Text + 光标，不变。
- 相机模式浮层（`CameraModeView` 的 `lastAiText`）：纯 Text，不变。
- Markdown 图片（`ChatMessage.image`）仍显示在正文上方；Markdown 正文内部若包含 `![](url)`，由 D6 禁止网络加载。
- 气泡最大宽度：容器宽度 75%（含内边距，`AppLayout.messageBubbleMaxWidth`），会话/History/Hot Slot 三处一致，对齐 Android `MessageBubble.widthIn(max = screenWidth * 0.75)`。AI 多行消息撑满统一最大宽度（右侧对齐），单行消息按文字长度自适应；用户消息贴右。含 GFM 表格时表格在气泡内占满可用宽度，但气泡本身仍受 75% 上限；iOS16+ 表格用内容自适应；iOS15 降级代码块可横向滚动。

### 5.2 支持的 Markdown

| 语法 | 行为 |
|---|---|
| 标题 `#`~`######` | 与正文同字号（16）、加粗；不放大（对齐 Android multipliers=1） |
| 粗体/斜体/删除线 | 支持 |
| 列表/嵌套列表 | 支持 |
| 任务列表 | 支持（复选框只读） |
| 引用 | 支持，左侧色条 SurfaceVariant |
| 行内代码/代码块 | 等宽、SurfaceVariant 底、TextPrimary 字；代码块横向滚动；不做语法高亮 |
| GFM 表格 | iOS16+ 原生；iOS15 降级为代码块文本（§5.4） |
| 显式链接/GFM 自动链接 | 可点，仅 http/https（§5.3） |
| HTML | 作为文本安全展示，不执行/不加载 |
| 图片 | 不加载网络图片（D6） |

### 5.3 链接规则

- 统一经 `OpenURLAction`：
  ```swift
  .environment(\.openURL, OpenURLAction { url in
      guard WebLinkPolicy.isSafe(url) else { return .discarded }
      linkHandler.open(url)
      return .handled
  })
  ```
- `WebLinkPolicy.isSafe`：scheme 小写为 http/https、host 非空、无空白字符；拒绝 `javascript:`、`file:`、`data:`、`intent:`、`mailto:`、`tel:`。
- 点击后在应用内 `SFSafariViewController` 打开。Safari 的安全限制（不接受自签证书）优于 Android WebView 的 `handler.proceed()`；不复制该放宽策略。
- `livachat_url://webroot/...` 在渲染前按当前服务器地址转换为 `https://<host>:<port>/webroot/...`，点击进入应用内 WKWebView；普通外链仍用 SFSafari。详见 `docs/ios_webroot_support.md`。

### 5.4 iOS 15 表格降级

MarkdownUI 的 `TableView` 只在 iOS16+ 存在；iOS15 parser 也未注册 cmark table 扩展，表格会退化为普通文本（管道符外露、列关系丢失）。为获得可读的降级展示：

- `MarkdownTableFallback.rewrite(markdown, isAvailable: #available(iOS 16, *))`：
  - 只在 iOS15 执行；
  - 扫描连续行，识别 GFM 表格块：表头行含 `|`，下一行为分隔行（整行只含 `|`、`-`、`:`、空白，且 `|`/`-` 各至少一个），后续行含 `|`；
  - 将整块包成 ```text fenced code block，保留所有单元格文本；
  - 线性扫描，不使用回溯正则（对齐 Android 对超长 `|-` 行的栈溢出教训）。
- iOS16+ 不改写，由 MarkdownUI `TableView` 渲染。
- 改写只发生在**渲染前**：不修改 `ChatMessage.text`，不参与折叠的源字符计数；折叠始终基于原始 Markdown。
- 表格识别扫描器必须跳过 fenced code block 内的表格形态行；与折叠扫描器共用同一套 fence/table 判定，避免两套启发式漂移。
- 已知降级：iOS15 上表格被包成代码块后，表格单元格内的链接不可点击（文本仍完整可见）；这是接受的内容可读性降级。
- 该降级是视觉降级，不丢内容；全屏查看器中同样适用。

### 5.5 主题 / 动态字体

- 主题由 `@ScaledMetric` 计算正文 16 / 行高 24 的缩放值，主题随 Dynamic Type 重建；
- 颜色：正文 TextPrimary；链接 BrandIndigo；代码/引用背景 SurfaceVariant；表格描边 SurfaceVariant；
- 标题统一 base 字号 + bold；
- 段落间距 8pt；列表 marker 与正文同色；
- `.markdownSoftBreakMode(.lineBreak)` 保留单换行，避免 LLM 段落粘连；
- 深色专一，不引入新色。

### 5.6 性能与安全

- 只渲染 final 消息；**预解析并缓存 `MarkdownContent`**（MarkdownUI 官方推荐预解析，避免 view 每次 body 重新解析）：
  - 在 Markdown 隔离层内维护小型缓存，key = message id + 原文 hash + iOS15/16 表格降级标志，容量上限（如 50 条）；
  - 会话清空/重建 runtime 时清空缓存；
  - 折叠前缀与全屏完整正文分别缓存；
  - 仅对可见消息布局；
- 16KB 上限文本，MarkdownUI 渲染可接受；
- 不做远程图片、HTML 执行、JS；链接白名单；
- 单条长文本在列表中的高度可能很大，交给 `LazyVStack`/`ScrollView`；P0 折叠缓解（仅 Agent Job，源边界截断 A）。

---

## 6. Agent Job 结果查看

### 6.1 识别与头行

- `ChatMessage.source`：来自 SDK `AITextSource?`；`source == .agent` 显示 Agent 头行。
- 气泡头行：Agent 图标（自定义 template asset `AgentRobot`，按 Android SmartToy 机器人头绘制）+ `Agent: HH:mm`（消息到达时间，本地时区）；全屏覆盖层头行只显示 Agent 图标 + `HH:mm`。
- Job 结果判定：`message.id.hasPrefix("ai-msg_agent_job_")`（与 Android `AGENT_JOB_RESULT_ID_PREFIX` 一致，包含适配器的 `ai-` 段）。
- 只有 job 结果显示“查看完整结果”图标（`arrow.up.left.and.arrow.down.right` 或 `arrow.up.right.square`），并允许整卡点击；其他 Agent 消息仅显示头行。
- 头行时间与消息列表气泡时间同源；iOS 气泡目前不显示普通消息时间，不为本功能给所有消息加时间。

### 6.2 全屏覆盖层

```
┌─────────────────────────────────────┐
│ [Agent icon] 14:32              [×] │  Surface，顶栏 64pt
├─────────────────────────────────────┤
│                                     │
│  Markdown 正文（整宽、可滚动）       │  horizontal padding 16
│  标题 / 列表 / 表格 / 链接…          │
│                                     │
└─────────────────────────────────────┘
```

- 统一由窗口级 `AgentJobResultWindowCenter` + `AgentJobResultWindowHost` 呈现：会话宿主挂在 `AppRootView` 根部（覆盖顶栏/消息/工具栏），History/Timeline 宿主挂在各自 `fullScreenCover` 根部、覆盖在 600pt 内容列之上；三种入口都不受 `maxWidth 600` 限制；不是新路由；
- 背景 `AppColor.surface`（与气泡底色一致，Android M1）；正文 `MarkdownMessageView` 整宽；
- 顶栏：Agent 图标（18pt）+ `HH:mm`（12pt regular）+ 关闭按钮（48pt）；不显示“Agent任务”文字（Android 二期实机反馈）；
- 正文 `ScrollView`，支持长内容；链接点击打开 Safari，覆盖层保持；
- 关闭：关闭按钮；不使用下滑手势（Android 只有 close/back）；
- 不隐藏系统状态栏（与图片查看器一致）；
- 进入/退出不清空消息、不停止音频；会话结束/消息消失时自动关闭（id 未命中守卫）。

### 6.3 状态与清理

- `ConversationViewModel` 增加 `agentJobResultMessageID: String?`（或 ConversationView 本地 @State；推荐 VM，便于测试）；
- 会话切换 / `clearConversation()` / `sessionEnded` 清空；
- 覆盖层渲染前按 id 查找消息；找不到则清空状态，不显示空白层；
- 覆盖层打开时**显式禁止底层交互**：Conversation 的 header/列表/工具栏 `.allowsHitTesting(false)`（或整个底层内容包一层），覆盖层根视图 `.contentShape(Rectangle())`；不依赖“不透明背景必然拦截手势”的假设。

### 6.4 长结果折叠（P0，已拍板 A：源边界截断）

Android 参数：原文 ≥800 字符且渲染超过 24 行；按行边界裁剪；底部“查看全文 · 还有 N 字”。

iOS 采用 **A：源安全边界截断**（不依赖 MarkdownUI 的行布局）：

1. **触发**：
   - 消息必须是 Agent Job 结果且存在全屏入口；
   - Markdown 源字符数（`String.count`）≥ `collapseMinChars = 400`。
2. **找边界**：
   - 线性扫描 Markdown，维护 fenced code block（``` / ~~~）与 GFM 表格块状态；
   - 记录所有**空行（段落边界）**对应的源偏移；空行必须位于 fence/table 之外；文档末尾的空行不算边界；
   - 先取 `[minVisibleChars=300, 400]` 区间内最后一个安全空行；
   - 若没有，取 400 之后、`maxVisibleChars=600` 之前的第一个安全空行；
   - 若仍没有安全空行，**不折叠**，完整显示（宁可长，不切坏结构）；
   - **不使用任意行尾边界**：跨行链接/强调/HTML 可能在行尾被切开；只有空行在 CommonMark 中能安全结束段落，作为截断点是确定性的；
   - 例如：前面 100 字后紧跟一个跨越 2000 字的 fenced code block → 不会返回 100 字前缀，而是找不到边界直接不折叠。
3. **渲染**：
   - 只把前缀 Markdown 交给 `MarkdownMessageView`；不在 Markdown 内追加省略号；
   - 前缀末尾若是列表/引用/表格，保留其完整行；
   - 底部入口行：`查看全文 · 还有约 N 字` + `common_view_full_result` 图标（14pt BrandIndigo）；
   - `N = 源字符总数 - 前缀字符数`，口径为源文本（含 Markdown 记号），因此文案使用“约”；
   - 前缀继续走 iOS15 表格降级与 URL 处理。
4. **展开**：
   - 折叠态与展开态都不在气泡内“就地展开”；点击气泡任意非链接区域 → 全屏查看完整正文（与 Android 一致）；
   - 链接点击优先于全屏点击（§6.5）。
5. **不可折叠/不显示入口**：
   - 长度 <400；
   - 非 Agent Job 结果（其他 AI 消息不折叠；Android O15）；
   - 找不到安全边界；
   - 全屏不可用（例如消息已从列表移除）。

实现落点：

- `AgentJobCollapse.swift`：纯函数 `collapse(markdown:) -> CollapsedMarkdown?`（前缀 + remainingChars），可单测；
- `AgentJobResultOverlay` 始终接收完整原文；
- `ConversationView` 的 Agent Job 气泡在渲染前调用折叠函数；入口行的点击不单独处理，沿用整卡点击；
- **处理顺序**：源 Markdown → `AgentJobCollapse`（前缀/剩余源字数）→ `MarkdownMessageView` 内 iOS15 表格降级 → 渲染；折叠不做在降级后的文本上；
- 折叠结果按 message id + 原文 hash 缓存，避免列表状态更新时重复扫描 16KB 文本；
- 折叠判定不进入 SDK/Domain，仅 UI 层派生。

边界：

| 场景 | 行为 |
|---|---|
| 300–400 内无空行 | 取 400–600 的第一个安全空行；仍无则不折叠（完整显示） |
| 超长 fenced code/表格跨过 400 | 只在块外找空行边界；找不到则不折叠；绝不使用行尾硬切 |
| 前缀中含表格 | iOS16 原生表格、iOS15 代码块降级；内容完整 |
| 同 id 重投/内容变化 | 重新计算；无持久化折叠状态 |
| Dynamic Type/宽度变化 | 折叠仅按源字符，不受布局影响；前缀不变 |
| 剩余字符为 0 | 不折叠（理论上不会发生） |

### 6.5 点击仲裁（链接 vs 整卡）

- 非 Agent Job 气泡：链接点击即可，无父级点击。
- Agent Job 气泡：
  - 头行显示“查看完整结果”图标；
  - 整卡点击打开全屏，但链接点击必须优先且只打开链接；
  - 实现策略：`WebLinkRouter` 记录最近一次链接打开时间；气泡点击延迟 120ms 执行，若期间发生链接打开则取消；或在真机上验证 MarkdownUI 链接是否已阻止父 tap，若已阻止则移除延迟逻辑。
  - 若仲裁不可靠，降级为“仅头行展开图标可点”，并在文档记录与 Android 的差异（不采用链接与全屏双触发）。
- 该交互必须纳入 UI 测试。

---

### 6.6 横竖屏与全屏布局（对齐 Android M3）

目标：Agent Job 全屏查看期间临时解锁竖屏/横屏，关闭后恢复竖屏；其余页面仍为竖屏。

- 方向：
  - `Info.plist` 列出 `Portrait` + `LandscapeLeft` + `LandscapeRight`；
  - `AppDelegate` 默认返回 `.portrait`；
  - `AgentJobResultOverlay.onAppear` → `AppOrientationController.unlockForAgentJobFullScreen()`（`.allButUpsideDown`，Android `FULL_SENSOR` 的 iPhone 等价）；`onDisappear` → `.portrait`；
  - iOS 16+：`setNeedsUpdateOfSupportedInterfaceOrientations()` + `requestGeometryUpdate(.iOS(interfaceOrientations:))`；iOS 15：`attemptRotationToDeviceOrientation()`；
  - 平台差异：iOS 会尊重系统控制中心的“方向锁定”，Android `FULL_SENSOR` 会忽略；不额外规避。
- 布局：
  - 竖屏：顶栏 64pt（AgentRobot 18pt + `HH:mm` 12pt regular + 关闭 48pt）；正文水平/底部 16pt；
  - 横屏：不渲染顶栏；关闭钮浮动右上角（48pt 圆形、黑色 35% 底、外边距 8pt）；正文顶部 4pt、水平/底部 16pt；关闭钮不随内容滚动；
  - 背景 `AppColor.surface`（Android M1，等于气泡底色）；
  - 旋转只重排，不重建覆盖层：滚动位置保留；关闭钮标识 `agent_job_result_close` 在两种布局保持一致。
- 窗口宽度：
  - 三种入口共用窗口级宿主，横屏使用整窗可用宽度（不受 `maxWidth 600` 内容列限制）；
  - History/Timeline 的宿主位于各自 `fullScreenCover` 根部、覆盖层之上，因此底下的 600pt 内容列不会再限制阅读器宽度。

---

## 7. 链接展示（应用内 Safari）

### 7.1 选择

- iOS 使用 `SFSafariViewController`（iOS 9+，系统框架）封装在 `SafariView.swift`：
  - `UIViewControllerRepresentable` 创建 `SFSafariViewController(url:)`；
  - 系统提供 Done、分享、进度、Reader；
  - 不注入 JS、不处理自签证书（安全默认）。
- 相比 Android 自定义 WebView：无自签 SSL 放行、无自定义重试/浏览器按钮，但行为符合 iOS 平台安全/可访问性；作为 iOS 等价交互。
- 若产品要求 Android 同等 WebView 行为（进度条/错误重试/自签放行），列为 P1 自定义 WKWebView，需单独安全评审。

### 7.2 呈现位置

- `WebLink` 是带 `id` 的 URL 包装（`URL` 本身不满足 `Identifiable`）；所有入口先经 `WebLinkPolicy.isSafe` 白名单。
- **Conversation**：`AppContainer.presentedWebLink` / `presentedWebPage` 驱动 `AppRootView` 根级 `.sheet(item:)`（`SafariView`）与 `.fullScreenCover(item:)`（webroot `WebPageView`）。
- **History / Timeline**：两个入口各自拥有 cover 级 presenter。外链由 `SafariLinkPresenter` 从当前最顶层 UIViewController 直接 present `SFSafariViewController`；webroot 链接由 cover 本地 `InAppLinkPresenter` + `.inAppLinkPresenters` 以 fullScreenCover 呈现。
  - 原因：在外层 fullScreenCover 上再让根级 SwiftUI sheet 呈现 Safari，iOS 会在 Safari 关闭时把 cover 一并 dismiss，丢失 History 详情/时间线页面状态（UI 测试 `testHistoryBubbleLinkKeepsDetail`、`testHistoryAgentOverlayLinkOpensSafariAndKeepsOverlay` 覆盖）。
- 覆盖层链接与气泡链接走同一套路由；链接关闭后覆盖层/详情页保持。
- webroot 转换后的链接由 `AppContainer` 分流到 WKWebView（`docs/ios_webroot_support.md`），其余外链走 Safari。
- URL 校验失败时不呈现、不弹错误（与 Android 静默拦截一致）。

### 7.3 UIKit 例外

`SFSafariViewController` 需要 `UIViewControllerRepresentable`，按 `ios_dev_rules.md` §4 报批：

- 场景：SwiftUI 无 SFSafariViewController 等价物（`Link` 会跳出 App）；
- 影响范围：单文件 `Core/PlatformAdapters/SafariView.swift`；
- 替代方案：`UIApplication.open`（外部 Safari）不需要例外但不符合应用内交互基线。

---

## 8. UI/UX 细节

### 8.1 气泡

- Agent 头行：16pt Agent 图标 + `Agent: HH:mm` 12pt TextSecondary + 展开图标 14pt TextSecondary（仅 job 结果）。
- Markdown 正文：16/24；链接 BrandIndigo；代码/引用 SurfaceVariant；表格消息气泡可整宽。
- AI 图片仍在上、Markdown 在下；用户气泡保持纯文本。
- 气泡最大宽度 75%（含内边距）；含表格时表格在气泡内占满，但气泡不放宽；会话/History/Hot Slot 共用 `AppLayout.messageBubbleMaxWidth`。

### 8.2 全屏 Agent 结果

- 顶栏 52pt，Background；Agent 图标 BrandIndigo；时间 TextSecondary；关闭 44pt。
- 正文整宽，左右 16pt，底部 16pt；ScrollView。
- 无标题“Agent任务”文字（对齐 Android 最终形态）。
- 覆盖层动画：淡入/上滑 200ms；reduceMotion 下无动画。
- iPad 兼容模式内容受限宽（AppRootView 600pt）；不实现原生分栏。

### 8.3 文案（en + zh-Hans）

| Key | English | 简体中文 |
|---|---|---|
| common_view_full_result | View full result | 查看完整结果 |
| conv_job_remaining_chars | View full text · about %d characters left | 查看全文 · 还有约 %d 字 |
| conv_agent_header | Agent | Agent |
| common_close | Close | 关闭 |

- Agent 头行时间直接数字 + 本地时区 HH:mm，不加“Agent任务”。

### 8.4 无障碍

- 链接有系统 Link trait；Agent 展开控件有 label；
- 全屏关闭 44×44，label 关闭；
- Markdown 文本支持 Dynamic Type（主题按 ScaledMetric 重建）；表格降级为代码块后仍可读；
- 覆盖层打开时 VoiceOver 焦点进入顶栏/正文；关闭后回到原气泡（可用 focus state 补充，P1）；
- 颜色对比满足暗色主题；不只用颜色表达状态。

---

## 9. 测试计划

### 9.1 单元测试

| 范围 | 用例 |
|---|---|
| Table fallback | 识别有/无前导管道的表格；分隔行；非表格 `a | b`；水平线 `---`；长 `|-` 行不崩溃；改写后内容完整 |
| Agent 判定 | `ai-msg_agent_job_` 命中；`ai-msg_agent_` 启动提示不命中；LLM 不命中 |
| URL 白名单 | http/https + host 通过；javascript/file/data/mailto/tel/空白/无 host 拒绝 |
| Markdown 源处理 | 表格降级只在 iOS15 执行；iOS16+ 不改写 |
| 折叠（P0 A） | 阈值；300–400 与 400–600 两段边界；只有早段小空行时不折叠；文档末尾空行不算边界；fence/table 内不截断；无安全空行不折叠；剩余源字数；折叠先于表格降级 |
| Source 映射 | `.agent` 头行；`.llm` 不显示 |

### 9.2 UI 测试（静态注入）

- AI Markdown 消息：标题/粗体/列表/链接/代码/表格（iOS 模拟器 18.6 显示原生表格）；
- 用户消息不渲染 Markdown；
- Agent 头行 + 展开图标；点击打开全屏；关闭返回；
- Agent 全屏横竖屏：打开后旋转到横屏（无顶栏、浮动关闭），关闭恢复竖屏；设备已横屏时打开覆盖层直接横屏；
- 窗口宽度：会话、History 详情、Timeline Hot Slot 三个入口在横屏下覆盖层宽度均超过 600pt 内容列，关闭后恢复竖屏；
- 链接点击使用 fake `WebLinkHandling` 记录 URL（不打开真实 Safari）；
- 超长 Agent 结果折叠/入口；短结果不折叠；无安全边界不折叠；
- 图片气泡 + Markdown 共存。

### 9.3 真机（iPad Pro 10.5 / iPadOS 17.7.5）

- 真实 server 返回含标题/列表/表格/链接的 Markdown；
- 链接在应用内 Safari 打开，危险 scheme 被拦截；
- Agent Job 结果长文本折叠（≥400 字符，显示约 N 字）、全屏滚动、关闭、再次打开；
- 表格整宽可读；Markdown 图片不发起网络请求；
- 动态字体/VoiceOver 冒烟；
- M1/M2 语音/相机/图库回归。

### 9.4 构建/回归

- `./scripts/build-ios.sh app` 签名构建；
- 单测/UI 测试；M2 基线不回归；
- 依赖解析可重复：三个包 vendored 在 `apps/ios/deps/`，构建零网络（见 `apps/ios/deps/README.md`）。

---

## 10. 实施顺序

### M3.0 设计 review

1. 锁定：库版本 2.4.1、iOS15 表格降级、Safari 方案、Agent 全屏、折叠 A（源边界截断）；
2. 报批 SFSafariViewController UIKit 例外。

### M3.1 Markdown 基础

3. 以 `apps/ios/deps/` 本地 vendored 包接入 MarkdownUI 2.4.1；
4. `MarkdownMessageView` + `MarkdownTheme`；
5. `ConversationView` AI final 气泡接入；用户/流式保持不变；
6. 表格检测与整宽（含 iOS15 fallback）；
7. 单测（表格降级/URL/agent 判定）。

### M3.2 链接

8. `WebLinkRouter` + `SafariView` + AppRootView sheet；
9. OpenURLAction 白名单；链接 UI 测试。

### M3.3 Agent 结果

10. `ChatMessage.source` + Coordinator/ViewModel 透传；
11. Agent 头行 + job id 判定 + 展开图标；
12. `AgentJobResultOverlay`（全屏、整宽、关闭、链接）；
13. 点击仲裁（链接优先）；
14. UI 测试 + 真机。

### M3.4 折叠（P0）

15. `AgentJobCollapse` 纯函数（400 阈值、fence/table 感知、空行/行尾回退、剩余源字数）；
16. 气泡折叠态渲染 + “查看全文 · 还有约 N 字”入口；
17. 单测/UI 测试（阈值、边界、无安全边界、全屏完整正文）。

---

## 11. 验收标准

- [ ] AI final 消息按 GFM 渲染，用户/流式消息保持纯文本；
- [ ] 标题、粗斜体、删除线、列表、任务列表、引用、代码块、表格、链接可见；
- [ ] iOS 16+ 原生表格；iOS 15 表格内容以代码块降级，不丢失；
- [ ] 链接仅 http/https 可点，应用内 Safari 打开，危险 scheme 被拒绝；
- [ ] Agent 消息显示头行；Agent Job 结果有展开提示并可全屏查看；
- [ ] 全屏查看器整宽、可滚动、可关闭，链接可用；
- [ ] Markdown 内嵌图片不发起网络请求；现有图片查看器不回归；
- [ ] 无 server/SDK/协议改动；无新增 Info.plist 权限；
- [ ] 单测、UI 测试、iPad 真机冒烟通过；
- [ ] 长结果折叠（A）与“还有约 N 字”按 §6.4 实现；全屏始终完整。

---

## 12. 风险与已拍板

| 编号 | 风险/决策 | 影响 | 建议 |
|---|---|---|---|
| R1 | MarkdownUI 原生表格仅 iOS16+ | iOS15 表格退化为普通文本/管道符，列关系丢失 | Table fallback 代码块降级（D5） |
| R2 | MarkdownUI 依赖 NetworkImage | Markdown 图片可能触发网络请求 | 自定义 InlineImageProvider（D6） |
| R3 | 链接点击与整卡点击双触发 | 点链接进入 Safari 同时打开全屏 | 延迟仲裁/仅头行入口（D10），UI 测试守护 |
| R4 | SwiftUI 主题/Dynamic Type | 字号不随系统缩放 | ScaledMetric 构建主题，真机冒烟 |
| R5 | 折叠按源字符而非渲染行 | 可见高度不严格等于 Android 24 行，“剩余字数”含 Markdown 记号 | 已拍板 A（§6.4）；文案使用“约”；无安全边界时不折叠 |
| R6 | SFSafariViewController 不支持自签证书 | 内部自签网站打不开 | 安全默认；P1 再评估 WKWebView |
| R7 | Android 横屏阅读 vs iOS 竖屏 | 宽表格横屏收益缺失 | 已解决：Agent Job 全屏期间解锁横竖屏；iOS 仍尊重系统方向锁（平台差异） |
| R8 | 16KB Markdown 渲染性能 | 滚动卡顿 | final-only、`MarkdownContent` 预解析缓存、可见布局、P0 折叠（A） |
| R9 | MarkdownUI 维护/版本升级 | API 变化 | 隔离层 + exact 版本锁定 |
| R10 | Markdown 图片/HTML/自动链接安全 | 远程请求/XSS 面 | 禁图片、HTML 当文本、URL 白名单 |
| R11 | MarkdownUI 2.4.1 与 Swift 6 strict concurrency / Xcode 版本 | 编译告警或不可用 | 先做依赖接入 spike；锁定 exact 版本；隔离层便于降级 |
| R12 | iOS16+ 宽表格在 75% 气泡宽度内溢出 | 横向截断、阅读困难 | 气泡统一 75% 上限（对齐 Android/Hot Slot）；表格在气泡内整宽；iOS15 降级代码块可横向滚动；真机渲染验收，必要时后续加表格横向滚动 |
| R13 | 折叠最小可见长度 | 早段空行导致只显示极少内容 | A 算法规定 300–400 优先、400–600 次选，否则不折叠 |

**已拍板记录（2026-10-20）**：

1. 折叠纳入本期 **P0**。
2. 折叠采用 **A：源安全边界截断**（≥400 源字符；显示“还有约 N 字”；无安全边界不折叠）。
3. 链接容器使用 **SFSafariViewController**（需 UIKit 例外）。
4. **禁止** Markdown 内嵌网络图片（自定义 InlineImageProvider，不发请求）。
5. **接受 iOS 15 表格降级为代码块**，不升级最低版本。

---

## 13. 文件索引

| 主题 | 文件 |
|---|---|
| 本文 | `docs/ios_agent_job_view.md` |
| webroot 链接支持 | `docs/ios_webroot_support.md` |
| Android Agent Job 设计 | `docs/android_agent_job_view.md` |
| Android Markdown 设计 | `docs/fd_markdown_view.md` |
| Android Markdown 实现 | `apps/android/LivaChatApp/.../ui/components/MarkdownContent.kt` |
| Android Agent Job 实现 | `.../AgentJobResultText.kt`、`.../AgentJobResultOverlay.kt` |
| Android 气泡 | `.../MessageBubble.kt` |
| iOS 会话 UI | `apps/ios/LivaChatApp/Features/Conversation/ConversationView.swift` |
| iOS 消息模型 | `apps/ios/LivaChatApp/Core/Conversation/ConversationModels.swift` |
| iOS SDK aiText | `apps/ios/LivaChatSDK/Sources/LivaChatSDK/LivaChatEvent.swift`（`AITextSource`） |
| iOS 开发规范 | `docs/ios_dev_rules.md` |
| MarkdownUI | https://github.com/gonzalezreal/swift-markdown-ui（2.4.1，MIT） |

---

## 14. 自审记录（2026-10-20）

| # | 问题 | 修正 |
|---|---|---|
| 1 | 文档称 iOS15 表格“直接不渲染/内容消失”不准确 | parser 在 iOS15 不注册 table 扩展，表格退化为普通文本；fallback 改为代码块以保留可读列结构 |
| 2 | D1 要求只有 `MarkdownMessageView.swift` import MarkdownUI，但 `MarkdownTheme.swift` 必须使用 MarkdownUI 类型 | 隔离层明确为 `MarkdownMessageView.swift` + `MarkdownTheme.swift` |
| 3 | 折叠取阈值前的最后空行，可能只显示极短前缀（如 100 字后接长代码块） | 改为 300–400 优先、400–600 次选；否则不折叠 |
| 4 | 折叠允许任意行尾截断，可能切断跨行链接/强调/HTML | 只允许 fence/table 之外的空行作为边界；无安全空行则不折叠 |
| 5 | 表格降级与折叠处理顺序未定义，可能用降级后文本计算源字数 | 明确：源 Markdown 折叠 → 降级 → 渲染 |
| 6 | 覆盖层未显式禁止底层交互，依赖“不透明背景拦截”假设 | 明确底层 `.allowsHitTesting(false)` + 覆盖层 contentShape |
| 7 | `WebLinkRouter.presentedURL: URL?` 不能直接用于 `.sheet(item:)`（URL 不是 Identifiable） | 引入 `WebLink` 包装类型 |
| 8 | MarkdownUI 每次 view body 可能重新解析长文本 | 使用 `MarkdownContent` 预解析 + 隔离层缓存（message id/text hash/降级标志） |
| 9 | Agent 气泡头行文案写成 Agent 图标 + 时间，遗漏 Android 的 `Agent:` 前缀 | 气泡头行明确 `Agent: HH:mm`；全屏覆盖层只显示图标 + 时间 |
| 10 | 折叠长度按渲染行数假设，iOS 无法精确复刻 | 已拍板 A，文案使用“还有约 N 字”，风险记录 |

---

## 15. 实施记录（2026-10-20）

**状态**：M3 已实现；模拟器单测/UI 测试与 iPad（iPad Pro 10.5，iPadOS 17.7.5）真机 UI 测试全部通过。

### 15.1 落地文件

| 文件 | 内容 |
|---|---|
| `Features/Conversation/MarkdownMessageView.swift` | MarkdownUI 隔离层；`@ScaledMetric` 字号；iOS15 表格降级；`OpenURLAction` 白名单；无网络图片 provider；`MarkdownContent` 预解析缓存（50 条） |
| `Features/Conversation/MarkdownTheme.swift` | 暗色主题 token；按字号缓存 `Theme` |
| `Features/Conversation/MarkdownTableFallback.swift` | GFM 表格识别与代码块降级；折叠扫描共用的 fence/分隔行判定 |
| `Features/Conversation/AgentJobCollapse.swift` | 源边界折叠（400/300/600）；fence/table 感知；结果缓存（50 条） |
| `Features/Conversation/AgentJobResultOverlay.swift` | 全屏结果阅读器（竖屏 64pt 顶栏、横屏浮动关闭、整宽 Markdown） |
| `Features/Conversation/AgentJobResultWindow.swift` | 窗口级共享宿主：owner/payload、`AgentJobResultWindowCenter`、`.agentJobResultWindow` 修饰符 |
| `App/AppOrientation.swift` | Agent Job 期间临时解锁横竖屏的 `AppDelegate` / `AppOrientationController` |
| `Core/PlatformAdapters/WebLinkRouter.swift` | `WebLink`、`WebLinkPolicy`、`SFSafariViewController` 适配（Done delegate） |
| `ConversationModels.swift` / `ConversationCoordinator.swift` / `ConversationViewModel.swift` | `source` 透传；job 结果浮层状态与清理 |
| `ConversationView.swift` | Markdown 气泡、Agent 头行、整卡点击仲裁、折叠入口、发布全屏 payload、表格整宽 |
| `AppContainer.swift` / `AppRootView.swift` | 全局 Safari sheet；UI 测试 Markdown/Agent 注入 |
| `LivaChatAppTests/M3MarkdownTests.swift` | 表格降级、URL 白名单、Agent 判定、折叠边界、VM 状态（21 条） |
| `LivaChatAppUITests/AppShellUITests.swift` | Markdown 渲染、链接 Safari、Agent 浮层/折叠/完整正文、job 链接仲裁（4 条） |

### 15.2 实现细节与差异

1. **依赖**：`apps/ios/deps/` 内置 MarkdownUI `2.4.1`、NetworkImage `6.0.1`、swift-cmark `0.9.0`（本地 SPM 引用，版本/revision 见 `apps/ios/deps/README.md`）；无远程包与 `Package.resolved`。
2. **Dynamic Type**：主题字号来自 `@ScaledMetric(relativeTo: .body)`，标题与正文同字号加粗；主题按字号缓存，避免每次 body 重建。
3. **预解析缓存**：`MarkdownContent` 以 `message id + 文本 hash + prefix/full` 为 key 缓存 50 条；会话清空时与 `AIImageDecoder`、折叠缓存一起清理。
4. **点击仲裁**：链接打开计数优先；Job 卡片点击延迟 120ms，若期间链接打开则取消。UI 测试验证链接只打开 Safari、不叠加全屏浮层。
5. **Safari 关闭**：iOS 26+ 使用浮动 X（标识 `Close`），旧系统为 `Done`；Conversation 通过 delegate 清空 SwiftUI sheet 状态，History/Timeline 由 `SafariLinkPresenter` 直接 present，关闭后 cover、详情页与结果覆盖层保持。
6. **覆盖层无障碍**：根视图使用 `.accessibilityElement(children: .contain)`；否则根 `accessibilityIdentifier` 会向子元素传播并覆盖关闭按钮标识。
7. **Agent 头行**：合并为一个无障碍元素 `Agent: HH:mm`；Job 卡片额外提供 “View full result” 无障碍动作。
8. **表格与折叠顺序**：折叠基于原始 Markdown；iOS15 表格降级仅发生在渲染前，不参与剩余字数计算。
9. **降级范围**：iOS15 表格转 `text` 代码块，单元格内链接不可点（内容完整）；已接受。

### 15.3 测试结果

- `LivaChatAppTests` 全量通过（含新增 `M3MarkdownTests` 21 条与 Coordinator `source` 透传用例）。
- 模拟器 `AppShellUITests` 全量通过（含新增 4 条 M3 用例）。
- iPad Pro 10.5 / iPadOS 17.7.5 真机：4 条 M3 UI 测试全部通过；截图确认标题、粗斜体、删除线、列表、引用、代码块、原生表格、链接；Agent 头行、全屏浮层、关闭与完整正文。
- `./scripts/build-ios.sh app` 签名构建成功（GUI Terminal）。

### 15.4 未覆盖/待人工验证

- 真实 server 语音端到端 Markdown/Agent Job 返回未跑：语音触发需人工说话；本次使用同一 UI 管线的静态注入 + SDK `source` 单测覆盖。
- VoiceOver 实机朗读与 Dynamic Type 大字号逐档冒烟未做。
- iOS 15 真机未覆盖（无设备）；表格降级由单测覆盖。

### 15.5 Review 修复（2026-10-20）

| # | 问题 | 修复 |
|---|---|---|
| 1 | 折叠阈值按实机反馈偏高 | 阈值 800 → **400**；窗口按同比例调整：可见区 300–400，次选 400–600（`minChars=400`、`minVisibleChars=300`、`maxVisibleChars=600`） |
| 2 | 文档末尾换行/空行会被当作安全边界，可能把 500 字消息折成“还有 1 字” | 只接受其后仍有正文的空行；末尾空行不算边界，新增单测 |
| 3 | **严重**：MarkdownUI 的块级图片（独占段落 `![](url)`）默认走 `DefaultImageProvider` → `NetworkImage` 发起网络请求，仅有 Inline provider 被禁用 | 同时注入 `.markdownImageProvider(NoNetworkImageProvider())`，块级图片不渲染、不发请求 |
| 4 | CRLF 文本的空行识别失败 | fence/表格/空行判定统一使用 `.whitespacesAndNewlines` |
| 5 | 折叠/解析缓存未会话清理 | 消息清空与 Settings 应用重连时清空 `MarkdownContent` 与折叠缓存；key 含 message id，避免跨消息复用 |
| 6 | 覆盖层状态在消息消失后可能残留 | 消息变化时若按 id 找不到 job 结果则自动关闭覆盖层 |
