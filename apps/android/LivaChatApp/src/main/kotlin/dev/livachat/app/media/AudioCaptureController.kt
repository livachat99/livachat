package dev.livachat.app.media

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.SystemClock
import androidx.core.content.ContextCompat
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.sdk.LivaChatSDK
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

open class MediaError(message: String, cause: Throwable? = null) : RuntimeException(message, cause)
class AudioCaptureFailed(message: String, cause: Throwable? = null) : MediaError(message, cause)

/**
 * AudioRecord 构建接缝：单测注入 fake，生产用 [Real]。
 * create() 内做 buffer 尺寸计算 + Builder 构建，避免 controller 触碰 android 静态
 * （getMinBufferSize 在纯 JVM 单测不可用）。
 */
fun interface AudioRecordFactory {
    fun create(source: Int, sampleRate: Int, frameSamples: Int): AudioRecord

    companion object {
        // 权限由 AudioCaptureController.start() 在调用 factory 前检查（RECORD_AUDIO）；
        // 该 seam 本身无 Context，无法重复检查，故在此抑制 lint。
        @SuppressLint("MissingPermission")
        val Real = AudioRecordFactory { source, sampleRate, frameSamples ->
            val minBuf = AudioRecord.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            )
            // minBuf 可能为负（ERROR/-1、ERROR_BAD_VALUE/-2，docs/android_audio_capture.md §3.2
            // 锚点指出的基线缺陷）：maxOf 地板到 frameSamples*2，构建失败再交 STATE 校验收敛
            val bufferSize = maxOf(minBuf * 2, frameSamples * 2)
            AudioRecord.Builder()
                .setAudioSource(source)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(sampleRate)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .build(),
                )
                .setBufferSizeInBytes(bufferSize)
                .build()
        }
    }
}

@SuppressLint("MissingPermission")
@Singleton
class AudioCaptureController @Inject constructor(
    private val sdk: LivaChatSDK,
    @ApplicationContext private val context: Context,
) {
    // 单测 seam（docs/android_audio_capture.md §7.1）
    internal var audioRecordFactory: AudioRecordFactory = AudioRecordFactory.Real
    // PCM 落盘接缝（docs/android_audio_capture.md §3.7）：null = 关闭；生产启用见 PcmDump.ENABLED
    internal var pcmDump: PcmDump? = null

    private val _isCapturing = MutableStateFlow(false)
    val isCapturing: StateFlow<Boolean> = _isCapturing.asStateFlow()

    private val _isMuted = MutableStateFlow(false)
    val isMuted: StateFlow<Boolean> = _isMuted.asStateFlow()

    private val audioManager: AudioManager =
        context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

    private val _preferredInputDeviceId = MutableStateFlow<Int?>(null)
    /**
     * 选中的输入设备 id（null=系统默认），供 UI 反显。事实源在控制器：源切换/读错误重试/
     * 挂起恢复等一切重建路径统一携带该偏好（docs/android_audio_input_devices.md §4）。
     */
    val preferredInputDeviceId: StateFlow<Int?> = _preferredInputDeviceId.asStateFlow()

    private var audioRecord: AudioRecord? = null
    private var readJob: Job? = null
    private var currentSampleRate: Int = 16_000
    // 当前采集源（默认语音）；离开转录模式时经 switchAudioSource 归位 VOICE_COMMUNICATION
    private var currentAudioSource: Int = MediaRecorder.AudioSource.VOICE_COMMUNICATION
    // 采集代数：每次 stop/switch 自增；旧读循环的迟到读错误按代作废（docs/android_audio_capture.md §3.2）
    private var generation = 0
    private var retryCount = 0
    private var lastMicServiceRestartMs = 0L
    // stop() 与 startInternal/handleReadError 在 start/stop/读错误重试间串行化：
    // 阻塞式 read 无法被 cancel 打断，stop() 释放 AudioRecord 时在途 read 会以错误
    // 返回并触发重试——若无此标志，重试会在 stop 之后经 startInternal 复活采集
    // （_isCapturing 重新置 true、无 FGS、micLevel 违反"停止恒 0"契约）
    private var stopping = false
    // 后台挂起标志（docs/android_audio_capture.md §3.6）：BG+muted 释放后置位，
    // resume/stop 清除；仅 @Synchronized 方法内读写
    private var suspendedForBackground = false
    private val flushGate = SilenceFlushGate()
    private val levelMeter = MicLevelMeter()

    private val _micLevel = MutableStateFlow(0)
    /**
     * 麦克风输入音量电平段（docs/android_mic_meter.md §3.1）。
     * 契约：0..MicLevelMeter.LEVELS（=4），controller 侧已量化+快攻慢放平滑，
     * muted 帧/停止恒 0（muted 期间 frame 仍是真实拾音数据，强制归零是隐私语义）；
     * 读帧协程（Dispatchers.IO）与生命周期复位点写入，StateFlow 等值去重，任意线程读安全。
     */
    val micLevel: StateFlow<Int> = _micLevel.asStateFlow()

    private companion object {
        // onMicServiceDestroyed 自愈冷却：防豁免窗口内 startForeground 失败→stopSelf→
        // onDestroy→再重启热循环（豁免为系统临时 allowlist，冷却后过期，循环自然终止）
        const val MIC_SERVICE_RESTART_COOLDOWN_MS = 1_000L
    }

    @Synchronized
    fun start(sampleRate: Int = 16_000) {
        if (_isCapturing.value) return
        stopping = false
        // 无 RECORD_AUDIO 时 AudioRecord.Builder.build() 抛 UnsupportedOperationException
        //（部分 OEM 直接崩在调用线程）。冷启动已由 Splash 权限门禁拦截，此处兜运行中
        // 变化（one-time 授权后台过期/策略回收后 read 死亡→重试重建）——缺失时早退，不裸崩
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            sdk.logWarn("AudioCaptureController", "start skipped: mic permission missing")
            return
        }
        currentSampleRate = sampleRate
        retryCount = 0
        startInternal()
        if (_isCapturing.value) {
            // 先采集、后补 FGS（与录屏相反是有意的，docs/android_background_support.md §5.2）；
            // 失败路径（含内部重试耗尽）不会置 true，此处不触发，无 FGS 泄漏
            startMicForegroundService()
        }
    }

    @Synchronized
    private fun startInternal() {
        // stop() 可能已完成（stopping=true 在读错误重试路径触发）——不再拉起新采集
        if (stopping) return
        val frameSamples = currentSampleRate * 20 / 1000

        try {
            val rec = audioRecordFactory.create(currentAudioSource, currentSampleRate, frameSamples)
            // D8：不校验 state 时 OEM 可能静默起录失败（Mock 工程 initAudioRecord 同校验先例）；
            // 异常收敛进 catch → 当前代重试路径
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                try { rec.release() } catch (_: Exception) {}
                throw AudioCaptureFailed(
                    "AudioRecord init failed: state=${rec.state} source=$currentAudioSource",
                )
            }
            audioRecord = rec
            // 输入设备偏好（docs/android_audio_input_devices.md §4）：三类"设备不可用"统一回落到默认路由
            // ——解析不到（设备被拔）/平台拒绝（返回 false）/调用抛异常。既有设计不变量"异常绝不能逃逸进
            // 本方法 catch"（否则 handleReadError 以同一坏 id 重试 ≤3 次全败进死态，而
            // start()/onAppForegrounded() 等冷启动路径没有设备级回退，采集会被一个坏 id 打死）——
            // 故 resolve（getDevices 为 binder 调用，自身也可抛）与 setPreferredDevice 同置 runCatching。
            // 插入点必须在字段赋值后：此块若有意外抛出，catch 释放的是字段 audioRecord，局部 rec 不泄漏。
            _preferredInputDeviceId.value?.let { id ->
                val applied = runCatching {
                    resolveInputDevice(id)?.let { dev -> rec.setPreferredDevice(dev) } ?: false
                }.getOrDefault(false)
                if (!applied) {
                    sdk.logWarn(
                        "AudioCaptureController",
                        "preferred device $id unavailable; using default",
                    )
                    // 静默回落默认（决策点 D4=A），防陈旧 id 反复重试
                    _preferredInputDeviceId.value = null
                }
            }
            audioRecord?.startRecording()
            // 实际路由（getRoutedDevice 仅 recording 中有效，§4 事实）——真机核对 preferred 生效的证据锚点
            val routed = rec.routedDevice
            sdk.logInfo(
                "AudioCaptureController",
                "routed device: id=${routed?.id} type=${routed?.type} " +
                    "addr=${if (Build.VERSION.SDK_INT >= 28) routed?.address else null}",
            )
        } catch (e: Exception) {
            // 构建/启动失败（无权限、OEM 音频栈/HAL 异常等）统一收敛到重试路径不裸崩——
            // 本方法会被 handleReadError 重试直接调用，权限早退在 start() 覆盖不到这里；
            // 重试耗尽后 handleReadError 复位状态并停 FGS，无孤儿通知。
            // 构建失败属当前代，传 generation（与 read 循环传捕获代区分，勿混）
            sdk.logWarn("AudioCaptureController", "audio start failed", e)
            handleReadError(e, generation)
            return
        }

        _isCapturing.value = true
        val gen = generation
        // PCM 落盘（临时排查）：按采集源/采样率分组，每次 startInternal 换新文件
        val dump = rotatePcmDump()
        readJob = CoroutineScope(Dispatchers.IO).launch {
            // 静音状态事实源是 _isMuted;循环起点校准基线,避免把"启动前已静音"误判为 mute 沿
            flushGate.resetBaseline(_isMuted.value)
            val frame = ShortArray(frameSamples)
            // 静音补发窗口:服务端 VAD 需 ≥600ms 连续静音才收尾当前 ASR 句,
            // 直接掐断会导致服务端永远等不到结束;故 mute 后先补发 1s 全零帧再丢。
            val zeroFrame = ShortArray(frameSamples)
            while (isActive && _isCapturing.value) {
                val read = try {
                    // 即使丢弃/补发也保持 read,避免 unmute 后积压旧数据
                    audioRecord?.read(frame, 0, frameSamples) ?: -1
                } catch (e: Exception) {
                    -1
                }
                if (read < 0) {
                    handleReadError(RuntimeException("AudioRecord.read() returned $read"), gen)
                    return@launch
                }
                // 电平旁路计算（纯旁路，不触碰 flushGate/上行帧逻辑）；以 _isMuted 为
                // 准而非 decide 分支结果——muted 期间帧仍是真实拾音，必须强制归零。
                // 活跃守卫：stop() 可能在 read() 阻塞期间执行，cancel 不中断阻塞式
                // read——在途帧返回后若照常写电平，会把 stop() 的归零覆盖成残留
                if (isActive && _isCapturing.value) {
                    _micLevel.value = levelMeter.onPcmFrame(frame, read, _isMuted.value)
                }
                when (flushGate.decide(_isMuted.value)) {
                    FrameAction.SEND_REAL -> {
                        // 落盘的是真实拾音（编码/增益前的麦克风电平）；muted 期间不落盘（隐私契约同电平表）
                        dump?.write(frame, read)
                        sdk.pushAudioPcm(frame, 0, read)
                    }
                    // muted 标记：payload vad_state=muted，服务端替换为零 PCM
                    //（lecture 分支 5×，omni/dialogue 1:1；docs/android_audio_capture.md §5.2）
                    FrameAction.SEND_SILENCE -> sdk.pushAudioPcm(zeroFrame, 0, frameSamples, muted = true)
                    FrameAction.DROP -> Unit
                }
            }
            // 正常退出（stop/cancel）终态归零：守卫已挡住多数在途帧，但守卫判定与
            // stop() 归零之间仍存极小竞态窗口——循环退出时再归零一次，保证 §4.5
            // "会话 stop → 全暗"不被并发写序破坏。读错误路径走 return@launch 不经
            // 此处：重试中途保留平滑态，重试耗尽分支已在 handleReadError 内归零
            _micLevel.value = 0
            levelMeter.reset()
        }
    }

    @Synchronized
    fun stop() {
        // 会话终结不复活（docs/android_audio_capture.md §3.6）
        suspendedForBackground = false
        teardownCapture()
    }

    /**
     * 进程退后台（docs/android_audio_capture.md §3.6）：仅"录音中且静音"时释放麦克风
     * ——补满 mute 静音窗口后完整拆除（同 stop 序列）+ 停 mic FGS，让出平台采集仲裁给其他
     * app（§2）；unmuted 保持现状（后台语音 + 屏幕交互）。
     */
    @Synchronized
    fun onAppBackgrounded() {
        if (!_isCapturing.value || !_isMuted.value) return
        suspendedForBackground = true
        topUpMuteFlushWindow()
        teardownCapture()
        sdk.logInfo("AudioCaptureController", "capture suspended for background (muted)")
    }

    /**
     * 进程回前台（docs/android_audio_capture.md §3.6）：挂起过则经 start() 全路径
     * 重获取（权限校验 + 重建 + 补启 FGS——此刻前台，FGS 启动合法）。非挂起态 no-op。
     */
    @Synchronized
    fun onAppForegrounded() {
        if (!suspendedForBackground) return
        suspendedForBackground = false
        start(currentSampleRate)
        sdk.logInfo(
            "AudioCaptureController",
            "capture resumed after foreground (capturing=${_isCapturing.value})",
        )
    }

    /**
     * 挂起与 stop 共用的完整拆除序列（原 stop() 主体）。调用方须持 @Synchronized。
     */
    private fun teardownCapture() {
        stopping = true
        // 代数自增：stop 后旧读循环的迟到错误按代作废（stopping 布尔为既有防线，
        // 代数与 switch 路径同一判别机制，双保险）
        generation++
        _isCapturing.value = false
        flushGate.reset()
        _micLevel.value = 0
        levelMeter.reset()
        readJob?.cancel()
        readJob = null
        audioRecord?.apply {
            try { stop() } catch (_: Exception) {}
            try { release() } catch (_: Exception) {}
        }
        audioRecord = null
        retryCount = 0
        closePcmDump()
        context.stopService(Intent(context, MicCaptureService::class.java))
    }

    /**
     * 补满本次 mute 的静音窗口（docs/android_audio_capture.md §3.5）：挂起可能截断
     * mute 沿后 1s 零帧补发，而服务端 turn 收尾阈值随灵敏度可变（low 900ms）——按剩余帧数
     * top-up，保证总静音 ≥50 帧（1s），与前台正常 mute"窗口跑满"等价。@Volatile 读数与
     * 读循环并发只多不少（见 docs/android_audio_capture.md §3.5）。
     */
    private fun topUpMuteFlushWindow() {
        val remaining = flushGate.remainingFrames
        if (remaining <= 0) return
        val frameSamples = currentSampleRate * 20 / 1000
        val zeroFrame = ShortArray(frameSamples)
        repeat(remaining) { sdk.pushAudioPcm(zeroFrame, 0, frameSamples, muted = true) }
        sdk.logInfo("AudioCaptureController", "mute flush window topped up: $remaining frames")
    }

    /**
     * 运行中切换采集源（docs/android_audio_capture.md §3.3）。
     *
     * 幂等短路；capturing=false 时仅记源（下次 start() 生效，含离开转录模式的归位）；
     * capturing=true 时走 [restartCaptureLocked] 冻结旧链路并立即以新源重建 AudioRecord。
     * 不停 MicCaptureService（D4）；不触碰 mute/电平/FGS 契约。
     * 新源重建失败（如个别 OEM 的 MIC 源构建异常）→ 回退原 source 再试一次，
     * 避免把原本正常工作的采集打死；回退成功需补启 FGS（死态收敛已停）。
     */
    @Synchronized
    fun switchAudioSource(source: Int) {
        if (source == currentAudioSource) return
        val previous = currentAudioSource
        currentAudioSource = source
        if (!_isCapturing.value) {
            sdk.logInfo("AudioCaptureController", "audio source recorded: $source (applies on next start)")
            return
        }
        val t0 = SystemClock.elapsedRealtime()
        when (restartCaptureLocked(revert = { currentAudioSource = previous })) {
            RebuildOutcome.OK -> sdk.logInfo(
                "AudioCaptureController",
                "audio source switched: $source in ${SystemClock.elapsedRealtime() - t0}ms",
            )
            RebuildOutcome.FELL_BACK -> sdk.logWarn(
                "AudioCaptureController",
                "audio source switch to $source failed, fell back to $previous",
            )
            RebuildOutcome.FAILED -> sdk.logWarn(
                "AudioCaptureController",
                "switchAudioSource rebuild failed: $source (fallback $previous also failed)",
            )
        }
    }

    /**
     * 选择输入设备（docs/android_audio_input_devices.md §4）。
     *
     * 幂等短路（点当前项只关窗）；capturing=false 时仅记录（下次 start 生效）；
     * capturing=true 时走 [restartCaptureLocked] 立即重建生效。
     * 设备不可用（解析不到/平台拒绝/调用异常）在 startInternal 内静默回落默认并清选择（D4），
     * 不算重建失败；重建本身失败才恢复 previous 再试（同 switchAudioSource 的 D7 回退）。
     */
    @Synchronized
    fun setPreferredInputDevice(deviceId: Int?) {
        if (deviceId == _preferredInputDeviceId.value) return
        val previous = _preferredInputDeviceId.value
        _preferredInputDeviceId.value = deviceId
        if (!_isCapturing.value) {
            sdk.logInfo(
                "AudioCaptureController",
                "preferred device recorded: $deviceId (applies on next start)",
            )
            return
        }
        val t0 = SystemClock.elapsedRealtime()
        when (restartCaptureLocked(revert = { _preferredInputDeviceId.value = previous })) {
            RebuildOutcome.OK -> sdk.logInfo(
                "AudioCaptureController",
                "preferred device switched: $deviceId in ${SystemClock.elapsedRealtime() - t0}ms",
            )
            RebuildOutcome.FELL_BACK -> sdk.logWarn(
                "AudioCaptureController",
                "preferred device switch to $deviceId failed, fell back to $previous",
            )
            RebuildOutcome.FAILED -> sdk.logWarn(
                "AudioCaptureController",
                "setPreferredInputDevice rebuild failed: $deviceId (fallback $previous also failed)",
            )
        }
    }

    private enum class RebuildOutcome { OK, FELL_BACK, FAILED }

    /**
     * 冻结旧链路并立即重建（switchAudioSource / setPreferredInputDevice 共用的重建序列）。
     * 不停 FGS，不重置 mute/电平：新读循环以 _isMuted 为准重建基线。
     * 首次重建失败（handleReadError 已收敛到死态并停 FGS）→ 执行 [revert] 恢复前一参数再重建一次；
     * 回退成功补启 FGS，恢复"采集运行中必有 FGS"（挂起态例外见 docs/android_audio_capture.md §3.6）。
     * 调用方须持 @Synchronized（本方法自身不加锁）。
     */
    private fun restartCaptureLocked(revert: () -> Unit): RebuildOutcome {
        generation++
        stopping = true
        _isCapturing.value = false
        readJob?.cancel()
        readJob = null
        audioRecord?.apply {
            try { stop() } catch (_: Exception) {}
            try { release() } catch (_: Exception) {}
        }
        audioRecord = null
        retryCount = 0
        stopping = false
        startInternal()
        if (_isCapturing.value) return RebuildOutcome.OK
        // 新参数重建失败（死态收敛已停 FGS）→ 回退原参数再试一次
        revert()
        retryCount = 0
        startInternal()
        if (_isCapturing.value) {
            startMicForegroundService()
            return RebuildOutcome.FELL_BACK
        }
        return RebuildOutcome.FAILED
    }

    /** 解析选中 id → 当前输入设备（设备连接期间 id 作匹配键，§5.4）。 */
    private fun resolveInputDevice(id: Int): AudioDeviceInfo? =
        audioManager.getDevices(AudioManager.GET_DEVICES_INPUTS).firstOrNull { it.id == id }

    /**
     * MicCaptureService.onDestroy 自愈入口（docs/android_background_support.md §5.4）：
     * onDestroy 仅系统回收这一罕见路径会走到这里（正常 stop 已先清标志，不触发）；
     * 若仍在录则重启 FGS 保持豁免。app 在后台时重启落入 catch 静默——不承诺后台
     * 自愈，仅防误停兜底。1s 冷却防"豁免窗口内 startForeground 失败→stopSelf→
     * onDestroy→再重启"热循环。
     */
    fun onMicServiceDestroyed() {
        if (!_isCapturing.value) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastMicServiceRestartMs < MIC_SERVICE_RESTART_COOLDOWN_MS) return
        lastMicServiceRestartMs = now
        startMicForegroundService()
    }

    /**
     * 为本次采集代换新 dump 实例（一实例=一文件；旧实例在 IO 线程收敛，不阻塞调用线程）。
     * 关闭态（[PcmDump.ENABLED]=false 或外部存储缺失）返回 null。见 docs/android_audio_capture.md §3.7。
     * 仅在 @Synchronized 的 startInternal 内调用。
     */
    private fun rotatePcmDump(): PcmDump? {
        val previous = pcmDump
        val next = if (PcmDump.ENABLED) {
            context.getExternalFilesDir(null)?.let { ext ->
                PcmDump(ext.resolve(PcmDump.SUBDIR), "s${currentAudioSource}_r${currentSampleRate}")
                    .also {
                        if (it.file != null) {
                            sdk.logDebug("AudioCaptureController", "pcm dump open: ${it.file?.name}")
                        } else {
                            sdk.logWarn("AudioCaptureController", "pcm dump unavailable (external storage)")
                        }
                    }
            }
        } else {
            null
        }
        pcmDump = next
        if (previous != null) {
            CoroutineScope(Dispatchers.IO).launch { previous.close() }
        }
        return next
    }

    /**
     * 关闭当前 PCM dump 并记录落盘量。采集的两条退出路径（`stop()` 与读错误死态）都必须走这里——
     * 漏掉任一条会让尾帧不落盘、无法用日志核对本次采集是否完整。
     */
    private fun closePcmDump() {
        val dump = pcmDump ?: return
        pcmDump = null
        val dropped = dump.close()
        sdk.logInfo(
            "AudioCaptureController",
            "pcm dump closed: ${dump.file?.name} ${dump.bytesWritten / 1024}KB dropped=$dropped capped=${dump.isCapped}",
        )
    }

    private fun startMicForegroundService() {
        try {
            context.startForegroundService(Intent(context, MicCaptureService::class.java))
        } catch (_: Exception) {
            // 此处仅能接到同步抛出的后台启动限制 ForegroundServiceStartNotAllowedException
            // （API 31+）；while-in-use 失效 SecurityException 抛在 MicCaptureService
            // onStartCommand 侧（此 catch 接不到），由该侧 catch→stopSelf 降级（§4.1）。
            // FGS 是保护/豁免而非功能必需，录音继续，仅降级
        }
    }

    fun setMuted(muted: Boolean) {
        // 仅更新状态;mute 沿由读帧循环在 flushGate.decide 内检测(避免跨线程状态机竞态)
        _isMuted.value = muted
    }

    /**
     * 读错误收敛。gen = 出错 read 循环启动时捕获的代数（D3）：
     * 代数不匹配（stop/switch 后旧循环尸体的迟到错误）→ 静默 no-op，
     * 不误释放新 record、不复活采集；匹配则走既有释放→重试≤3→死态停 FGS。
     * internal 供单测直呼验证代数守卫语义。
     */
    @Synchronized
    internal fun handleReadError(e: Exception, gen: Int) {
        if (gen != generation) return
        audioRecord?.apply {
            try { stop() } catch (_: Exception) {}
            try { release() } catch (_: Exception) {}
        }
        audioRecord = null

        retryCount++
        if (retryCount <= 3 && !stopping) {
            startInternal()
        } else {
            _isCapturing.value = false
            _micLevel.value = 0
            levelMeter.reset()
            readJob?.cancel()
            readJob = null
            retryCount = 0
            closePcmDump()
            // 死态必须可观测：此前该路径全静默（事故排查无锚点，docs/android_background_support.md §4.1）；
            // 恢复依赖回前台自愈（ConversationCoordinator）
            sdk.logInfo("AudioCaptureController", "capture dead: read retries exhausted (FGS stopped)")
            // 录音已死（_isCapturing=false）；若不停 FGS 则孤儿化——"正在使用麦克风"
            // 通知永久残留至进程死亡
            context.stopService(Intent(context, MicCaptureService::class.java))
        }
    }
}
