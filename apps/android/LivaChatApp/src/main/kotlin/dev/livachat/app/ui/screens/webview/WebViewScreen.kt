package dev.livachat.app.ui.screens.webview

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.view.ViewGroup
import android.net.http.SslError
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import dev.livachat.app.R
import dev.livachat.app.ui.components.isHttpUrl
import dev.livachat.app.ui.navigation.LocalAllowSelfSigned
import dev.livachat.app.ui.navigation.LocalServerBaseUrl
import dev.livachat.app.ui.navigation.isSameOrigin
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
    // 中文等非 ASCII 路径必须转成 %XX 形式，WebView 直接 loadUrl 会导航失败且无回调
    val asciiUrl = remember(safeUrl) {
        safeUrl?.let { runCatching { java.net.URI(it).toASCIIString() }.getOrNull() ?: it }
    }
    if (safeUrl == null) {
        ErrorPlaceholder(
            detail = stringResource(R.string.webview_unsupported_link),
            onNavigateBack = onNavigateBack,
        )
        return
    }

    var webView by remember { mutableStateOf<WebView?>(null) }
    var progress by remember { mutableIntStateOf(0) }
    var pageTitle by remember { mutableStateOf<String?>(null) }
    var loadError by remember { mutableStateOf(false) }

    val host = Uri.parse(safeUrl).host ?: safeUrl

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
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            if (progress in 1 until 100 && !loadError) {
                LinearProgressIndicator(
                    progress = { progress / 100f },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
            // WebView 常驻组合（错误态用覆盖层而非分支切换），保证重试/返回栈状态可用
            Box(modifier = Modifier.fillMaxSize()) {
                AndroidView(
                    factory = { ctx ->
                        WebView(ctx).apply {
                            settings.javaScriptEnabled = true
                            settings.domStorageEnabled = true
                            settings.allowFileAccess = false
                            settings.allowContentAccess = false
                            settings.setSupportMultipleWindows(false)
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
                            }
                            android.util.Log.d(WEBVIEW_TAG, "loadUrl: ${asciiUrl ?: safeUrl}")
                            loadUrl(asciiUrl ?: safeUrl)
                        }
                    },
                    update = { webView = it },
                    modifier = Modifier.fillMaxSize(),
                )
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
