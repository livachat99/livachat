package dev.livachat.app.ui.screens.conversation

import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.domain.model.ChatMessage
import io.mockk.coVerify
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * fd_pipeline_transcribe_mode.md §10 用例 15：onSessionChanged 回写、
 * 模式标记落库（D4=B，后缀改展示时拼接）、translate 的 mark 不受影响、clearSession 复位。
 */
class ConversationListenerAdapterTranscribeTest {

    @Before
    fun setUp() {
        // adapter 的持久化 scope 跑在 Dispatchers.Main（appendMessage 入仓库）
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun onSessionChanged_transcribe_sets_transcribe_mode() {
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        adapter.onSessionChanged("transcribe", null, null, null)
        assertTrue(adapter.transcribeMode.value)
        assertFalse(adapter.translateMode.value)
        assertFalse(adapter.lectureMode.value)
    }

    @Test
    fun onSessionChanged_chat_clears_transcribe_mode() {
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        adapter.onSessionChanged("transcribe", null, null, null)
        assertTrue(adapter.transcribeMode.value)

        adapter.onSessionChanged("chat", null, null, null)
        assertFalse(adapter.transcribeMode.value)
        assertFalse(adapter.translateMode.value)
    }

    @Test
    fun onSessionChanged_translate_leaves_transcribe_off() {
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        adapter.onSessionChanged("translate", "zh", "en", "dialogue")
        assertTrue(adapter.translateMode.value)
        assertFalse(adapter.lectureMode.value)
        assertFalse(adapter.transcribeMode.value)
    }

    @Test
    fun onTranscription_final_in_transcribe_appends_transcribe_suffix() {
        val chatRepo = mockk<ChatRepository>(relaxed = true)
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.setSessionId("s1")
        adapter.setTranscribeMode(true)
        adapter.onTranscription("现场会议内容", true, "t9")

        val message = slot<ChatMessage>()
        runBlocking { coVerify { chatRepo.appendMessage(capture(message)) } }
        // D4=B：正文原样落库，后缀不再拼入 text（展示时由 MessageBubble 按语言拼）
        assertEquals("现场会议内容", message.captured.text)
        assertEquals(ChatMessage.MODE_TRANSCRIBE, message.captured.mode)
        assertTrue(adapter.transcribeMode.value)
    }

    @Test
    fun onTranscription_final_in_translate_still_appends_original_suffix() {
        val chatRepo = mockk<ChatRepository>(relaxed = true)
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.setSessionId("s1")
        adapter.setTranscribeMode(false)
        adapter.setTranslateMode(true)
        adapter.onTranscription("你好", true, "t8")

        val message = slot<ChatMessage>()
        runBlocking { coVerify { chatRepo.appendMessage(capture(message)) } }
        assertEquals("你好", message.captured.text)
        assertEquals(ChatMessage.MODE_TRANSLATE, message.captured.mode)
    }

    @Test
    fun clearSession_resets_transcribe_mode() {
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        adapter.setSessionId("s1")
        adapter.onSessionChanged("transcribe", null, null, null)
        assertTrue(adapter.transcribeMode.value)

        adapter.clearSession()
        assertFalse(adapter.transcribeMode.value)
    }

    // ── §6.9 失败回落（pending 窗口）────────────────────────────────

    @Test
    fun error_before_echo_rolls_back_transcribe() {
        // 服务端 1009 拒绝（专属失败码；fatal=false 映射 NetworkError 不影响判定）→ 回落
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        adapter.setTranscribeMode(true)  // 乐观置位
        adapter.markModeChangePending("transcribe")

        adapter.onError(code = 1009, message = "invalid mode: transcribe", fatal = false, recoverable = false)

        assertFalse(adapter.transcribeMode.value)
    }

    @Test
    fun unrelated_error_in_pending_window_keeps_mode_and_echo_resolves() {
        // 非 1009 错误与本次切换无关：不回落、不清 pending
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        adapter.setTranscribeMode(true)  // 乐观置位
        adapter.markModeChangePending("transcribe")

        adapter.onError(code = 1001, message = "ASR unavailable", fatal = false, recoverable = false)
        assertTrue(adapter.transcribeMode.value)  // 不回落

        // pending 仍存活：随后真正的拒绝帧（1009）仍能触发回落
        adapter.onError(code = 1009, message = "invalid mode: transcribe", fatal = false, recoverable = false)
        assertFalse(adapter.transcribeMode.value)

        // 回声路径不受影响：无关错误后回声照常结算并清 pending（1009 随之变为 no-op）
        val adapter2 = ConversationListenerAdapter(mockk(relaxed = true))
        adapter2.setTranscribeMode(true)
        adapter2.markModeChangePending("transcribe")
        adapter2.onError(code = 1001, message = "ASR unavailable", fatal = false, recoverable = false)
        adapter2.onSessionChanged("transcribe", null, null, null)
        assertTrue(adapter2.transcribeMode.value)
        adapter2.onError(code = 1009, message = "invalid mode: transcribe", fatal = false, recoverable = false)
        assertTrue(adapter2.transcribeMode.value)
    }

    @Test
    fun session_change_failed_without_pending_only_emits_banner() {
        // 无 pending（回声已结算）时收到 1009：仅出 banner，不动模式开关
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        adapter.onSessionChanged("transcribe", null, null, null)
        assertTrue(adapter.transcribeMode.value)

        adapter.onError(code = 1009, message = "invalid mode: transcribe", fatal = false, recoverable = false)

        assertTrue(adapter.transcribeMode.value)
    }

    @Test
    fun error_after_echo_keeps_confirmed_transcribe() {
        // 回声已确认后发生无关错误 → 不误伤（服务端已进入 transcribe）
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        adapter.onSessionChanged("transcribe", null, null, null)
        assertTrue(adapter.transcribeMode.value)

        adapter.onError(code = 1001, message = "ASR unavailable", fatal = false, recoverable = false)

        assertTrue(adapter.transcribeMode.value)
    }

    @Test
    fun session_changed_clears_pending() {
        // 回声到达 → pending 清除 → 后续错误不再回落
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        adapter.setTranscribeMode(true)
        adapter.markModeChangePending("transcribe")
        adapter.onSessionChanged("transcribe", null, null, null)

        adapter.onError(code = 1001, message = "ASR unavailable", fatal = false, recoverable = false)

        assertTrue(adapter.transcribeMode.value)
    }

    // ── §4.6.4 新会话模式复位 ────────────────────────────────────────

    @Test
    fun onSessionReady_resets_mode_flags() {
        // 重连/新会话：服务端把模式重置回 chat，客户端残留开关必须同步复位
        // （否则后续轮会被错误地按 transcribe 加 "(转录)" 后缀，而服务端在跑 chat + LLM）
        val adapter = ConversationListenerAdapter(mockk(relaxed = true))
        adapter.setTranslateMode(true)
        adapter.setLectureMode(true)
        adapter.setTranscribeMode(true)

        adapter.onSessionReady("sess-2", restored = true)

        assertFalse(adapter.translateMode.value)
        assertFalse(adapter.lectureMode.value)
        assertFalse(adapter.transcribeMode.value)
    }
}
