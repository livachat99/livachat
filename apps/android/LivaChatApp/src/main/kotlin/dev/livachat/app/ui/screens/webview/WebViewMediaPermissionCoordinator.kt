package dev.livachat.app.ui.screens.webview

import android.util.Log
import android.webkit.PermissionRequest

private const val WEBVIEW_MEDIA_TAG = "LivaChatWebViewMedia"

/**
 * 串行管理 WebView 的 [PermissionRequest] 和 Android 运行时权限申请。
 *
 * 关键约束：
 * - 只对受信任会议页 + 同 origin 的请求工作；
 * - 同一时间只发起一轮系统权限申请；
 * - grant 只包含 `checkSelfPermission == GRANTED` 的资源，避免 WebView 进入
 *   “对未授予权限 grant 后不再回调 onPermissionRequest”的坏状态；
 * - 页面导航/取消/WebView 销毁后不再 grant；Activity 因权限对话框暂停时，
 *   结果仍处理，缺失权限的 follow-up 等 ON_RESUME 再发起。
 *
 * 所有方法应在主线程调用。构造参数注入可测试回调，生产由 WebViewScreen 提供。
 */
internal class WebViewMediaPermissionCoordinator(
    private val pageUrlProvider: () -> String?,
    private val serverBaseUrl: String,
    private val allowDebugLoopback: Boolean,
    private val isRuntimePermissionGranted: (String) -> Boolean,
    private val isHostResumed: () -> Boolean,
    private val isWebViewAlive: () -> Boolean,
    private val requestRuntimePermissions: (Set<String>) -> Unit,
    private val log: (String) -> Unit = { message -> Log.d(WEBVIEW_MEDIA_TAG, message) },
) {
    private data class PendingRequest(
        val request: PermissionRequest,
        val requestOrigin: String?,
        val webResources: Set<String>,
        val requiredRuntimePermissions: Set<String>,
    )

    private val pending = LinkedHashMap<PermissionRequest, PendingRequest>()
    private val attemptedRuntimePermissions = mutableSetOf<String>()
    private var runtimeRequestInFlight = false
    private var permissionFlowActive = false
    private var disposed = false

    fun onPermissionRequest(request: PermissionRequest) {
        if (disposed) {
            deny(request, "coordinator disposed")
            return
        }
        if (!isWebViewAlive()) {
            deny(request, "webview not alive")
            return
        }
        // 权限对话框会让 Activity pause；此时仍要接收同一轮授权流程中的后续请求
        // （例如 LiveKit 在摄像头授权后紧接着申请已授权的麦克风）。
        if (!isHostResumed() && !permissionFlowActive) {
            deny(request, "host not resumed")
            return
        }

        val pageUrl = pageUrlProvider()
        val requestOrigin = request.origin?.toString()
        if (!isAllowedMediaRequest(pageUrl, requestOrigin, serverBaseUrl, allowDebugLoopback)) {
            deny(request, "untrusted media request")
            return
        }

        val requestedResources = request.resources?.toSet().orEmpty()
        val plan = planMediaPermission(requestedResources, isRuntimePermissionGranted)
        if (plan.webResources.isEmpty()) {
            deny(request, "no supported media resources")
            return
        }

        val grantable = plan.webResources.filter { resource ->
            runtimePermissionFor(resource)?.let(isRuntimePermissionGranted) == true
        }.toSet()
        if (plan.missingRuntimePermissions.isEmpty()) {
            if (grantable.isNotEmpty()) {
                grant(request, grantable, "all runtime permissions granted")
            } else {
                deny(request, "no granted runtime permission")
            }
            return
        }

        pending[request] = PendingRequest(
            request = request,
            requestOrigin = requestOrigin,
            webResources = plan.webResources,
            requiredRuntimePermissions = plan.runtimePermissions,
        )
        scheduleRuntimePermissions()
    }

    /** WebView 取消了页面请求（导航、页面销毁等）；不要对已取消的 request 再 grant。 */
    fun onPermissionRequestCanceled(request: PermissionRequest) {
        if (pending.remove(request) != null) {
            resetAttemptedIfIdle()
        }
    }

    /** ActivityResult 返回后调用；Coordinator 会复核实际权限状态。 */
    fun onRuntimePermissionResult() {
        if (disposed || !runtimeRequestInFlight) return
        runtimeRequestInFlight = false
        // 权限对话框可能让 Activity 短暂 pause，且部分系统版本会在 onResume 之前
        // 投递结果；这里不能因为 isHostResumed == false 就 deny，否则正常授权会失败。
        processPending()
        scheduleRuntimePermissions()
        if (isHostResumed() && !runtimeRequestInFlight) {
            permissionFlowActive = false
        }
    }

    /** Activity 回到 RESUMED 时调用；处理因权限对话框暂停而延后的 follow-up 申请。 */
    fun onHostResumed() {
        if (disposed) return
        if (runtimeRequestInFlight) return
        if (pending.isNotEmpty()) {
            // 先重新校验页面/权限状态，避免为已导航到非会议页的请求继续弹权限框。
            processPending()
            if (pending.isNotEmpty()) {
                // Lifecycle 的 ON_RESUME 回调时机由实现决定；以事件本身为准，不再依赖
                // currentState 的瞬时值，避免 follow-up 永远不发起。
                scheduleRuntimePermissions(force = true)
            }
        }
        if (!runtimeRequestInFlight) {
            permissionFlowActive = false
        }
    }

    /** WebViewScreen 离开组合时调用：拒绝未完成请求并停止后续申请。 */
    fun dispose() {
        if (disposed) return
        disposed = true
        runtimeRequestInFlight = false
        permissionFlowActive = false
        attemptedRuntimePermissions.clear()
        val requests = pending.values.toList()
        pending.clear()
        requests.forEach { deny(it.request, "coordinator disposed") }
    }

    private fun scheduleRuntimePermissions(force: Boolean = false) {
        if (disposed || runtimeRequestInFlight) return
        if (!isWebViewAlive()) {
            denyAllPending("webview not alive")
            return
        }
        // 权限对话框会让 Activity 短暂 pause。pending 不应被 deny；等 onHostResumed()
        // 后再发起 follow-up，避免正常授权被生命周期误伤。
        if (!force && !isHostResumed()) return

        val needed = pending.values
            .flatMap { it.requiredRuntimePermissions }
            .filter { !isRuntimePermissionGranted(it) && it !in attemptedRuntimePermissions }
            .toSet()
        if (needed.isEmpty()) {
            processPending()
            return
        }

        attemptedRuntimePermissions += needed
        permissionFlowActive = true
        runtimeRequestInFlight = true
        runCatching { requestRuntimePermissions(needed) }
            .onFailure { error ->
                runtimeRequestInFlight = false
                permissionFlowActive = false
                log("runtime permission launch failed: ${error.message}")
                denyAllPending("launch failed")
            }
    }

    private fun processPending() {
        if (disposed) return
        if (!isWebViewAlive()) {
            denyAllPending("webview not alive")
            return
        }

        val iterator = pending.entries.iterator()
        while (iterator.hasNext()) {
            val entry = iterator.next()
            val item = entry.value
            val currentPageUrl = pageUrlProvider()
            if (!isAllowedMediaRequest(currentPageUrl, item.requestOrigin, serverBaseUrl, allowDebugLoopback)) {
                deny(item.request, "page no longer trusted")
                iterator.remove()
                continue
            }

            val unresolved = item.requiredRuntimePermissions.filter {
                !isRuntimePermissionGranted(it) && it !in attemptedRuntimePermissions
            }
            if (unresolved.isNotEmpty()) continue

            val grantable = item.webResources.filter { resource ->
                runtimePermissionFor(resource)?.let(isRuntimePermissionGranted) == true
            }.toSet()
            if (grantable.isEmpty()) {
                deny(item.request, "no runtime permission granted")
            } else {
                grant(item.request, grantable, "grant runtime permissions")
            }
            iterator.remove()
        }
        resetAttemptedIfIdle()
    }

    private fun resetAttemptedIfIdle() {
        if (pending.isEmpty() && !runtimeRequestInFlight) {
            attemptedRuntimePermissions.clear()
        }
    }

    private fun denyAllPending(reason: String) {
        val requests = pending.values.toList()
        pending.clear()
        attemptedRuntimePermissions.clear()
        permissionFlowActive = false
        requests.forEach { deny(it.request, reason) }
    }

    private fun grant(request: PermissionRequest, resources: Set<String>, reason: String) {
        if (resources.isEmpty()) {
            deny(request, "empty grant")
            return
        }
        log("grant ${resources.joinToString()} ($reason)")
        runCatching { request.grant(resources.toTypedArray()) }
            .onFailure { error -> log("grant failed: ${error.message}") }
    }

    private fun deny(request: PermissionRequest, reason: String) {
        log("deny ($reason)")
        runCatching { request.deny() }
            .onFailure { error -> log("deny failed: ${error.message}") }
    }
}
