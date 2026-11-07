package dev.livachat.app.media

import android.Manifest
import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.SystemClock
import dev.livachat.sdk.LivaChatSDK
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.shadows.AudioDeviceInfoBuilder

/**
 * docs/android_audio_capture.md §7.1 Controller 用例 1-7：
 * switchAudioSource / 幂等 / 代数守卫 / 构建失败当前代 / STATE 校验 / 连续 switch / 重建失败死态。
 *
 * Robolectric + AudioRecordFactory seam（fake record），不触碰 android 运行时采集；
 * FGS 启动经 context.startForegroundService（main looper 暂停，服务体不执行）。
 */
@RunWith(RobolectricTestRunner::class)
class AudioCaptureControllerSourceTest {

    private lateinit var context: Context
    private lateinit var controller: AudioCaptureController
    private lateinit var audioManager: AudioManager

    // factory 每成功 create 追加 (source, record)；createdBySource 计数同 key；records 保序（设备应用断言用）
    private val createdBySource = mutableMapOf<Int, Int>()
    private val records = mutableListOf<AudioRecord>()
    private var factoryCalls = 0
    private var throwWhen: ((source: Int, attempt: Int) -> Throwable?)? = null
    private var stateOverride: Int? = null
    // 输入设备偏好桩开关：preferredThrows = 新建 record 的 setPreferredDevice 抛异常（§7.1 ⑪）；
    // preferredFails = 返回 false（§5.4 三类失败之"平台拒绝"）
    private var preferredThrows = false
    private var preferredFails = false

    private fun fakeRecord(): AudioRecord {
        val rec = mockk<AudioRecord>(relaxed = true)
        every { rec.state } answers { stateOverride ?: AudioRecord.STATE_INITIALIZED }
        every { rec.startRecording() } returns Unit
        every { rec.stop() } returns Unit
        every { rec.release() } returns Unit
        // 稳态帧流（3ms/帧）；测试终止由 stop()/switch 冻结 + cancel 兜底
        every { rec.read(any<ShortArray>(), any(), any()) } answers { SystemClock.sleep(3); 160 }
        // relaxed mockk 布尔返回默认 false——必须显式 stub，否则会误命中"返回 false → 清选择"分支
        if (preferredThrows) {
            every { rec.setPreferredDevice(any()) } throws RuntimeException("preferred boom")
        } else if (preferredFails) {
            every { rec.setPreferredDevice(any()) } returns false
        } else {
            every { rec.setPreferredDevice(any()) } returns true
        }
        records.add(rec)
        return rec
    }

    @Before
    fun setUp() {
        context = org.robolectric.RuntimeEnvironment.getApplication()
        // start() 权限门禁：Robolectric 默认未授予运行时权限
        shadowOf(context as android.app.Application).grantPermissions(Manifest.permission.RECORD_AUDIO)
        audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        controller = AudioCaptureController(mockk(relaxed = true), context)
        controller.audioRecordFactory = AudioRecordFactory { source, sampleRate, frameSamples ->
            factoryCalls++
            val failure = throwWhen?.invoke(source, factoryCalls)
            if (failure != null) throw failure
            val rec = fakeRecord()
            createdBySource[source] = (createdBySource[source] ?: 0) + 1
            rec
        }
    }

    @After
    fun tearDown() {
        if (controller.isCapturing.value) controller.stop()
    }

    private fun awaitCapturing(expected: Boolean, timeoutMs: Long = 3000) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline && controller.isCapturing.value != expected) {
            SystemClock.sleep(10)
        }
    }

    private fun awaitFactoryCalls(count: Int, timeoutMs: Long = 3000) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline && factoryCalls < count) {
            SystemClock.sleep(10)
        }
    }

    private val voice = MediaRecorder.AudioSource.VOICE_COMMUNICATION
    private val mic = MediaRecorder.AudioSource.MIC

    // ── 1. switch：运行中重建为新源；未运行仅记源 ─────────────────

    @Test
    fun `switch while capturing rebuilds with MIC source`() {
        controller.start()
        awaitCapturing(true)
        assertEquals(1, createdBySource[voice])

        controller.switchAudioSource(mic)
        awaitFactoryCalls(2)
        awaitCapturing(true)

        assertEquals(1, createdBySource[mic])
        assertTrue(controller.isCapturing.value)
    }

    @Test
    fun `switch while not capturing only records source for next start`() {
        controller.start()
        awaitCapturing(true)
        controller.stop()
        awaitCapturing(false)

        controller.switchAudioSource(mic)
        assertEquals(1, factoryCalls)  // 未重建

        controller.start()
        awaitCapturing(true)
        assertEquals(1, createdBySource[mic])
        controller.stop()
    }

    // ── 2. 幂等短路 ─────────────────────────────────────────────

    @Test
    fun `switch to same source is no-op`() {
        controller.start()
        awaitCapturing(true)

        controller.switchAudioSource(voice)

        assertEquals(1, factoryCalls)
        assertTrue(controller.isCapturing.value)
    }

    // ── 3. 代数守卫：旧代错误 no-op ──────────────────────────────

    @Test
    fun `stale generation read error is ignored`() {
        controller.start()
        awaitCapturing(true)

        controller.switchAudioSource(mic)
        awaitFactoryCalls(2)
        awaitCapturing(true)

        // 旧代（switch 前首循环捕获 gen=0）迟到读错误 → no-op：不重建、采集保持
        controller.handleReadError(RuntimeException("stale"), gen = 0)

        assertEquals(2, factoryCalls)
        assertTrue(controller.isCapturing.value)
    }

    @Test
    fun `current generation read error triggers retry`() {
        controller.start()
        awaitCapturing(true)

        // 当前代（无 stop/switch → 首循环捕获代 0）→ 释放→重试
        controller.handleReadError(RuntimeException("real"), gen = 0)
        awaitFactoryCalls(2)

        assertEquals(2, factoryCalls)
        assertTrue(controller.isCapturing.value)
    }

    @Test
    fun `stop then stale error does not revive capture`() {
        controller.start()
        awaitCapturing(true)

        controller.stop()
        awaitCapturing(false)

        // stop 已 generation++：旧代 0 错误 no-op，不复活
        controller.handleReadError(RuntimeException("late after stop"), gen = 0)
        SystemClock.sleep(100)
        assertFalse(controller.isCapturing.value)
        assertEquals(1, factoryCalls)
    }

    // ── 4/5. 构建失败当前代重试 + STATE 校验 ─────────────────────

    @Test
    fun `build failure retries and recovers`() {
        throwWhen = { _, attempt -> if (attempt == 1) RuntimeException("build boom") else null }
        controller.start()
        awaitCapturing(true)

        assertEquals(2, factoryCalls)  // 首建失败 → 当前代重试成功
        assertTrue(controller.isCapturing.value)
    }

    @Test
    fun `uninitialized record state exhausts into dead state`() {
        stateOverride = AudioRecord.STATE_UNINITIALIZED
        controller.start()
        awaitCapturing(false, timeoutMs = 5000)

        assertTrue(factoryCalls >= 4)   // 初始 + 重试耗尽
        assertFalse(controller.isCapturing.value)
    }

    // ── 6/7. 连续 switch 收敛；重建失败死态 ───────────────────────

    @Test
    fun `rapid consecutive switches converge to last source`() {
        controller.start()
        awaitCapturing(true)

        controller.switchAudioSource(mic)
        controller.switchAudioSource(voice)
        controller.switchAudioSource(mic)
        awaitFactoryCalls(4)
        awaitCapturing(true)

        // voice → mic → voice → mic：4 次 create，最终 MIC
        assertEquals(4, factoryCalls)
        assertEquals(2, createdBySource[mic])
        assertEquals(2, createdBySource[voice])
    }

    @Test
    fun `mic rebuild failure falls back to previous voice source`() {
        controller.start()
        awaitCapturing(true)
        val voiceCreatesBefore = createdBySource[voice] ?: 0

        // MIC 构建总失败 → 回退原 source（VOICE）再试，采集保持存活
        throwWhen = { source, _ -> if (source == mic) RuntimeException("mic build boom") else null }
        controller.switchAudioSource(mic)
        awaitFactoryCalls(voiceCreatesBefore + 2)
        awaitCapturing(true)

        assertTrue(controller.isCapturing.value)
        assertEquals(0, createdBySource[mic] ?: 0)                // MIC 从未建成
        assertTrue((createdBySource[voice] ?: 0) > voiceCreatesBefore)  // 回退重建成功
    }

    @Test
    fun `mic and voice rebuild failures end in dead state`() {
        controller.start()
        awaitCapturing(true)

        // 新源与回退源都失败 → 才走既有死态（停 FGS 由 handleReadError 收敛负责）
        throwWhen = { _, _ -> RuntimeException("build boom") }
        controller.switchAudioSource(mic)
        awaitCapturing(false, timeoutMs = 5000)

        assertFalse(controller.isCapturing.value)
    }

    // ── 8. 输入设备选择（docs/android_audio_input_devices.md §7.1 ④-⑦⑪⑫）────────────

    /**
     * Robolectric 构造的 device id 恒为 0（builder 源码 mId=0，无法设置）→ 测试以 deviceId=0 选中。
     * 类型取可往返的 WIRED_HEADSET（TYPE_BUILTIN_MIC 经 shadow 往返为 TYPE_UNKNOWN，见 AudioDeviceManagerTest 备注）。
     */
    private fun selectableInput(): AudioDeviceInfo =
        AudioDeviceInfoBuilder.newBuilder().setType(AudioDeviceInfo.TYPE_WIRED_HEADSET).build()

    @Test
    fun `select device while not capturing only records and applies on next start`() {
        shadowOf(audioManager).setInputDevices(listOf(selectableInput()))

        controller.setPreferredInputDevice(0)
        assertEquals(0, factoryCalls)   // 未采集不重建
        assertEquals(0, controller.preferredInputDeviceId.value)

        controller.start()
        awaitCapturing(true)

        assertEquals(1, factoryCalls)
        verify { records.last().setPreferredDevice(any()) }
    }

    @Test
    fun `select device while capturing rebuilds and applies to new record`() {
        shadowOf(audioManager).setInputDevices(listOf(selectableInput()))
        controller.start()
        awaitCapturing(true)
        assertEquals(1, factoryCalls)

        controller.setPreferredInputDevice(0)
        awaitFactoryCalls(2)
        awaitCapturing(true)

        assertEquals(2, factoryCalls)
        verify { records.last().setPreferredDevice(any()) }
        assertEquals(0, controller.preferredInputDeviceId.value)
    }

    @Test
    fun `selecting current device id again is a no-op`() {
        shadowOf(audioManager).setInputDevices(listOf(selectableInput()))
        controller.start()
        awaitCapturing(true)
        controller.setPreferredInputDevice(0)
        awaitFactoryCalls(2)
        awaitCapturing(true)

        controller.setPreferredInputDevice(0)   // 同 id 重复选择（D1：点当前项只关窗）
        SystemClock.sleep(100)

        assertEquals(2, factoryCalls)           // 幂等短路：不触发第三次重建
        assertEquals(0, controller.preferredInputDeviceId.value)
    }

    @Test
    fun `unresolvable device id falls back to default and clears selection`() {
        // shadow 无任何输入设备：id 解析不到 → 正常回落，不算重建失败
        controller.start()
        awaitCapturing(true)

        controller.setPreferredInputDevice(123)
        awaitFactoryCalls(2)
        awaitCapturing(true)

        assertTrue(controller.isCapturing.value)
        assertNull(controller.preferredInputDeviceId.value)
        verify(exactly = 0) { records.last().setPreferredDevice(any()) }
    }

    @Test
    fun `switching audio source preserves preferred device`() {
        shadowOf(audioManager).setInputDevices(listOf(selectableInput()))
        controller.start()
        awaitCapturing(true)
        controller.setPreferredInputDevice(0)
        awaitFactoryCalls(2)
        awaitCapturing(true)

        controller.switchAudioSource(mic)
        awaitFactoryCalls(3)
        awaitCapturing(true)

        verify { records.last().setPreferredDevice(any()) }
        assertEquals(0, controller.preferredInputDeviceId.value)
    }

    @Test
    fun `throwing setPreferredDevice falls back silently without killing capture`() {
        shadowOf(audioManager).setInputDevices(listOf(selectableInput()))
        controller.start()
        awaitCapturing(true)
        preferredThrows = true   // 之后新建的 record 调用即抛

        controller.setPreferredInputDevice(0)
        awaitFactoryCalls(2)
        awaitCapturing(true)

        // runCatching 收敛：异常不进 handleReadError 重试路径 → 无第三次 create、采集存活、选择清空
        assertTrue(controller.isCapturing.value)
        assertEquals(2, factoryCalls)
        assertNull(controller.preferredInputDeviceId.value)
    }

    @Test
    fun `platform rejecting preferred device falls back silently`() {
        shadowOf(audioManager).setInputDevices(listOf(selectableInput()))
        controller.start()
        awaitCapturing(true)
        preferredFails = true   // 之后新建的 record：setPreferredDevice 返回 false（平台拒绝）

        controller.setPreferredInputDevice(0)
        awaitFactoryCalls(2)
        awaitCapturing(true)

        // 被调用但遭拒 → 同义静默回落：采集存活、无第三次 create、选择清空
        assertTrue(controller.isCapturing.value)
        assertEquals(2, factoryCalls)
        assertNull(controller.preferredInputDeviceId.value)
        verify { records.last().setPreferredDevice(any()) }
    }

    @Test
    fun `preferred device is reapplied after background suspend and foreground resume`() {
        shadowOf(audioManager).setInputDevices(listOf(selectableInput()))
        controller.start()
        awaitCapturing(true)
        controller.setPreferredInputDevice(0)
        awaitFactoryCalls(2)
        awaitCapturing(true)

        controller.setMuted(true)
        controller.onAppBackgrounded()
        awaitCapturing(false)

        controller.onAppForegrounded()
        awaitCapturing(true)

        // 第三条 record（挂起恢复重建）同样携带 preferred device
        assertEquals(3, factoryCalls)
        verify { records.last().setPreferredDevice(any()) }
        assertEquals(0, controller.preferredInputDeviceId.value)
    }
}
