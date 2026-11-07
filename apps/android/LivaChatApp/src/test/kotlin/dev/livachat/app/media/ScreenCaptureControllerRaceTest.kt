package dev.livachat.app.media

import android.content.Context
import android.content.Intent
import dev.livachat.sdk.LivaChatSDK
import io.mockk.mockk
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows
import org.robolectric.shadows.ShadowApplication

/**
 * stop→start 快速交替竞态的状态机测试（docs/android_screen_recording.md §4.1）：
 * stop() 的 stopService 与 start() 的 startForegroundService 是独立 AMS 调用，
 * 乱序时旧 service 回调可能误扰新一轮录制——controller 以"代次 + onDestroy 自愈"
 * 保证：正常停录不复活、录制意图仍在时 service 被回收会自愈重启。
 */
@RunWith(RobolectricTestRunner::class)
class ScreenCaptureControllerRaceTest {

    private lateinit var context: Context
    private lateinit var controller: ScreenCaptureController

    private val shadowApp: ShadowApplication
        get() = Shadows.shadowOf(RuntimeEnvironment.getApplication())

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        controller = ScreenCaptureController(
            mockk<LivaChatSDK>(relaxed = true),
            context,
            dagger.Lazy { mockk<ScreenCaptureBubble>(relaxed = true) },
            mockk<CompositeLivaChatListener>(relaxed = true),
            CaptureTurnCounter(),
        )
        // 清空历史启动记录（getNextStartedService pop 语义）
        while (shadowApp.nextStartedService != null) {
        }
    }

    @After
    fun tearDown() {
        controller.stop()
    }

    /** 弹出并统计所有 startForegroundService 调用，校验最近一次指向 ScreenCaptureService。 */
    private fun consumeStartedServices(expectedTotal: Int): List<Intent> {
        val intents = mutableListOf<Intent>()
        while (true) {
            val next = shadowApp.nextStartedService ?: break
            intents += next
        }
        assertEquals(expectedTotal, intents.size)
        if (expectedTotal > 0) {
            val last = intents.last()
            assertEquals(
                "unexpected component",
                ScreenCaptureService::class.java.name,
                last.component?.className,
            )
        }
        return intents
    }

    // ── 场景 2：ready 前 service 被收掉 → token 不残留、后续 start 不被卡死 ──

    @Test
    fun `rapid stop then start is not blocked by stale consent`() {
        controller.start(1, Intent())
        controller.stop()

        // 若旧 token 残留（service 从未回调 ready），此处会因 pendingToken != null 早退
        controller.start(2, Intent())

        consumeStartedServices(2)
    }

    // ── 空 token ready（stop 的 stopService 与 service 启动乱序后）→ stopService 兜底 ──

    @Test
    fun `ready after stop sends fallback stopService`() {
        controller.start(1, Intent())
        controller.stop()
        while (shadowApp.nextStoppedService != null) {
        } // 消费 stop() 本身发出的 stopService 记录

        // 乱序场景：stopService 先被 AMS 消费（service 未启动时空操作），
        // service 随后仍前台化并回调 ready——此时 token 已空，需兜底收掉 service
        controller.onForegroundServiceReady(1)
        Thread.sleep(150) // ready 经 handler 线程执行，兜底 stopService 异步

        assertNotNull("fallback stopService expected", shadowApp.nextStoppedService)
    }

    // ── 场景 1+2：service 被回收而录制意图仍在 → 自愈重启 FGS，而非误停 ──

    @Test
    fun `service destroyed while consent pending restarts foreground service`() {
        controller.start(1, Intent())

        controller.onForegroundServiceDestroyed()

        consumeStartedServices(2) // 原启动 + 自愈重启
        assertFalse(controller.isRecording.value)
    }

    // ── 干净停止后 onDestroy 不自愈 ─────────────────────────────────

    @Test
    fun `destroyed after clean stop does not restart`() {
        controller.start(1, Intent())
        controller.stop()

        controller.onForegroundServiceDestroyed()

        consumeStartedServices(1) // 仅最初一次启动，无自愈
    }

    // ── 代次校验：旧代次 ready 不消费新 token ───────────────────────

    @Test
    fun `stale generation ready does not consume pending token`() {
        controller.start(1, Intent()) // gen=1

        // 竞态：迟到的旧 service 实例 ready（代次不匹配）
        controller.onForegroundServiceReady(2)
        Thread.sleep(150) // 等待 handler 线程消费 post

        // 未被错误消费 → 未触发 capture 尝试（无启动失败错误上报）
        val error = runBlocking {
            withTimeoutOrNull(300) { controller.startErrors.first() }
        }
        assertNull("stale ready must not start capture", error)
        assertFalse(controller.isRecording.value)

        // 新一轮 start 仍可用（token 未被旧回调吞掉后残留）
        controller.stop()
        controller.start(3, Intent())
        consumeStartedServices(2)
    }
}
