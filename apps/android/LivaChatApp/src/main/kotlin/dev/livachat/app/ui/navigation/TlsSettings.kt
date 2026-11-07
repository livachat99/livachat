package dev.livachat.app.ui.navigation

import androidx.compose.runtime.staticCompositionLocalOf
import java.net.URI

/**
 * 是否允许自签名证书（进程启动时读取持久化设置；修改后需重启，与 SDK/MyLife 一致）。
 *
 * 默认 `false`：预览/单测/未注入时保持系统校验；生产由 `MainActivity` 注入用户设置。
 */
internal val LocalAllowSelfSigned = staticCompositionLocalOf { false }

/**
 * 两个 URL 是否同源（host 大小写不敏感 + 有效端口）。
 *
 * 用于 WebView 的 SSL 错误处理：只在用户启用自签名、且失败资源属于已配置服务器时
 * 才放行，避免对任意站点关闭证书校验（对齐 iOS `WebPageView` 的 serverHost/port 判定）。
 */
internal fun isSameOrigin(a: String, b: String): Boolean {
    val ua = runCatching { URI(a) }.getOrNull() ?: return false
    val ub = runCatching { URI(b) }.getOrNull() ?: return false
    val hostA = ua.host ?: return false
    val hostB = ub.host ?: return false
    return hostA.equals(hostB, ignoreCase = true) && effectivePort(ua) == effectivePort(ub)
}

private fun effectivePort(uri: URI): Int = if (uri.port != -1) {
    uri.port
} else {
    when (uri.scheme?.lowercase()) {
        "https" -> 443
        "http" -> 80
        else -> -1
    }
}
