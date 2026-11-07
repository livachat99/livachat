package dev.livachat.app.ui.screens.history

import dev.livachat.app.R
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.domain.usecase.HistoryUseCase
import dev.livachat.app.media.ImageGallerySaver
import dev.livachat.app.testStringContext
import dev.livachat.sdk.model.AiImage
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import java.time.Instant
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/** 三期：历史详情页图片查看器 + 保存到相册（模板 ConversationViewModelImageViewerTest）。 */
class HistoryDetailViewModelTest {

    private val dispatcher: TestDispatcher = UnconfinedTestDispatcher()

    private lateinit var historyUseCase: HistoryUseCase
    private lateinit var saver: ImageGallerySaver
    private lateinit var viewModel: HistoryDetailViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        historyUseCase = mockk(relaxed = true)
        saver = mockk(relaxed = true)
        viewModel = HistoryDetailViewModel(
            historyUseCase = historyUseCase,
            coordinator = mockk<ConversationCoordinator>(relaxed = true),
            imageGallerySaver = saver,
            appContext = testStringContext(),
        )
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun imageMessage(id: String, seq: Int = 0) = ChatMessage(
        id = id, sessionId = "s1", role = MessageRole.AI, text = "图片 $id",
        isFinal = true, seq = seq, timestamp = Instant.now(),
        image = AiImage(data = "x", mime = "image/jpeg", width = 1, height = 1),
    )

    private fun loadWith(vararg msgs: ChatMessage) {
        coEvery { historyUseCase.getSessionDetail("s1") } returns (null to msgs.toList())
        viewModel.loadSession("s1")
    }

    // ── 查看器开关 ──────────────────────────────────────────────

    @Test
    fun imageViewerMessageId_defaults_to_null() {
        assertNull(viewModel.imageViewerMessageId.value)
    }

    @Test
    fun openImageViewer_sets_message_id() {
        viewModel.openImageViewer("ai-1")
        assertEquals("ai-1", viewModel.imageViewerMessageId.value)
    }

    @Test
    fun closeImageViewer_clears_message_id() {
        viewModel.openImageViewer("ai-1")
        viewModel.closeImageViewer()
        assertNull(viewModel.imageViewerMessageId.value)
    }

    // ── 保存到相册 ──────────────────────────────────────────────

    @Test
    fun saveViewerImage_success_showsTip_andClearsAfterTtl() {
        loadWith(imageMessage("ai-1"))
        coEvery { saver.save(any()) } returns true

        viewModel.saveViewerImage("ai-1")

        assertEquals("res:${R.string.common_saved_to_gallery}", viewModel.imageSaveTip.value)
        dispatcher.scheduler.advanceTimeBy(2_001)
        assertNull(viewModel.imageSaveTip.value)
    }

    @Test
    fun saveViewerImage_failure_showsFailureTip() {
        loadWith(imageMessage("ai-1"))
        coEvery { saver.save(any()) } returns false

        viewModel.saveViewerImage("ai-1")

        assertEquals("res:${R.string.common_save_failed}", viewModel.imageSaveTip.value)
    }

    @Test
    fun saveViewerImage_saverThrows_collapsesToFailureTip_notCrash() {
        loadWith(imageMessage("ai-1"))
        coEvery { saver.save(any()) } throws RuntimeException("boom")

        viewModel.saveViewerImage("ai-1")

        assertEquals("违约异常须收敛为失败提示", "res:${R.string.common_save_failed}", viewModel.imageSaveTip.value)
        // finally 已复位重入 flag：再次点击仍能进入保存（共两次调用）
        viewModel.saveViewerImage("ai-1")
        coVerify(exactly = 2) { saver.save(any()) }
    }

    @Test
    fun saveViewerImage_unknownId_noSaverCall_noTip() {
        loadWith(imageMessage("ai-1"))

        viewModel.saveViewerImage("ai-999")

        coVerify(exactly = 0) { saver.save(any()) }
        assertNull(viewModel.imageSaveTip.value)
    }

    @Test
    fun saveViewerImage_reentryDuringInFlight_secondClickIgnored() {
        loadWith(imageMessage("ai-1"))
        val gate = CompletableDeferred<Boolean>()
        coEvery { saver.save(any()) } coAnswers { gate.await() }

        viewModel.saveViewerImage("ai-1")
        viewModel.saveViewerImage("ai-1")
        gate.complete(true)
        dispatcher.scheduler.advanceUntilIdle()

        coVerify(exactly = 1) { saver.save(any()) }
    }

    @Test
    fun consecutiveSaves_secondTipSurvivesFullTtl() {
        loadWith(imageMessage("ai-1"), imageMessage("ai-2", seq = 1))
        coEvery { saver.save(any()) } returns true

        viewModel.saveViewerImage("ai-1")               // t=0：提示 A（定时器 2000 到期）
        dispatcher.scheduler.advanceTimeBy(500)
        viewModel.saveViewerImage("ai-2")               // t=500：提示 B（同文案），A 的定时器须被取消
        dispatcher.scheduler.advanceTimeBy(1_700)       // t=2200：越过 A 的到期点

        assertEquals(
            "第二次保存的提示不得被第一次的定时器提前清掉",
            "res:${R.string.common_saved_to_gallery}", viewModel.imageSaveTip.value,
        )

        dispatcher.scheduler.advanceTimeBy(400)         // t=2600：B 的到期点（2500）之后
        assertNull(viewModel.imageSaveTip.value)
    }

    @Test
    fun savePermissionDenied_showsTip_andClearsAfterTtl() {
        viewModel.onSavePermissionDenied()

        assertEquals("res:${R.string.common_save_failed_storage}", viewModel.imageSaveTip.value)
        dispatcher.scheduler.advanceTimeBy(2_001)
        assertNull(viewModel.imageSaveTip.value)
    }

    @Test
    fun openImageViewer_clearsStaleTip() {
        loadWith(imageMessage("ai-1"))
        coEvery { saver.save(any()) } returns true
        viewModel.saveViewerImage("ai-1")
        assertEquals("res:${R.string.common_saved_to_gallery}", viewModel.imageSaveTip.value)

        viewModel.openImageViewer("ai-1")   // 2s 内重开查看器

        assertNull(viewModel.imageSaveTip.value)
    }

    // ── agent 结果查看器（§12.3.2）──────────────────────────────

    @Test
    fun agentJobViewer_defaults_null_openClose_and_loadSessionClears() {
        assertNull(viewModel.agentJobResultViewId.value)
        assertNull(viewModel.agentJobResultInitialCharOffset.value)

        loadWith(imageMessage("ai-msg_agent_job_jk1"))
        viewModel.openAgentJobResult("ai-msg_agent_job_jk1", 42)
        assertEquals("ai-msg_agent_job_jk1", viewModel.agentJobResultViewId.value)
        assertEquals(42, viewModel.agentJobResultInitialCharOffset.value)

        // 换会话重载 → 两个覆盖层都清（防悬空）
        loadWith()
        assertNull(viewModel.agentJobResultViewId.value)
        assertNull(viewModel.agentJobResultInitialCharOffset.value)
        assertNull(viewModel.imageViewerMessageId.value)

        viewModel.openAgentJobResult("ai-msg_agent_job_jk1", null)
        viewModel.closeAgentJobResult()
        assertNull(viewModel.agentJobResultViewId.value)
        assertNull(viewModel.agentJobResultInitialCharOffset.value)
    }
}
