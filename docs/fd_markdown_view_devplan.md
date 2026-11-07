# AI 消息 Markdown 渲染与应用内 WebView — 开发与测试计划

> **Date**: 2026-08-11
> **Source**: `./fd_markdown_view.md`（最终 v1，已定稿）
> **Status**: draft（待评审）
> **决策摘要**: Markwon 4.6.2 自封装（`MarkdownContent` 隔离层，d4 方案 A）+ 应用内 `WebViewScreen`（k3 体系）；零工具链升级；工作量 **2–3 人日**

---

## 1. 开发阶段总览

| 阶段 | 内容 | 预估工期 | 验收方式 |
|------|------|----------|----------|
| **P0 — POC 验证** | 设计文档 §3.3 全 8 项（§3.1 明细） | 0.5d | 每项判定标准明确，任一不通过按回退方案执行 |
| **P1 — 依赖接入 + MarkdownContent 组件** | `LivaChatApp/build.gradle.kts` +4（至多 6）个 Markwon 依赖；隔离层组件 + LinkResolver 白名单 + 主题映射 | 0.5–0.75d | `assembleDebug` 通过；`dependencies` 无版本牵引；MD-01~05 用例过 |
| **P2 — MessageBubble 改造 + 导航接线** | AI/final 分支替换；`onLinkClick` 参数；ConversationScreen/HistoryDetailScreen 透传 | 0.25d | 编译通过；MD-06~08 用例过 |
| **P3 — WebViewScreen + 路由** | 屏幕 + `Routes.webview` + NavGraph destination + §6.3 安全基线全项 | 0.5d | WV-01~06 用例过；安全清单逐项核对签字 |
| **P4 — 测试补齐** | 单测 + Compose UI + E2E 扩展 + 回归 | 0.5–1d | §3 全部用例过；CI 全量绿 |
| **P5 — 手工验收** | 深/浅色、历史页、长文本、断网、长按复制、宽表格 | 0.25d | §6.1 手工清单逐项签字 |

**合计 ≈ 2.5–3.25 人日**（与设计文档 §8 的"POC 0.5 + 实施 1–1.5 + 测试 0.5–1"口径一致；P5 可与 P4 并行）。

---

## 2. 测试策略

### 2.1 测试分层

```
┌──────────────────────────────────────────────┐
│  E2E (androidTest)                           │
│  ─ MockServerHarness 下发 ai_text (含链接/表格)│
│  ─ 全链路: 渲染 → 点击 → WebView 打开          │
├──────────────────────────────────────────────┤
│  Compose UI (androidTest)                    │
│  ─ MarkdownContent 渲染/链接/表格             │
│  ─ WebViewScreen (mockwebserver 本地页)       │
├──────────────────────────────────────────────┤
│  单元 (test/, 纯 JVM)                        │
│  ─ isHttpUrl scheme 白名单                   │
│  ─ 主题映射纯逻辑（若有）                     │
└──────────────────────────────────────────────┘
```

### 2.2 Mock 策略

| 组件 | Mock 方式 | 工具 |
|------|-----------|------|
| Markdown 渲染 | 固定样本字符串（含全部元素），不 mock 库 | 无（断言 TextView 文本/Spannable 样式） |
| 链接点击 | Compose UI 测试 `performClick` + 回调收集断言 | `compose-ui-test-junit4`（已有） |
| WebView 页面 | mockwebserver 起本地 http 页 | `mockwebserver:4.12.0`（已有） |
| E2E ai_text 下发 | 复用现有 `MockServerHarness` | 已有 |
| URL scheme 判定 | 纯函数断言 | JUnit（local） |

---

## 3. 详细测试用例

### 3.1 P0 — POC 验证（设计文档 §3.3 映射）

| ID | 验证内容 | 方法 | 判定标准 | 不通过的回退 |
|----|----------|------|----------|--------------|
| POC-1 | 依赖引入零版本牵引 | 加依赖后 `./gradlew :LivaChatApp:assembleDebug` + `dependencies` 检查 | 构建通过；Compose/Kotlin/AGP 版本无变化；无 `META-INF` 冲突 | 按冲突项追加 `packaging.excludes`（预期不发生） |
| POC-2 | 渲染样例（标题/粗斜体/列表/引用/代码块/表格/任务列表/链接） | `@Preview` 暗色气泡目视 | 全部元素可读、配色正确 | 调整 `MarkwonTheme` 映射 |
| POC-3 | LinkResolver 全量出口 | 构造 `[t](u)` / 裸 URL / **表格单元格内链接** 样本 | 三者均触发 `onLinkClick`；非 http(s) scheme 不触发 | 检查插件拼装；表格链接若有独立路径则评估适配（方案 A 的核心差异化能力，必须解决） |
| POC-4 | soft break × 空行折叠（adapter L128） | 构造单 `\n` 折叠样本 | 段落版式可接受；或 `MarkwonConfiguration.softBreakAddsNewLine` 调整后可接受 | 渲染层预处理；**原则上不动 adapter**（其折叠逻辑同时服务 overlay）——需评审确认（§8 R-01） |
| POC-5 | `html` 插件取舍 | 加/不加 `HtmlPlugin` 对比 `<a>` 渲染 | `<a>` 可点且有样式，或团队接受降级 | 不引入插件，`<a>` 按字面文本显示（评审确认接受度，§8 R-02） |
| POC-6 | LazyColumn 性能 | 50+ 条 Markdown 气泡滚动 | 无掉帧 | Markwon 实例缓存 + 滚动实测调参 |
| POC-7 | 文本选择 × 链接点击 | 手工长按/点按 | 点按开链、长按选择不冲突 | `setTextIsSelectable(false)` 或自定义 `LinkMovementMethod` |
| POC-8 | 宽表格显示 | 多列表格在 75% 屏宽气泡内 | 可读或容器横滚 | 压缩列宽 / 横滚兜底（评审确认接受度，§8 R-03） |

**门槛**：POC-1/3 必须通过才进入 P1；POC-4/5/7/8 的结论记录进实现（影响主题映射/插件清单/交互）。

### 3.2 单元测试 — URL scheme 白名单（local）

**文件**: `LivaChatApp/src/test/kotlin/dev/livachat/app/ui/components/MarkdownUrlTest.kt`（`isHttpUrl`，设计 §4.6）

| ID | 用例 | 输入 | 预期 |
|----|------|------|------|
| URL-01 | http 放行 | `http://example.com/a?b=1#f` | `true` |
| URL-02 | https 放行 | `https://example.com` | `true` |
| URL-03 | javascript 拒绝 | `javascript:alert(1)` | `false` |
| URL-04 | file 拒绝 | `file:///etc/passwd` | `false` |
| URL-05 | data 拒绝 | `data:text/html,<script>` | `false` |
| URL-06 | intent 拒绝 | `intent://example.com/#Intent;end` | `false` |
| URL-07 | 大写 scheme | `HTTPS://example.com` | `true`（大小写不敏感） |
| URL-08 | 畸形 URL | `http://exa mple.com` | `false` |
| URL-09 | 无 scheme | `example.com` | `false` |
| URL-10 | 空 scheme | `://x` | `false` |

### 3.3 Compose UI 测试 — MarkdownContent（androidTest）

**文件**: `LivaChatApp/src/androidTest/kotlin/dev/livachat/app/ui/components/MarkdownContentTest.kt`

> 断言方式说明：组件内部为 `AndroidView { TextView }`，用 `onNodeWithText` 匹配文本；Markdown 解析后文本不应含 `**`/`#` 等字面量；链接点击经 `LinkMovementMethod` 分发，回调收集后断言。

| ID | 用例 | 输入 | 预期 |
|----|------|------|------|
| MD-01 | 常用语法渲染 | `**粗体**`、`# 标题`、列表、`> 引用`、代码块 | TextView 文本无字面量标记；`**粗体**` 显示为"粗体" |
| MD-02 | `[text](url)` 链接可点 | `[文档](https://example.com)` | `performClick` → `onLinkClick` 收到 `https://example.com` |
| MD-03 | 裸 URL 自动成链 | `看这里 https://example.com` | 点击 URL 段 → 回调正确 URL（依赖 linkify 插件，POC-3 前置） |
| MD-04 | **表格 + 表格内链接** | 含 `[链接](url)` 的 GFM 表格 | 表格渲染存在；单元格内链接点击触发回调（方案 A 差异化用例） |
| MD-05 | 非法 scheme 不导航 | `[x](javascript:alert(1))` | 点击 → `onLinkClick` 不被调用 |
| MD-06 | 用户气泡保持纯文本 | `isUser=true`，文本含 `**`、`#` | 原样字面量显示，不解析 |
| MD-07 | AI 非 final 防御分支 | `isFinal=false` | 纯文本 + 光标（现状） |
| MD-08 | `onLinkClick=null` 兼容 | AI 消息，未传回调 | 保持现有 `Text` 行为（设计 §4.4） |

### 3.4 Compose UI 测试 — WebViewScreen（androidTest + mockwebserver）

**文件**: `LivaChatApp/src/androidTest/kotlin/dev/livachat/app/ui/screens/webview/WebViewScreenTest.kt`

| ID | 用例 | 输入 | 预期 |
|----|------|------|------|
| WV-01 | 正常加载 | mockwebserver 本地 http 页 | 页面标题/内容显示；进度条出现后消失 |
| WV-02 | 非法入参 | `javascript:` / `file:` URL | 错误占位，不加载 |
| WV-03 | 站内链接跳转 | 页内链接指向同服务器另一页 | 继续在 WebView 内加载（`shouldOverrideUrlLoading` 拦截路径） |
| WV-04 | 重定向到非法 scheme | 页面含 `javascript:` 链接 | 被丢弃，不执行 |
| WV-05 | 加载失败 | 404 / 断网 | 错误占位 + "重试/浏览器打开"可见 |
| WV-06 | 返回键行为 | 加载两页后按返回 | `canGoBack` → 回退一页；首页按返回 → `popBackStack` |

### 3.5 E2E — Markdown 链接全链路（androidTest）

**文件**: 扩展 `ConversationE2ETest` 或新增 `MarkdownLinkE2ETest.kt`（复用 `MockServerHarness` 与现有 E2E 的 Activity 启动方式，同 `ColdStartE2ETest` 模式）

| ID | 用例 | 输入 | 预期 |
|----|------|------|------|
| E2E-01 | 含链接/表格的 ai_text 全链路 | Mock 后端下发 final 消息：`**粗体**` + `[链接](http://127.0.0.1:<harness-port>/page)` + 表格 | 消息列表渲染（无字面量）；点击链接 → WebView 显示 harness 页面内容 |
| E2E-02 | 用户消息不误渲染 | 用户文本含 `*`、`#`、`_` 字符 | 原样显示 |

### 3.6 回归用例

| ID | 用例 | 验证方式 |
|----|------|----------|
| RG-01 | 现有 `ConversationViewModelTest` / `ConversationListenerAdapterTest` | CI 全量（无 VM/Adapter 改动，应零影响） |
| RG-02 | 现有 `MessageBubbleTest`（纯 JUnit 模型测试） | 不破坏，保持绿 |
| RG-03 | 现有 E2E（Reconnect / ColdStart / Interrupt / Settings / HistoryPersistence） | CI 全量 |
| RG-04 | 依赖树回归 | `./gradlew :LivaChatApp:dependencies` 与 POC-1 基线一致 |
| RG-05 | 用户消息、camera/lecture overlay 纯文本行为 | 手工冒烟（P5 清单） |

---

## 4. 测试基础设施

### 4.1 新增文件

```
LivaChatApp/src/test/kotlin/dev/livachat/app/ui/components/MarkdownUrlTest.kt        ← URL scheme 单测
LivaChatApp/src/androidTest/kotlin/dev/livachat/app/ui/components/MarkdownContentTest.kt  ← 组件 UI 测试
LivaChatApp/src/androidTest/kotlin/dev/livachat/app/ui/screens/webview/WebViewScreenTest.kt ← WebView UI 测试
LivaChatApp/src/androidTest/kotlin/dev/livachat/app/e2e/MarkdownLinkE2ETest.kt       ← E2E（或并入 ConversationE2ETest）
```

### 4.2 测试依赖

**无新增**：复用现有 `compose-ui-test-junit4`、`ui-test-manifest`、`mockwebserver:4.12.0`、`hilt-android-testing`、`mockk`、`turbine`；local 纯逻辑用 JUnit + 现有 Robolectric 配置（`isIncludeAndroidResources=true`）。

### 4.3 注意事项

- **AndroidView + Compose 测试**：`onNodeWithText` 可匹配 TextView 文本；链接点击需在文本节点上 `performClick`（`LinkMovementMethod` 分发），断言回调用 `CountDownLatch`/`runOnIdle` 收集。
- **WebView 测试限制**：Robolectric 对 `android.webkit.WebView` 支持有限 → WebViewScreen 相关用例**全部走 androidTest**（本地 mockwebserver 起真实页面）。
- **E2E 复用点**：`MockServerHarness` 现有 ai_text 下发通道直接复用；确认 `AndroidManifest` 已含 `INTERNET` 权限（App 现有网络栈，应有，核对即可）。
- **URL 编码**：导航用 `Routes.webview(url)` 内 `Uri.encode`；测试含 `%`、`?`、`&` 的 URL（用例 URL-01 覆盖）。

---

## 5. 开发顺序（建议）

```
P0 (POC, 0.5d) ──必须全过──▶ P1 (依赖+组件, 0.5-0.75d)
                                    │
P2 (MessageBubble+接线, 0.25d) ◀────┘   ← 此步完成即具备手工冒烟条件
        │
P3 (WebViewScreen+路由, 0.5d)          ← 此步完成即全链路可点
        │
P4 (测试补齐, 0.5-1d)  ═╗  P5 (手工验收, 0.25d)  ← 可并行
        └──────────────┴──▶ 验收签字
```

- 每阶段结束均可编译、对应用例绿，才进入下一阶段。
- P0 的 POC-3（表格内链接）与 POC-1（版本牵引）为硬门槛；POC-4/5/8 结论写入实现（影响插件清单与主题映射）。
- P2 与 P3 顺序可调（先做 WebView 屏可提前暴露路由问题）。

---

## 6. 验收标准（DoD）

### 6.1 功能验收（对应设计文档 §1.2 目标）

| # | 验收项 | 方式 |
|---|--------|------|
| F-01 | AI 消息 Markdown 渲染（标题/粗斜体/列表/引用/代码块/**表格**/任务列表/链接） | UI 测试 + 手工 |
| F-02 | 链接（`[t](u)`/裸 URL/`<a>`）点击 → 应用内 WebView | E2E-01 + 手工 |
| F-03 | 历史详情页同步获得能力 | 手工（`HistoryDetailScreen` 复用 `MessageBubble`） |
| F-04 | 用户气泡保持纯文本（含 `*`/`#` 字符不误渲染） | MD-06/E2E-02 |
| F-05 | 流式 overlay / camera / lecture 保持纯文本 | 手工冒烟（RG-05） |
| F-06 | 协议/模型/Room/构建链零改动 | git diff 核查（仅 §5 列出的文件 + 测试） |
| F-07 | 非 http(s) scheme 不导航 | URL-03~06、MD-05、WV-02/04 |
| F-08 | 深色/浅色配色正确（正文/链接/代码块/引用） | 手工（P5） |

### 6.2 质量验收

- §3 全部用例通过；CI 全量绿（含现有 VM/Adapter/E2E 测试）。
- `./gradlew :LivaChatApp:lintDebug` 无新增 error。
- 代码审查约束：**全 App 仅 `MarkdownContent.kt` import markwon**（隔离原则）；`onLinkClick` 默认 null 保持调用兼容；无 VM/Repository 改动。

### 6.3 安全验收（设计文档 §4.6 逐项核对）

- [ ] `javaScriptEnabled = false`；`domStorageEnabled = false`；`allowFileAccess`/`allowContentAccess = false`；`setSupportMultipleWindows(false)`；无任何 JS bridge
- [ ] scheme 白名单**双重校验**：`MarkdownContent` 拦截处 + `WebViewScreen` 入参
- [ ] `shouldOverrideUrlLoading` 拦截重定向：仅 http/https 内部加载，其余丢弃
- [ ] 非法入参显示错误占位 + "浏览器打开"兜底
- [ ] `DisposableEffect` 中 `webView.destroy()`（无泄漏）

### 6.4 性能验收

- POC-6 复验：50+ 条 Markdown 气泡滚动无掉帧。
- 单条消息（KB 级）同步解析耗时无感知卡顿。

---

## 7. 未覆盖的边界场景（后续迭代）

- 流式（token 级）Markdown：需工具链升级后走 mikepenz `StreamingMarkdownState`（设计 §9）。
- Markdown 内嵌网络图片（Markwon coil 插件；`ChatMessage.image` 通道已独立，互不影响）。
- 代码块语法高亮、复制按钮。
- 宽表格横向滚动等增强交互。
- `LivaChatAppMock` 接入（本期明确不做）。
- `VideoPreview.lastAiText` / `TranscriptionBubble` 的 Markdown 化。
- WebView 内 JavaScript 按白名单开启（当前全关，纯阅读场景）。

---

## 9. 执行记录（2026-08-11）

### 9.1 实施完成清单

| # | 文件 | 状态 |
|---|------|------|
| 1 | `LivaChatApp/build.gradle.kts` | ✅ +4 Markwon 依赖（core/linkify/ext-tables/ext-tasklist）；espresso-core 3.6.1→3.7.0（修复 API 33+ 兼容） |
| 2 | `ui/components/MarkdownContent.kt` | ✅ 隔离层 + `isHttpUrl` 白名单 + `buildMarkwon` 工厂（可测）+ 主题映射 + `SoftBreakAddsNewLinePlugin`（POC-4 结论：adapter 折叠空行后段落需换行渲染） |
| 3 | `ui/components/MessageBubble.kt` | ✅ `onLinkClick` 参数 + AI/final 分支 + Markdown Preview |
| 4 | `ui/screens/webview/WebViewScreen.kt` | ✅ 全项（安全基线、错误占位、进度条、BackHandler、destroy 前先脱离视图树） |
| 5 | `ui/navigation/Routes.kt` / `NavGraph.kt` | ✅ `webview/{url}` 路由 + 两屏幕 `onOpenLink` 接线 |
| 6 | `ui/screens/conversation/ConversationScreen.kt` | ✅ `onOpenLink` 参数（含 `ConversationVoiceContent` 透传） |
| 7 | `ui/screens/history/HistoryDetailScreen.kt` | ✅ `onOpenLink` 参数 |
| 8 | 测试 | ✅ 见 §9.2 |

### 9.2 测试结果

| 套件 | 位置 | 结果 |
|------|------|------|
| `MarkdownUrlTest`（10 例，scheme 白名单） | local | ✅ 全过（需 Robolectric，`android.net.Uri` 在纯 JVM 不可用） |
| `MarkdownRenderTest`（9 例：渲染/链接/裸 URL/**表格内链接**/soft break/畸形/空文本） | local | ✅ 全过（POC-2/3/4 的 JVM 验证；表格以 `TableRowSpan` 绘制，经反射取单元格 LinkSpan 验证解析链路） |
| `WebViewUrlPolicyTest`（6 例，导航拦截策略） | local | ✅ 全过 |
| `MarkdownContentTest`（4 例：interop 冒烟 + 真实触摸链路） | androidTest | ✅ 全过（模拟器） |
| `MessageBubbleRenderingTest`（4 例：用户纯文本/AI Markdown/非 final 光标/无回调） | androidTest | ✅ 全过（模拟器） |
| `WebViewScreenTest`（4 例） | androidTest | ⚠️ 1/4（非法入参占位）；3 例（页面加载/404/返回键）受环境阻塞（见 §9.3） |
| 回归：现有 `testDebugUnitTest` 全量 | local | ✅ 91 过 / 2 失败（`InterruptCoordinatorTest`，**既有缺陷**，与本次改动无关，见 §9.3） |
| `lintDebug` | — | ✅ 无 error |

**过程中修复的既有测试破损**（与 Markdown 无关，`87a6133` 引入）：`ChatRepositoryTest`/`HistoryRepositoryTest`/`HistoryUseCaseTest`/`SettingsViewModelTest` 编译失败（构造参数、`deleteSession` 迁移、`vadInterruptEnabled` 移除、`defaultFrontCamera` 默认值），已最小化同步修复。

### 9.3 环境阻塞项（非代码缺陷）

| 项 | 现象 | 影响 | 处理 |
|----|------|------|------|
| 模拟器 WebView 渲染进程崩溃 | WebView 134.0.6998.135 在 x86_64 模拟器上加载页面即崩溃（`Renderer process crash detected (code -1)`，host GPU 与 swiftshader 均复现） | WV-01/03/04/05/06 无法在模拟器验证 | 逻辑层已由 `WebViewUrlPolicyTest`（JVM）覆盖；加载路径需在健康设备/模拟器复验 |
| 华为真机（LIO-AN00, API 31）`createComposeRule` 不可用 | "No compose hierarchies found"（测试 Activity 0x0，espresso 升级后仍复现；同 APK 中 `ActivityScenario` 测试正常） | 全部 compose UI 测试无法在该设备运行 | 改用 x86_64 模拟器执行 androidTest |
| 全 UI E2E 不可行（本期） | App 硬编码 `wss://<lan-host>:8080`（TLS），无 TLS mock 能力；且 `MockServerHarness` 为明文 ws | E2E-01 无法落地 | 记入后续迭代：需 TLS 化 harness 或 App 配置注入；组件级测试（渲染+点击链路）已覆盖 E2E 的 UI 侧 |
| `InterruptCoordinatorTest` 2 例稳定失败 | `AudioPlaybackController.flush()` 验证失败 | 与本功能无关的既有缺陷 | 记录，建议单独提单 |

### 9.4 POC 结论与评审待决项回填

| POC | 结论 |
|-----|------|
| POC-1 版本牵引 | ✅ 无（BOM/Compose/Kotlin 全部不变） |
| POC-2 渲染 | ✅ JVM + 设备双验证 |
| POC-3 链接全量出口 | ✅ 普通链接/裸 URL/**表格单元格内链接**均触发 resolver（方案 A 差异化能力实测成立） |
| POC-4 soft break | ✅ 默认渲染为空格会粘连段落 → 已加 `SoftBreakAddsNewLinePlugin` 改为换行（未动 adapter） |
| POC-5 html 插件 | ⏸ 未引入（R-02 默认：`<a>` 降级字面文本，待评审确认） |
| POC-6 列表性能 | ⏸ 需真机 50+ 条滚动目测（P5 手工项） |
| POC-7 文本选择 | ⏸ 本期未启用 `setTextIsSelectable`（R-04 默认：链接点击优先，待评审确认） |
| POC-8 宽表格 | ⏸ 表格经 `TableRowSpan` 画布绘制，需真机目测列宽（R-03，P5 手工项） |

### 9.5 待手工验收（P5，需真机）

- 深色/浅色主题下 Markdown 配色（链接色/代码块底色/引用边线）。
- 历史详情页渲染与链接跳转（复用 `MessageBubble`，逻辑已随组件验证）。
- 50+ 条 Markdown 气泡滚动流畅度（POC-6）。
- 宽表格在 75% 屏宽气泡内的显示（POC-8）。
- 长按文本选择（当前未启用，见 R-04）。
- WebView 加载路径（页面显示/内链导航/重定向拦截/断网重试）——需健康设备（§9.3）。
- WebView 安全配置复核（JS off / domStorage off / 文件访问 off / 无 bridge / 双重 scheme 校验）。
- SSL 错误放行验证（`onReceivedSslError` → proceed，产品决策 2026-08）：自签名/过期证书页面可打开；确认 JS 关闭前提下风险接受。

---

## 9.6 评审修复记录（2026-08-11，二轮）

| 来源 | 问题 | 修复 |
|------|------|------|
| 二轮评审 P1 | **主题切换后已渲染气泡的 span 配色不更新**：tag 仅比较 markdown 字符串，主题变化时 markwon 重建但 markdown 未变 → `setMarkdown` 跳过 → 烘焙进 span 的代码块底色/引用边线/listItem 色保持旧主题（正文/链接色经 `setTextColor` 已更新，半新半旧） | tag 改为绑定 `renderKey = (markwon, markdown)` 身份，主题或内容任一变化即重渲（`MarkdownContent.kt`） |
| 二轮评审 P1 | **行距换算误用 density**：`setLineSpacing` 的 add 参数是像素，`textSize` 按 sp 经 `scaledDensity` 换算；系统字体缩放时两者不等，行距偏窄 | 改用 `displayMetrics.scaledDensity`（factory + update 两处） |
| 首轮评审 | WebView 错误态重试无法恢复（loadError 无重置路径 + AndroidView 被移出组合） | Box 覆盖层常驻 WebView；`onPageStarted` 重置错误态 |
| 首轮评审 | 404 等 HTTP 错误码不触发 `onReceivedError`（应走 `onReceivedHttpError`） | 补 `onReceivedHttpError`（main frame 判定） |
| 首轮评审 | "在浏览器中打开"无 try/catch 会崩溃 | 提取 `openInBrowser()` 兜底 |
| 首轮评审 | `onPageStarted` 签名错误（API 24+ 为三参含 favicon） | 修正签名 |
| 首轮评审 | 自定义 `LinkHandler` 可简化；字体缩放不响应 | 改 `rememberUpdatedState`；update 块同步 textSize/lineSpacing |

---

## 8. 评审待决事项（Review Items）

| ID | 事项 | 默认立场（设计文档） | 需评审确认 |
|----|------|---------------------|-----------|
| R-01 | POC-4 空行折叠版式问题 | 渲染层解决，**不动 adapter**（L128 同时服务 overlay） | 若渲染层手段不足，是否允许改 adapter？ |
| R-02 | POC-5 `html` 插件取舍 | POC 后决定 | 是否接受 `<a>` 标签降级为字面文本（不引入插件）？ |
| R-03 | POC-8 宽表格接受度 | 压缩列宽显示 | 接受"压缩"还是要求"容器横滚"（影响约 0.25d）？ |
| R-04 | 文本选择（POC-7） | `setTextIsSelectable(true)` | 若与链接点击冲突，接受禁用文本选择？ |
| R-05 | E2E 形态 | 扩展 `ConversationE2ETest` | 接受新增独立 `MarkdownLinkE2ETest`（含 UI 点击断言，维护成本略高）？ |
