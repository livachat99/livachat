package dev.livachat.app.ui.screens.conversation

import dev.livachat.app.data.repository.ChatRepository
import io.mockk.mockk
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.yield
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * 断网重连自动恢复：rememberModeRequest 跨 session.ready 存活，
 * onSessionReady 乐观置位 + emit restoreModeRequest（translate/lecture/transcribe）。
 *
 * SharedFlow 无订阅者时发射不缓存，故断言前先挂起收集再触发 ready。
 */
class ConversationListenerAdapterModeRestoreTest {

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newAdapter() = ConversationListenerAdapter(mockk(relaxed = true))

    @Test
    fun onSessionReady_restores_translate_request() {
        val adapter = newAdapter()
        val request = ModeChangeRequest("translate", "zh", "ja", "dialogue")
        adapter.rememberModeRequest(request)

        runBlocking {
            val deferred = async(start = CoroutineStart.UNDISPATCHED) { adapter.restoreModeRequest.first() }
            adapter.onSessionReady("sess-2", restored = true)

            assertTrue(adapter.translateMode.value)
            assertFalse(adapter.lectureMode.value)
            assertFalse(adapter.transcribeMode.value)
            assertEquals(request, deferred.await())
        }
    }

    @Test
    fun onSessionReady_restores_lecture_request() {
        val adapter = newAdapter()
        adapter.rememberModeRequest(ModeChangeRequest("translate", "zh", "ja", "lecture"))

        runBlocking {
            val deferred = async(start = CoroutineStart.UNDISPATCHED) { adapter.restoreModeRequest.first() }
            adapter.onSessionReady("sess-2", restored = true)

            assertTrue(adapter.translateMode.value)
            assertTrue(adapter.lectureMode.value)
            assertEquals("lecture", deferred.await().usecase)
        }
    }

    @Test
    fun onSessionReady_restores_transcribe_request_with_pending_rollback() {
        val adapter = newAdapter()
        adapter.rememberModeRequest(ModeChangeRequest("transcribe"))

        runBlocking {
            val deferred = async(start = CoroutineStart.UNDISPATCHED) { adapter.restoreModeRequest.first() }
            adapter.onSessionReady("sess-2", restored = true)

            assertTrue(adapter.transcribeMode.value)
            assertFalse(adapter.translateMode.value)
            assertEquals("transcribe", deferred.await().mode)

            // 恢复请求发出后、回声前收到 1009 拒绝 → 回落为关（pending 已武装）
            adapter.onError(code = 1009, message = "invalid mode: transcribe", fatal = false, recoverable = false)
            assertFalse(adapter.transcribeMode.value)
        }
    }

    @Test
    fun onSessionReady_restores_omni_request_with_pending_rollback() {
        val adapter = newAdapter()
        adapter.rememberModeRequest(ModeChangeRequest("omni"))

        runBlocking {
            val deferred = async(start = CoroutineStart.UNDISPATCHED) { adapter.restoreModeRequest.first() }
            adapter.onSessionReady("sess-2", restored = true)

            assertTrue(adapter.omniMode.value)
            assertFalse(adapter.translateMode.value)
            assertFalse(adapter.lectureMode.value)
            assertFalse(adapter.transcribeMode.value)
            assertEquals("omni", deferred.await().mode)

            // 回声前收到 1009（服务端未启用 omni）→ 回落为关
            adapter.onError(code = 1009, message = "omni mode unavailable", fatal = false, recoverable = false)
            assertFalse(adapter.omniMode.value)
        }
    }

    @Test
    fun onSessionChanged_omni_echo_clears_other_modes() {
        val adapter = newAdapter()
        // 先乐观置位 translate，再收 omni 回声 → 三 flag 全由回声决定
        adapter.setTranslateMode(true)

        adapter.onSessionChanged(mode = "omni", myLanguage = null, localLanguage = null, usecase = null)

        assertTrue(adapter.omniMode.value)
        assertFalse(adapter.translateMode.value)
        assertFalse(adapter.lectureMode.value)
        assertFalse(adapter.transcribeMode.value)
    }

    @Test
    fun clearSession_forgets_request_no_restore_on_ready() {
        val adapter = newAdapter()
        adapter.rememberModeRequest(ModeChangeRequest("transcribe"))
        adapter.clearSession()

        runBlocking {
            var emitted: ModeChangeRequest? = null
            val job: Job = async(start = CoroutineStart.UNDISPATCHED) { adapter.restoreModeRequest.collect { emitted = it } }
            adapter.onSessionReady("sess-2", restored = true)
            job.cancel()

            assertEquals(null, emitted)
            assertFalse(adapter.transcribeMode.value)
        }
    }

    @Test
    fun translate_request_survives_repeated_reconnects() {
        val adapter = newAdapter()
        val request = ModeChangeRequest("translate", "zh", "en", "dialogue")
        adapter.rememberModeRequest(request)

        runBlocking {
            val emitted = mutableListOf<ModeChangeRequest>()
            val job: Job = async(start = CoroutineStart.UNDISPATCHED) { adapter.restoreModeRequest.collect { emitted.add(it) } }

            adapter.onSessionReady("sess-2", restored = true)
            yield()  // 让 flow 投递在 runBlocking 事件队列落地
            adapter.onSessionReady("sess-3", restored = true)
            yield()
            job.cancel()

            assertTrue(adapter.translateMode.value)
            assertEquals(listOf(request, request), emitted)
        }
    }

    @Test
    fun permanent_reject_clears_request_stops_future_restore() {
        // 服务端 1009 拒绝：回落 + 清请求 → 后续重连不再自动重试
        val adapter = newAdapter()
        adapter.rememberModeRequest(ModeChangeRequest("transcribe"))

        runBlocking {
            adapter.onSessionReady("sess-1", restored = true)
            assertTrue(adapter.transcribeMode.value)

            adapter.onError(code = 1009, message = "invalid mode: transcribe", fatal = false, recoverable = false)
            assertFalse(adapter.transcribeMode.value)

            var emitted: ModeChangeRequest? = null
            val job: Job = async(start = CoroutineStart.UNDISPATCHED) {
                adapter.restoreModeRequest.collect { emitted = it }
            }
            adapter.onSessionReady("sess-2", restored = true)
            yield()
            job.cancel()

            assertEquals(null, emitted)
            assertFalse(adapter.transcribeMode.value)
        }
    }

    @Test
    fun transient_error_keeps_request_retries_on_next_ready() {
        // 非 1009 错误（如 1001）：不回落、不清 pending；请求保留 → 下次重连自动再试
        val adapter = newAdapter()
        adapter.rememberModeRequest(ModeChangeRequest("transcribe"))

        runBlocking {
            adapter.onSessionReady("sess-1", restored = true)
            adapter.onError(code = 1001, message = "ASR unavailable", fatal = false, recoverable = false)
            assertTrue(adapter.transcribeMode.value)  // 不回落（非 1009）

            val emitted = mutableListOf<ModeChangeRequest>()
            val job: Job = async(start = CoroutineStart.UNDISPATCHED) {
                adapter.restoreModeRequest.collect { emitted.add(it) }
            }
            adapter.onSessionReady("sess-2", restored = true)
            yield()
            job.cancel()

            assertTrue(adapter.transcribeMode.value)  // 再次乐观恢复
            assertEquals(1, emitted.size)             // 仅收到 #2 的恢复（#1 发射于订阅前）
        }
    }

    @Test
    fun translate_reject_rolls_back_translate_and_lecture_flags() {
        val adapter = newAdapter()
        adapter.rememberModeRequest(ModeChangeRequest("translate", "zh", "ja", "lecture"))

        runBlocking {
            adapter.onSessionReady("sess-1", restored = true)
            assertTrue(adapter.translateMode.value)
            assertTrue(adapter.lectureMode.value)

            adapter.onError(code = 1009, message = "session not started", fatal = false, recoverable = false)
            assertFalse(adapter.translateMode.value)
            assertFalse(adapter.lectureMode.value)

            var emitted: ModeChangeRequest? = null
            val job: Job = async(start = CoroutineStart.UNDISPATCHED) {
                adapter.restoreModeRequest.collect { emitted = it }
            }
            adapter.onSessionReady("sess-2", restored = true)
            yield()
            job.cancel()

            assertEquals(null, emitted)  // 永久拒绝已清请求
        }
    }
}
