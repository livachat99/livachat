# LivaChat iOS webroot 链接支持（livachat_url）

**状态**：已实现并通过模拟器/真机测试（2026-10-20）
**上游文档**：
- `docs/webroot_feature.md`（Android 渲染期转换与应用内 WebView）
- `docs/ios_agent_job_view.md`（iOS Markdown 渲染与链接入口）
- `docs/setting_server_address.md`（服务器地址推导）

---

## 1. 目标

Agent 任务结果会包含内部页面链接 `livachat_url://webroot/<uuidv7>[/<path>][/]`。iOS 需要与 Android 一致：

1. 渲染 Markdown 前，把链接替换为当前服务器的实际地址；
2. 点击后在应用内打开页面，支持自签证书的局域网服务器；
3. 非法 token 原样保留，不发请求；
4. 普通外链行为不变。

**已拍板（2026-10-20）**：仅转换后的 webroot 链接进入应用内 WKWebView；其他 http(s) 链接继续使用 SFSafariViewController；自签证书仅对当前 server 的 host:port 放行。

---

## 2. Android 基线

`apps/android/.../ui/navigation/ServerLinks.kt`：

- 渲染期把文本中所有 `livachat_url://webroot/<uuidv7>[/…]` 替换为 `<serverBase>/webroot/<rest>`；
- `serverBase` 由 WebSocket 地址推导：`wss→https`、`ws→http`，缺省端口 443/80，**始终带端口**；
- 当前不做 IP→域名映射（代码中已注释）；
- token 边界：空白、`)`、`]`、`>`、`"`、`'`、任意非 ASCII；尾部 ASCII 标点 `.,;:!?` 剥离后留在原位；
- 校验：首段必须是 UUIDv7（大小写不敏感），token 不得含空白、`..`、`\`；非法片段原样保留；
- 支持 Markdown 链接、裸文本、多枚链接、query、深层路径；
- 点击进入 `WebViewScreen`：关闭/标题/浏览器打开/进度/错误重试；拦截非 http(s) 导航；SSL 错误无条件 `proceed()`。

---

## 3. iOS 实现

### 3.1 转换（纯函数）

`Features/Conversation/LivachatURLConverter.swift`：

- `convert(_:serverBaseURL:)` 1:1 移植 Android 语义（前缀大小写不敏感、UUIDv7、token 边界、尾随标点、非法保留、空 base 原样返回、幂等）；
- 使用 Swift `Character` 扫描；UUIDv7 用位置/字符校验，避免正则；
- 只在 **AI final Markdown** 渲染前调用（普通 AI 气泡与 Agent Job 全屏共用 `MarkdownMessageView`）；
- 流式气泡、相机浮层、用户消息保持纯文本，不做转换（与 Android 一致）；
- 折叠仍基于原始 Markdown；转换发生在折叠前缀选定之后，剩余字数仍按源字符计算。

### 3.2 服务器基址

`Core/Settings/ServerAddress.swift` 新增 `ServerBase`：

```swift
ServerBase(webSocketURL: URL, allowsSelfSigned: Bool) -> ServerBase?
```

- `wss → https`、`ws → http`，端口缺省 443/80，`baseURLString` 始终包含端口；
- `isWebrootURL(_:)` 判断 scheme/host/port 是否与当前 server 一致且路径以 `/webroot/` 开头；
- IPv6 host 输出 `[addr]:port`；
- 与 Android 当前行为一致：**不做 IP→域名映射**。

`ConversationViewModel.serverBaseURL` 由 `AppContainer` 在解析 launch 配置后写入（Settings 重连/DEBUG override 同样生效）；Markdown 转换与 webroot 路由共用同一基址。

### 3.3 链接路由

`AppContainer.openWebLink`：

- `WebLinkPolicy` 先做 http/https 白名单；
- `ServerBase.isWebrootURL` 命中 → `presentedWebPage`（应用内 WKWebView）；
- 其他 → `presentedWebLink`（现有 SFSafariViewController）。

两层入口（气泡 + Agent Job 全屏）共用同一个 `AppContainer` 路由。

### 3.4 应用内 WebView

`Features/WebView/WebPageView.swift`：

- `fullScreenCover` 呈现；顶栏 52pt：关闭、可选返回、页面标题（页面 title，回退 host）、在浏览器打开；
- 加载中显示线性进度；错误覆盖层：重试 + 在浏览器打开；
- 导航策略：仅允许 http(s)；非 http(s) 一律取消；主框架 HTTP ≥400 视为失败；
- `target=_blank` 以同窗口加载；
- JS 与 DOM storage 开启（与 Android 一致）；无 JS bridge、无凭据注入；`allowsLinkPreview=false`；
- 页面进程崩溃、网络失败显示错误覆盖层；
- 自签证书：仅当 `allowsSelfSigned == true` 且 challenge 的 host/port 与当前 server 完全一致时 `useCredential`；其他 host 走系统默认处理；
- `allowsSelfSigned` 来源：当前 launch 的 `TLSPolicy`（Settings 的允许自签开关或 DEBUG override）。

> 实现注意：iOS 18 SDK 的 `WKNavigationDelegate` 同时暴露完成回调与 async 两个认证方法签名。完成回调写法因 actor/Sendable 注解无法被 ObjC 运行时识别（`responds == false`，证书错误直接失败）；改用 `webView(_:respondTo:) async` 后才正确回调。不要改回 completion-handler 版本。

### 3.5 无需修改的部分

- `Info.plist` 无新增权限；ATS 保持原样（`NSAllowsLocalNetworking`）。经验证，自签 HTTPS 页面通过 delegate 放行即可加载，不需要 `NSAllowsArbitraryLoadsInWebContent`。
- 服务端/协议零改动；存量消息不迁移（渲染期转换）。

---

## 4. 与 Android 的差异

| 项 | Android | iOS |
|---|---|---|
| 打开容器 | 所有 Markdown 链接走自定义 WebView | **仅 webroot 链接**走 WKWebView；普通外链 SFSafari |
| 自签证书 | 任意 host 的 SSL 错误 `proceed()` | 仅当前 server host:port，且 TLS 允许自签时放行；其他失败 |
| IP→域名映射 | 暂未启用 | 未实现（一致） |
| 浏览器能力 | 自定义 WebView | 系统 WKWebView |
| 返回手势 | 系统返回键 | 顶栏返回 + 侧滑手势 |

差异均为已拍板的安全收敛；页面功能（进度/标题/重试/浏览器打开/导航拦截）保持一致。

---

## 5. 测试

### 5.1 单元测试

`LivaChatAppTests/WebrootSupportTests.swift`（表驱动）：

- 基址推导：LAN 显式端口、wss 缺省 443、ws 缺省 80、非 ws(s) 拒绝、IPv6 方括号、webroot 分类（host/port/scheme/path）；
- 转换：Markdown 链接上下文、裸文本、无尾斜杠、深层 `static/`、query、多枚链接、scheme/host 大小写、大写 UUID、中文句号、ASCII 标点、引号/尖括号、非法 token（非 UUID、UUIDv4、`..`、空 token、非 webroot host、缺斜杠）、非法片段不阻断后续合法片段、空 base、无命中、幂等。

### 5.2 UI 测试

- `testWebrootLinkOpensInAppWebView`：静态 Markdown 含 `livachat_url://webroot/<uuidv7>/`，点击后进入应用内 WebView（关闭/重试/错误页），关闭返回会话；
- 既有 `testMarkdownLinkOpensInAppSafari` 保证普通外链仍走 SFSafari；
- 既有 M3 UI 测试全量回归。

### 5.3 自签证书实测

本地起 HTTPS 自签服务（127.0.0.1:49152，证书 CN/SAN=127.0.0.1），用临时 UI 测试验证：

- 日志：`challenge ... host=127.0.0.1 port=49152` → `accept self-signed` → `finished ...`；
- 页面内容成功显示；验证后移除临时测试，避免 CI 依赖本地服务。

### 5.4 结果

- 模拟器单测 + UI 全量通过；
- iPad Pro 10.5 / iPadOS 17.7.5 真机 5 条相关 UI 测试通过；
- `./scripts/build-ios.sh app` 签名构建成功并安装。

**Review 修复（2026-10-20）**：首次加载 failed 后 `WKNavigation.reload()` 可能返回非 nil 但不再发起请求，导致“重试”无效；改为记录最后一次主框架请求 URL 并重新 `load`，UI 测试增加重试断言。

---

## 6. 已知限制 / 后续

- 未做真实 agent 产出页面的端到端语音验证（需人工对话触发）；转换/容器/自签路径已分别覆盖。
- iOS 15 真机未覆盖；WKWebView API 在 iOS 15 可用。
- 页面内下载、文件上传、JS 原生 alert/confirm/prompt、JS bridge、登录态注入不在本期。
- 失败重试按最后一次主框架请求 URL 重新发起 GET（不保留 POST 表单状态）。
- 若未来 server 改为对外域名（`<server-host> → <public-domain>`），需在 `ServerBase` 同步 Android 的映射策略。

---

## 7. 文件索引

| 文件 | 内容 |
|---|---|
| `Features/Conversation/LivachatURLConverter.swift` | 渲染期转换（纯函数） |
| `Core/Settings/ServerAddress.swift` | `ServerBase` 推导与 webroot 分类 |
| `Features/WebView/WebPageView.swift` | 应用内 WKWebView、错误/进度/重试/自签 scope |
| `Core/Conversation/ConversationViewModel.swift` | `serverBaseURL` |
| `Features/Conversation/MarkdownMessageView.swift` | 渲染前转换 + 缓存 |
| `Features/Conversation/AgentJobResultOverlay.swift` | 全屏正文转换 |
| `App/AppContainer.swift` / `App/AppRootView.swift` | 路由与 fullScreenCover |
| `LivaChatAppTests/WebrootSupportTests.swift` | 转换/基址/分类单测 |
| `LivaChatAppUITests/AppShellUITests.swift` | webroot 打开 UI 测试 |
