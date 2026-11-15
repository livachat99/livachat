package dev.livachat.app.ui.screens.webview

import android.Manifest
import android.net.Uri
import android.webkit.PermissionRequest

/**
 * WebView 媒体权限的纯策略：受信任会议页判断、请求 origin 校验、资源映射。
 *
 * 这些函数不启动系统权限申请，也不调用 [PermissionRequest]；Coordinator 负责生命周期和队列。
 */

internal data class MediaPermissionPlan(
    /** 页面请求中 App 支持的 WebView 资源（未知资源已过滤）。 */
    val webResources: Set<String>,
    /** 上述资源对应的 Android 运行时权限。 */
    val runtimePermissions: Set<String>,
    /** 当前尚未授予的运行时权限。 */
    val missingRuntimePermissions: Set<String>,
)

private const val MEETING_PATH_SEGMENT = "meeting"
private const val HOST_LOCALHOST = "localhost"
private const val HOST_LOOPBACK_V6 = "::1"
private const val HOST_EMULATOR = "10.0.2.2"

private val MEDIA_RESOURCE_PERMISSIONS = mapOf(
    PermissionRequest.RESOURCE_VIDEO_CAPTURE to Manifest.permission.CAMERA,
    PermissionRequest.RESOURCE_AUDIO_CAPTURE to Manifest.permission.RECORD_AUDIO,
)

/** WebView 资源对应的 Android 运行时权限；未知资源返回 null。 */
internal fun runtimePermissionFor(resource: String): String? =
    MEDIA_RESOURCE_PERMISSIONS[resource]

/**
 * 只信任同时满足以下条件的页面：
 * - http/https，且 host 非空；
 * - path 第一段严格是 `meeting`，不接受 `.` / `..` 或解码后的斜杠/反斜杠；
 * - origin 与 App 配置的 [serverBaseUrl] 同源；debug 构建额外允许 loopback/模拟器 host。
 *
 * 注意：不能只判断“请求 origin == 当前页面 origin”，否则任意第三方页面都能申请媒体。
 */
internal fun isTrustedMeetingPage(
    pageUrl: String?,
    serverBaseUrl: String,
    allowDebugLoopback: Boolean,
): Boolean {
    if (pageUrl.isNullOrBlank()) return false
    val uri = runCatching { Uri.parse(pageUrl) }.getOrNull() ?: return false
    val pageOrigin = parseWebOrigin(uri) ?: return false
    val segments = runCatching { uri.pathSegments }.getOrNull() ?: return false
    if (segments.isEmpty() || segments.first() != MEETING_PATH_SEGMENT) return false
    if (segments.any { it == "." || it == ".." || it.contains('/') || it.contains('\\') }) {
        return false
    }

    if (isSameWebOrigin(pageOrigin, serverBaseUrl)) return true
    return allowDebugLoopback && isLoopbackHost(pageOrigin.host)
}

/** 受信任会议页且请求 origin 与页面 origin 完全一致时，才允许 WebView 媒体请求。 */
internal fun isAllowedMediaRequest(
    pageUrl: String?,
    requestOrigin: String?,
    serverBaseUrl: String,
    allowDebugLoopback: Boolean,
): Boolean {
    if (!isTrustedMeetingPage(pageUrl, serverBaseUrl, allowDebugLoopback)) return false
    val pageOrigin = pageUrl?.let { runCatching { Uri.parse(it) }.getOrNull() }
        ?.let(::parseWebOrigin) ?: return false
    val request = requestOrigin?.let { runCatching { Uri.parse(it) }.getOrNull() }
        ?.let(::parseWebOrigin) ?: return false
    return pageOrigin == request
}

/** 过滤未知资源并计算需要检查的 Android 运行时权限；不发起申请。 */
internal fun planMediaPermission(
    requestedResources: Set<String>,
    isRuntimePermissionGranted: (String) -> Boolean,
): MediaPermissionPlan {
    val webResources = requestedResources
        .filter { it in MEDIA_RESOURCE_PERMISSIONS }
        .toSet()
    val runtimePermissions = webResources
        .mapNotNull { MEDIA_RESOURCE_PERMISSIONS[it] }
        .toSet()
    return MediaPermissionPlan(
        webResources = webResources,
        runtimePermissions = runtimePermissions,
        missingRuntimePermissions = runtimePermissions.filterNot(isRuntimePermissionGranted).toSet(),
    )
}

private data class WebOrigin(
    val scheme: String,
    val host: String,
    val effectivePort: Int,
)

private fun parseWebOrigin(uri: Uri): WebOrigin? {
    val scheme = uri.scheme?.lowercase() ?: return null
    if (scheme != "http" && scheme != "https") return null
    val host = uri.host?.lowercase() ?: return null
    if (host.isEmpty()) return null
    val port = when {
        uri.port != -1 -> uri.port
        scheme == "https" -> 443
        else -> 80
    }
    if (port !in 1..65535) return null
    return WebOrigin(scheme = scheme, host = host, effectivePort = port)
}

private fun isSameWebOrigin(page: WebOrigin, baseUrl: String): Boolean {
    if (baseUrl.isBlank()) return false
    val base = runCatching { Uri.parse(baseUrl) }.getOrNull()?.let(::parseWebOrigin) ?: return false
    return page == base
}

private fun isLoopbackHost(host: String): Boolean {
    val normalized = host.removePrefix("[").removeSuffix("]").lowercase()
    if (normalized == HOST_LOCALHOST || normalized == HOST_LOOPBACK_V6 || normalized == HOST_EMULATOR) {
        return true
    }
    if (!normalized.startsWith("127.")) return false
    val parts = normalized.split('.')
    return parts.size == 4 && parts.all { part ->
        val value = part.toIntOrNull()
        value != null && value in 0..255
    }
}
