package dev.livachat.app.media

import android.Manifest
import android.app.Application
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.SystemClock
import dev.livachat.sdk.LivaChatSDK
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import java.util.concurrent.atomic.AtomicInteger
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf

/**
 * docs/android_audio_capture.md §3.6：后台释放麦克风门控——
 * muted+后台→补满静音窗口后拆除+停 FGS；unmuted/前台 no-op；前台重获取重建+FGS；
 * 幂等/连续挂起恢复；stop 清标志不复活；resume 权限早退；挂起中 switch 源。
 *
 * 与 AudioCaptureControllerSourceTest 同型（Robolectric + AudioRecordFactory seam）：
 * 帧速由 fake read 的 sleep 控制；静音补发按长度区分（真实帧=read 长度 160，静音帧=frameSamples 320）。
 */
@RunWith(RobolectricTestRunner::class)
class AudioCaptureControllerBackgroundTest {

    private lateinit var application: Application
    private val sdk = mockk<LivaChatSDK>(relaxed = true)
    private lateinit var controller: AudioCaptureController

    private val silencePushes = AtomicInteger()
    private val realPushes = AtomicInteger()
    // muted 标记契约（docs/android_audio_capture.md §5.2）：静音帧必须 muted=true、真实帧必须 false
    private val mutedFlagViolations = AtomicInteger()
    private val records = mutableListOf<AudioRecord>()
    private val createdBySource = mutableMapOf<Int, Int>()
    private var factoryCalls = 0
    private var readSleepMs = 3L

    private fun fakeRecord(): AudioRecord {
        val rec = mockk<AudioRecord>(relaxed = true)
        every { rec.state } returns AudioRecord.STATE_INITIALIZED
        every { rec.read(any<ShortArray>(), any(), any()) } answers {
            SystemClock.sleep(readSleepMs)
            160
        }
        records.add(rec)
        return rec
    }

    @Before
    fun setUp() {
        application = RuntimeEnvironment.getApplication()
        shadowOf(application).grantPermissions(Manifest.permission.RECORD_AUDIO)
        every { sdk.pushAudioPcm(any(), any(), any(), any()) } answers {
            if (arg<Int>(2) == 320) {
                if (!arg<Boolean>(3)) mutedFlagViolations.incrementAndGet()  // 静音帧必须 muted
                silencePushes.incrementAndGet()
            } else {
                if (arg<Boolean>(3)) mutedFlagViolations.incrementAndGet()   // 真实帧不得 muted
                realPushes.incrementAndGet()
            }
        }
        controller = AudioCaptureController(sdk, application)
        controller.audioRecordFactory = AudioRecordFactory { source, _, _ ->
            factoryCalls++
            createdBySource[source] = (createdBySource[source] ?: 0) + 1
            fakeRecord()
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

    private fun awaitSilence(count: Int, timeoutMs: Long = 3000) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline && silencePushes.get() < count) {
            SystemClock.sleep(10)
        }
    }

    /** 等读循环已过 resetBaseline 基线：首个真实帧上行后，mute 才构成"沿"（既有契约）。 */
    private fun awaitRealFrames(count: Int, timeoutMs: Long = 3000) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline && realPushes.get() < count) {
            SystemClock.sleep(10)
        }
    }

    private fun startedServiceClass(): String? =
        shadowOf(application).nextStartedService?.component?.className

    private fun stoppedServiceClass(): String? =
        shadowOf(application).nextStoppedService?.component?.className

    private val voice = MediaRecorder.AudioSource.VOICE_COMMUNICATION

    // ── 1. 门控：unmuted / 未采集退后台 no-op ──────────────────────

    @Test
    fun `background while unmuted keeps capturing`() {
        controller.start()
        awaitCapturing(true)

        controller.onAppBackgrounded()
        SystemClock.sleep(50)

        assertTrue(controller.isCapturing.value)
        assertEquals(1, factoryCalls)
        assertNull(stoppedServiceClass())
    }

    @Test
    fun `background before start is no-op`() {
        controller.onAppBackgrounded()
        SystemClock.sleep(30)

        assertFalse(controller.isCapturing.value)
        assertEquals(0, factoryCalls)
    }

    // ── 2. muted 退后台 → 完整拆除 + 停 FGS ────────────────────────

    @Test
    fun `muted background suspends capture and stops FGS`() {
        controller.start()
        awaitCapturing(true)
        controller.setMuted(true)

        controller.onAppBackgrounded()
        awaitCapturing(false)

        assertFalse(controller.isCapturing.value)
        assertEquals(1, factoryCalls)                       // 无重建
        verify(exactly = 1) { records.first().stop() }
        verify(exactly = 1) { records.first().release() }
        assertTrue(stoppedServiceClass()?.endsWith("MicCaptureService") == true)
    }

    // ── 3. 回前台 → start() 全路径重获取 + 补启 FGS ────────────────

    @Test
    fun `foreground resumes capture and restarts FGS with mute preserved`() {
        controller.start()
        awaitCapturing(true)
        assertTrue(startedServiceClass()?.endsWith("MicCaptureService") == true)  // 首次 start 的 FGS

        controller.setMuted(true)
        controller.onAppBackgrounded()
        awaitCapturing(false)
        assertTrue(stoppedServiceClass()?.endsWith("MicCaptureService") == true)

        controller.onAppForegrounded()
        awaitCapturing(true)

        assertEquals(2, factoryCalls)
        assertEquals(2, createdBySource[voice])
        assertTrue(controller.isMuted.value)                // mute 状态跨挂起保持
        assertTrue(startedServiceClass()?.endsWith("MicCaptureService") == true)
    }

    // ── 4. 静音窗口 top-up（只多不少 ≥50 帧）───────────────────────

    @Test
    fun `suspend within flush window tops up to full silence window`() {
        readSleepMs = 25
        controller.start()
        awaitCapturing(true)
        awaitRealFrames(1)                                  // 循环跑过基线后再 mute，保证 edge 被观察
        controller.setMuted(true)
        awaitSilence(49)                                    // 窗口尾段挂起（剩余 ≤1 帧）：topUp 窗口极短

        controller.onAppBackgrounded()
        awaitCapturing(false)
        SystemClock.sleep(200)                              // 等在途帧落定

        // 不变式"只多不少"：总静音 ≥50 帧（窗口补满）。上界 52 = 50 + topUp 期间并发读循环
        // ≤1 帧（mockk 调用耗时放大该窗口；生产 push 为微秒级，j=0 → 上界 51，见 docs/android_audio_capture.md §3.5）
        // + teardown 后迟到帧 ≤1。
        val total = silencePushes.get()
        assertTrue("total silence frames = $total, want >= 50", total >= 50)
        assertTrue("total silence frames = $total, want <= 52", total <= 52)
        // muted 契约（docs/android_audio_capture.md §5.2）：补发帧必须全部带 muted=true，真实帧不得带
        assertEquals("muted-tag violations", 0, mutedFlagViolations.get())
    }

    @Test
    fun `suspend in steady mute state tops up no extra frames`() {
        readSleepMs = 25
        controller.start()
        awaitCapturing(true)
        awaitRealFrames(1)                                  // 同上：先过基线
        controller.setMuted(true)
        awaitSilence(50)                                    // 窗口耗尽，进入稳态 DROP
        val before = silencePushes.get()
        assertTrue("steady window not drained: before=$before", before >= 50)

        controller.onAppBackgrounded()
        awaitCapturing(false)
        SystemClock.sleep(200)

        val after = silencePushes.get()
        assertTrue("after=$after before=$before", after >= before)
        assertTrue("after=$after before=$before", after <= before + 1)  // 至多一帧迟到重播种，无 top-up
        assertEquals("muted-tag violations", 0, mutedFlagViolations.get())
    }

    // ── 5. stop 清标志 / 幂等 / 连续挂起恢复 ───────────────────────

    @Test
    fun `stop clears suspend flag so foreground does not revive`() {
        controller.start()
        awaitCapturing(true)
        controller.setMuted(true)
        controller.onAppBackgrounded()
        awaitCapturing(false)

        controller.stop()
        controller.onAppForegrounded()
        SystemClock.sleep(100)

        assertFalse(controller.isCapturing.value)
        assertEquals(1, factoryCalls)
    }

    @Test
    fun `double suspend is idempotent and suspend-resume cycles converge`() {
        controller.start()
        awaitCapturing(true)
        controller.setMuted(true)

        controller.onAppBackgrounded()
        awaitCapturing(false)

        controller.onAppBackgrounded()                      // 第二次：守卫 no-op
        SystemClock.sleep(50)
        assertEquals(1, factoryCalls)
        assertFalse(controller.isCapturing.value)

        controller.onAppForegrounded()
        awaitCapturing(true)
        assertEquals(2, factoryCalls)

        controller.onAppBackgrounded()                      // 恢复后仍 muted → 再次挂起
        awaitCapturing(false)
        controller.onAppForegrounded()
        awaitCapturing(true)
        assertEquals(3, factoryCalls)
    }

    // ── 6. resume 权限早退 / 挂起中 switch 源 ─────────────────────

    @Test
    fun `resume with revoked permission does not rebuild or crash`() {
        controller.start()
        awaitCapturing(true)
        controller.setMuted(true)
        controller.onAppBackgrounded()
        awaitCapturing(false)

        shadowOf(application).denyPermissions(Manifest.permission.RECORD_AUDIO)
        controller.onAppForegrounded()
        SystemClock.sleep(100)

        assertFalse(controller.isCapturing.value)
        assertEquals(1, factoryCalls)                       // 权限早退：未重建
    }

    @Test
    fun `source switch while suspended applies on resume`() {
        controller.start()
        awaitCapturing(true)
        controller.setMuted(true)
        controller.onAppBackgrounded()
        awaitCapturing(false)

        controller.switchAudioSource(MediaRecorder.AudioSource.MIC)
        assertEquals(1, factoryCalls)                       // 挂起态只记源

        controller.onAppForegrounded()
        awaitCapturing(true)
        assertEquals(1, createdBySource[MediaRecorder.AudioSource.MIC])
    }
}
