package dev.livachat.app.media

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Build
import android.os.SystemClock
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.domain.model.AudioRoute
import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.model.PlaybackFlushReason
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlin.coroutines.coroutineContext
import kotlinx.coroutines.withContext
import java.util.concurrent.Executors
import javax.inject.Inject
import javax.inject.Singleton

private const val AUDIO_TAG = "LivaChat-Audio"

/**
 * AudioTrack 构建接缝：单测注入 fake，生产用 [Real]（镜像 AudioCaptureController 的
 * `AudioRecordFactory` 先例，docs/android_audio_output_devices.md §4）；getMinBufferSize 计算
 * 一并收在 Real 内（纯 JVM 不可用）。
 */
fun interface AudioTrackFactory {
    fun create(sampleRate: Int): AudioTrack

    companion object {
        val Real = AudioTrackFactory { sampleRate ->
            val minBuf = AudioTrack.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            )
            val bufferSize = if (minBuf > 0) minBuf * 3 else sampleRate * 2
            val bufferMs = bufferSize * 1000 / (sampleRate * 2)
            Log.d(AUDIO_TAG, "track created: rate=$sampleRate minBuf=$minBuf bufferSize=$bufferSize bytes = ${bufferMs}ms")
            AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(sampleRate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build(),
                )
                .setBufferSizeInBytes(bufferSize)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
        }
    }
}

@Singleton
class AudioPlaybackController @Inject constructor(
    private val audioRouteManager: AudioRouteManager,
    private val deviceManager: AudioDeviceManager,
    @ApplicationContext private val context: Context,
) : LivaChatListener {

    companion object {
        private const val TAG = AUDIO_TAG
    }

    private val audioManager: AudioManager =
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    // 单测 seam（镜像 AudioRecordFactory 先例）
    internal var trackFactory: AudioTrackFactory = AudioTrackFactory.Real

    private val _isPlaying = MutableStateFlow(false)
    val isPlaying: StateFlow<Boolean> = _isPlaying.asStateFlow()

    private val _isMuted = MutableStateFlow(false)
    val isMuted: StateFlow<Boolean> = _isMuted.asStateFlow()

    private val _audioRoute = MutableStateFlow(AudioRoute.SPEAKER)
    val audioRoute: StateFlow<AudioRoute> = _audioRoute.asStateFlow()

    // 初值 = 冷启动默认规则（D10 v3，2026-10-11 用户定案，docs §6.1）：仅内置 → 内置扬声器；
    // 内置+仅经典蓝牙 → 蓝牙优先（SCO>A2DP，多台取 id 升序第一台）；其余 → null（系统默认）。
    // 进程启动计算一次，重启重算（不持久化），见 [resolveColdStartSelection]
    private val _preferredOutputDeviceId = MutableStateFlow(resolveColdStartSelection())
    /**
     * 选中的输出设备 id（null=系统默认），供 UI 反显（docs/android_audio_output_devices.md §4.1）。
     * 事实源在控制器：重建/断连兜底/清选择均收敛到该流。
     */
    val preferredOutputDeviceId: StateFlow<Int?> = _preferredOutputDeviceId.asStateFlow()

    // internal 供控制器测试直读（跨重建不丢帧断言，§7.1；同类可见性让步，不改生产行为）
    internal val jitterBuffer = JitterBuffer(targetFrames = 3, maxLatencyFrames = 15000)

    private val playbackDispatcher = Executors
        .newSingleThreadExecutor { Thread(it, "AudioPlayback").apply {
            isDaemon = true
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO)
        }}
        .asCoroutineDispatcher()
    private val playbackScope = CoroutineScope(SupervisorJob() + playbackDispatcher)

    @Volatile
    private var playbackJob: Job? = null

    @Volatile
    private var rebuildJob: Job? = null

    // SCO 请求状态（方案 A′ 去抖）：仅控制器的持锁段（start / rebuild 字段变更段 / stop）读写
    private var scoRequested = false

    @Volatile
    private var audioTrack: AudioTrack? = null

    private var currentSampleRate: Int = 16_000

    private var pushCount: Long = 0
    private var pullCount: Long = 0
    private var writeCount: Long = 0
    private var nullCount: Long = 0

    /**
     * 设备表观察（D10 断连兜底）专用 scope，进程级常驻不取消。
     * 不挂 playbackDispatcher：回调会取控制器监视器，若与 start() 的持锁 join 共线存在死锁环，
     * 且播放线程保持纯播放职责。
     */
    private val deviceWatchScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    init {
        // 设备表观察（D10 v3）：只有"所选设备消失"触发兜底（回落扬声器优先，见 [resolveSelectedLossFallback]）；
        // 非所选设备的增减一律不动（§6.1 运行时规则 2/3——v3 无 auto/manual 概念）。
        deviceWatchScope.launch {
            deviceManager.outputDevices.collect { handleDeviceSetChange() }
        }
    }

    /** 设备表变化处理（观察协程；决策与写入同监视器）：仅"所选设备消失"→ 回落扬声器（无则系统默认）。 */
    @Synchronized
    private fun handleDeviceSetChange() {
        val devices = deviceManager.outputDevices.value
        val current = _preferredOutputDeviceId.value
        // 空表按"无信息"处理不判消失（真机内置恒在、不会为空；防夹具/数据未就绪态误清选择）
        if (current == null || devices.isEmpty() || devices.any { it.id == current }) {
            Log.d(TAG, "device set changed; selection $current unchanged")
            return
        }
        val fallback = resolveSelectedLossFallback()
        Log.d(TAG, "selected output device $current disconnected; fallback → $fallback")
        applySelectionLocked(fallback)
    }

    // ── Public API ────────────────────────────────────────────────────

    @Synchronized
    fun start(sampleRate: Int = 16_000) {
        if (_isPlaying.value) return

        runBlocking { playbackJob?.join() }

        currentSampleRate = sampleRate

        audioRouteManager.switchRoute(AudioRoute.SPEAKER)
        audioRouteManager.requestAudioFocus()

        Log.d(TAG, "start: rate=$sampleRate jbTarget=${jitterBuffer.targetFrames}x20ms=${jitterBuffer.targetFrames*20}ms")
        val track = trackFactory.create(sampleRate)

        // 输出设备偏好（docs/android_audio_output_devices.md §4.3）：三类"设备不可用"（解析不到/平台拒绝/
        // 调用抛异常）→ 回落 = 扬声器优先（无扬声器→系统默认；D10 v3 规则 1，与断连兜底同一计算）
        var appliedDev: AudioDeviceInfo? = null
        val preferredId = _preferredOutputDeviceId.value
        if (preferredId != null) {
            appliedDev = applyPreferredDevice(track, preferredId)
            if (appliedDev == null) {
                Log.w(TAG, "preferred output device $preferredId unavailable; applying speaker-first fallback")
                _preferredOutputDeviceId.value = resolveSelectedLossFallback()
                _preferredOutputDeviceId.value?.let { fallbackId ->
                    appliedDev = applyPreferredDevice(track, fallbackId)
                    if (appliedDev == null) _preferredOutputDeviceId.value = null
                }
            }
        }
        syncBluetoothScoLocked(appliedDev)
        audioTrack = track
        // 静音重放（与重建同一不变量，2026-10-11 终审补齐）：setVolume 为 track 实例属性、新 track 默认
        // 全音量；_isMuted 为单例级状态跨会话存活且无重置点——不重放则"会话 1 静音 → 会话 2"UI 显示
        // 已关闭但实际全音量播放
        setMuted(_isMuted.value)

        _isPlaying.value = true
        pushCount = 0
        pullCount = 0
        writeCount = 0
        nullCount = 0
        jitterBuffer.reset()
        startPlaybackLoop()
    }

    @Synchronized
    fun stop() {
        // stop × rebuild 竞争收敛（docs/android_audio_output_devices.md §4.4）：先停重建协程再走既有拆除序列；
        // 取消是协作式——重建协程在锁外挂起点（join）与锁内变更段（ensureActive 守卫）双重点收敛
        rebuildJob?.cancel()
        // SCO 链路收口（方案 A′ 兜底）：会话结束释放（输出选择已同生共死管理；此处兜底，未持有则 no-op）
        syncBluetoothScoLocked(null)
        Log.d(TAG, "stop: push=$pushCount pull=$pullCount write=$writeCount null=$nullCount dropLate=${jitterBuffer.droppedLate} dropOverflow=${jitterBuffer.droppedOverflow}")
        _isPlaying.value = false
        playbackJob?.cancel()
        playbackJob = null
        jitterBuffer.reset()
        audioRouteManager.abandonAudioFocus()
    }

    fun setMuted(muted: Boolean) {
        _isMuted.value = muted
        audioTrack?.setVolume(if (muted) 0f else 1f)
        Log.d(TAG, "muted=$muted")
    }

    fun switchRoute(route: AudioRoute) {
        if (audioRouteManager.switchRoute(route)) {
            _audioRoute.value = route
        }
    }

    /**
     * 选择输出设备（docs/android_audio_output_devices.md §4.4；D10 v3：任何显式选择都只是改值——之后仅
     * "所选设备消失"会触发兜底，见 [handleDeviceSetChange]；无 auto/manual 概念）。
     *
     * 幂等短路；未播放仅记录（下次 start 生效）；播放中向 playbackScope 投递重建协程——
     * 锁内不做 join/构建（join 为挂起等待、构建为 ~ms 级 binder，均留锁外，见 §5.4 线程约束；
     * 字段变更段在重建协程内取同一监视器）。
     */
    @Synchronized
    fun setPreferredOutputDevice(deviceId: Int?) {
        applySelectionLocked(deviceId)
    }

    /**
     * Audio Gallery 跟随的实时会话当前输出设备（docs/android_audio_output_devices.md §4.7/§6.1）。
     *
     * 选中设备可解析 → 用它；选中设备失效 → 扬声器优先 → 实时 Track 的 routedDevice
     * → null（系统默认）；选中为 null（系统默认）→ 实时 Track routedDevice → null。
     * 锁内只快照选择/扬声器 id 与 Track 引用；getDevices/routedDevice 等 binder 调用
     * 一律在锁外（§5.4），并 runCatching 兜底 playback loop 的异步 release 窗口。
     */
    internal fun currentOutputDevice(): AudioDeviceInfo? {
        val selectedId: Int?
        val speakerId: Int?
        val track: AudioTrack?
        synchronized(this) {
            selectedId = preferredOutputDeviceId.value
            speakerId = if (selectedId != null) resolveSelectedLossFallback() else null
            track = audioTrack
        }

        if (selectedId != null) {
            resolveOutputDevice(selectedId)?.let { return it }
            speakerId?.let { resolveOutputDevice(it) }?.let { return it }
        }
        return runCatching { track?.routedDevice }.getOrNull()
    }

    /** 选择应用（幂等短路 + 记录 or 投递重建）；调用方须持监视器（本类 @Synchronized 方法内）。 */
    private fun applySelectionLocked(deviceId: Int?) {
        if (deviceId == _preferredOutputDeviceId.value) return
        val previous = _preferredOutputDeviceId.value
        _preferredOutputDeviceId.value = deviceId
        if (!_isPlaying.value) {
            Log.d(TAG, "preferred output device recorded: $deviceId (applies on next start)")
            return
        }
        // 两次快速切换在 playbackScope 单线程 dispatcher 上自然串行；目标值在协程内实时读取（最新生效）
        rebuildJob = playbackScope.launch { rebuildTrack(previous) }
    }

    private enum class RebuildAttempt { OK, FAILED, ABANDONED }

    /**
     * 重建 AudioTrack 协程体（playbackScope 单线程 dispatcher）。
     * 构建失败（与设备无关的实例化异常）→ 回退上一 id 再建一次（镜像输入侧 D7 回退语义）；
     * 再败 → 终态收敛：`_isPlaying=false` + logError，等下一次 `start()`（不收敛会留下
     * "正在播放但无 track 无 loop"的半死态）。
     */
    private suspend fun rebuildTrack(previous: Int?) {
        val target = _preferredOutputDeviceId.value
        val t0 = SystemClock.elapsedRealtime()
        when (rebuildAttempt(target)) {
            RebuildAttempt.OK -> Log.d(
                TAG,
                "preferred output device switched: $target in ${SystemClock.elapsedRealtime() - t0}ms",
            )
            RebuildAttempt.ABANDONED -> Unit   // stop() 已接管拆除，无需收敛动作
            RebuildAttempt.FAILED -> {
                // 仅当选择未被更新的用户操作覆盖时才回退（CAS 返回值放行，§5.4 并发纪律）——
                // CAS 失败 = 用户已并发改选，由其排队的重建收敛；无条件回退会把 track 瞬时钉到
                // 过期目标（选择流已是新值）
                if (_preferredOutputDeviceId.compareAndSet(target, previous)) {
                    when (rebuildAttempt(previous)) {
                        RebuildAttempt.OK -> Log.w(
                            TAG,
                            "preferred output device switch to $target failed, fell back to $previous",
                        )
                        RebuildAttempt.ABANDONED -> Unit
                        RebuildAttempt.FAILED -> {
                            Log.e(
                                TAG,
                                "rebuild failed: $target (fallback $previous also failed); waiting for next start()",
                            )
                            synchronized(this) {
                                // 终态收敛（A′ 同生共死）：无播放即释放可能驻留的 SCO 链路——双构建失败时
                                // 首个 rebuildAttempt 已取消旧 loop、但未走到字段变更段的 sync，不释放则
                                // SCO 驻留（钉住耳机通话模式/压制 A2DP）至下一次 stop()
                                syncBluetoothScoLocked(null)
                                _isPlaying.value = false
                            }
                        }
                    }
                }
            }
        }
    }

    /**
     * 单次重建尝试：旧 loop 取消并（挂起）join 收敛 → 锁外构建 → 应用偏好（三类设备不可用 → 扬声器
     * 优先回落，D10 v3） → 同锁「字段变更段」（ensureActive → 赋值 → 静音重放 → 启 loop；stop 先行则
     * 释放新 track 退出）。返回 ABANDONED = stop() 已接管本次重建。
     */
    private suspend fun rebuildAttempt(deviceId: Int?): RebuildAttempt {
        val oldLoop = playbackJob
        oldLoop?.cancel()
        try {
            oldLoop?.join()
        } catch (_: CancellationException) {
            return RebuildAttempt.ABANDONED
        }
        val newTrack = try {
            trackFactory.create(currentSampleRate)
        } catch (e: Exception) {
            Log.w(TAG, "rebuild track construction failed", e)
            return RebuildAttempt.FAILED
        }
        // 设备不可用三态与 start() 同义（解析不到/拒绝/异常）：不算构建失败；回落 = 扬声器优先
        // （无扬声器→系统默认；D10 v3 规则 1）。写入同监视器防落后于并发手选，抢先则放弃（由其收敛）
        var appliedDev = deviceId?.let { applyPreferredDevice(newTrack, it) }
        if (deviceId != null && appliedDev == null) {
            Log.w(TAG, "preferred output device $deviceId unavailable; applying speaker-first fallback")
            val fallbackId = resolveSelectedLossFallback()
            val fallbackApplied = synchronized(this) {
                if (_preferredOutputDeviceId.value != deviceId) {
                    false
                } else {
                    _preferredOutputDeviceId.value = fallbackId
                    true
                }
            }
            if (fallbackApplied && fallbackId != null) {
                appliedDev = applyPreferredDevice(newTrack, fallbackId)
                if (appliedDev == null) {
                    synchronized(this) {
                        if (_preferredOutputDeviceId.value == fallbackId) _preferredOutputDeviceId.value = null
                    }
                }
            }
        }
        val outcome = synchronized(this) {
            if (!coroutineContext.isActive || !_isPlaying.value) {
                try { newTrack.release() } catch (_: Exception) {}
                RebuildAttempt.ABANDONED
            } else {
                audioTrack = newTrack
                setMuted(_isMuted.value)   // 静音重放：setVolume 为 track 实例属性，新 track 默认全音量（§5.4）
                // SCO 联动在字段变更段内（同锁）：stop 不可能与本行交错（§5.4 互斥契约）
                syncBluetoothScoLocked(appliedDev)
                startPlaybackLoop()
                RebuildAttempt.OK
            }
        }
        return outcome
    }

    /**
     * BT SCO 链路联动（方案 A′，2026-10-11，docs/android_audio_output_devices.md §4.6）：真机实测平台不会因
     * track 路由到 SCO 而自动建链（`SCO_STATE_INACTIVE`），需显式 `startBluetoothSco()`
     * （需 MODIFY_AUDIO_SETTINGS——manifest 已声明；deprecated=34 属主动选择；官方原文：建立可能
     * 耗时数秒，音频随链路就绪开始）。
     * 生命周期与输出选择**同生共死**：解析为 BT SCO → 拉起；其余（其它设备 / 系统默认 null）→ 释放——
     * 否则 SCO 驻留把耳机钉在通话模式、本进程持续持有 `bt_sco` 通信设备请求，切到 A2DP 时媒体流被
     * 压制（2026-10-11 用户实测缺陷）。
     * 仅在控制器持锁段调用（start / rebuild 字段变更段 / stop）；[scoRequested] 去抖避免重复请求。
     */
    private fun syncBluetoothScoLocked(device: AudioDeviceInfo?) {
        val want = device?.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
        if (want == scoRequested) return
        @Suppress("DEPRECATION")
        try {
            if (want) {
                audioManager.startBluetoothSco()
                Log.d(TAG, "bluetooth sco requested (output = BT SCO)")
            } else {
                audioManager.stopBluetoothSco()
                Log.d(TAG, "bluetooth sco released (output = ${device?.type ?: "default"})")
            }
            scoRequested = want
        } catch (_: Exception) {}
    }

    /** 解析选中 id → 当前输出设备（设备连接期间 id 作匹配键，§5.4）。 */
    private fun resolveOutputDevice(id: Int): AudioDeviceInfo? =
        audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS).firstOrNull { it.id == id }

    /** 解析 id → 设备 → 应用到 track；三类"设备不可用"（解析不到/平台拒绝/抛异常，runCatching 全包
     *  ——resolve 为 binder 调用自身可抛）→ null（§5.4 统一语义）。start 与 rebuild 共用。 */
    private fun applyPreferredDevice(track: AudioTrack, id: Int): AudioDeviceInfo? =
        runCatching {
            resolveOutputDevice(id)?.takeIf { dev -> track.setPreferredDevice(dev) }
        }.getOrNull()

    /**
     * D10 v3 冷启动默认选择（2026-10-11 用户定案，docs §6.1；进程启动计算一次、重启重算、不持久化）：
     * 当前可见列表（deviceManager 白名单过滤列表，与 picker 同源）——
     * ① 仅内置（听筒/扬声器）→ 内置扬声器 id；
     * ② 外设**全部为经典蓝牙**（SCO/A2DP；`BLE_*` 不计——LE Audio 本版不考虑）→ 蓝牙优先：
     *    SCO 中 id 升序第一台，无 SCO 则 A2DP 中 id 升序第一台；
     * ③ 其余（含 USB/有线/BLE 等）→ null（系统默认，交还平台仲裁）。
     */
    private fun resolveColdStartSelection(): Int? {
        val devices = deviceManager.outputDevices.value
        if (devices.isEmpty()) return null
        val externals = devices.filter {
            it.type != AudioDeviceInfo.TYPE_BUILTIN_EARPIECE && it.type != AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
        }
        return when {
            externals.isEmpty() ->
                devices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }?.id
            externals.all {
                it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO || it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP
            } -> {
                val scos = externals.filter { it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO }
                val a2dps = externals.filter { it.type == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP }
                (scos.minByOrNull { it.id } ?: a2dps.minByOrNull { it.id })?.id
            }
            else -> null
        }
    }

    /** D10 v3 所选设备丢失兜底：**总是扬声器**；列表无扬声器 → null（系统默认，§6.1 运行时规则 1）。 */
    private fun resolveSelectedLossFallback(): Int? =
        deviceManager.outputDevices.value.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }?.id

    fun flush() {
        Log.d(TAG, "flush: jbSize=${jitterBuffer.size()}")
        jitterBuffer.reset()
    }

    // ── LivaChatListener ──────────────────────────────────────────────

    override fun onAiAudioPcm(
        pcm: ShortArray,
        sampleRate: Int,
        sampleBits: Int,
        durationMs: Int,
        sequenceNumber: Long,
        turnId: String,
    ) {
        if (sampleRate != currentSampleRate) {
            Log.w(TAG, "sampleRate mismatch: frame=$sampleRate track=$currentSampleRate seq=$sequenceNumber")
        }

        pushCount++
        if (pushCount % 50 == 1L) {
            Log.d(TAG, "push: seq=$sequenceNumber pcm=${pcm.size} dur=$durationMs rate=$sampleRate jbSize=${jitterBuffer.size()}")
        }

        jitterBuffer.push(
            JitterBuffer.PcmFrame(
                pcm = pcm,
                sampleRate = sampleRate,
                sampleBits = sampleBits,
                durationMs = durationMs,
                sequenceNumber = sequenceNumber,
                turnId = turnId,
            )
        )
    }

    override fun onPlaybackShouldFlush(reason: PlaybackFlushReason) {
        val dropped = jitterBuffer.size()
        jitterBuffer.reset()
        Log.d(TAG, "flush jitter buffer: reason=$reason dropped=$dropped frames")
    }

    // ── Playback loop ─────────────────────────────────────────────────

    private fun startPlaybackLoop() {
        playbackJob = playbackScope.launch {
            val track = audioTrack ?: return@launch
            try {
                runLoop(track)
            } finally {
                Log.d(TAG, "playback loop exiting: write=$writeCount null=$nullCount")
                withContext(NonCancellable) {
                    try { track.pause() } catch (_: Exception) {}
                    try { track.flush() } catch (_: Exception) {}
                    try { track.stop() } catch (_: Exception) {}
                    try { track.release() } catch (_: Exception) {}
                    audioTrack = null
                }
            }
        }
    }

    private suspend fun runLoop(track: AudioTrack) {
        val tickIntervalMs = 10L

        // Phase 1: initial buffering
        Log.d(TAG, "buffering: waiting for target=${jitterBuffer.targetFrames} frames")
        while (coroutineContext.isActive && !jitterBuffer.isReady()) {
            delay(10)
        }
        if (!coroutineContext.isActive) return

        val bufSize = jitterBuffer.size()
        Log.d(TAG, "buffered: jbSize=$bufSize → track.play()")

        track.play()

        val playState = track.playState
        Log.d(TAG, "track.play() → playState=$playState (3=PLAYING)")

        // 实际路由（getRoutedDevice 仅 playing 中有效，docs/android_audio_output_devices.md §2.4）——
        // 真机核对 preferred 生效的证据锚点；纯静音期无帧 → 无 play → 此行不出现（可接受）
        val routed = track.routedDevice
        Log.d(TAG, "routed device: id=${routed?.id} type=${routed?.type} addr=${if (Build.VERSION.SDK_INT >= 28) routed?.address else null}")

        // Phase 2: continuous streaming — 10ms tick, WRITE_BLOCKING paces output
        while (coroutineContext.isActive) {
            val tickStart = System.nanoTime()

            val frame = jitterBuffer.pull()
            pullCount++
            if (frame != null) {
                val written = track.write(frame.pcm, 0, frame.pcm.size, AudioTrack.WRITE_BLOCKING)
                if (written != frame.pcm.size) {
                    Log.w(TAG, "short write: $written / ${frame.pcm.size}")
                }
                writeCount++
            } else {
                nullCount++
                // if (nullCount % 100 == 1L) {
                //     Log.d(TAG, "underrun: nullCount=$nullCount jbSize=${jitterBuffer.size()}")
                // }
            }

            val elapsedMs = (System.nanoTime() - tickStart) / 1_000_000
            if (elapsedMs > 50) {
                Log.w(TAG, "slow tick: elapsedMs=$elapsedMs jbSize=${jitterBuffer.size()}")
            }
            val remainingMs = tickIntervalMs - elapsedMs
            if (remainingMs > 0) delay(remainingMs)
        }
    }
}
