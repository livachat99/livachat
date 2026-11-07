package dev.livachat.app.media

import android.os.Looper
import dev.livachat.sdk.LivaChatSDK
import io.mockk.mockk
import io.mockk.verify
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows
import org.robolectric.shadows.ShadowLooper

/**
 * v4 等待态接线测试（docs/android_screen_recording.md §8.1）：
 * `startWaiting`/`endWaiting` 与 `replyListener` 判据的接线（纯函数表见
 * [ScreenCaptureControllerTest]）。竞态集中在接线而非纯函数，故用 Robolectric 主
 * 线程 + `ShadowLooper` 控时。
 *
 * 说明：`finishCapture(SENT) → startWaiting()` 一行接线由真机用例覆盖——
 * `_isRecording`/`finishCapture` 保持 private，单测不触碰私有状态。
 */
@RunWith(RobolectricTestRunner::class)
class ScreenCaptureControllerWaitingTest {

    private lateinit var bubble: ScreenCaptureBubble
    private lateinit var controller: ScreenCaptureController

    private val mainLooper: ShadowLooper
        get() = Shadows.shadowOf(Looper.getMainLooper())

    @Before
    fun setUp() {
        bubble = mockk(relaxed = true)
        controller = ScreenCaptureController(
            mockk<LivaChatSDK>(relaxed = true),
            RuntimeEnvironment.getApplication(),
            dagger.Lazy { bubble },
            mockk<CompositeLivaChatListener>(relaxed = true),
            CaptureTurnCounter(),
        )
    }

    @After
    fun tearDown() {
        controller.stop()
    }

    @Test
    fun `start waiting - ring on and 15s fallback scheduled, timeout ends it`() {
        controller.startWaiting()

        assertTrue(controller.isWaitingForReply)
        verify { bubble.setWaiting(true) }

        mainLooper.idleFor(java.time.Duration.ofSeconds(15))

        assertFalse(controller.isWaitingForReply)
        verify { bubble.setWaiting(false) }
    }

    @Test
    fun `first non-agent ai_text chunk ends the wait`() {
        controller.startWaiting()

        controller.replyListener.onAiText(
            text = "你好", messageId = "msg_a", index = 0L, isFinal = false, image = null, source = null,
        )
        mainLooper.idle() // WS reader 线程 → post 回主线程

        assertFalse(controller.isWaitingForReply)
        verify { bubble.setWaiting(false) }
    }

    @Test
    fun `empty final chunk with index zero still ends the wait`() {
        // 服务端零 token 空轮：final 就是 index=0 && isFinal=true——判据不按 isFinal 过滤
        //（orchestrator.py:1120/:1180；防实现里误加 !isFinal）
        controller.startWaiting()

        controller.replyListener.onAiText(
            text = "", messageId = "msg_empty", index = 0L, isFinal = true, image = null, source = null,
        )
        mainLooper.idle()

        assertFalse(controller.isWaitingForReply)
    }

    @Test
    fun `agent push and later chunks do not end the wait`() {
        controller.startWaiting()

        // agent job 结果：恒 index=0 且 source="agent"——只能靠 source 排除
        controller.replyListener.onAiText(
            text = "任务结果", messageId = "msg_agent_job_x", index = 0L, isFinal = true,
            image = null, source = "agent",
        )
        // 旧轮在飞的第 3 片：index>0，属于被打断的那一轮
        controller.replyListener.onAiText(
            text = "续片", messageId = "msg_old", index = 3L, isFinal = false, image = null, source = null,
        )
        mainLooper.idle()

        assertTrue(controller.isWaitingForReply)
        verify(exactly = 0) { bubble.setWaiting(false) }
    }

    @Test
    fun `end waiting is idempotent - no-op before start, once per wait after timeout`() {
        controller.endWaiting("noop")
        verify(exactly = 0) { bubble.setWaiting(false) }

        controller.startWaiting()
        mainLooper.idleFor(java.time.Duration.ofSeconds(20)) // 超时触发
        mainLooper.idleFor(java.time.Duration.ofSeconds(20)) // 再推进不得重复动作

        assertFalse(controller.isWaitingForReply)
        verify(exactly = 1) { bubble.setWaiting(false) }
    }

    @Test
    fun `ai_text after timeout is a no-op`() {
        controller.startWaiting()
        mainLooper.idleFor(java.time.Duration.ofSeconds(16))
        verify(exactly = 1) { bubble.setWaiting(false) }

        controller.replyListener.onAiText(
            text = "迟到", messageId = "msg_late", index = 0L, isFinal = false, image = null, source = null,
        )
        mainLooper.idle()

        assertFalse(controller.isWaitingForReply)
        verify(exactly = 1) { bubble.setWaiting(false) }
    }
}
