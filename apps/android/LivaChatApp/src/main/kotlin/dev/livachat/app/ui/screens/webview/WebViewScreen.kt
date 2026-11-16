package dev.livachat.app.ui.screens.webview

import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.net.http.SslError
import android.view.View
import android.view.ViewGroup
import android.webkit.PermissionRequest
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import dev.livachat.app.BuildConfig
import dev.livachat.app.R
import dev.livachat.app.ui.components.isHttpUrl
import dev.livachat.app.ui.navigation.LocalAllowSelfSigned
import dev.livachat.app.ui.navigation.LocalServerBaseUrl
import dev.livachat.app.ui.navigation.isSameOrigin
import dev.livachat.app.ui.util.appLanguageCode
import dev.livachat.app.ui.theme.Spacing

private const val WEBVIEW_TAG = "LivaChatWebView"

/** 非 http(s) 导航一律阻止（防重定向到非法 scheme）。返回 true = 拦截。 */
internal fun shouldBlockNavigation(url: String): Boolean = !url.isHttpUrl()

/** 系统浏览器打开（无可用浏览器时忽略，不崩溃）。 */
internal fun openInBrowser(context: android.content.Context, url: String) {
    try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    } catch (_: Exception) {
    }
}

/**
 * WebView 首次加载的时序保护：AndroidView.factory 执行时 WebView 还没有完成测量/布局。
 * 部分厂商 WebView（特别是 Huawei WebView 14 / Chromium 99）会把初始视口锁在
 * 极小高度，导致页面 100vh 塌缩。这里等到 View 真正 attach 且宽高非零后再 loadUrl。
 *
 * 该 loader 由 remember 持有；离开组合时调用 [dispose]，避免销毁后的布局回调再触发加载。
 */
private class InitialUrlLoader(private val url: String) : View.OnLayoutChangeListener {
    private var webView: WebView? = null
    private var loaded = false

    fun attach(view: WebView) {
        webView = view
        if (tryLoad()) return
        view.addOnLayoutChangeListener(this)
    }

    override fun onLayoutChange(
        v: View,
        left: Int,
        top: Int,
        right: Int,
        bottom: Int,
        oldLeft: Int,
        oldTop: Int,
        oldRight: Int,
        oldBottom: Int,
    ) {
        if (tryLoad()) {
            v.removeOnLayoutChangeListener(this)
        }
    }

    private fun tryLoad(): Boolean {
        val view = webView ?: return false
        if (loaded || !view.isAttachedToWindow || view.width <= 0 || view.height <= 0) {
            return false
        }
        loaded = true
        android.util.Log.d(WEBVIEW_TAG, "layout ready ${view.width}x${view.height}; loadUrl")
        view.loadUrl(url)
        return true
    }

    fun dispose() {
        loaded = true
        webView?.removeOnLayoutChangeListener(this)
        webView = null
    }
}

@SuppressLint("SetJavaScriptEnabled")
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WebViewScreen(
    url: String,
    onNavigateBack: () -> Unit,
) {
    val context = LocalContext.current
    val allowSelfSigned = LocalAllowSelfSigned.current
    val serverBaseUrl = LocalServerBaseUrl.current
    // 入参校验：非 http/https 直接错误占位
    val safeUrl = remember(url) { if (url.isHttpUrl()) url else null }
    // 某些厂商 WebView 的 navigator.language 与系统/App 语言不一致（实测 Huawei
    // WebView 返回英文）。把 App 资源语言显式传给会议页，避免 WebView 回落英文。
    val appLanguage = remember(context) {
        val locales = context.resources.configuration.locales
        if (locales.isEmpty()) "en" else appLanguageCode(locales[0])
    }
    val localizedUrl = remember(safeUrl, appLanguage, serverBaseUrl) {
        safeUrl?.let {
            localizedWebUrl(it, appLanguage, serverBaseUrl, BuildConfig.DEBUG)
        }
    }
    // 中文等非 ASCII 路径必须转成 %XX 形式，WebView 直接 loadUrl 会导航失败且无回调
    val asciiUrl = remember(localizedUrl) {
        localizedUrl?.let {
            runCatching { java.net.URI(it).toASCIIString() }.getOrNull() ?: it
        }
    }

    var webView by remember { mutableStateOf<WebView?>(null) }
    var progress by remember { mutableIntStateOf(0) }
    var pageTitle by remember { mutableStateOf<String?>(null) }
    var loadError by remember { mutableStateOf(false) }

    val lifecycleOwner = LocalLifecycleOwner.current
    val currentLifecycleOwner by rememberUpdatedState(lifecycleOwner)
    // rememberLauncherForActivityResult 必须在 safeUrl == null 的 early return 之前注册，
    // 否则非法 URL 分支和正常分支的 Composable 调用顺序不一致。
    val permissionLauncherHolder = remember {
        mutableStateOf<ActivityResultLauncher<Array<String>>?>(null)
    }
    val mediaPermissionCoordinator = remember(serverBaseUrl) {
        WebViewMediaPermissionCoordinator(
            pageUrlProvider = { webView?.url },
            serverBaseUrl = serverBaseUrl,
            allowDebugLoopback = BuildConfig.DEBUG,
            isRuntimePermissionGranted = { permission ->
                context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED
            },
            isHostResumed = {
                currentLifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)
            },
            isWebViewAlive = { webView?.isAttachedToWindow == true },
            requestRuntimePermissions = { permissions ->
                val launcher = permissionLauncherHolder.value
                    ?: error("media permission launcher unavailable")
                launcher.launch(permissions.toTypedArray())
            },
        )
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) {
        mediaPermissionCoordinator.onRuntimePermissionResult()
    }
    SideEffect {
        permissionLauncherHolder.value = permissionLauncher
    }

    if (safeUrl == null) {
        ErrorPlaceholder(
            detail = stringResource(R.string.webview_unsupported_link),
            onNavigateBack = onNavigateBack,
        )
        return
    }

    val initialUrl: String = asciiUrl ?: localizedUrl ?: safeUrl
    val initialLoader = remember(initialUrl) { InitialUrlLoader(initialUrl) }
    val currentCoordinator by rememberUpdatedState(mediaPermissionCoordinator)
    val currentInitialLoader by rememberUpdatedState(initialLoader)
    val host = Uri.parse(safeUrl).host ?: safeUrl

    // 系统权限对话框会让 Activity 短暂 pause；回到 RESUMED 时继续处理被延后的 follow-up。
    DisposableEffect(lifecycleOwner, mediaPermissionCoordinator) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                mediaPermissionCoordinator.onHostResumed()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = pageTitle ?: host,
                        style = MaterialTheme.typography.titleLarge,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onNavigateBack) {
                        Icon(Icons.Default.Close, contentDescription = stringResource(R.string.common_close))
                    }
                },
                actions = {
                    IconButton(onClick = { openInBrowser(context, safeUrl) }) {
                        Icon(Icons.AutoMirrored.Filled.OpenInNew, contentDescription = stringResource(R.string.webview_open_in_browser))
                    }
                },
            )
        },
    ) { padding ->
        // WebView 常驻组合（错误态用覆盖层而非分支切换），保证重试/返回栈状态可用。
        // 进度条也改为 Box 叠加，避免加载过程中 WebView 尺寸变化影响厂商视口计算。
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            AndroidView(
                factory = { ctx ->
                    WebView(ctx).apply {
                        layoutParams = ViewGroup.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.MATCH_PARENT,
                        )
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        settings.allowFileAccess = false
                        settings.allowContentAccess = false
                        settings.setSupportMultipleWindows(false)
                        settings.useWideViewPort = true
                        settings.loadWithOverviewMode = true
                        // 只对受信任会议页关闭“必须用户手势才播放”；普通网页保持默认。
                        settings.mediaPlaybackRequiresUserGesture = !isTrustedMeetingPage(
                            pageUrl = safeUrl,
                            serverBaseUrl = serverBaseUrl,
                            allowDebugLoopback = BuildConfig.DEBUG,
                        )
                        if (BuildConfig.DEBUG) {
                            WebView.setWebContentsDebuggingEnabled(true)
                        }
                        webViewClient = object : WebViewClient() {
                            override fun shouldOverrideUrlLoading(
                                view: WebView,
                                request: WebResourceRequest,
                            ): Boolean {
                                if (!shouldBlockNavigation(request.url.toString())) return false
                                android.util.Log.w(WEBVIEW_TAG, "blocked navigation to ${request.url}")
                                return true
                            }

                            override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
                                android.util.Log.d(WEBVIEW_TAG, "pageStarted: $url")
                                // 不能在这里重置 loadError：404 等主框架错误可能先于
                                // onPageStarted 回调（实测 mainFrameHttpError → pageStarted），
                                // 在此清除会把错误占位符吞掉。错误只在重试时显式清除。
                                progress = 0
                            }

                            override fun onPageFinished(view: WebView, url: String) {
                                android.util.Log.d(WEBVIEW_TAG, "pageFinished: $url")
                                // 某些厂商/页面下 onProgressChanged 可能停在 99；显式收敛，
                                // 避免叠加的进度条一直显示。
                                progress = 100
                            }

                            override fun onReceivedError(
                                view: WebView,
                                request: WebResourceRequest,
                                error: WebResourceError,
                            ) {
                                if (request.isForMainFrame) {
                                    android.util.Log.e(WEBVIEW_TAG, "mainFrameError: ${error.errorCode} ${error.description} url=${request.url}")
                                    loadError = true
                                }
                            }

                            override fun onReceivedHttpError(
                                view: WebView,
                                request: WebResourceRequest,
                                errorResponse: android.webkit.WebResourceResponse,
                            ) {
                                if (request.isForMainFrame) {
                                    android.util.Log.e(WEBVIEW_TAG, "mainFrameHttpError: ${errorResponse.statusCode} url=${request.url}")
                                    loadError = true
                                }
                            }

                            override fun onReceivedSslError(
                                view: WebView,
                                handler: SslErrorHandler,
                                error: SslError,
                            ) {
                                // 与 iOS WebPageView 一致：仅在“用户允许自签名”且失败资源属于
                                // 已配置服务器时放行；外部站点/关闭开关时保持系统证书校验。
                                val allowed = allowSelfSigned &&
                                    isSameOrigin(error.url, serverBaseUrl)
                                android.util.Log.w(
                                    WEBVIEW_TAG,
                                    "sslError: ${error.primaryError} url=${error.url} allowed=$allowed",
                                )
                                if (allowed) {
                                    handler.proceed()
                                } else {
                                    handler.cancel()
                                    loadError = true
                                }
                            }
                        }
                        webChromeClient = object : WebChromeClient() {
                            override fun onProgressChanged(view: WebView, newProgress: Int) {
                                progress = newProgress
                            }

                            override fun onReceivedTitle(view: WebView, title: String?) {
                                pageTitle = title
                            }

                            override fun onPermissionRequest(request: PermissionRequest) {
                                mediaPermissionCoordinator.onPermissionRequest(request)
                            }

                            override fun onPermissionRequestCanceled(request: PermissionRequest) {
                                mediaPermissionCoordinator.onPermissionRequestCanceled(request)
                            }
                        }
                        initialLoader.attach(this)
                    }
                },
                update = { webView = it },
                modifier = Modifier.fillMaxSize(),
            )
            if (progress in 1 until 100 && !loadError) {
                LinearProgressIndicator(
                    progress = { progress / 100f },
                    modifier = Modifier
                        .fillMaxWidth()
                        .align(Alignment.TopCenter),
                )
            }
            if (loadError) {
                ErrorPlaceholder(
                    detail = stringResource(R.string.webview_load_failed),
                    onNavigateBack = onNavigateBack,
                    onRetry = { loadError = false; webView?.reload() },
                    onOpenBrowser = { openInBrowser(context, safeUrl) },
                )
            }
        }
    }

    BackHandler(enabled = true) {
        if (webView?.canGoBack() == true) {
            webView?.goBack()
        } else {
            onNavigateBack()
        }
    }

    // key 必须是 Unit：以 webView 实例为 key 会在实例写入 state 时触发
    // recomposition 并 dispose 旧 effect，导致刚创建的 WebView 被立即销毁
    // （页面加载中止、白屏）。onDispose 在真正离开组合时才读取当前实例。
    DisposableEffect(Unit) {
        onDispose {
            // 先拒绝未完成媒体请求，再移除/销毁 WebView，保证 PermissionRequest
            // 在 WebView 仍然有效时被清理。
            currentCoordinator.dispose()
            currentInitialLoader.dispose()
            webView?.let { wv ->
                // 先脱离视图树再 destroy，避免“destroy while attached”崩溃
                (wv.parent as? ViewGroup)?.removeView(wv)
                wv.destroy()
            }
        }
    }
}

@Composable
private fun ErrorPlaceholder(
    detail: String,
    onNavigateBack: () -> Unit,
    onRetry: (() -> Unit)? = null,
    onOpenBrowser: (() -> Unit)? = null,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(Spacing.xl),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(stringResource(R.string.webview_cannot_open), style = MaterialTheme.typography.titleLarge)
        Spacer(modifier = Modifier.height(Spacing.sm))
        Text(
            detail,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(modifier = Modifier.height(Spacing.lg))
        Row(horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
            OutlinedButton(onClick = onNavigateBack) {
                Text(stringResource(R.string.common_back))
            }
            if (onOpenBrowser != null) {
                OutlinedButton(onClick = onOpenBrowser) {
                    Text(stringResource(R.string.webview_open_in_browser))
                }
            }
            if (onRetry != null) {
                Button(onClick = onRetry) {
                    Text(stringResource(R.string.common_retry))
                }
            }
        }
    }
}
