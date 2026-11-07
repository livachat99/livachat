package dev.livachat.app.media

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.DisplayMetrics
import android.view.Display
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.ProcessLifecycleOwner
import dagger.Lazy
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.R
import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.AiImage
import dev.livachat.sdk.model.Orientation
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicInteger
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.math.max
import kotlin.math.min
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow

/** 一帧待推送的预编码 JPEG 与其实际尺寸。 */
data class ScreenFrame(val jpeg: ByteArray, val width: Int, val height: Int)

/** 等比缩放后的捕获尺寸。 */
data class CaptureSize(val width: Int, val height: Int)

/**
 * 点按抓取的结论（v2，docs/android_screen_recording.md §4.1）。
 *
 * 定义归本文件（controller），浮窗仅消费并映射为提示文案；不是 StateFlow 的载荷
 * ——由 controller 以 [ScreenCaptureBubble.presentFeedback] 直接投递（隐藏动作必须
 * 在主线程同步完成，跨线程流投递不保证先后，§2.2 握手）。
 */
enum class CaptureFeedback { SENT, REJECTED_FOREGROUND, REJECTED_LOCKED, FAILED_NO_FRAME }

/** [ScreenCaptureController.captureDecision] 的结果（v2 内部类型，纯函数可直测）。 */
internal sealed interface CaptureDecision {
    data object Ignore : CaptureDecision

    /** 等待态专用（v4 §4.3）：已发出、等本轮 ai_text，点按静默丢弃（可测/可日志区分）。 */
    data object IgnoreWaiting : CaptureDecision

    data class Reject(val feedback: CaptureFeedback) : CaptureDecision

    data object StartCapture : CaptureDecision
}

/**
 * 屏幕采集控制器（docs/android_screen_recording.md §4.1）。
 *
 * 与 [CameraController] 同风格的媒体单例：持有 [MediaProjection] +
 * [VirtualDisplay] + [ImageReader]，放行期每 2s 一次 [acquireLatestImage]
 *（v2 D7），JPEG 编码后走 [LivaChatSDK.pushVideoJpeg]——与摄像头帧完全同一条
 * 上行链路。
 *
 * 线程模型：采集相关的全部资源（MediaProjection/VirtualDisplay/ImageReader/
 * ticker）只在内部 [HandlerThread] 上创建与释放；`stop()` 可从任意线程调用，
 * 同步复位状态字段后把资源回收投递到该线程串行执行。
 *
 * v2 帧门控（D12-D14）：每次 tick 先判 [shouldPushFrame]——仅
 * **进程后台 × 屏幕交互 × 未锁屏** 才上行（新帧或 keepalive）；app 前台
 *（不采集自身）、熄屏/doze、锁屏一律丢弃：排空 ImageReader 待取帧 + 清
 * keepalive 缓存，随后 return。进程前后台由 [ProcessLifecycleOwner] observer
 * 驱动 [appForeground]（首个 `start()` 注册一次，D13）。
 *
 * v3 旋转自适应（D15-D17）：常驻 [DisplayManager.DisplayListener]（首个
 * `start()` 注册一次，回调经 capture handler 投递，与 ticker/stopLocked 串行）
 * + 每 tick [maybeResizeForRotation] 兜底检测默认屏逻辑尺寸变化——横竖互换后
 * [resizeCaptureLocked] 走官方 `VirtualDisplay.resize` + ImageReader `setSurface`
 * 换面适配（§3.10/3.11：不重建 VD、不触授权 token）；resize 清 keepalive 缓存防
 * 旧尺寸帧复活。连续失败收敛（v3.1）：超 [MAX_RESIZE_FAILURES] 升级 stop。
 *
 * v2 点按单次抓屏（docs/android_screen_recording.md §4.1）：2s 定时上行停用
 * （[AUTO_TICK_ENABLED] 门控，ticker/keepalive 代码与单测保留）；改为浮窗点按
 * [captureNow] → 隐藏浮窗 → 排空 → 等 settle → 取一帧 → 上行一帧。浮窗
 * （[ScreenCaptureBubble]）由本类经 `dagger.Lazy` 持有并按录制生命周期挂载/移除；
 * 抓取序列的共享状态（在飞标记/防连点时间戳）只在主线程读写。
 */
@Singleton
class ScreenCaptureController @Inject constructor(
    private val sdk: LivaChatSDK,
    @ApplicationContext private val context: Context,
    private val bubble: Lazy<ScreenCaptureBubble>,
    private val compositeListener: CompositeLivaChatListener,
    private val turnCounter: CaptureTurnCounter,
) : ScreenCaptureBubbleHost {
    /**
     * 全模态（omni）模式查询（只读）：omni 下抓屏不随帧发送 payload.text
     * （服务端 D6b 会忽略，客户端不发以省带宽 + 语义干净）；
     * 由持有本控制器与监听器的上层（ConversationViewModel）注入。
     * 见 docs/fd_pipeline_omni_mode.md §4 D6b 客户端配套。
     */
    internal var isOmniModeProvider: () -> Boolean = { false }

    private val _isRecording = MutableStateFlow(false)
    val isRecording: StateFlow<Boolean> = _isRecording.asStateFlow()

    // v2 门控输入一（D13）：进程前台感知，默认 true——录制只可能在 app 前台启动，
    // 默认"前台"保证注册 observer 到首次离开 app 之间无把前台画面当后台放行的空窗；
    // 首个 start() 在主线程注册 ProcessLifecycleOwner observer 后开始翻转
    private val _appForeground = MutableStateFlow(true)
    val appForeground: StateFlow<Boolean> = _appForeground.asStateFlow()

    private var foregroundObserverAttached = false

    // v3 D15：DisplayListener 注册仅首次（进程生命周期内常驻，停录不注销——每次
    // 开录都要检测旋转；非录制态回调早退零开销）
    private var displayListenerAttached = false

    /**
     * v3 D15：常驻默认屏监听。registerDisplayListener 回调投递 capture handler
     *（与 ticker / stopLocked / onDisplayChanged 天然串行）；非默认屏事件忽略
     *（AUTO_MIRROR VD 镜像的是默认屏，§1.3）。
     */
    private val displayListener = object : DisplayManager.DisplayListener {
        override fun onDisplayAdded(displayId: Int) = Unit
        override fun onDisplayRemoved(displayId: Int) = Unit
        override fun onDisplayChanged(displayId: Int) {
            if (displayId != Display.DEFAULT_DISPLAY) return
            maybeResizeForRotation()
            // v3.1：旋转动画期个别 OEM 系统 metrics 瞬态，延迟复测免等整 tick；
            // 幂等（尺寸相等即零动作），重复回调叠加的复测无害
            handler.postDelayed({ maybeResizeForRotation() }, RESIZE_RECHECK_DELAY_MS)
        }
    }

    // ── v2 点按抓取（docs/android_screen_recording.md §4.1）─────────
    // 以下共享状态只在主线程读写：captureNow() 置位，抓取序列的结论经
    // mainHandler.post 回主线程收尾；capture handler 线程只做排空/取帧/编码/
    // 推帧等纯执行（无需 @Volatile/锁）
    private var capturing = false
    private var lastCaptureMs = 0L
    private val mainHandler = Handler(Looper.getMainLooper())

    // ── v4 等待态（docs/android_screen_recording.md §4.1）──────────
    // 抓取已发出、等本轮 ai_text：浮窗绿环旋转 + 点按被吞（IgnoreWaiting）。
    // 以下状态只在主线程读写（startWaiting/finishCapture 主线程；
    // replyListener 回调在 WS reader 线程 → 一律 post 回主线程）
    private var waitingForReply = false
    private var waitStartMs = 0L

    /** 测试缝：等待态观测（只读）。 */
    internal val isWaitingForReply: Boolean get() = waitingForReply

    /**
     * 本轮 ai_text 订阅者（D12）；**只覆写 onAiText**（其余走接口默认空实现）。
     *
     * 硬约束：不得抛、不得阻塞——Composite 扇出（CompositeLivaChatListener.kt:51）在
     * WS reader 线程同步 forEach，异常会中断后续订阅者并沿 OkHttp onMessage 冒泡
     * （ConnectionManager.kt:171 → LivaChatSDK.kt:195 → dispatchMessage，全链路无
     * try/catch）。本实现 = 纯比较 + post，天然满足。
     */
    internal val replyListener: LivaChatListener = object : LivaChatListener {
        override fun onAiText(
            text: String,
            messageId: String,
            index: Long,
            isFinal: Boolean,
            image: AiImage?,
            source: String?,
        ) {
            if (!endsWait(source, index)) return
            mainHandler.post { endWaiting("ai_text") }
        }
    }

    private val waitTimeout = Runnable { endWaiting("timeout") }

    // 启动失败消息（SDK 侧启动异常，非用户取消），VM 映射为 ConversationError
    private val _startErrors = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val startErrors: SharedFlow<String> = _startErrors.asSharedFlow()

    private data class ConsentToken(val generation: Int, val resultCode: Int, val data: Intent)

    @Volatile
    private var pendingToken: ConsentToken? = null

    // 每次 start 分配单调代次，经 serviceIntent extra 传回 Service，
    // 用于 onForegroundServiceReady 校验回调归属（防 stop→start 竞态下旧回调误消费新 token）
    private val generationSeq = AtomicInteger(0)

    private val captureThread = HandlerThread("screen-capture").apply { start() }
    private val handler = Handler(captureThread.looper)

    // 以下资源仅由 handler 线程读写
    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var lastFrame: ScreenFrame? = null
    // v3 D15：VD 当前捕获尺寸基线——启动时置、每次 resize 后更新、stopLocked 复位；
    // maybeResizeForRotation 以此与重算尺寸比较判定是否需适配
    private var currentCaptureSize: CaptureSize? = null
    // v3.1：resize/setSurface 连续失败计数（成功清零；达 MAX_RESIZE_FAILURES 升级 stop）
    private var resizeFailCount = 0
    // v3.1 P1-1：resize 失败时刻（elapsedRealtime；stopLocked 复位）。重试除每 2s tick
    // 外还由 onDisplayChanged + 300ms 复测驱动（旋转动画期 1s 内可达 4-6 次）——瞬时失败
    //（OEM 表面态未稳）可能秒级烧完失败计数误停健康录制；失败后 RESIZE_RETRY_MIN_INTERVAL_MS
    // 内跳过重试，把失败节奏拉长到 ≥1s 间隔（文档 §4.1「跨 tick 累计」口径）。成功不设限
    // （成功即状态一致，无计数问题；真实连续旋转仍即时适配）。
    private var lastResizeFailureMs = 0L

    // v2 门控输入二（D14）：屏幕交互态与锁屏态的系统服务引用（只读，构造时取一次）
    private val powerManager = context.getSystemService(PowerManager::class.java)
    private val keyguardManager = context.getSystemService(KeyguardManager::class.java)

    private val ticker = object : Runnable {
        override fun run() {
            try {
                captureOnce()
            } catch (e: Exception) {
                // 单次 tick 的未捕获异常（编码/推送偶发）不得杀死 2s 心跳链：
                // 链断后 isRecording 仍 true，keepalive 与门控静默失效且无自愈。
                sdk.logDebug(TAG, "tick failed: ${e.message}")
            } finally {
                handler.postDelayed(this, TICK_MS)
            }
        }
    }

    /** 由 consent ActivityResult 触发（VM 主线程）。 */
    fun start(resultCode: Int, data: Intent) {
        if (_isRecording.value || pendingToken != null) return
        // 新采集轮（docs/fd_pipeline_video_process.md §8）：录屏开始视为新 turn——服务端对
        // 变化后的首帧立即起轮（屏幕帧带 text 仍走 v3 文本回合，不受影响）
        turnCounter.next()
        // v2 D13：注册 ProcessLifecycleOwner observer 翻转 appForeground（仅首次；主线程）。
        // ProcessLifecycleOwner 与 LivaChatApplication 的日志 observer 同源，状态无分歧。
        attachForegroundObserverOnce()
        // v3 D15：注册常驻 DisplayListener（仅首次；回调投递 capture handler 与内部串行）
        attachDisplayListenerOnce()
        val generation = generationSeq.incrementAndGet()
        pendingToken = ConsentToken(generation, resultCode, data)
        try {
            context.startForegroundService(serviceIntent(generation))
        } catch (e: Exception) {
            // 极端场景：后台 FGS 启动限制等。清 token 防残留卡死，错误走 startErrors → ErrorBanner
            sdk.logDebug(TAG, "startForegroundService rejected: ${e.message}")
            pendingToken = null
            _startErrors.tryEmit(context.getString(R.string.capture_start_failed, e.message ?: e.javaClass.simpleName))
        }
    }

    /**
     * v3 D15：注册常驻默认屏 DisplayListener（仅首个 start() 一次；回调投递
     * capture handler）。录制可能全程后台（FGS 语义），MainActivity 锁竖屏且无
     * configChanges——activity 生命周期不可作旋转感知源（§2.8）。
     */
    private fun attachDisplayListenerOnce() {
        if (displayListenerAttached) return
        displayListenerAttached = true
        try {
            context.getSystemService(DisplayManager::class.java)
                .registerDisplayListener(displayListener, handler)
        } catch (e: Exception) {
            // 注册失败（binder 异常等）不得崩 start() 主线程：仅丢即时通道，
            // tick 兜底（每 2s 重算尺寸）仍保证旋转适配，最坏延迟一个 tick
            sdk.logDebug(TAG, "registerDisplayListener failed: ${e.message}")
        }
    }

    /**
     * 进程级前后台感知接线（v2 D13）：仅在首个 start() 注册一次（单例生命周期内
     * 常驻，停录不注销——下次开录仍需要门控）。库语义：回前台 ON_START 即时，
     * 退后台 ON_STOP 带 ~700ms debounce。
     */
    private fun attachForegroundObserverOnce() {
        if (foregroundObserverAttached) return
        foregroundObserverAttached = true
        ProcessLifecycleOwner.get().lifecycle.addObserver(
            LifecycleEventObserver { _, event ->
                when (event) {
                    Lifecycle.Event.ON_START -> _appForeground.value = true
                    Lifecycle.Event.ON_STOP -> _appForeground.value = false
                    else -> Unit
                }
            },
        )
    }

    /**
     * 幂等停止：所有停止路径（按钮、相机互斥、系统投屏 onStop、会话终结、
     * VM 销毁）都汇于此。
     *
     * 注意 stop→start 快速交替竞态：本方法发出的 stopService 与紧接着
     * start() 的 startForegroundService 是两次独立 AMS 调用，到达顺序无保证。
     * 迟到的 stopService 可能使 service 在录制进行中/等 ready 时被销毁——
     * 该情况由 [onForegroundServiceDestroyed] 自愈处理，不在本方法内兜底。
     */
    fun stop() {
        pendingToken = null
        _isRecording.value = false
        handler.post { stopLocked() }
        context.stopService(Intent(context, ScreenCaptureService::class.java))
    }

    /**
     * Service `startForeground()` 完成后回调（Service onStartCommand，主线程）。
     *
     * [generation] 为 Service 从启动 Intent 读回的代次；只有与当前
     * pendingToken 同代才消费——stop→start 竞态下迟到的旧 service 回调
     * 不会误启动/误消费新一轮授权。
     */
    fun onForegroundServiceReady(generation: Int) {
        handler.post {
            val token = pendingToken
            if (token == null) {
                if (!_isRecording.value) {
                    // token 已被 stop() 清除（ready 前用户已停）。
                    // stop() 的 stopService 与 service 启动存在 AMS 乱序：stop 请求可能
                    // 先被消费（service 未启动时 stopService 为空操作），service 随后
                    // 前台化后无人收——此处兜底 stopService 防 FGS/通知残留。
                    context.stopService(Intent(context, ScreenCaptureService::class.java))
                }
                // 录制中（isRecording=true）的 ready 来自 onForegroundServiceDestroyed
                // 自愈重启：必须保留 service，若在此兜底 stopService 会被其 onDestroy
                // 再次自愈，形成无限 FGS 启停循环。
                return@post
            }
            if (token.generation != generation) {
                // 回调属于旧代次 service 实例（本轮 start 的 ready 会随新实例到达）：
                // 忽略，不动当前 pendingToken，也不停 service（可能正服务新一轮录制）
                return@post
            }
            pendingToken = null
            startCaptureLocked(token)
        }
    }

    /**
     * Service `onDestroy` 回调（主线程）。
     *
     * 正常停止路径（stop() 已同步清空 pendingToken/isRecording）不触发任何动作。
     * 竞态场景下 service 可能被"上一代次的迟到 stopService"或系统回收，而录制意图
     * 仍在（录制中 / 授权等待 ready）——此时不是停录，而是按期望代次自愈重启
     * FGS（MediaProjection 采集不依赖本次 service 实例，重启仅恢复前台服务语义）。
     */
    fun onForegroundServiceDestroyed() {
        if (!_isRecording.value && pendingToken == null) return
        val generation = pendingToken?.generation ?: generationSeq.get()
        sdk.logDebug(TAG, "service destroyed while capture pending — restarting FGS (gen=$generation)")
        try {
            context.startForegroundService(serviceIntent(generation))
        } catch (e: Exception) {
            // 后台 FGS 启动限制等：采集仍由已建 VirtualDisplay 支撑，降级为无通知前台语义
            sdk.logDebug(TAG, "FGS restart rejected: ${e.message}")
        }
    }

    private fun serviceIntent(generation: Int): Intent =
        Intent(context, ScreenCaptureService::class.java)
            .putExtra(ScreenCaptureService.EXTRA_GENERATION, generation)

    // ── handler 线程内部 ────────────────────────────────────────────

    private fun startCaptureLocked(token: ConsentToken) {
        var newProjection: MediaProjection? = null
        var newReader: ImageReader? = null
        try {
            // v3.1：与 DisplayListener 同源（DEFAULT_DISPLAY）；默认屏缺失属系统极端态，
            // 抛错走下方 catch 启动失败上报
            val metrics = realDisplayMetrics()
                ?: throw IllegalStateException("no default display")
            val captureSize = scaledCaptureSize(metrics.widthPixels, metrics.heightPixels)
            val projectionManager =
                context.getSystemService(MediaProjectionManager::class.java)
            val mediaProjection = projectionManager.getMediaProjection(token.resultCode, token.data)
                ?: throw IllegalStateException("media projection unavailable")
            newProjection = mediaProjection
            // API 34：registerCallback 必须先于 createVirtualDisplay，否则抛 IllegalStateException
            mediaProjection.registerCallback(object : MediaProjection.Callback() {
                override fun onStop() {
                    sdk.logDebug(TAG, "projection stopped by system")
                    stopLocked()
                }
            }, handler)

            newReader = ImageReader.newInstance(
                captureSize.width, captureSize.height, PixelFormat.RGBA_8888, MAX_IMAGE_READER_BUFFERS,
            )
            val newDisplay = mediaProjection.createVirtualDisplay(
                VIRTUAL_DISPLAY_NAME,
                captureSize.width,
                captureSize.height,
                metrics.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                newReader.surface,
                null,
                handler,
            )
            projection = newProjection
            virtualDisplay = newDisplay
            imageReader = newReader
            // v3 D15：resize 判定基线（启动时置；stopLocked 复位）
            currentCaptureSize = captureSize
            _isRecording.value = true
            sdk.logDebug(TAG, "capture started ${captureSize.width}x${captureSize.height}")
            if (AUTO_TICK_ENABLED) handler.post(ticker)
            showBubbleOrStop()
        } catch (e: Exception) {
            sdk.logDebug(TAG, "start failed: ${e.message}")
            newProjection?.stop()
            newReader?.close()
            _startErrors.tryEmit(context.getString(R.string.capture_start_failed, e.message ?: e.javaClass.simpleName))
            stop()
        }
    }

    private fun captureOnce() {
        if (!_isRecording.value) return
        // v3 D15：每 tick 兜底 rotation 自检（DisplayListener 为即时通道，此处防漏回调）；
        // 先于门控执行——resize 与门控解耦，丢弃期（前台/熄屏/锁屏）同样保持 VD 尺寸
        // 正确，放行后首帧即新尺寸。尺寸未变（含 180° 旋转）零动作（needsResize）。
        maybeResizeForRotation()
        // resize 连续失败达限时 resizeCaptureLocked 已升级 stop()（isRecording=false、
        // stopLocked 排队中）——本 tick 不得再 acquire/推帧，由 stopLocked 收场
        if (!_isRecording.value) return
        val reader = imageReader
        // v2 门控（D12-D14）：app 前台 / 熄屏 / 锁屏 → 丢弃——不上传也不 keepalive。
        // 丢弃 = 排空 ImageReader 待取帧（防解锁/回后台瞬间把锁屏/自身旧画面当
        // "最新帧"送出）+ 清 keepalive 缓存（旧帧不得复活）；录制会话与 FGS 保持，
        // 门控放行后下一 tick 即恢复上行。交互态短路语义（D14）：熄屏/doze 下
        // keyguard 返回值不可靠，但 isInteractive=false 已使整体判定关闭，锁屏
        // 输入只在交互态下影响结果——三输入急切求值不影响判定正确性。
        if (!shouldPushFrame(
                _appForeground.value,
                powerManager.isInteractive(),
                keyguardManager.isKeyguardLocked(),
            )
        ) {
            reader?.acquireLatestImage()?.close()
            lastFrame = null
            return
        }
        val encoded = reader?.acquireLatestImage()?.let { image ->
            try {
                if (image.format != PixelFormat.RGBA_8888) {
                    sdk.logDebug(TAG, "frame skipped | format=${image.format}")
                    null
                } else {
                    imageToFrame(image)
                }
            } catch (e: Exception) {
                sdk.logDebug(TAG, "encode failed: ${e.message}")
                null
            } finally {
                image.close()
            }
        }
        // v3.1 二次门控复查：「检查→编码」窗口内锁屏/熄屏的，丢缓存不推——
        // 泄漏窗口从编码耗时压至近似零（§8「tick 内锁屏竞态」）。与门控丢弃同样
        // 排空待取帧：encode 窗口内新产画面（锁屏动画帧）不得在解锁后当"最新帧"送出
        if (!shouldPushFrame(
                _appForeground.value,
                powerManager.isInteractive(),
                keyguardManager.isKeyguardLocked(),
            )
        ) {
            reader?.acquireLatestImage()?.close()
            lastFrame = null
            return
        }
        val newCache = decideFrameToPush(encoded, lastFrame) { frame ->
            sdk.logDebug(TAG, "push ${frame.width}x${frame.height} new=${encoded != null}")
            // 帧本身正立（VirtualDisplay 输出），协议 orientation 恒传 0（D5）；
            // camera_facing 传 "screen"（fd_pipeline_video_process.md §8 三值枚举，替代原
            // "front" 占位 D4）；turnId 逐帧携带（App 层 CaptureTurnCounter）
            sdk.pushVideoJpeg(frame.jpeg, frame.width, frame.height, Orientation.PORTRAIT_0,
                "screen", turnCounter.current)
        }
        if (newCache != null) lastFrame = newCache
    }

    // ── v2 点按抓取（主线程入口 + capture handler 序列，§2.2）───────────

    /** 浮窗点按回调（主线程；ScreenCaptureBubbleHost）。 */
    override fun onBubbleTap() = captureNow()

    /**
     * 点按浮窗入口（主线程）。
     *
     * 决策（未录制/等待态/在飞/防连点/门控）走 [captureDecision] 纯函数——门控复用既有
     * [shouldPushFrame]（App 前台 / 熄屏 / 锁屏一律不抓）；拒绝只弹提示，不隐藏
     * 浮窗、不抓帧；等待态静默丢弃（v4 D7：只吞点按，拖动不受影响）。
     */
    fun captureNow() {
        val now = SystemClock.elapsedRealtime()
        when (val decision = captureDecision(
            isRecording = _isRecording.value,
            capturing = capturing,
            nowMs = now,
            lastCaptureMs = lastCaptureMs,
            appForeground = _appForeground.value,
            isInteractive = powerManager.isInteractive(),
            isKeyguardLocked = keyguardManager.isKeyguardLocked(),
            waiting = waitingForReply,
        )) {
            CaptureDecision.Ignore -> return
            CaptureDecision.IgnoreWaiting -> {
                sdk.logDebug(TAG, "tap ignored: waiting for ai_text")
                return
            }
            is CaptureDecision.Reject -> {
                sdk.logDebug(TAG, "tap capture rejected: ${decision.feedback}")
                // 本方法跑在浮窗触摸回调（主线程）：异常绝不可逃逸到输入分发，否则崩
                try {
                    bubble.get().presentFeedback(decision.feedback)
                } catch (e: Exception) {
                    sdk.logDebug(TAG, "feedback failed: ${e.message}")
                }
            }
            CaptureDecision.StartCapture -> {
                sdk.logDebug(TAG, "tap capture start")
                lastCaptureMs = now
                capturing = true
                val generation = generationSeq.get()
                handler.post { captureOnceOnDemand(generation) }
            }
        }
    }

    /**
     * capture handler：抓取序列第一步。
     *
     * ⚠ 硬约束（§2.2「异常兜底」）：本 runnable 与后续 handler runnable 必须整体 try/catch
     * ——裸异常会终止 capture HandlerThread 的 Looper（ticker/stopLocked/ready 全部
     * 静默失效）；且"已隐藏、未收尾"窗口内抛出会让浮窗永久 INVISIBLE、capturing 永
     * true（后续点按全被吞）。catch 一律收敛到 finishCapture(FAILED_NO_FRAME)。
     */
    private fun captureOnceOnDemand(generation: Int) {
        try {
            // v3 链路保留：ticker 已停 → 点按路径兜底自检；resize 触发的产帧会在
            // 下一步排空时丢弃（无副作用）
            maybeResizeForRotation()
            // 对齐 v1 captureOnce 的复查：resize 三连败已升级 stop（isRecording=false、
            // stopLocked 排队中）→ 不得继续隐藏/排空，由 stopLocked 收场
            if (!_isRecording.value) {
                postResetCapturing()
                return
            }
            mainHandler.post { hideAndScheduleGrab(generation) }
        } catch (e: Exception) {
            sdk.logDebug(TAG, "capture sequence failed: ${e.message}")
            postFinish(CaptureFeedback.FAILED_NO_FRAME)
        }
    }

    /**
     * 主线程：瞬时隐藏浮窗（无动画：半透明残留会进帧）。
     *
     * 隐藏先于排空是硬约束（§2.2「顺序硬约束」）：反序时动画画面会用隐藏前的旧帧重新填满
     * 2 个缓冲，隐藏后的新帧因无空位被丢弃，取到的是含浮窗的旧帧。
     */
    private fun hideAndScheduleGrab(generation: Int) {
        try {
            // 代次校验（隐藏前）：stop→start 后旧序列不得在**新会话浮窗**上执行隐藏
            //（finishSilently 不恢复可见性 → 新浮窗隐形卡死）
            if (generation != generationSeq.get()) {
                capturing = false
                return
            }
            bubble.get().hideForCapture()
        } catch (e: Exception) {
            sdk.logDebug(TAG, "hide for capture failed: ${e.message}")
            finishCapture(CaptureFeedback.FAILED_NO_FRAME)
            return
        }
        handler.post { drainAndScheduleGrab(generation) }
    }

    /** capture handler：排空"可能含浮窗"的旧帧并腾空缓冲，等 settle 后取帧。 */
    private fun drainAndScheduleGrab(generation: Int) {
        try {
            drainReader()
        } catch (e: Exception) {
            sdk.logDebug(TAG, "drain failed: ${e.message}")
            postFinish(CaptureFeedback.FAILED_NO_FRAME)
            return
        }
        handler.postDelayed({
            try {
                grabAndPush(generation)
            } catch (e: Exception) {
                sdk.logDebug(TAG, "grab failed: ${e.message}")
                postFinish(CaptureFeedback.FAILED_NO_FRAME)
            }
        }, HIDE_SETTLE_MS)
    }

    /** capture handler：门控/代次复查 → 取一帧 → 上行（结论回主线程收尾）。 */
    private fun grabAndPush(generation: Int) {
        if (!_isRecording.value) {
            postResetCapturing()
            return
        }
        // 二次门控复查（对齐 v3.1 加固）：hide→push 窗口内锁屏/熄屏/切回前台的不推
        if (!shouldPushFrame(
                _appForeground.value,
                powerManager.isInteractive(),
                keyguardManager.isKeyguardLocked(),
            )
        ) {
            postFinish(
                if (_appForeground.value) CaptureFeedback.REJECTED_FOREGROUND
                else CaptureFeedback.REJECTED_LOCKED,
            )
            return
        }
        if (generation != generationSeq.get()) {
            postResetCapturing()
            return
        }
        val frame = acquireFrameWithRetry()
        if (frame == null) {
            postFinish(CaptureFeedback.FAILED_NO_FRAME)
            return
        }
        sdk.pushVideoJpeg(
            frame.jpeg, frame.width, frame.height, Orientation.PORTRAIT_0, "screen",
            turnCounter.current,
            // 全模态模式：不带指令文本（服务端会忽略 payload.text，见 D6b）
            text = if (isOmniModeProvider()) null else context.getString(R.string.conv_image_action_text),
        )
        sdk.logDebug(TAG, "tap capture pushed ${frame.width}x${frame.height} bytes=${frame.jpeg.size}")
        postFinish(CaptureFeedback.SENT)
    }

    /** 排空待取帧（沿用 v1 惯用法：单次 acquireLatestImage 取最新并丢弃其余）。 */
    private fun drainReader() {
        imageReader?.acquireLatestImage()?.close()
    }

    /**
     * 等"无浮窗"帧并取一帧：重试 [CAPTURE_RETRY_COUNT] 次 × [CAPTURE_RETRY_DELAY_MS]，
     * 段间复查停止意图（`stop()` 在调用线程同步置位 isRecording，立即生效）。
     * 全程 `Thread.sleep` 串行（不用 postDelayed 自链）——取帧逻辑单块可读，代价是
     * stopLocked 排队延迟 ≤ 一个间隔（可接受，§2.2「重试串行」）。
     */
    private fun acquireFrameWithRetry(): ScreenFrame? {
        var attempt = 0
        while (attempt < CAPTURE_RETRY_COUNT) {
            attempt++
            val image = imageReader?.acquireLatestImage()
            if (image != null) {
                try {
                    if (image.format != PixelFormat.RGBA_8888) {
                        sdk.logDebug(TAG, "frame skipped | format=${image.format}")
                        return null
                    }
                    return imageToFrame(image)
                } catch (e: Exception) {
                    sdk.logDebug(TAG, "encode failed: ${e.message}")
                    return null
                } finally {
                    image.close()
                }
            }
            if (!_isRecording.value) return null
            // 末次尝试后不再空等（此前 repeat 写法会在最后一次失败后再睡一个间隔）
            if (attempt < CAPTURE_RETRY_COUNT) SystemClock.sleep(CAPTURE_RETRY_DELAY_MS)
        }
        return null
    }

    private fun postFinish(feedback: CaptureFeedback) {
        if (Looper.myLooper() == Looper.getMainLooper()) finishCapture(feedback)
        else mainHandler.post { finishCapture(feedback) }
    }

    private fun postResetCapturing() {
        if (Looper.myLooper() == Looper.getMainLooper()) capturing = false
        else mainHandler.post { capturing = false }
    }

    /** 主线程：抓取序列唯一收尾点（复位在飞标记 + 浮窗恢复可见并渲染反馈）。 */
    private fun finishCapture(feedback: CaptureFeedback) {
        capturing = false
        try {
            bubble.get().presentFeedback(feedback)
        } catch (e: Exception) {
            sdk.logDebug(TAG, "feedback failed: ${e.message}")
        }
        // v4 D1：仅"推帧成功"进等待态（拒绝/失败各自反馈后回常态）；且**仅当会话仍活着**
        // ——抓取序列在"推帧成功 → 收尾投递"之间存在 ~0.2-0.4s 窗口（acquireFrameWithRetry
        // 至多 3×100ms），期间用户停录会让本次 SENT 晚于停录收尾到达；等待态不得落在
        // 已结束的会话上（否则 15s 内吞掉下一轮会话的首个点按，或在浮窗上凭空显示旋转环）
        if (feedback == CaptureFeedback.SENT && _isRecording.value) startWaiting()
    }

    /** 主线程：进入等待态（浮窗绿环旋转 + 15s 兜底超时）。 */
    internal fun startWaiting() {
        waitingForReply = true
        waitStartMs = SystemClock.elapsedRealtime()
        runCatching { bubble.get().setWaiting(true) }
        mainHandler.postDelayed(waitTimeout, WAIT_TIMEOUT_MS)
    }

    /**
     * 主线程：退出等待态（幂等；首个本轮 ai_text / 15s 超时 / 停录三条路径共用）。
     *
     * 日志带等待时长：上线后据此拉首分片时延分布与超时率（docs §4.3）。
     */
    internal fun endWaiting(reason: String) {
        if (!waitingForReply) return
        waitingForReply = false
        mainHandler.removeCallbacks(waitTimeout)
        sdk.logDebug(TAG, "wait ended: $reason after ${SystemClock.elapsedRealtime() - waitStartMs}ms")
        runCatching { bubble.get().setWaiting(false) }
    }

    /** 浮窗挂载（主线程）；失败即启动失败——浮窗是 v2 唯一抓取入口，缺之毫无产出。 */
    private fun showBubbleOrStop() {
        mainHandler.post {
            // 新一轮录制不复用上一轮残留的等待态：系统侧停投屏（onStop）路径下
            // `_isRecording` 在收尾投递之后才归零，晚到的 postFinish(SENT) 仍可能把标志
            // 置上——不从边界清掉的话，本轮首个点按会被 IgnoreWaiting 吞、浮窗还会凭空
            // 显示旋转环（show() 以 bubble 层 flag 初始化新 view，§4.2 注意⑥）。
            // 幂等：无残留时零动作（endWaiting 自带 guard），且必须在 show() 之前。
            endWaiting("restart")
            val shown = try {
                bubble.get().show(this)
            } catch (e: Exception) {
                sdk.logDebug(TAG, "bubble show failed: ${e.message}")
                false
            }
            if (!shown) {
                _startErrors.tryEmit(context.getString(R.string.capture_overlay_failed))
                stop()
            } else {
                // v4 D12：等待态订阅随录制会话生死（浮窗即抓取入口，未挂载不可能有等待态）
                compositeListener.register(replyListener)
            }
        }
    }

    /**
     * v3 D15：检测默认屏 rotation 变化并适配捕获尺寸——DisplayListener 即时与
     * tick 兜底共用同一实现；仅 capture handler 线程调用（与 ticker/stopLocked/
     * onDisplayChanged 串行无并发）。180° 等尺寸未变的旋转由 [needsResize] 判为
     * 零动作（一次重算比较，无状态副作用）。
     */
    private fun maybeResizeForRotation() {
        if (!_isRecording.value) return
        try {
            val metrics = realDisplayMetrics() ?: return
            val size = scaledCaptureSize(metrics.widthPixels, metrics.heightPixels)
            if (!needsResize(currentCaptureSize, size.width, size.height)) return
            resizeCaptureLocked(size, metrics.densityDpi)
        } catch (e: Exception) {
            // DisplayListener 即时路径的裸异常（getRealMetrics binder 失败等）会终止
            // capture HandlerThread 的 Looper → ticker/stopLocked/ready 全部静默失效，
            // 录制卡死且无法停止——此处必须收敛（与 ticker run 的 catch 等效）；
            // resizeCaptureLocked 内部失败不在此路径（自兜底计数收敛）
            sdk.logDebug(TAG, "rotation check failed: ${e.message}")
        }
    }

    /**
     * v3 D16：`VirtualDisplay.resize` + 新尺寸 ImageReader `setSurface` 换面适配
     * （§3.10/3.11 官方通道：不 release VD、不触授权 token）。换面序列在
     * setSurface(新) **之后**才 close 旧 reader——旋转动画残帧随 close 直接丢弃
     * 不上行（§8「旧 ImageReader 残帧」）。resize 清 keepalive 缓存（[lastFrame]）：
     * 旧尺寸帧不得在旋转后当 keepalive 复活。
     *
     * v3.1 收敛：resize 连续失败达 [MAX_RESIZE_FAILURES] 升级 stop + 错误上报——
     * 确定性失败（VD 异常态而系统 onStop 未至）不得静默无限重试；重建 VD 有单次
     * 授权 SecurityException 风险（§3.2），stop 是唯一安全升级路径。
     */
    private fun resizeCaptureLocked(size: CaptureSize, dpi: Int) {
        // stop 竞态下 VD 已释放（virtualDisplay=null）：直接 return，由 onStop/stop 路径收场
        val display = virtualDisplay ?: return
        // P1-1 节流：上次失败起 RESIZE_RETRY_MIN_INTERVAL_MS 内的重复尝试直接跳过——
        // 正常路径首次即成功不受影响；瞬时失败（动画期 OEM 表面态未稳）被自然吸收，
        // 3 次计数被拉长到 ≥3s，确定性失败仍由系统 onStop 快速收场 / 计数数秒内收敛 stop
        if (SystemClock.elapsedRealtime() - lastResizeFailureMs < RESIZE_RETRY_MIN_INTERVAL_MS) return
        lastFrame = null // D16：旧尺寸帧不得复活
        var newReader: ImageReader? = null
        try {
            newReader = ImageReader.newInstance(
                size.width, size.height, PixelFormat.RGBA_8888, MAX_IMAGE_READER_BUFFERS,
            )
            display.resize(size.width, size.height, dpi)
            display.setSurface(newReader.surface)
            imageReader?.close()
            imageReader = newReader
            currentCaptureSize = size
            resizeFailCount = 0
            sdk.logDebug(TAG, "capture resized to ${size.width}x${size.height}")
        } catch (e: Exception) {
            // 平台异常（VD 已失效/新面创建失败等）：回收新 reader，不动既有状态
            newReader?.close()
            sdk.logDebug(TAG, "capture resize failed: ${e.message}")
            lastResizeFailureMs = SystemClock.elapsedRealtime() // P1-1：失败计时起点
            val next = nextResizeFailureCount(resizeFailCount)
            if (next == null) {
                _startErrors.tryEmit(context.getString(R.string.capture_stopped))
                stop()
                return
            }
            resizeFailCount = next
            // 未超限：VD 已失效由系统 onStop → stopLocked 收场；VD 健在则下一 tick
            // 尺寸仍不匹配 → 自然重试（自愈）
        }
    }

    private fun imageToFrame(image: Image): ScreenFrame {
        val plane = image.planes[0]
        val buffer = plane.buffer
        val pixels = ByteArray(buffer.remaining())
        buffer.get(pixels)
        val bitmap = rgbaToBitmap(pixels, image.width, image.height, plane.rowStride, plane.pixelStride)
        try {
            val out = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
            return ScreenFrame(out.toByteArray(), bitmap.width, bitmap.height)
        } finally {
            bitmap.recycle()
        }
    }

    /** 资源回收（幂等；仅在 handler 线程调用）。 */
    private fun stopLocked() {
        // v4：浮窗收尾（结束等待 + 注销订阅 + 移除浮窗）必须在**早退之前**投递——
        // 早退分支（资源已空）不得留下悬挂的等待态与订阅。三者幂等（endWaiting 有
        // guard / Set.remove / hide() 查 attached），重复投递零害；窗口操作必在主线程。
        mainHandler.post {
            endWaiting("stop")
            runCatching { compositeListener.unregister(replyListener) }
            try {
                bubble.get().hide()
            } catch (e: Exception) {
                sdk.logDebug(TAG, "bubble hide failed: ${e.message}")
            }
        }
        if (projection == null && virtualDisplay == null && imageReader == null &&
            lastFrame == null && !_isRecording.value
        ) {
            return
        }
        handler.removeCallbacks(ticker)
        virtualDisplay?.release()
        virtualDisplay = null
        imageReader?.close()
        imageReader = null
        // MediaProjection.stop() 会触发注册的 onStop 回调 → stopLocked() 重入；
        // 先置空 projection 保证重入路径直接 return，防双重释放。
        val active = projection
        projection = null
        try {
            // 系统侧 onStop 路径下 projection 已被平台停止，对其再次 stop() 的
            // 幂等性依赖平台实现（部分版本抛 IllegalStateException），包裹防御
            active?.stop()
        } catch (e: Exception) {
            sdk.logDebug(TAG, "projection stop failed: ${e.message}")
        }
        lastFrame = null
        // v3 D15：复位 resize 判定基线；下次 start 重新以启动时尺寸置位
        currentCaptureSize = null
        resizeFailCount = 0
        lastResizeFailureMs = 0L // P1-1：旧会话失败时刻不得压制新会话的首次适配尝试
        _isRecording.value = false
        // 系统侧停止（Callback.onStop）等路径同样要收掉前台服务与通知；
        // stopService 幂等，已销毁时无副作用。
        context.stopService(Intent(context, ScreenCaptureService::class.java))
        // v2：浮窗随录制会话移除 —— 见方法头部的统一收尾投递（v4 起上移至早退之前）
    }

    /**
     * 默认屏当前逻辑尺寸（v3.1：与 DisplayListener 同源——DEFAULT_DISPLAY，弃用
     * WindowManager.defaultDisplay）。getRealMetrics 随 rotation 返回当前方向尺寸
     *（竖 1080×2400 ↔ 横 2400×1080），本函数天然产出旋转后的新 CaptureSize。
     * 返回 null 表示默认屏暂不可用（系统极端态），调用方各自早退/上报。
     */
    private fun realDisplayMetrics(): DisplayMetrics? {
        val display = context.getSystemService(DisplayManager::class.java)
            .getDisplay(Display.DEFAULT_DISPLAY) ?: return null
        return DisplayMetrics().also { display.getRealMetrics(it) }
    }

    companion object {
        private const val TAG = "ScreenCaptureController"
        private const val VIRTUAL_DISPLAY_NAME = "livachat-screen"
        // v2 D7：1s → 2s，上行带宽减半；相对服务端 5s 空闲清 buffer 仍有 2.5 倍余量
        private const val TICK_MS = 2_000L
        // 抓屏长边上限：1080 → 1920（2026-09-26）——点按抓屏由用户主动触发且低频，
        // 提高上限保住屏幕小字可读性。真机 1176×2400 → 940×1920（scale=0.8），
        // JPEG q70 实测 158.6KB/帧（静态桌面；base64 上线 ≈212KB），远低于服务端
        // uvicorn ws_max_size 默认 16MiB。旋转自适应（scaledCaptureSize 默认参）自动跟随。
        private const val MAX_LONG_SIDE = 1920
        private const val JPEG_QUALITY = 70
        private const val MAX_IMAGE_READER_BUFFERS = 2
        // v3.1：resize 连续失败收敛阈值——达限升级 stop（§8「resize 平台异常」）
        private const val MAX_RESIZE_FAILURES = 3
        // v3.1：onDisplayChanged 后延迟复测（旋转动画期 OEM metrics 瞬态）
        private const val RESIZE_RECHECK_DELAY_MS = 300L
        // v3.1 P1-1：resize 失败后最小重试间隔——防动画期（DisplayListener + 复测驱动，
        // 1s 内 4-6 次尝试）瞬时失败秒级烧完失败计数误停健康录制；失败节奏拉长到 ≥1s
        // 间隔后，3 次计数跨 ≥3s（对齐文档「跨 tick 累计」口径），确定性失败不受影响
        private const val RESIZE_RETRY_MIN_INTERVAL_MS = 1_000L

        // ── v2 点按抓取（docs/android_screen_recording.md §4.1）────────
        // 2s 定时上行停用（需求 4：功能取消、code 保留）：置回 true 即恢复 v1/v2/v3
        // 的定时 + 门控 + keepalive 全部语义，无需其他改动
        private const val AUTO_TICK_ENABLED = false
        // 防连点：一次抓取约 150-450ms；避免双击重推同一画面（也避免隐藏-恢复抖动）
        private const val MIN_CAPTURE_INTERVAL_MS = 600L
        /** v4 需求：等待本轮 ai_text 的兜底时长（服务端不起回合/断网等情形由此收场）。 */
        private const val WAIT_TIMEOUT_MS = 15_000L
        // 隐藏后等合成器产出"无浮窗"帧的 settle 时长（≈9 帧 @60Hz）
        private const val HIDE_SETTLE_MS = 150L
        private const val CAPTURE_RETRY_COUNT = 3
        private const val CAPTURE_RETRY_DELAY_MS = 100L

        // 点按抓屏随帧上行的发图指令按 App 语言取 R.string.conv_image_action_text
        // （docs/android_screen_recording.md §5.1；omni 下不发送）。

        // ── 以下纯函数为可测单元（test/ 直测），不依赖系统服务 ──────

        /**
         * 点按抓取决策（v2 纯函数，可直测）：未录制/在飞/防连点 → 忽略；门控拒绝
         * （App 前台 / 熄屏 / 锁屏，复用 [shouldPushFrame]）→ 带文案原因的拒绝；
         * 否则启动抓取序列。
         */
        internal fun captureDecision(
            isRecording: Boolean,
            capturing: Boolean,
            nowMs: Long,
            lastCaptureMs: Long,
            appForeground: Boolean,
            isInteractive: Boolean,
            isKeyguardLocked: Boolean,
            waiting: Boolean = false,
            minIntervalMs: Long = MIN_CAPTURE_INTERVAL_MS,
        ): CaptureDecision = when {
            !isRecording -> CaptureDecision.Ignore
            // v4 D7：等待期不允许再次点击（压过其余分支——含防连点/门控拒绝）
            waiting -> CaptureDecision.IgnoreWaiting
            capturing -> CaptureDecision.Ignore
            nowMs - lastCaptureMs < minIntervalMs -> CaptureDecision.Ignore
            shouldPushFrame(appForeground, isInteractive, isKeyguardLocked) -> CaptureDecision.StartCapture
            appForeground -> CaptureDecision.Reject(CaptureFeedback.REJECTED_FOREGROUND)
            else -> CaptureDecision.Reject(CaptureFeedback.REJECTED_LOCKED)
        }

        /**
         * v4 D6：本轮 `ai_text` 判据（纯函数，可直测）——**非 agent 的 `index == 0` 分片**
         * 即"某一轮 LLM 文本开始"（不保证是本次抓取那轮：口述轮 / 译文轮 / 同传轨同样命中，
         * 语义与残余竞态见 docs/android_screen_recording.md §7）。
         *
         * `index` 从 0 起（服务端各轨约定）、中断/取消不发 `ai_text`、agent 推送恒
         * `index=0 && source="agent"`（后者靠 source 排除）。`isFinal` 不过滤：
         * 空轮 final 就是 `index=0 && isFinal=true`（§6.1）。
         */
        internal fun endsWait(source: String?, index: Long): Boolean =
            source != "agent" && index == 0L

        /**
         * 按长边 ≤ [maxLongSide] 等比缩放并取偶（D6）；输入非法（0/负）时
         * 产出最小 2×2，调用方靠 VirtualDisplay 创建失败兜底。
         */
        internal fun scaledCaptureSize(
            realWidth: Int,
            realHeight: Int,
            maxLongSide: Int = MAX_LONG_SIDE,
        ): CaptureSize {
            val longSide = max(realWidth, realHeight)
            if (longSide <= 0) return CaptureSize(2, 2)
            val scale = min(1f, maxLongSide.toFloat() / longSide)
            // 向下取偶：不放大真实像素，保证长边 ≤ maxLongSide
            fun toEven(v: Float): Int = ((v / 2f).toInt() * 2).coerceAtLeast(2)
            return CaptureSize(toEven(realWidth * scale), toEven(realHeight * scale))
        }

        /**
         * 单 tick 推送决策（D7/D8）：
         * - [encoded] 非空（本 tick 有新帧）→ 推新帧，返回它作为新缓存；
         * - [encoded] 空但 [cached] 非空（画面静止）→ 重发缓存，keepalive 维持服务端 buffer 活性；
         * - 两者皆空（启动首秒尚无帧）→ 不推，返回 null。
         */
        internal fun decideFrameToPush(
            encoded: ScreenFrame?,
            cached: ScreenFrame?,
            push: (ScreenFrame) -> Unit,
        ): ScreenFrame? {
            val frame = encoded ?: cached ?: return null
            push(frame)
            return frame
        }

        /**
         * v2 帧门控判定（D12/D14）纯函数：仅 **进程后台 × 屏幕交互 × 未锁屏**
         * 放行（新帧或 keepalive），其余一律丢弃。
         *
         * 语义要点：
         * - app 前台 → 恒丢弃（不采集自身，防截图回显循环）；
         * - `isInteractive=false`（熄屏/doze/AOD 均非交互态）→ 恒丢弃，且此时
         *   `isKeyguardLocked` 输入已不影响结果——熄屏态 keyguard 返回值不可靠，
         *   由交互态先行"短路"（D14）；
         * - keyguard 判定只在交互态下起作用，锁屏（含滑动锁屏、"showing" 语义）→ 丢弃。
         */
        internal fun shouldPushFrame(
            appForeground: Boolean,
            isInteractive: Boolean,
            isKeyguardLocked: Boolean,
        ): Boolean = !appForeground && isInteractive && !isKeyguardLocked

        /**
         * v3 D15/D16 resize 判定：当前捕获尺寸与重算目标不等即需 resize；null 视为
         * 需初始化基线（启动后首 tick / 复位后）。180° 旋转等尺寸未变 → false——
         * 零动作，仅一次重算比较，无状态副作用。
         */
        internal fun needsResize(
            current: CaptureSize?,
            width: Int,
            height: Int,
        ): Boolean = current == null || current.width != width || current.height != height

        /**
         * v3.1 resize 连续失败计数推进：失败 +1，达 [maxFailures]（默认 3）返回 null
         * ——调用方必须升级为 stop + 错误上报（确定性失败不得静默无限重试，§8
         * 「resize 平台异常」；重建 VD 有单次授权风险，stop 是唯一安全升级路径）。
         * 成功清零由调用方直接置 0。
         */
        internal fun nextResizeFailureCount(
            failCount: Int,
            maxFailures: Int = MAX_RESIZE_FAILURES,
        ): Int? = if (failCount + 1 >= maxFailures) null else failCount + 1

        /**
         * RGBA_8888 平面 → ARGB_8888 Bitmap。输入为去除 ByteBuffer 包装后的
         * 原始字节，按 [rowStride]/[pixelStride] 逐像素拷贝，正确剥离行对齐
         * padding（§8：勿直接整体灌入）。
         */
        internal fun rgbaToBitmap(
            pixels: ByteArray,
            width: Int,
            height: Int,
            rowStride: Int,
            pixelStride: Int,
        ): Bitmap {
            val out = ByteBuffer.allocateDirect(width * height * 4)
            for (y in 0 until height) {
                val rowStart = y * rowStride
                for (x in 0 until width) {
                    val i = rowStart + x * pixelStride
                    out.put(pixels[i]) // R
                    out.put(pixels[i + 1]) // G
                    out.put(pixels[i + 2]) // B
                    out.put(pixels[i + 3]) // A
                }
            }
            out.rewind()
            val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
            bitmap.copyPixelsFromBuffer(out)
            return bitmap
        }
    }
}
