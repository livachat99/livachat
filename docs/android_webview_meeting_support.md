# Android 应用内 WebView 会议页支持：问题分析与修复设计

> 状态：已实施（2026-11-15，采用推荐方案 A/A/A）。Android WebView 布局/视口/媒体权限代码已落地；WebView 单元测试与 Huawei 真机仪器测试通过，实际会议首页已在应用内 WebView 验证铺满。**2026-11-16 增加 WebView 语言桥（§13）和设备 label fallback（§14）**。尚未提交 commit。
>
> 范围：Android App 内通过 Markdown 链接打开的 `/meeting/` 页面。
> iOS、Web 会议应用本身、LiveKit 服务端不在本次改动范围内。

## 0. 已确认的设计决策（2026-11-15）

| 决策点 | 结论 |
| --- | --- |
| 媒体权限信任边界 | **A**：只允许路径为 `/meeting...` 且与 App 配置的服务器基址同源的页面；debug 构建额外允许 loopback/模拟器地址。部署时要求 `meeting.external_server_addr` 与 App 服务器 origin 一致。 |
| 权限被拒绝时的原生提示 | **A**：本次不新增原生提示 UI，依赖会议页自身错误提示 + AppBar“在浏览器中打开”兜底。 |
| WebView 屏幕共享 | **A**：本次不支持；Android WebView 无浏览器等价的 `getDisplayMedia`，继续用外部浏览器兜底。 |
| 进度条 | 改为 `Box` 叠加层，避免加载期间 WebView 尺寸变化。 |
| 摄像头/麦克风部分授权 | 支持：用户只允许其中一个时，grant 可用的资源，不整体拒绝。 |
| debug 本地信任范围 | 仅 loopback/模拟器地址（`localhost`、`127.0.0.0/8`、`::1`、`10.0.2.2`），不默认信任整个局域网。 |
| Web 端 `--vh` 兜底 | 本次不做；先验证原生时序修复。 |

## 1. 背景与现象

Agent 在回复中用 Markdown 给出会议链接。用户点击后：

- Android App 进入内置 WebView 页面（`Routes.webview(url)`）。
- 外部浏览器（Chrome / 系统浏览器）打开同一链接，页面显示和入会都正常。
- 内置 WebView 中页面没有正常铺满：顶部原生 AppBar 正常，下面只有很窄的一条浅色区域（会议首页卡片顶部），其余区域是页面 `body` 的深色背景。

### 1.1 现场证据

测试设备为 Huawei 设备（Android 12），系统 WebView provider：

```text
package: com.huawei.webview
version: 14.0.2.319
内核: Chromium 99.0.4844.88
```

已确认：

1. 原生布局正常。`uiautomator` dump 显示 WebView 的实际 bounds 覆盖 AppBar 以下的完整屏幕区域，不是原生 View 高度为 0。
2. 页面已经加载。AppBar 标题从 host 变成页面标题 `LivaChat Meeting`，logcat 有 `pageStarted` 和 `pageFinished`。
3. 没有主框架网络/证书错误。没有 `mainFrameError`、`mainFrameHttpError` 或未放行的 `sslError`。
4. WebView 内部 CSS 视口高度异常。会议首页根容器 `.page` 使用 `height: 100vh; height: 100dvh`，实测高度只剩很小的一条；其下方露出 `body` 的 `#111` 背景。
5. 同一 URL 在外部浏览器中正常，说明页面资源、LiveKit 配置和 CSS 本身没有问题。

### 1.2 当前代码路径

```text
MarkdownContent（链接点击）
  -> Routes.webview(url)
  -> NavGraph 的 WEBVIEW composable
  -> WebViewScreen(url)
```

关键文件：

- `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewScreen.kt`
  - `AndroidView(factory = { WebView(ctx)... })`，在 factory 内直接 `loadUrl()`（当前约 139–227 行）。
  - WebView 设置只有 JavaScript、DOM storage、禁止 file/content、禁止多窗口（约 141–146 行）。
  - `WebChromeClient` 只处理进度和标题，没有 `onPermissionRequest`（约 212–220 行）。
- `apps/web/meeting/src/styles/home.module.css`
  - `.page { height: 100vh; height: 100dvh; ... overflow-y: auto; }`
- `apps/web/meeting/src/styles/globals.css`
  - `html, body, #root { height: 100%; margin: 0; }`
  - `body { overflow: hidden; background: #111; }`
- `apps/web/meeting/index.html`
  - 已有 `viewport` meta：`width=device-width, initial-scale=1.0, viewport-fit=cover`。
- `apps/android/LivaChatApp/src/main/AndroidManifest.xml`
  - 已声明 `CAMERA`、`RECORD_AUDIO` 权限。
- `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/permission/PermissionScreen.kt`
  - 麦克风是入会前置权限；摄像头可以选择跳过，因此进入 WebView 时摄像头运行时权限不一定已授予。

## 2. 根因分析

### 2.1 主因：WebView 在 0×0 尺寸时开始加载

`AndroidView` 的 `factory` 在 Compose 完成测量/布局之前执行。当前实现创建 WebView 后立即调用 `loadUrl()`：

```kotlin
AndroidView(
    factory = { ctx ->
        WebView(ctx).apply {
            // settings...
            webViewClient = ...
            webChromeClient = ...
            loadUrl(asciiUrl ?: safeUrl)   // 此时 WebView 还没有非零尺寸
        }
    },
)
```

Huawei WebView 14 / Chromium 99 在首次加载时如果宿主 View 尚未完成布局，会把初始视口（initial containing block）锁在一个极小的高度。之后即使 View 被测量为全屏，页面内：

- `100vh` / `100dvh` 仍按错误的小视口计算；
- `html/body/#root` 的 `height: 100%` 也无法改变已经建立的视口高度；
- `.page` 因此塌缩成一条窄带；
- `body` 的深色 `#111` 背景露出来，形成“上面一条浅色、下面全黑”的现象。

这与 Huawei 官方文档中描述的 Compose/WebView 加载时序问题一致：
<https://developer.huawei.com/consumer/cn/blog/topic/03225130377356153>

外部浏览器没有该问题，因为浏览器 Activity/窗口在加载页面前已经完成布局，WebView 不存在 0×0 初始视口。

### 2.2 次因一：viewport 设置缺失

当前 WebView 没有设置：

- `settings.useWideViewPort`
- `settings.loadWithOverviewMode`

Android WebView 默认不启用 wide viewport。即使加载时序修复，页面已有的：

```html
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
```

也可能不被正确采用，表现为按约 980px 的布局宽度渲染后再缩放，或移动端布局比例不正确。

### 2.3 次因二：媒体权限未接入 WebView

会议页通过 `getUserMedia()` 获取摄像头/麦克风。Android WebView 的媒体采集需要两层授权：

1. **网页层**：`WebChromeClient.onPermissionRequest()` 必须授权 `RESOURCE_VIDEO_CAPTURE` / `RESOURCE_AUDIO_CAPTURE`。
2. **Android 运行时权限**：App 必须已持有 `CAMERA` / `RECORD_AUDIO`。

当前 `WebChromeClient` 没有覆写 `onPermissionRequest`，因此网页层请求无法被 App 授权，LiveKit PreJoin 会得到媒体设备错误。即使页面显示问题修好，入会仍会失败。

另外，`settings.mediaPlaybackRequiresUserGesture` 未设置，默认值为 `true`，远端音频可能因为“没有用户手势”而被阻止播放。

### 2.4 已知但不在本次修复范围的问题（已确认）

- Android WebView 不提供与浏览器一致的 `getDisplayMedia()` 屏幕共享能力。会议页的屏幕共享按钮在 WebView 中不可用；继续通过“在浏览器中打开”兜底。本次不做 MediaProjection/JS bridge。
- WebView 的媒体能力依赖系统 WebView provider。若厂商 WebView 本身不支持 `getUserMedia` 或存在实现缺陷，原生层无法完全补齐，仍以外部浏览器为兜底。

## 3. 目标、非目标与约束

### 3.1 目标

1. 会议页在应用内 WebView 中首次加载即按真实 View 尺寸建立视口，铺满可用区域。
2. 页面按移动端 viewport 渲染，不出现 980px 缩放或横向溢出。
3. 网页请求摄像头/麦克风时，App 只对受信任的会议页授权，并在需要时请求 Android 运行时权限。
4. 保持现有安全边界：非 http(s) 导航仍被拦截；file/content 访问仍关闭；自签名证书放行逻辑不变。
5. 现有 WebView 错误页、重试、返回键、外部浏览器入口行为不回归。

### 3.2 非目标

- 不修改会议 Web 应用的业务逻辑、LiveKit 配置或 CSS 布局。
- 不为 Android WebView 实现屏幕共享。
- 不改变 iOS WKWebView 行为。
- 不引入用户代理伪装、不关闭 TLS 校验、不放宽混合内容策略。
- 不新增“所有网站自动获得摄像头/麦克风”的能力。

### 3.3 安全约束

- 媒体授权只允许**受信任的会议页面**：
  - 页面路径属于会议 SPA（第一段路径为 `meeting`）；
  - 请求 origin 与页面 origin 完全一致（scheme + host + effective port）；
  - 页面 origin 与 App 已配置的 `LocalServerBaseUrl` 同源；
  - debug 构建额外允许 loopback / 模拟器 host，用于本地 Vite 调试。
- **不能只判断“当前页面同源”**：任意第三方页面都能满足“请求 origin == 自己页面的 origin”，若仅凭这一点授权，从 Markdown 打开的恶意站点也能拿到摄像头/麦克风。
- 跨域 iframe、`origin == "null"`、非 http(s) origin 一律拒绝。
- 仅支持 `RESOURCE_VIDEO_CAPTURE` 和 `RESOURCE_AUDIO_CAPTURE`；其他 WebView 资源（如 protected media、MIDI）一律拒绝。
- 不因为媒体权限修改 `shouldOverrideUrlLoading` 的白名单。
- 自签名证书的放行仍只针对 `LocalAllowSelfSigned == true` 且与 `serverBaseUrl` 同 host/port 的主框架资源。

## 4. 设计

### 4.1 总体改动

| 模块 | 改动 |
| --- | --- |
| WebView 创建 | 显式设置 `MATCH_PARENT` layout params |
| 首次加载 | 延迟到 WebView 获得非零宽高后，只加载一次 |
| WebSettings | `useWideViewPort=true`、`loadWithOverviewMode=true`；受信任会议页设 `mediaPlaybackRequiresUserGesture=false`，其他页面保持默认 |
| WebChromeClient | 新增 `onPermissionRequest` / `onPermissionRequestCanceled` |
| 媒体权限策略 | 新增独立、可单测的受信任会议页判断与资源映射 |
| 运行时权限 | 用 `RequestMultiplePermissions` 按需申请，结果返回后再 grant/deny |
| 生命周期 | 销毁 WebView 时清理未完成的媒体请求 |

不新增第三方依赖。布局监听使用平台 `View.addOnLayoutChangeListener`，不依赖 `core-ktx` 的 `doOnLayout` 扩展。

### 4.2 首次加载时序

创建 WebView 时：

1. 设置 `layoutParams = ViewGroup.LayoutParams(MATCH_PARENT, MATCH_PARENT)`。
2. 完成所有 `WebSettings`、`WebViewClient`、`WebChromeClient` 配置。
3. **不在 factory 内调用 `loadUrl`**。
4. 注册一个一次性的布局监听：只有 `width > 0 && height > 0` 时才调用 `loadUrl`，随后移除监听。
5. 记录一条日志：实际布局尺寸 + 开始加载。便于后续用 logcat 确认时序。

伪代码：

```kotlin
private fun WebView.loadInitialUrlWhenSized(url: String) {
    var loaded = false

    fun loadIfReady() {
        if (loaded || !isAttachedToWindow || width <= 0 || height <= 0) return
        loaded = true
        Log.d(WEBVIEW_TAG, "layout ready ${width}x${height}; loadUrl")
        loadUrl(url)
    }

    if (isLaidOut && isAttachedToWindow && width > 0 && height > 0) {
        loadIfReady()
        return
    }

    addOnLayoutChangeListener(object : View.OnLayoutChangeListener {
        override fun onLayoutChange(
            v: View,
            left: Int, top: Int, right: Int, bottom: Int,
            oldLeft: Int, oldTop: Int, oldRight: Int, oldBottom: Int,
        ) {
            if (v.width > 0 && v.height > 0) {
                removeOnLayoutChangeListener(this)
                loadIfReady()
            }
        }
    })
}
```

说明：

- 布局监听在 UI 线程触发，`loadUrl` 也在 UI 线程执行。
- 只加载一次，避免后续键盘弹出、旋转、Progress 状态变化导致重复加载。
- `loadIfReady` 检查 `isAttachedToWindow`；布局 listener 的引用保存到一个可清理的 holder，`DisposableEffect.onDispose` 中先清理媒体请求、移除 listener，再 `removeView`/`destroy`。即使某些时序下 listener 未被移除，`isAttachedToWindow == false` 也能阻止销毁后的 `loadUrl`。
- 错误重试仍使用 `webView.reload()`；此时首次加载必然已经开始，不受延迟逻辑影响。
- 现有 `loadError` 清除策略保持不变：`onPageStarted` 不清除，只有重试按钮显式清除，避免 404 的 `onReceivedHttpError` 先于 `onPageStarted` 到达时错误占位被吞掉。
- 按已确认决策，把 `LinearProgressIndicator` 从 `Column` 的布局子项改为 `Box` 内叠加（`align(TopCenter)`），让 WebView 在加载期间始终保持全尺寸。否则进度条出现/消失会让 WebView 高度发生两次变化，增加厂商 WebView 视口处理的不确定性；叠加后进度条只覆盖页面顶部几个像素。

### 4.3 Viewport 设置

在 factory 中增加：

```kotlin
settings.useWideViewPort = true
settings.loadWithOverviewMode = true
// 仅受信任会议页关闭“必须用户手势才播放”，普通 webroot/第三方页面保持默认 true
settings.mediaPlaybackRequiresUserGesture = !isTrustedMeetingPage(
    pageUrl = safeUrl,
    serverBaseUrl = serverBaseUrl,
    allowDebugLoopback = BuildConfig.DEBUG,
)
```

原因：

- `useWideViewPort=true`：让 WebView 采用页面 `<meta name="viewport">`，按设备宽度布局。
- `loadWithOverviewMode=true`：没有 viewport meta 的页面也按可用宽度适配，避免默认 980px 布局。
- `mediaPlaybackRequiresUserGesture=false`：会议页在用户点击入会后由 LiveKit 动态挂载远端 `<audio>`，需要允许无额外手势播放。只对受信任会议页关闭，避免普通网页自动播放音频。

`WebSettings` 是 WebView 实例级设置。会议页是单页应用，当前不会跳到第三方页面；如果后续出现页面内跨站跳转，可在 `onPageStarted` 中按当前 URL 重新评估该值。本次不扩大改动。

保持不变：

```kotlin
settings.javaScriptEnabled = true
settings.domStorageEnabled = true
settings.allowFileAccess = false
settings.allowContentAccess = false
settings.setSupportMultipleWindows(false)
```

`setSupportMultipleWindows(false)` 与会议页当前用法一致；本次不改 `target=_blank` 行为，避免扩大改动面。

### 4.4 媒体权限策略

#### 4.4.1 受信任会议页面判断

新增一个 WebView 专用的判断，不复用只比较 host/port 的 `isSameOrigin()`：

```text
isTrustedMeetingPage(pageUrl, serverBaseUrl, allowDebugLoopback):
  1. pageUrl 能解析出 scheme + host
  2. scheme 是 http 或 https
  3. 页面路径第一段严格等于 "meeting"（接受 /meeting、/meeting/、/meeting/<number>），
     且路径段中不含 "." / ".."，解码后的路径段不含 "/" / "\\"
  4. 页面 origin 属于以下之一：
     a. 与 serverBaseUrl 同源（scheme + host + effective port 完全一致）
     b. allowDebugLoopback == true 且 host 是 loopback / 模拟器地址
        （localhost、127.0.0.0/8、::1、10.0.2.2）

allowedMediaOrigin(pageUrl, requestOrigin, serverBaseUrl, allowDebugLoopback):
  - isTrustedMeetingPage(pageUrl, serverBaseUrl, allowDebugLoopback) == true
  - requestOrigin 能解析，且与 pageUrl 的 scheme/host/effective port 完全一致
  - requestOrigin 不是 opaque origin（"null"）
```

effective port 规则与现有 `TlsSettings.kt` 一致：显式端口优先，否则 https=443、http=80。

信任锚是 App 已配置的服务器基址 `LocalServerBaseUrl`，不是“当前页面自己”：

- 会议页 `https://<server-host>/meeting/` 与 `serverBaseUrl` 同源，可以授权；
- 从 Markdown 打开的第三方页面即使路径伪造为 `/meeting/`，只要 origin 不是已配置服务器，仍被拒绝；
- 受信任服务器上的非会议路径（如 `/webroot/...`）不会被授予摄像头/麦克风；
- 跨域 iframe 的请求被拒绝。

生产部署要求：`meeting.external_server_addr` 的 origin 应与 App 配置的服务器地址 origin 一致。若反向代理把会议页放在不同 host/port，应用内 WebView 会拒绝媒体请求，此时仍可用“在浏览器中打开”兜底。后续如需支持任意独立会议域名，应增加显式的受信任 origin 配置，而不是放宽同源判断。

debug 构建的 loopback/模拟器例外只用于本地 Vite 调试（例如 adb reverse 到 `127.0.0.1:<port>`），release 构建不会生效。因此本地 `http://127.0.0.1:<port>/meeting/` 的媒体测试必须使用 debug 构建；release 构建应使用与 App 服务器同 origin 的 HTTPS 会议地址。局域网 IP（如 `192.168.x.x`）不在 debug 例外内；本地真机测试优先用 adb reverse 或外部浏览器。

#### 4.4.2 资源映射

| WebView 资源 | Android 运行时权限 | 处理 |
| --- | --- | --- |
| `RESOURCE_VIDEO_CAPTURE` | `Manifest.permission.CAMERA` | 允许（受信任会议页且请求同源时） |
| `RESOURCE_AUDIO_CAPTURE` | `Manifest.permission.RECORD_AUDIO` | 允许（受信任会议页且请求同源时） |
| `RESOURCE_PROTECTED_MEDIA_ID` | 无 | 拒绝 |
| `RESOURCE_MIDI_SYSEX` | 无 | 拒绝 |
| 其他 | 无 | 拒绝 |

#### 4.4.3 授权流程

```text
网页 getUserMedia()
  -> WebView 回调 onPermissionRequest(request)
  -> 1. 受信任会议页面 / 请求 origin 检查失败：
         request.deny()，记录日志，结束
  -> 2. 过滤不支持的资源；全部不支持：deny
  -> 3. 对应 Android 运行时权限已全部授予（checkSelfPermission 复核）：
         request.grant(支持的资源，且只包含实际已授予的权限)
  -> 4. 有缺失的运行时权限：
         把 (request, 支持的资源) 放入 pending 队列
         计算“尚未授予且未在申请中”的运行时权限并集
         如果没有正在进行的申请，调用 RequestMultiplePermissions
         等待系统权限对话框结果
  -> 5. 结果返回：
         重新检查每个 pending request 的页面仍受信任、WebView 仍存活
         对每个 request 计算：
           - 已授予的 Android 权限
           - 本轮刚被拒绝的 Android 权限
           - 尚未申请过的 Android 权限
         如果仍有“尚未申请过”的权限：
           不提前 grant/deny 该 request，保留在 pending 队列
         否则按已授予权限 grant(子集)；一个都没有则 deny()
         如果还有 pending request 需要新的运行时权限：
           发起下一轮 RequestMultiplePermissions（串行，不重叠）
```

`WebChromeClient` 接线示意：

```kotlin
webChromeClient = object : WebChromeClient() {
    override fun onProgressChanged(view: WebView, newProgress: Int) { ... }

    override fun onReceivedTitle(view: WebView, title: String?) { ... }

    override fun onPermissionRequest(request: PermissionRequest) {
        mediaPermissionCoordinator.onPermissionRequest(request)
    }

    override fun onPermissionRequestCanceled(request: PermissionRequest) {
        mediaPermissionCoordinator.onPermissionRequestCanceled(request)
    }
}
```

设计要点：

- **Compose 调用顺序**：`rememberLauncherForActivityResult` 必须在 `WebViewScreen` 顶部、`safeUrl == null` 的 early return **之前**注册；回调通过稳定的 coordinator/holder 转发。不能把它放在 early return 之后，否则非法 URL 分支与正常分支的 Composable 调用顺序不一致。
- **按需申请**：不在 WebView 创建时申请摄像头/麦克风。用户可能只是打开普通网页，不能因为进入 WebView 就弹权限框。
- **支持部分授权**：会议页可能先请求摄像头、再请求麦克风；如果用户只允许麦克风，就 grant 麦克风资源，让页面自行处理摄像头不可用。
- **绝不 grant 未授予的运行时权限**：每次 grant 前都用 `checkSelfPermission` 复核实际状态，只传入已授予的资源。部分 WebView 实现中，对未授予的 CAMERA/RECORD_AUDIO 调用 `grant()` 会让页面报错，并可能导致后续 `onPermissionRequest` 不再回调；这比单纯拒绝页面请求更糟。
- **请求队列**：LiveKit PreJoin 或 `setCameraEnabled` / `setMicrophoneEnabled` 可能触发多个 `PermissionRequest`。用一个 pending 队列合并缺失的运行时权限，权限结果返回后逐个处理。
  - 同一时间只允许一轮系统权限申请，避免多个 `RequestMultiplePermissions` 重叠。
  - 如果第二个请求带来第一轮没有申请过的权限，不能直接 grant/deny；保留在 pending 队列，等第一轮结果后再发起下一轮申请。
  - 一个 request 同时包含摄像头和麦克风时，只有两者的运行时权限都已“授予或拒绝”后才 grant 子集/deny，避免提前 grant 导致页面误以为整个 `getUserMedia` 已成功。
- **取消处理**：实现 `onPermissionRequestCanceled(request)`，把对应请求从 pending 队列移除；之后即使系统权限回调到达，也不再对已取消的 request 调用 `grant`。
- **销毁清理**：WebView 销毁时对仍未完成的 pending request 调用 `deny()`，并清空队列；销毁后到达的新 `onPermissionRequest` 直接 `deny()`，不再启动权限申请。所有 `grant/deny` 都用异常保护，符合 WebView 在页面导航/销毁后使 request 失效的行为。
- **线程**：`onPermissionRequest` 和 ActivityResult 回调都在主线程；队列只在该线程访问，不需要额外锁。
- **闭包稳定性**：Coordinator 用 `remember` 保持单例，`WebChromeClient` 只持有它；当前页面 URL 从 WebView 实例读取，不把易变的 Composable 局部变量直接捕获进长期存活的回调。
- **Activity 生命周期**：Coordinator 维护 `permissionFlowActive`（从发起系统权限申请到 Activity 恢复为止）。Activity 不处于 `RESUMED` 时：
  - 若 `permissionFlowActive == true`：同一轮授权流程中的后续请求仍被接收；已有运行时权限的请求可以立即 grant（不弹系统 UI），缺失权限的请求进入 pending，等 `ON_RESUME` 再弹系统权限框。这避免 LiveKit 在摄像头授权后紧接着申请已授权麦克风时，第二个请求因权限对话框 pause 被误拒。
  - 若 `permissionFlowActive == false`：视为后台请求，直接 deny，不弹权限框。
  - launcher 不可用或 `launch` 抛异常时仍直接 deny，不崩溃；页面可重试或走浏览器兜底。
  - 系统权限对话框会让 Activity 短暂 pause，且部分系统版本在 `onResume` 之前投递 ActivityResult。**权限结果返回时不能因为“当前非 RESUMED”就 deny**，否则正常授权会失败；结果处理完若还有 follow-up，等 `ON_RESUME` 后再发起。
  - WebViewScreen 通过 `LifecycleEventObserver` 在 `ON_RESUME` 调用 `coordinator.onHostResumed()`；恢复后清理 `permissionFlowActive`。
- **无 Activity 场景**（预览、测试）：直接 deny，不崩溃。

#### 4.4.4 原生提示 UI（已确认不新增）

本次**不新增**权限拒绝弹窗/横幅：

- 页面自身已有 `onError` / LiveKit 媒体错误提示；
- AppBar 始终保留“在浏览器中打开”；
- 新增多语言字符串和 UI 状态会扩大改动面。

如果后续需要更明确的引导，可以再增加一个仅在受信任会议页的媒体请求被拒绝时显示的 Snackbar，文案建议为“摄像头/麦克风权限被拒绝，请在系统设置中开启，或使用浏览器打开”。该增强不影响本次核心修复，不纳入本次实施范围。

### 4.5 错误、重试与生命周期

- 首次加载延迟不会改变错误处理：
  - 主框架 HTTP 错误、网络错误、SSL 错误仍设置 `loadError`；
  - 错误覆盖层的“重试”仍调用 `reload()`；
  - “在浏览器中打开”仍打开原始安全 URL。
- `DisposableEffect(Unit)` 继续在真正离开组合时移除并 `destroy()` WebView；新增的清理逻辑只清理 pending 媒体请求，不改变现有销毁顺序（先 `removeView` 再 `destroy`）。
- Activity 的 `configChanges` 已包含 `orientation|screenSize|...`，旋转不会重建 Activity；WebView 保留，视口随新尺寸重新布局。
- 键盘弹出由 `adjustResize` 处理；修复后的 `100vh` 应随 WebView 高度变化。验收时需单独测试输入姓名时页面是否仍完整。

### 4.6 调试支持

只在 `BuildConfig.DEBUG` 下：

```kotlin
WebView.setWebContentsDebuggingEnabled(true)
```

并保留以下日志：

- WebView 首次布局尺寸和 `loadUrl` 时机；
- `onPermissionRequest` 的 origin、请求资源、受信任页面判断结果；
- 系统权限结果和最终 grant/deny 的资源集合。

release 不输出敏感 URL 的额外日志；现有日志策略不变。

## 5. 代码改动清单

### 5.1 修改

`apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewScreen.kt`

- factory 内设置 `MATCH_PARENT` 布局参数。
- 移除 factory 内立即 `loadUrl`，改为 `loadInitialUrlWhenSized()`。
- 增加 viewport 和媒体播放设置。
- 将 `LinearProgressIndicator` 改为 `Box` 叠加层，避免加载过程中 WebView 尺寸变化。
- `WebChromeClient` 增加 `onPermissionRequest` / `onPermissionRequestCanceled`。
- 在 `safeUrl == null` early return 之前注册 `rememberLauncherForActivityResult(RequestMultiplePermissions())`，回调转发给 coordinator。
- `DisposableEffect` 清理 pending 媒体请求。

### 5.2 新增

`apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewMediaPermissions.kt`

拆出纯策略，便于单测：

```kotlin
internal data class MediaPermissionPlan(
    val webResources: Set<String>,
    val runtimePermissions: Set<String>,
    val missingRuntimePermissions: Set<String>,
)

internal fun isTrustedMeetingPage(
    pageUrl: String,
    serverBaseUrl: String,
    allowDebugLoopback: Boolean,
): Boolean

internal fun isAllowedMediaRequest(
    pageUrl: String,
    requestOrigin: String?,
    serverBaseUrl: String,
    allowDebugLoopback: Boolean,
): Boolean

internal fun planMediaPermission(
    requestedResources: Set<String>,
    isRuntimePermissionGranted: (String) -> Boolean,
): MediaPermissionPlan
```

`allowDebugLoopback` 由调用方传入 `BuildConfig.DEBUG`，避免策略函数直接依赖 build 配置，便于单测覆盖 release 行为。

`apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewMediaPermissionCoordinator.kt`

队列/Launcher 逻辑放入 `WebViewMediaPermissionCoordinator` 类：构造时注入“查询当前页面 URL”“查询运行时权限”“发起系统权限申请”“查询 Activity 是否存在且处于 RESUMED”等回调。这样队列、取消、销毁清理可以在 Robolectric/JVM 测试中用 fake request 覆盖，而不需要真实系统权限对话框。

### 5.3 测试文件

- 新增 `apps/android/LivaChatApp/src/test/kotlin/dev/livachat/app/ui/screens/webview/WebViewMediaPermissionTest.kt`
- 新增 `apps/android/LivaChatApp/src/test/kotlin/dev/livachat/app/ui/screens/webview/WebViewMediaPermissionCoordinatorTest.kt`
- 扩展 `apps/android/LivaChatApp/src/androidTest/kotlin/dev/livachat/app/ui/screens/webview/WebViewScreenTest.kt`

### 5.4 不改动

- Web 会议应用代码和 CSS。
- iOS `WebPageView.swift`。
- Android Manifest 权限声明（`CAMERA`、`RECORD_AUDIO` 已存在）。
- 现有 `shouldBlockNavigation`、SSL 放行、外部浏览器入口逻辑。

### 5.5 实施后需同步的文档

本次代码合入后，以下文档中“Android WebView 没有媒体权限桥接/不能应用内入会”的已知限制需要改为“已支持受信任会议页的摄像头/麦克风；屏幕共享仍走外部浏览器”：

- `docs/livachat_meeting.md`：§0.2 第 6 条、§0.3 U2、§2.5、§2.8、§3.1/§3.3 的流程标注、§8.3、测试矩阵/已知限制等引用处。
- `docs/webmeeting_design.md`：§3.10 “WebView：本 app 面向系统浏览器…”一段。
- `docs/personal_meeting_design.md`：§3.7 “现有 LivaChat WebView 两者都没有”一段。
- `docs/android_app_arch.md`：WebView 屏幕职责说明（如仍描述为仅展示网页）。

这些文档更新不改变本次代码行为，但不应遗漏，避免后续维护者按旧限制做决策。

## 6. 测试计划

### 6.1 单元测试（Robolectric / JVM）

`WebViewMediaPermissionTest`：

1. 受信任服务器上的 `/meeting`、`/meeting/`、`/meeting/<number>` → 允许。
2. 受信任服务器上的 `/webroot/...`、`/`、`/meetingevil/...`、`/meeting/../evil`、`/meeting/..%2Fevil` → 拒绝。
3. 未受信任 origin 上的 `/meeting/` → 拒绝。
4. 请求 origin 与页面 origin 不同 → 拒绝。
5. host 大小写不同、默认端口显式/省略 → 允许。
6. scheme 不同（http vs https）→ 拒绝。
7. 端口不同 → 拒绝。
8. `origin == null`、`"null"`、非 http(s)、畸形 URL → 拒绝。
9. debug 构建 + loopback host + `/meeting/` → 允许；release 构建同条件 → 拒绝。
10. 仅请求 `VIDEO_CAPTURE` / `AUDIO_CAPTURE` → 映射到正确运行时权限。
11. 请求 `PROTECTED_MEDIA_ID` / `MIDI_SYSEX` / 未知资源 → 不允许。
12. 运行时权限已全部授予 → 不产生待申请权限。
13. 缺少摄像头权限 → 只申请 `CAMERA`。
14. 缺少麦克风权限 → 只申请 `RECORD_AUDIO`。
15. 两者都缺 → 申请两者。
16. 部分资源不支持时，只保留支持资源，不因未知资源整体失败。
17. 两个 pending request 的缺失权限相同：共用同一轮运行时权限申请，结果返回后分别 grant/deny。
18. 第一个请求的权限申请进行中，第二个请求带来新的运行时权限：第二个请求不被提前 deny，第一轮结束后串行发起第二轮，最终分别 grant/deny。
19. 一个 request 同时请求摄像头和麦克风，其中麦克风权限尚未申请过：不提前 grant 摄像头，等麦克风也完成授权/拒绝后再 grant 子集或 deny。
20. pending request 被 `onPermissionRequestCanceled` 取消后，权限结果返回时不再 grant。
21. WebView 销毁时，未完成的 pending request 被 deny 且队列清空；销毁后到达的新请求直接 deny，不启动权限申请。
22. Activity 不处于 `RESUMED`：`permissionFlowActive` 时缺失权限的 request 排队不弹框，`onHostResumed` 后申请；同一流程中已授予权限的 request 可 grant；无流程的后台 request deny；`launch` 抛异常时 deny 且不崩溃。
23. 非受信任会议页请求媒体：直接 deny，不触发运行时权限申请。
24. grant 集合只包含 `checkSelfPermission == GRANTED` 的资源；没有可授予资源时调用 deny，绝不 grant 未授予的权限。
25. 当前页面 URL 为 null/空/`about:blank` 时：直接 deny，不触发运行时权限申请。
26. 运行时权限已授予但 Activity 不在 `RESUMED` 且无 permission flow：request deny；同一权限 flow 中可 grant。
27. 权限对话框导致 Activity pause 后，ActivityResult 结果仍能处理并 grant；follow-up 等 `onHostResumed()` 后串行发起。
28. 同一轮权限流程中，摄像头授权后紧接着申请已授权的麦克风时，第二个请求不被 pause 误拒；无权限流程的后台请求仍 deny。

### 6.2 仪器测试

仪器测试运行 debug 变体；项目 `src/debug/AndroidManifest.xml` 已设置 `usesCleartextTraffic=true`，因此本地 `MockWebServer` 的 HTTP 页面可用。release 变体仍保持 `usesCleartextTraffic=false`，不用于该 HTTP 测试页。

扩展 `WebViewScreenTest`：

1. **布局时序 / 视口测试**
   - MockWebServer 返回一个带 viewport meta 的页面：
     ```html
     <meta name="viewport" content="width=device-width, initial-scale=1">
     <style>
       html, body { margin: 0; height: 100%; }
       #page { height: 100vh; background: #fff; }
     </style>
     <div id="page"></div>
     <script>
       requestAnimationFrame(() => {
         const h = document.getElementById('page').getBoundingClientRect().height;
         document.title = [
           'probe',
           window.innerHeight,
           Math.round(h),
           window.innerWidth,
           document.documentElement.clientWidth,
         ].join(':');
       });
     </script>
     ```
   - 等待 AppBar 标题出现 `probe:`。
   - 解析 `innerHeight`、`#page` 高度、`innerWidth`、`clientWidth`，断言：
     - `innerWidth > 0`、`innerHeight > 0`；
     - `#page` 高度与 `innerHeight` 的差值不超过几个像素；
     - `clientWidth` 与 `innerWidth` 一致，且不是 980px 级别的默认布局宽度。
   - 该测试在普通模拟器上验证时序契约；在 Huawei 设备上可复现并验证原 bug。

2. **首次加载只发生一次**
   - 页面加载完成并触发键盘/重组后，MockWebServer 只收到一次主文档请求。
   - 该断言用于防止布局监听导致重复 `loadUrl`。

3. **错误与返回回归**
   - 保留现有非法 URL、404 错误占位、返回键测试。
   - 确认延迟加载后 404 仍能进入错误态并显示重试。

4. **首次布局前销毁**
   - 组合 `WebViewScreen` 后立即离开/销毁（在首次非零布局前），不应触发 `loadUrl`，也不应因销毁后的布局回调崩溃。

5. **媒体权限（可选，设备/CI 能力允许时）**
   - 使用 localhost/HTTPS 测试页调用 `getUserMedia`，预先授予 Android 权限，断言页面能拿到媒体流或至少不收到 `NotAllowedError`。
   - 若测试环境的 WebView provider 不支持媒体采集，则标记为 manual-only，不阻塞 CI。

### 6.3 手工验收（Huawei 设备）

1. 从 Markdown 点击会议链接，进入应用内 WebView：
   - 页面完整铺满 AppBar 以下区域；
   - 首页卡片完整显示，不再出现“上面一条浅色、下面全黑”；
   - 无横向滚动/异常缩放。
2. 输入姓名，键盘弹出/收起后布局仍正确；旋转屏幕（如设备允许）后仍正确。
3. 点击入会：
   - 首次触发摄像头/麦克风系统权限对话框；
   - 允许后 PreJoin 预览正常，能入会并看到/听到其他参与者；
   - 拒绝摄像头、允许麦克风时，仍能音频入会或至少页面显示明确错误；
   - 拒绝麦克风时，页面显示媒体错误，AppBar 的“在浏览器中打开”可兜底；
   - 拒绝一次后再次触发媒体请求（重新入会/重试），`onPermissionRequest` 仍应正常回调；若不再回调，说明发生了“grant 未授予权限”导致的 WebView 状态损坏，必须复查 `checkSelfPermission` 过滤。
4. 在 release 构建中打开一个第三方 http(s) 页面（即使路径为 `/meeting/`、host 不是已配置服务器）并调用 `getUserMedia`（测试页）：
   - 不应获得媒体权限；
   - 日志记录“非受信任会议页面”并 deny。
5. 回归：
   - 普通 webroot 页面仍能打开；
   - 非 http(s) 链接仍被拦截；
   - 自签名本地服务器仍只在允许开关打开时放行；
   - 404 错误页、重试、返回键正常。
6. 屏幕共享：
   - WebView 中不可用属预期；
   - 外部浏览器中仍可用。

### 6.4 执行命令

```bash
cd apps/android

# JVM / Robolectric 单测
./gradlew :LivaChatApp:testDebugUnitTest \
  --tests 'dev.livachat.app.ui.screens.webview.*'

# 连接设备后的 WebView 仪器测试
./gradlew :LivaChatApp:connectedDebugAndroidTest

# 编译检查
./gradlew :LivaChatApp:assembleDebug
```

## 7. 验收标准

- [x] Huawei 设备上应用内 WebView 的会议首页完整铺满可用区域，`.page` 高度与 `window.innerHeight` 基本一致（实际 `/meeting/<number>` 页面探针验证）。
- [x] 页面按 `width=device-width` 布局，无 980px 缩放或横向滚动（仪器测试断言 `clientWidth == innerWidth`）。
- [x] 首次加载只发生一次，且发生在 WebView 非零尺寸之后（仪器测试）。
- [x] 受信任服务器上的会议页可请求摄像头/麦克风；权限已授予时直接 grant（设备探针 `getUserMedia` 成功）。缺失权限时的系统对话框仍需真机手工验收。
- [x] 非会议路径、非受信任 origin、跨域/非法 origin 的媒体请求被拒绝（Coordinator/策略单测）。
- [x] 摄像头被拒绝但麦克风允许时，支持部分授权，不因一个资源失败而拒绝全部（Coordinator 单测）。
- [x] 现有 WebView 单元测试和仪器测试全部通过；404、重试、返回、外部浏览器入口无回归。
- [x] 非 http(s) 导航拦截单测通过；自签名证书逻辑未被本次改动触碰。
- [x] 屏幕共享在 WebView 中不可用作为已知限制记录，外部浏览器兜底可用。

## 8. 风险与后续项

1. **Huawei WebView 行为只能在真机复现**：普通模拟器/Google WebView 可能不会出现 0×0 视口 bug。仪器测试验证时序契约，最终必须用 Huawei 设备手工验收。
2. **权限请求跨系统对话框持有**：`PermissionRequest` 需要在 ActivityResult 返回后继续 grant/deny。若某些 WebView provider 在对话框期间取消请求，`onPermissionRequestCanceled` 会清理；grant/deny 仍需异常保护。另一个已知坑是：对未授予的运行时权限调用 `grant()` 可能让 WebView 的后续 `onPermissionRequest` 失效；设计已要求 grant 前 `checkSelfPermission` 复核并只传入已授予资源。
3. **部署 origin 不一致**：按已确认的决策 A，如果 `meeting.external_server_addr` 与 App 配置的服务器 origin 不同（独立域名、不同端口），应用内 WebView 会拒绝媒体，只能用外部浏览器。要支持这种部署，需要后续增加显式的受信任会议 origin 配置；本次不放宽判断。
4. **`getUserMedia` provider 支持差异**：Huawei WebView 对 WebRTC 媒体采集的支持不保证与 Chrome 一致；若仍失败，保持“外部浏览器打开”兜底。
5. **屏幕共享**：已确认本次不做原生 MediaProjection 桥接；Android WebView 不支持 `getDisplayMedia`，外部浏览器兜底。
6. **权限拒绝提示 UI**：已确认本次不新增，依赖页面错误提示 + 浏览器兜底。后续如需更明确的原生引导，再新增多语言字符串和 Snackbar/对话框。
7. **Web 端 `--vh` 兜底**：已确认本次不做。原生修复后 WebView 视口应正确；若后续仍有厂商兼容问题再评估。

## 9. 实施顺序

1. 修改 `WebViewScreen.kt`：布局参数、延迟加载、viewport 设置、进度条叠加。
2. 扩展仪器测试，验证页面高度与 `innerHeight` 一致；在 Huawei 设备上确认原始现象消失。
3. 新增媒体权限策略、Coordinator 与单测。
4. 接入 `onPermissionRequest` / 运行时权限队列，完成设备手工媒体验收。
5. 跑全量 WebView 单测/仪器测试与 `assembleDebug`。
6. 完成 Huawei 真机回归；如仍存在厂商兼容问题，再评估 Web 端 `--vh` 兜底（默认不做）。

## 10. 参考

- Android `WebChromeClient.onPermissionRequest`：
  <https://developer.android.com/reference/android/webkit/WebChromeClient#onPermissionRequest(android.webkit.PermissionRequest)>
- Android `PermissionRequest`：
  <https://developer.android.com/reference/android/webkit/PermissionRequest>
- Android `WebSettings`：
  <https://developer.android.com/reference/android/webkit/WebSettings>
- WebView 对未授予运行时权限调用 `grant()` 导致后续 `onPermissionRequest` 失效的案例（Cordova Android #1888）：
  <https://github.com/apache/cordova-android/issues/1888>
- Huawei WebView / Compose 加载时序问题：
  <https://developer.huawei.com/consumer/cn/blog/topic/03225130377356153>
- 现有实现：
  - `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewScreen.kt`
  - `apps/android/LivaChatApp/src/androidTest/kotlin/dev/livachat/app/ui/screens/webview/WebViewScreenTest.kt`
  - `apps/android/LivaChatApp/src/test/kotlin/dev/livachat/app/ui/screens/webview/WebViewUrlPolicyTest.kt`
  - `apps/web/meeting/src/styles/home.module.css`
  - `apps/web/meeting/src/styles/globals.css`

## 11. 修订记录

- 2026-11-15：确认推荐方案 A/A/A；完成自审并修复以下设计问题：
  - 媒体权限队列：补充“系统权限申请进行中又收到需要新权限的请求”时的串行 follow-up，避免第二个请求被提前 deny；一个 request 同时请求多个资源时，等所有运行时权限都有结果后再 grant 子集/deny。
  - Compose 调用顺序：`rememberLauncherForActivityResult` 必须在非法 URL early return 之前注册。
  - 生命周期：无 permission flow 的暂停态请求 deny；同一权限 flow 中已授予资源可 grant、缺失权限等恢复后申请；launcher 不可用或 launch 失败时 deny；WebView 销毁/布局回调竞态保护。
  - 路径校验：拒绝 `.` / `..` 路径段，避免 `/meeting/../...` 绕过会议路径判断。
  - 测试计划补充队列 follow-up、取消、销毁、Activity 生命周期、非受信任页面不触发权限申请等用例。
  - 增加实施后文档同步清单，避免 `docs/livachat_meeting.md`、`docs/webmeeting_design.md` 等继续描述旧的“WebView 无媒体权限”限制。
  - 实施阶段进一步修正：系统权限对话框会 pause Activity，ActivityResult 可能在 onResume 前投递；结果处理不再因非 RESUMED 而 deny，follow-up 由 ON_RESUME 继续。
  - 复审修正：Coordinator 保持稳定实例（lifecycleOwner 通过 `rememberUpdatedState` 读取）；`onHostResumed` 先重新校验 pending 页面再 force follow-up，避免为已导航到非会议页的请求弹权限框；`onPageFinished` 显式收敛 `progress=100`，避免叠加进度条停留。
  - 复审修正：用 `permissionFlowActive` 区分“权限对话框导致的 pause”和“后台请求”。同一轮权限流程中，已授权资源可直接 grant，缺失权限排队到 `ON_RESUME`；无流程的后台请求仍 deny。解决 LiveKit 摄像头授权后紧接着申请已授权麦克风时，第二个请求因权限对话框 pause 被误拒的问题。

## 12. 实施记录（2026-11-15）

### 12.1 已落地文件

- 修改 `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewScreen.kt`
  - `InitialUrlLoader`：attach + 非零尺寸后只加载一次；dispose 时移除监听。
  - `MATCH_PARENT`、`useWideViewPort`、`loadWithOverviewMode`、按会议页条件设置 `mediaPlaybackRequiresUserGesture`。
  - 进度条移到 `Box` 叠加层。
  - `WebChromeClient.onPermissionRequest` / `onPermissionRequestCanceled` 接入 Coordinator。
  - launcher 在 early return 前注册；协调器与 loader 在 `onDispose` 中先清理，再销毁 WebView。
- 新增 `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewMediaPermissions.kt`
  - 受信任会议页/origin 判断、资源映射、`MediaPermissionPlan`（含 `missingRuntimePermissions`）。
- 新增 `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewMediaPermissionCoordinator.kt`
  - pending 队列、串行系统权限申请、follow-up、取消/销毁清理、grant 前 `checkSelfPermission` 复核。
  - 系统权限对话框导致 Activity pause 时，结果仍处理；follow-up 由 `LifecycleEventObserver.ON_RESUME` 调 `onHostResumed()` 继续。
- 新增单元测试：
  - `WebViewMediaPermissionTest.kt`
  - `WebViewMediaPermissionCoordinatorTest.kt`
- 扩展 `WebViewScreenTest.kt`：viewport/首次加载单次测试，保留原有 404、返回键、非法 URL 测试。

### 12.2 测试结果

```bash
cd apps/android
./gradlew :LivaChatApp:testDebugUnitTest \
  --tests 'dev.livachat.app.ui.screens.webview.*'
# BUILD SUCCESSFUL

./gradlew :LivaChatApp:testDebugUnitTest
# BUILD SUCCESSFUL（全量 App 单测）

./gradlew :LivaChatApp:connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.class=dev.livachat.app.ui.screens.webview.WebViewScreenTest
# Starting 6 tests ... 6/6 completed, 0 failed

./gradlew :LivaChatApp:assembleDebug
# BUILD SUCCESSFUL
```

Huawei 真机额外探针（临时测试，验证后已删除，不进入提交）：

- 实际本地 `/meeting/<number>` 页面在应用内 WebView 中完整显示：AppBar 下整页浅色背景、会议卡片完整可见，不再出现 0×0 视口导致的“窄条 + 黑底”。
- 在预授予 CAMERA/RECORD_AUDIO 运行时权限后，测试页 `getUserMedia({audio:true,video:true})` 返回成功；说明 `onPermissionRequest` 接线和同源/loopback 授权路径可用。
- 未预授予权限时，确认页面请求会拉起系统相机权限对话框；但 Compose 测试在系统对话框导致 Activity pause 期间无法读取语义树，未做自动点击授权。系统对话框的允许/拒绝/部分授权和真实 LiveKit 入会仍需按 §6.3 手工验收。

### 12.3 未完成事项

- 真机手工：首次系统权限对话框、拒绝摄像头/麦克风、真实 LiveKit 入会与 A/V。
- 合入后同步 §5.5 列出的旧文档限制。
- 提交 commit（本次未提交）。


## 13. WebView 语言 locale 修复（2026-11-16）

### 13.1 现象与证据

- 设备：Huawei 设备，Android 12；系统/App 语言为 `zh-Hans-CN`。
- WebView provider：`com.huawei.webview 14.0.2.319`（Chromium 99）。
- 同一会议 URL：系统浏览器显示中文；应用内 WebView 显示英文。
- 结论：该 WebView 提供给 `navigator.language` / `navigator.languages` 的 locale 不能可靠代表系统/App 语言，Web 端不能只依赖浏览器语言探测。

### 13.2 修复方案

1. Android 侧用 `appLanguageCode(context.resources.configuration.locales[0])` 取得 canonical app locale。
2. `WebViewLocale.localizedWebUrl(url, language, serverBaseUrl, allowDebugLoopback)` 仅对**受信任会议页**（`isTrustedMeetingPage`）追加 `?lang=<canonical>`；外部页面不改，避免破坏第三方/签名 URL。具体编码由 `withLanguageQuery` 完成：
   - 已有 `lang` 会被替换；
   - 保留其他 query 参数和 fragment；
   - 仅修改用于 WebView 加载的 URL；“在浏览器中打开”仍使用原始 URL。
3. Web 端 `localeFromQuery(window.location.search)` 优先读取 `lang`，存在时用 `resolveLocale([value])`；否则使用 `navigator.languages`。
4. `lang` 只影响 UI 文案，不改变会议号、鉴权、媒体权限或 WebView 信任边界；不支持的语言（如 Web 不支持的 `ar`）按 Web 规则回落 `en`。

### 13.3 改动文件

- 新增 `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewLocale.kt`
- 修改 `apps/android/LivaChatApp/src/main/kotlin/dev/livachat/app/ui/screens/webview/WebViewScreen.kt`
  - 计算 app locale 并传入 `localizedWebUrl`（仅受信任会议页）；`asciiUrl`/`InitialUrlLoader` 使用带 `lang` 的 URL。
- 修改 `apps/web/meeting/src/i18n/resolveLocale.ts`
  - 新增 `localeFromQuery(search)`。
- 修改 `apps/web/meeting/src/main.tsx`
  - `?lang=` 优先于 `navigator.languages`。
- 扩展 `apps/web/meeting/src/i18n/resolveLocale.test.ts`
  - 覆盖 `?lang=zh-CN`、`?foo=1&lang=ja-JP`、空值。
- 扩展 `apps/android/LivaChatApp/src/test/kotlin/dev/livachat/app/ui/screens/webview/WebViewUrlPolicyTest.kt`
  - 覆盖新增、替换旧 `lang`、保留 query/fragment，以及只对受信任会议页追加 `lang`。

> 本文 §5.4 的“Web 会议应用代码和 CSS 不改动”是 2026-11-15 当轮的记录；2026-11-16 的语言桥新增了上述 Web 改动。

### 13.4 验证

```bash
# Android WebView locale policy tests
ANDROID_HOME="$HOME/Library/Android/sdk" ./gradlew -p apps/android \
  :LivaChatApp:testDebugUnitTest \
  --tests 'dev.livachat.app.ui.screens.webview.WebViewUrlPolicyTest'
# BUILD SUCCESSFUL

# Web
cd apps/web/meeting
npm run check
npm test
npm run build
# 14 files / 77 tests passed; build successful
```

- 安装带语言桥的 release APK 后，系统/App 为中文时，应用内 WebView 的会议首页显示中文。
- 系统浏览器仍按自身语言显示中文；外部浏览器 URL 不追加 `lang`。

### 13.5 未做

- iOS `WKWebView` 尚未加同类语言桥；本次只处理 Android WebView。
- 未把语言写入 LiveKit token、BFF join 请求或会议记录。


## 14. WebView 设备 label 为空（2026-11-16）

### 14.1 现象与证据

- 应用内会议页可正常使用摄像头（本地预览有画面），但点击摄像头下拉箭头时菜单只有空白/蓝色条，看起来“设备为空”。
- 系统浏览器打开同一会议 URL 时设备名称正常。
- 设备为 Huawei WebView 14.0.2.319（Chromium 99）。该 WebView 的 `navigator.mediaDevices.enumerateDevices()` 返回设备项，但 `label` 为空；LiveKit `MediaDeviceMenu` 直接渲染 label，因此出现空白项。
- 这不是设备权限失败：`getUserMedia()` 已成功，摄像头画面正常。

### 14.2 修复

- 新增 `apps/web/meeting/src/deviceLabels.ts`：
  - `addDeviceLabelFallbacks(devices, messages, grantedKinds?)` 对空 label 按 kind 生成稳定的本地化名称；
  - `videoinput` → `settingsCamera`，`audioinput` → `settingsMicrophone`，`audiooutput` → `settingsSpeaker`，后接序号；
  - 非空 label 原样保留；未知 kind 或未授权 kind 保持原样；
  - `installDeviceLabelFallback()` 在 React/LiveKit 挂载前包装 `navigator.mediaDevices.enumerateDevices()` 和 `getUserMedia()`；只有对应 kind 的 `getUserMedia` 成功后，才替换该 kind 的空 label。
  - 只改变 label，不改变 deviceId/groupId/kind。
- **权限探测兼容性**：Chrome/WebView 在权限授予前返回空 label 且 deviceId 为空；LiveKit `DeviceManager.getDevices()` 依赖空 label 判断是否调用 `getUserMedia`。如果直接替换全部空 label，会跳过这次权限探测并显示不可用的占位设备。因此 fallback 按 kind 延迟到 `getUserMedia` 成功后启用。
- 新增 `settingsSpeaker` 到 15 个 locale 目录（第一阶段目录从 30 增至 31 key）。
- 仅影响空 label 环境；系统浏览器返回真实 label 时不改变设备信息。

### 14.3 验证

- Web 单测：`deviceLabels.test.ts` 覆盖空 label、已有 label、中文 fallback、未授权 kind 保持原样，以及权限前/后的 `installDeviceLabelFallback` 行为；`npm test` 为 **14 个文件 / 77 个测试通过**。
- Android 真机：重新打开会议页后，摄像头下拉显示「摄像头 1..6」；选择摄像头 1 可恢复画面，选择其他条目可切换（部分虚拟/不可用设备可能显示黑屏，取决于 WebView 枚举结果）。
- 系统浏览器设备名称仍由浏览器提供，不受该 fallback 影响。

### 14.4 限制

- WebView 不提供真实 label，无法恢复厂商/系统设备名称；fallback 只能保证菜单不空白。
- 若 WebView 返回空数组（而不是空 label），fallback 无法生成设备条目；当前设备返回的是空 label。该情况可通过原生设备枚举另行处理，暂未实施。
- fallback 依赖对应 kind 的 `getUserMedia` 成功；权限前或权限被拒绝时保持原生空 label，避免显示不可用的占位设备。
