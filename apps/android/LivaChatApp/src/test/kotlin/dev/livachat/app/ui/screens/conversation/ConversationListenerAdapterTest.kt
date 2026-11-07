package dev.livachat.app.ui.screens.conversation

import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.ConnectionState
import io.mockk.mockk
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Ignore
import org.junit.Test

class ConversationListenerAdapterTest {

    private val chatRepo = mockk<ChatRepository>(relaxed = true)

    private fun job(id: String, startedAtMs: Long = 1_000L) =
        AgentJob(jobId = id, agentName = "hermes", summary = "任务 $id", startedAtMs = startedAtMs, runningTimeMs = 10L)

    /** job / transcription frames are dropped unless the adapter is CONNECTED (D6/D4 race guards). */
    private fun connectedAdapter(): ConversationListenerAdapter {
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.onConnectionStateChanged(ConnectionState.CONNECTED)
        return adapter
    }

    @Test
    fun onTranscription_nonfinal_updates_flow() {
        val adapter = connectedAdapter()
        adapter.onTranscription("Hello", false, "t1")
        assertEquals("Hello", adapter.transcription.value)
        assertFalse(adapter.isTranscriptionFinal.value)
    }

    @Ignore("Requires Dispatchers.Main setup for CoroutineScope in adapter")
    @Test
    fun onTranscription_final_clears_after_persist() {
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.setSessionId("s1")
        adapter.onTranscription("Done", true, "t2")
        assertEquals("", adapter.transcription.value)
        assertFalse(adapter.isTranscriptionFinal.value)
    }

    @Test
    fun onSessionReady_does_not_crash() {
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.onSessionReady("s1", false)
    }

    @Test
    fun onSessionReady_records_server_session_id_for_resume() {
        // 重连 resume 载体：服务端 id（sess_<18>）与本地 Room id 不同，必须单独留存
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.setSessionId("local-uuid")
        adapter.onSessionReady("sess_abcdefghij01234567", false)
        assertEquals("sess_abcdefghij01234567", adapter.serverSessionId)
    }

    @Test
    fun clearSession_clears_server_session_id() {
        // 启停/换会话后不得把旧服务端 session 当 resume 目标
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.setSessionId("local-uuid")
        adapter.onSessionReady("sess_abcdefghij01234567", false)
        adapter.clearSession()
        assertNull(adapter.serverSessionId)
    }

    @Test
    fun late_sessionReady_after_clearSession_does_not_resurrect_resume_target() {
        // End/换会话（主线程）与 in-flight session.ready（WS reader 线程）竞态：
        // 清除之后到达的 ready 不得复活旧服务端 id
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.setSessionId("local-uuid")
        adapter.onSessionReady("sess_abcdefghij01234567", false)
        adapter.clearSession()

        adapter.onSessionReady("sess_abcdefghij01234567", false)

        assertNull(adapter.serverSessionId)
    }

    @Test
    fun onSessionEnded_clears_server_session_id() {
        // 服务端已结束该会话 → 后续重连不得 resume 它
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.setSessionId("local-uuid")
        adapter.onSessionReady("sess_abcdefghij01234567", false)

        adapter.onSessionEnded("user_quit", "sess_abcdefghij01234567")

        assertNull(adapter.serverSessionId)
    }

    @Test
    fun onConnectionStateChanged_does_not_crash() {
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.onConnectionStateChanged(dev.livachat.sdk.model.ConnectionState.CONNECTED)
    }

    @Test
    fun onSessionStateChanged_does_not_crash() {
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.onSessionStateChanged(dev.livachat.sdk.model.SessionState.LISTENING)
    }

    @Test
    fun clearSession_resets_state() {
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.setSessionId("s1")
        adapter.clearSession()
    }

    // ── job status ────────────────────────────────────────────────

    @Test
    fun onJobStatus_replaces_full_list() {
        val adapter = connectedAdapter()
        adapter.onJobStatus(listOf(job("job-1"), job("job-2")))
        assertEquals(listOf("job-1", "job-2"), adapter.agentJobs.value.map { it.jobId })

        // Replace semantics: a later frame fully replaces the earlier one.
        adapter.onJobStatus(listOf(job("job-3")))
        assertEquals(listOf("job-3"), adapter.agentJobs.value.map { it.jobId })
    }

    @Test
    fun onJobStatus_empty_list_clears() {
        val adapter = connectedAdapter()
        adapter.onJobStatus(listOf(job("job-1")))
        adapter.onJobStatus(emptyList())
        assertTrue(adapter.agentJobs.value.isEmpty())
    }

    @Test
    fun onJobStatus_not_gated_by_suppressMessages() {
        val adapter = connectedAdapter()
        adapter.suppressMessages = true
        adapter.onJobStatus(listOf(job("job-1")))
        assertEquals(1, adapter.agentJobs.value.size)
    }

    @Test
    fun onJobStatus_dropped_when_not_connected() {
        // D6 race guard: a frame in flight during disconnect must not resurrect
        // a ghost list after the self-clear.
        val adapter = connectedAdapter()
        adapter.onJobStatus(listOf(job("job-1")))
        adapter.onConnectionStateChanged(ConnectionState.DISCONNECTED)
        adapter.onJobStatus(listOf(job("job-2")))
        assertTrue(adapter.agentJobs.value.isEmpty())
    }

    @Test
    fun onJobStatus_accepted_again_after_reconnect() {
        val adapter = connectedAdapter()
        adapter.onConnectionStateChanged(ConnectionState.DISCONNECTED)
        adapter.onConnectionStateChanged(ConnectionState.CONNECTED)
        adapter.onJobStatus(listOf(job("job-1")))
        assertEquals(1, adapter.agentJobs.value.size)
    }

    @Test
    fun clearSession_clears_jobs() {
        val adapter = connectedAdapter()
        adapter.onJobStatus(listOf(job("job-1")))
        adapter.clearSession()
        assertTrue(adapter.agentJobs.value.isEmpty())
    }

    @Test
    fun onSessionEnded_clears_jobs() {
        // §5: session.ended → self-clear, even if the connection stays up.
        val adapter = connectedAdapter()
        adapter.onJobStatus(listOf(job("job-1")))
        adapter.onSessionEnded("user_quit", "s1")
        assertTrue(adapter.agentJobs.value.isEmpty())
    }

    @Test
    fun onConnectionStateChanged_non_connected_clears_jobs() {
        val adapter = connectedAdapter()
        adapter.onJobStatus(listOf(job("job-1")))
        adapter.onConnectionStateChanged(ConnectionState.CONNECTING)
        assertTrue(adapter.agentJobs.value.isEmpty())
    }

    @Test
    fun onConnectionStateChanged_connected_keeps_jobs() {
        val adapter = connectedAdapter()
        adapter.onJobStatus(listOf(job("job-1")))
        adapter.onConnectionStateChanged(ConnectionState.CONNECTED)
        assertEquals(1, adapter.agentJobs.value.size)
    }

    // ── 断连清转写（方案 B）────────────────────────────────

    @Test
    fun onConnectionStateChanged_disconnected_clears_transcription() {
        val adapter = connectedAdapter()
        adapter.setSessionId("s1")
        adapter.onTranscription("partial", false, "t1")
        adapter.onAiText("译文", "m1", 0L, false, null, null)
        assertEquals("partial", adapter.transcription.value)
        assertEquals("译文", adapter.streamingAiText.value)

        adapter.onConnectionStateChanged(ConnectionState.DISCONNECTED)

        assertEquals("", adapter.transcription.value)
        assertFalse(adapter.isTranscriptionFinal.value)
        assertEquals("", adapter.streamingAiText.value)
    }

    @Test
    fun onConnectionStateChanged_reconnecting_clears_transcription() {
        val adapter = connectedAdapter()
        adapter.onTranscription("partial", false, "t1")
        adapter.onConnectionStateChanged(ConnectionState.RECONNECTING)
        assertEquals("", adapter.transcription.value)
    }

    @Test
    fun onConnectionStateChanged_connected_keeps_transcription() {
        val adapter = connectedAdapter()
        adapter.onTranscription("partial", false, "t1")
        adapter.onConnectionStateChanged(ConnectionState.CONNECTED)
        assertEquals("partial", adapter.transcription.value)
    }

    @Test
    fun onTranscription_partial_dropped_when_not_connected() {
        // D4: 断连后迟到的非 final 帧不得复活气泡
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.onTranscription("ghost", false, "t1")
        assertEquals("", adapter.transcription.value)
    }

    @Test
    fun onAiText_partial_dropped_when_not_connected() {
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.setSessionId("s1")
        adapter.onAiText("ghost", "m1", 0L, false, null, null)
        assertEquals("", adapter.streamingAiText.value)
    }

    @Test
    fun onTranscription_final_still_accepted_when_not_connected() {
        // final 放行：断连瞬间在途的最后一条仍写 flow；无 sessionId 时入史分支短路，
        // 不触发 Main 调度器依赖
        val adapter = ConversationListenerAdapter(chatRepo)
        adapter.onTranscription("done", true, "t1")
        assertEquals("done", adapter.transcription.value)
        assertTrue(adapter.isTranscriptionFinal.value)
    }
}
