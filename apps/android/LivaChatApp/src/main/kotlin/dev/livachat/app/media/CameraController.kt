package dev.livachat.app.media

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.ImageFormat
import android.graphics.Matrix
import android.os.SystemClock
import android.util.Size
import android.view.OrientationEventListener
import android.view.Surface
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.UseCase
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.Orientation
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors
import javax.inject.Inject
import javax.inject.Singleton

@SuppressLint("MissingPermission")
@Singleton
class CameraController @Inject constructor(
    private val sdk: LivaChatSDK,
    @ApplicationContext private val context: Context,
    private val turnCounter: CaptureTurnCounter,
) {
    private val _isCameraOn = MutableStateFlow(false)
    val isCameraOn: StateFlow<Boolean> = _isCameraOn.asStateFlow()

    private val _isFrontCamera = MutableStateFlow(true)
    val isFrontCamera: StateFlow<Boolean> = _isFrontCamera.asStateFlow()

    private val _previewSurface = MutableStateFlow<Surface?>(null)
    val previewSurface: StateFlow<Surface?> = _previewSurface.asStateFlow()

    private var cameraProvider: ProcessCameraProvider? = null
    private var preview: Preview? = null
    private var imageAnalysis: ImageAnalysis? = null
    private var currentLifecycle: LifecycleOwner? = null
    // 每次 bind/unbind 递增：异步 providerFuture 回调只认最新一次，过期回调直接丢弃，
    // 避免旧的失败/成功结果覆盖新状态（如快速切换前后摄像头或开相机后又立刻关闭）。
    // @Volatile：analyzer 线程读取（§8.3 代次守卫）；写仅主线程 unbind() ++，单写者
    @Volatile
    private var bindGeneration = 0
    private var analysisExecutor = Executors.newSingleThreadExecutor()
    private var lastFrameTimeMs = 0L
    private var throttledDropCount = 0L

    // 最近一次实际推流的镜头朝向（analyzer 线程私有：单一 analysisExecutor 串行访问）。
    // 镜头切换检测（§8.2.2 P2-1）：null = 本进程尚未推过帧——首帧不比较，防误递增
    private var lastPushedFacing: String? = null

    private val orientationEventListener = object : OrientationEventListener(context) {
        override fun onOrientationChanged(orientation: Int) {
            if (orientation == ORIENTATION_UNKNOWN) return
            val rotation = UseCase.snapToSurfaceRotation(orientation)
            imageAnalysis?.targetRotation = rotation
            sdk.logDebug("CameraController", "orientation: deg=$orientation targetRotation=$rotation")
        }
    }

    fun bind(
        lifecycleOwner: LifecycleOwner,
        previewView: PreviewView,
        frontCamera: Boolean = true,
        onBindFailed: (() -> Unit)? = null,
    ) {
        val wasOn = _isCameraOn.value
        unbind()

        // 无 CAMERA 权限时 CameraX bindToLifecycle 不抛异常，只会得到无帧黑屏预览——
        // 必须在绑定前显式检查，让所有入口（手动 toggle / autoCameraOnJoin / 返回重绑）
        // 统一收敛到 onBindFailed（UI 复位 + 错误横幅）。
        // 放在状态恢复之前：早退时 unbind() 刚完成全量复位，无需任何手动清理
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED
        ) {
            sdk.logWarn("CameraController", "bind skipped: camera permission missing")
            onBindFailed?.invoke()
            return
        }

        _isCameraOn.value = wasOn  // preserve state across rebind, avoids toolbar flicker
        val generation = bindGeneration
        currentLifecycle = lifecycleOwner
        _isFrontCamera.value = frontCamera

        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        cameraProviderFuture.addListener({
            if (generation != bindGeneration) return@addListener
            try {
                cameraProvider = cameraProviderFuture.get()
                bindUseCases(previewView, frontCamera)
                _isCameraOn.value = true
            } catch (e: Exception) {
                // 兜底：绑定期异常收敛为状态复位而非崩溃/黑屏残留；
                // 直接走 unbind() 全量复位（含 currentLifecycle/_previewSurface）。
                // WARN+堆栈：绑定期异常属异常事件（权限撤销/无匹配镜头），
                // debug 日志在 release 不可见，留 warn 供线上排障
                sdk.logWarn("CameraController", "bind failed", e)
                unbind()
                onBindFailed?.invoke()
            }
        }, ContextCompat.getMainExecutor(context))
    }

    private fun bindUseCases(previewView: PreviewView, front: Boolean) {
        val provider = cameraProvider ?: return
        // 本轮代次：analyzer 用它丢弃滞后旧流残帧（§8.3-3）
        val generation = bindGeneration
        val lensFacing = if (front) CameraSelector.LENS_FACING_FRONT else CameraSelector.LENS_FACING_BACK
        val cameraSelector = CameraSelector.Builder().requireLensFacing(lensFacing).build()

        preview = Preview.Builder().build().also {
            it.setSurfaceProvider(previewView.surfaceProvider)
        }

        // 显式目标 1280×960：目标尺寸只是提示，设备/镜头不支持时按 fallback rule
        // 就近回退（CLOSEST_LOWER_THEN_HIGHER：优先更小尺寸，流量保守）；上行体积的
        // 真正上界由 imageProxyToJpeg 的 长边 ≤1280 硬闸保证
        imageAnalysis = ImageAnalysis.Builder()
            .setResolutionSelector(
                ResolutionSelector.Builder()
                    .setResolutionStrategy(
                        ResolutionStrategy(
                            TARGET_RESOLUTION,
                            ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER,
                        ),
                    )
                    .build(),
            )
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .build()
            .also { analysis ->
                // §8.3（docs/camera_enhance.md）：暖机哨兵做成 lambda 局部态——随本轮 bind 重生，
                // 仅 analyzer 线程访问（无需 @Volatile / unbind 复位），旧流残帧无从触碰新一轮
                var streamStartMs = 0L
                var warmupSkipLogged = false
                var warmupDoneLogged = false
                analysis.setAnalyzer(analysisExecutor) { imageProxy ->
                    // 滞后旧流残帧（unbind 与重绑间隙的队列回调）直接丢弃，避免按新 facing 误推
                    if (generation != bindGeneration) {
                        imageProxy.close()
                        return@setAnalyzer
                    }
                    sdk.logDebug("CameraController", "analyzer: format=${imageProxy.format} w=${imageProxy.width} h=${imageProxy.height}")
                    val now = SystemClock.elapsedRealtime()
                    if (streamStartMs == 0L) {  // 本轮首个回调
                        streamStartMs = now
                        lastFrameTimeMs = 0     // 新流新节拍：跨窗首帧不受上一轮残余节流窗推迟
                        sdk.logDebug("CameraController", "warmup: start")
                    }
                    if (shouldSkipWarmup(now - streamStartMs)) {  // 开流暖机：AE 收敛窗内丢帧
                        if (!warmupSkipLogged) {
                            warmupSkipLogged = true
                            sdk.logDebug("CameraController", "warmup: skipped (elapsedMs=${now - streamStartMs})")
                        }
                        imageProxy.close()
                        return@setAnalyzer
                    }
                    if (!warmupDoneLogged) {
                        warmupDoneLogged = true
                        sdk.logDebug("CameraController", "warmup: done (elapsedMs=${now - streamStartMs})")
                    }
                    if (now - lastFrameTimeMs >= THROTTLE_MS) {
                        lastFrameTimeMs = now
                        processFrame(imageProxy)
                    } else {
                        throttledDropCount++
                        if (throttledDropCount % 50 == 0L) {
                            sdk.logDebug("CameraController", "frame dropped (throttle window) | count=$throttledDropCount")
                        }
                        imageProxy.close()
                    }
                }
            }

        provider.unbindAll()
        val lifecycle = currentLifecycle ?: return
        sdk.logDebug("CameraController", "bind: front=$front imageAnalysis=${imageAnalysis != null}")
        provider.bindToLifecycle(lifecycle, cameraSelector, preview, imageAnalysis)
        orientationEventListener.enable()
    }

    private var frameLogCount = 0

    private data class JpegFrame(val data: ByteArray, val width: Int, val height: Int)

    private fun processFrame(imageProxy: ImageProxy) {
        try {
            val frame = imageProxyToJpeg(imageProxy)
            if (frame != null) {
                val facing = if (_isFrontCamera.value) "front" else "back"
                // 镜头切换 = 新采集轮（§8.2.2 P2-1）：与切换后首帧严格同帧递增——tap→重绑
                // 窗口（重组异步驱动）内的旧镜头帧仍持旧 id，不会被错标为"新视野首帧"；
                // 自动重绑同镜头 facing 不变 → 天然不递增
                if (shouldBumpTurn(lastPushedFacing, facing)) {
                    turnCounter.next()
                }
                lastPushedFacing = facing
                sdk.logDebug(
                    "CameraController",
                    "capture: facing=$facing w=${frame.width} h=${frame.height} rotation=${imageProxy.imageInfo.rotationDegrees}",
                )
                // 帧已在 imageProxyToJpeg 内按 rotationDegrees 旋正，协议 orientation 恒传 0；
                // turnId 逐帧携带（App 层 CaptureTurnCounter，§8）——SDK 直通落线
                sdk.pushVideoJpeg(frame.data, frame.width, frame.height, Orientation.PORTRAIT_0,
                    facing, turnCounter.current)
            }
        } catch (e: Exception) {
            // 单帧异常（缩放/编码）不得跳过 close：ImageProxy 泄漏会耗尽 ImageAnalysis
            // 队列，相机帧流静默停死；收敛为丢帧 + 日志（与 ScreenCaptureController
            // 的 finally-close 纪律对齐）。OOM 等 Error 由 finally 保证 close 后继续上抛
            sdk.logDebug("CameraController", "frame processing failed: ${e.message}")
        } finally {
            imageProxy.close()
        }
    }

    private fun imageProxyToJpeg(imageProxy: ImageProxy): JpegFrame? {
        if (imageProxy.format != ImageFormat.YUV_420_888) {
            sdk.logDebug("CameraController", "frame skipped | unsupported format=${imageProxy.format} w=${imageProxy.width} h=${imageProxy.height}")
            return null
        }
        val bitmap = imageProxy.toBitmap()
        // 长边硬闸先于旋正：长边在 90°/270° 旋转下不变，先缩放可避免回退大尺寸
        // （如 1600×1200/1920×1440）时同时持有全尺寸原图与全尺寸旋转副本（峰值约减半）
        val scaled = scaleToMaxLongSide(bitmap, VIDEO_MAX_LONG_SIDE)
        if (scaled !== bitmap) bitmap.recycle()
        val rotation = imageProxy.imageInfo.rotationDegrees
        val upright = if (rotation == 0) {
            scaled
        } else {
            val matrix = Matrix().apply { postRotate(rotation.toFloat()) }
            Bitmap.createBitmap(scaled, 0, 0, scaled.width, scaled.height, matrix, true)
        }
        if (upright !== scaled) scaled.recycle()
        val width = upright.width
        val height = upright.height
        val out = ByteArrayOutputStream()
        upright.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
        upright.recycle()
        return JpegFrame(out.toByteArray(), width, height)
    }

    /** 长边超过 [maxLongSide] 时等比缩小（只缩不放），与 SDK JpegEncoder.scaleIfNeeded 同规则。 */
    private fun scaleToMaxLongSide(bitmap: Bitmap, maxLongSide: Int): Bitmap {
        val target = scaledMaxLongSide(bitmap.width, bitmap.height, maxLongSide) ?: return bitmap
        sdk.logDebug(
            "CameraController",
            "frame scaled ${bitmap.width}x${bitmap.height} -> ${target.width}x${target.height}",
        )
        return Bitmap.createScaledBitmap(bitmap, target.width, target.height, true)
    }

    fun unbind() {
        bindGeneration++
        orientationEventListener.disable()
        cameraProvider?.unbindAll()
        _isCameraOn.value = false
        preview = null
        imageAnalysis = null
        currentLifecycle = null
        _previewSurface.value = null
    }

    fun toggleCamera() {
        // Full rebind requires the caller to call bind() again with the toggled frontCamera.
        // In practice, the ConversationViewModel will handle this via the UI.
    }

    fun setZoom(ratio: Float) {
        // CameraX zoom via CameraControl — deferred to Phase 6 UI integration.
    }

    companion object {
        /** 摄像头目标分辨率（提示值：设备/镜头不支持时按 fallback rule 优先就近更小尺寸回退）。 */
        private val TARGET_RESOLUTION = Size(1280, 960)
        /** 上行帧长边硬上限（camera 路径自有；SDK `videoMaxLongSide`=1080 仅约束 pushBitmap 路径）。 */
        private const val VIDEO_MAX_LONG_SIDE = 1280
        private const val JPEG_QUALITY = 70
        /** 开流暖机窗（§8.3）：每轮流前 1000ms 丢帧，避开 AE 收敛期暗帧。 */
        private const val WARMUP_MS = 1000L
        /** analyzer 推送节流间隔（原字面量 900 提取）。 */
        private const val THROTTLE_MS = 900L

        /** 距流开始（analyzer 首个回调置位）不足 [warmupMs] 的帧应丢弃（§8.3；纯函数，可直测）。 */
        internal fun shouldSkipWarmup(elapsedMs: Long, warmupMs: Long = WARMUP_MS): Boolean =
            elapsedMs < warmupMs

        /**
         * 镜头切换判定（§8.2.2 P2-1）：实际推流 facing 发生变化才递增 turn id——与切换后
         * 首帧严格同帧；首帧（null）不比较、同镜头重绑（自动重绑）不递增（纯函数，可直测）。
         */
        internal fun shouldBumpTurn(lastPushedFacing: String?, facing: String): Boolean =
            lastPushedFacing != null && lastPushedFacing != facing

        /**
         * 长边超限时返回等比缩放后尺寸（只缩不放；下限 1 防 createScaledBitmap
         * 非法参数），null = 无需缩放。纯函数，可测单元。
         */
        internal fun scaledMaxLongSide(
            width: Int,
            height: Int,
            maxLongSide: Int = VIDEO_MAX_LONG_SIDE,
        ): CaptureSize? {
            val longSide = maxOf(width, height)
            if (longSide <= maxLongSide) return null
            val scale = maxLongSide.toFloat() / longSide
            return CaptureSize(
                (width * scale).toInt().coerceAtLeast(1),
                (height * scale).toInt().coerceAtLeast(1),
            )
        }
    }
}
