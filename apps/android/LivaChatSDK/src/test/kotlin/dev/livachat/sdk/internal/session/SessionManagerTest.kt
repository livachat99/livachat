package dev.livachat.sdk.internal.session

import dev.livachat.protocol.generated.ClientAudioPayload
import dev.livachat.protocol.generated.ClientSessionStartConfig
import dev.livachat.protocol.generated.SessionStatusCode
import dev.livachat.protocol.generated.StatusReasonCode
import dev.livachat.protocol.generated.VadSensitivity
import dev.livachat.protocol.generated.VadState
import dev.livachat.sdk.internal.protocol.MessageCodec
import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.InterruptReason
import dev.livachat.sdk.model.PlaybackFlushReason
import dev.livachat.sdk.model.SessionEndReason
import dev.livachat.sdk.model.SessionState
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.assertj.core.api.Assertions.assertThat
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

class SessionManagerTest {

    private val sentMessages = CopyOnWriteArrayList<String>()
    private val listenerEvents = CopyOnWriteArrayList<String>()
    private var onSessionReadyCalled = false
    private var onSessionEndedCalled = false
    private var onErrorCalled = false

    private val forceFlushEvents = CopyOnWriteArrayList<PlaybackFlushReason>()

    private var currentTime = 1000L
    private var configuredLanguage: String? = null

    private lateinit var manager: SessionManager

    @Before
    fun setUp() {
        sentMessages.clear()
        listenerEvents.clear()
        forceFlushEvents.clear()
        onSessionReadyCalled = false
        onSessionEndedCalled = false
        onErrorCalled = false
        currentTime = 1000L
        configuredLanguage = null

        val dispatcher = object : ListenerDispatcher {
            override fun onSessionReady(sessionId: String, sessionRestored: Boolean) {
                onSessionReadyCalled = true
                listenerEvents.add("ready:$sessionId:$sessionRestored")
            }
            override fun onSessionEnded(sessionId: String, reason: String) {
                onSessionEndedCalled = true
                listenerEvents.add("ended:$sessionId:$reason")
            }
            override fun onJobStatus(jobs: List<AgentJob>) {
                listenerEvents.add("jobstatus:" + jobs.joinToString("|") {
                    "${it.jobId},${it.agentName},${it.summary},${it.startedAtMs},${it.runningTimeMs}"
                })
            }
            override fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {
                listenerEvents.add("trans:$text:$isFinal:$transcriptionId")
            }
            override fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean, image: dev.livachat.sdk.model.AiImage?, source: String?) {
                listenerEvents.add("aitext:$text:$messageId:$index:$isFinal")
            }
            override fun onAiAudio(data: String, codec: String, durationMs: Int) {
                listenerEvents.add("aiaudio:$codec:$durationMs")
            }
            override fun onAiVideoAck(frameId: Long, ackFrameId: Long, processed: Boolean) {
                listenerEvents.add("videoack:$frameId:$ackFrameId")
            }
            override fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) {
                onErrorCalled = true
                listenerEvents.add("error:$code:$message:$fatal")
            }
            override fun onSessionChanged(mode: String, myLanguage: String?, localLanguage: String?, usecase: String?) {
                listenerEvents.add("sessionChanged:$mode:$myLanguage:$localLanguage:$usecase")
            }
            override fun onPlaybackShouldFlush(reason: PlaybackFlushReason) {
                forceFlushEvents.add(reason)
            }
        }

        manager = SessionManager(
            codec = MessageCodec,
            sendRaw = { sentMessages.add(it) },
            listenerDispatcher = dispatcher,
            now = { currentTime },
            configProvider = {
                ClientSessionStartConfig(
                    enableVideo = false,
                    audioSampleRate = 16000,
                    vadSensitivity = VadSensitivity.MEDIUM,
                    clientVersion = "1.0.0",
                    timezone = "Asia/Shanghai",
                    language = configuredLanguage,
                )
            },
        )
    }

    // ── Seq / outbound ────────────────────────────────────────────

    @Test
    fun `startSession sends SessionStart with seq 1`() {
        manager.startSession()
        assertThat(sentMessages).hasSize(1)
        assertThat(sentMessages[0]).contains(""""type":"session.start"""")
        assertThat(sentMessages[0]).contains(""""seq":1""")
    }

    @Test
    fun `startSession config carries timezone`() {
        manager.startSession()
        assertThat(sentMessages[0]).contains(""""timezone":"Asia/Shanghai"""")
    }

    @Test
    fun `startSession uses latest config provider language`() {
        configuredLanguage = "zh-Hans"
        manager.startSession()
        assertThat(sentMessages[0]).contains(""""language":"zh-Hans"""")

        configuredLanguage = "ja"
        manager.startSession()
        assertThat(sentMessages[1]).contains(""""language":"ja"""")
    }

    @Test
    fun `startSession omits language when config provider returns null`() {
        manager.startSession()
        assertThat(sentMessages[0]).doesNotContain(""""language"""")
    }

    @Test
    fun `outgoing seq numbers are monotonically increasing`() {
        manager.startSession()
        val payload = ClientAudioPayload("data", "opus", 20, VadState.SPEECH)
        manager.sendAudio(payload)
        manager.sendAudio(payload)
        assertThat(sentMessages).hasSize(3)
        assertThat(sentMessages[0]).contains(""""seq":1""")
        assertThat(sentMessages[1]).contains(""""seq":2""")
        assertThat(sentMessages[2]).contains(""""seq":3""")
    }

    @Test
    fun `reset then startSession resets seq to 1`() {
        manager.startSession()
        manager.sendAudio(ClientAudioPayload("d", "opus", 20, VadState.SPEECH))
        manager.reset()
        sentMessages.clear()
        manager.startSession()
        assertThat(sentMessages[0]).contains(""""seq":1""")
    }

    // ── session.ready ─────────────────────────────────────────────

    @Test
    fun `session dot ready updates listener`() {
        manager.startSession()
        val json = buildSessionReadyJson(seq = 1, sessionId = "sess-abc", restored = false)
        manager.handleInbound(json)
        assertThat(onSessionReadyCalled).isTrue()
        assertThat(listenerEvents.any { it.startsWith("ready:sess-abc:false") }).isTrue()
    }

    // ── status transitions ────────────────────────────────────────

    @Test
    fun `status speaking sets sessionState to SPEAKING`() {
        manager.startSession()
        manager.handleInbound(buildStatusJson(seq = 2, status = SessionStatusCode.SPEAKING, reason = StatusReasonCode.TTS_START))
        assertThat(manager.sessionState.value).isEqualTo(SessionState.SPEAKING)
    }

    @Test
    fun `status listening after SPEAKING does NOT emit INTERRUPT_ACK`() {
        // 82307cf：INTERRUPT_ACK 由 THINKING 触发（新 LLM turn 开始清旧音频），
        // SPEAKING→LISTENING 不触发（正常 TTS 结束也走 LISTENING，无需 flush）
        manager.startSession()
        manager.handleInbound(buildStatusJson(seq = 2, status = SessionStatusCode.SPEAKING, reason = StatusReasonCode.TTS_START))
        forceFlushEvents.clear()
        manager.handleInbound(buildStatusJson(seq = 3, status = SessionStatusCode.LISTENING, reason = StatusReasonCode.INTERRUPTED))
        assertThat(forceFlushEvents).doesNotContain(PlaybackFlushReason.INTERRUPT_ACK)
    }

    @Test
    fun `status thinking emits INTERRUPT_ACK`() {
        manager.startSession()
        manager.handleInbound(buildStatusJson(seq = 2, status = SessionStatusCode.THINKING, reason = StatusReasonCode.ASR_FINAL))
        assertThat(forceFlushEvents).contains(PlaybackFlushReason.INTERRUPT_ACK)
    }

    @Test
    fun `status listening from IDLE does NOT emit INTERRUPT_ACK`() {
        manager.startSession()
        manager.handleInbound(buildStatusJson(seq = 2, status = SessionStatusCode.LISTENING, reason = StatusReasonCode.CONNECTED))
        assertThat(forceFlushEvents).doesNotContain(PlaybackFlushReason.INTERRUPT_ACK)
    }

    // ── session.ended ─────────────────────────────────────────────

    @Test
    fun `session dot ended calls listener and emits SESSION_ENDED`() {
        manager.startSession()
        val json = buildSessionEndedJson(seq = 2, sessionId = "sess-abc", reason = "user_quit")
        manager.handleInbound(json)
        assertThat(onSessionEndedCalled).isTrue()
        assertThat(forceFlushEvents).contains(PlaybackFlushReason.SESSION_ENDED)
    }

    // ── error ─────────────────────────────────────────────────────

    @Test
    fun `fatal error emits SERVER_ABORT and calls listener`() {
        manager.startSession()
        val json = buildErrorJson(seq = 2, code = 4001, message = "Session expired", fatal = true)
        manager.handleInbound(json)
        assertThat(onErrorCalled).isTrue()
        assertThat(forceFlushEvents).contains(PlaybackFlushReason.SERVER_ABORT)
    }

    @Test
    fun `non-fatal error does NOT emit SERVER_ABORT`() {
        manager.startSession()
        val json = buildErrorJson(seq = 2, code = 3001, message = "Bandwidth low", fatal = false)
        manager.handleInbound(json)
        assertThat(onErrorCalled).isTrue()
        assertThat(forceFlushEvents).doesNotContain(PlaybackFlushReason.SERVER_ABORT)
    }

    // ── ping auto-reply ───────────────────────────────────────────

    @Test
    fun `server ping is auto-replied with pong`() {
        manager.startSession()
        sentMessages.clear()
        val json = buildServerPingJson(seq = 5)
        manager.handleInbound(json)
        assertThat(sentMessages).hasSize(1)
        assertThat(sentMessages[0]).contains(""""type":"pong"""")
        assertThat(sentMessages[0]).contains(""""ping_seq":5""")
    }

    // ── Unknown type ──────────────────────────────────────────────

    @Test
    fun `unknown message type is silently ignored`() {
        manager.startSession()
        val before = sentMessages.size
        manager.handleInbound("""{"type":"unknown.future.type","seq":1,"timestamp":1000,"payload":{}}""")
        assertThat(sentMessages).hasSize(before)
    }

    @Test
    fun `malformed JSON is silently ignored`() {
        manager.startSession()
        manager.handleInbound("not json")
    }

    // ── endSession / sendInterrupt ────────────────────────────────

    @Test
    fun `endSession sends session dot end message`() {
        manager.startSession()
        sentMessages.clear()
        manager.endSession(SessionEndReason.USER_HANGUP)
        assertThat(sentMessages).hasSize(1)
        assertThat(sentMessages[0]).contains(""""type":"session.end"""")
        assertThat(sentMessages[0]).contains(""""reason":"user_quit"""")
    }

    @Test
    fun `sendInterrupt sends interrupt message`() {
        manager.startSession()
        sentMessages.clear()
        manager.sendInterrupt(InterruptReason.USER_VOICE)
        assertThat(sentMessages).hasSize(1)
        assertThat(sentMessages[0]).contains(""""type":"interrupt"""")
        assertThat(sentMessages[0]).contains(""""reason":"user_voice"""")
    }

    // ── transcription / ai_text / ai_audio / ai_video_ack ─────────

    @Test
    fun `transcription is dispatched to listener`() {
        manager.startSession()
        val json = buildTranscriptionJson(seq = 2, text = "Hello", isFinal = true, id = "tr-1")
        manager.handleInbound(json)
        assertThat(listenerEvents.any { it.startsWith("trans:Hello:true:tr-1") }).isTrue()
    }

    @Test
    fun `ai text is dispatched to listener`() {
        manager.startSession()
        val json = buildAiTextJson(seq = 2, text = "Hi", messageId = "msg-1", index = 0, isFinal = false)
        manager.handleInbound(json)
        assertThat(listenerEvents.any { it.startsWith("aitext:Hi:msg-1:0:false") }).isTrue()
    }

    @Test
    fun `ai audio is dispatched to listener`() {
        manager.startSession()
        val json = buildAiAudioJson(seq = 2, data = "SGVsbG8=", messageId = "msg-1", index = 0, durationMs = 20, isFinal = true)
        manager.handleInbound(json)
        assertThat(listenerEvents.any { it.startsWith("aiaudio:opus:20") }).isTrue()
    }

    @Test
    fun `ai video ack is dispatched to listener`() {
        manager.startSession()
        val json = buildAiVideoAckJson(seq = 2, frameId = 1, ackFrameId = 1, processed = true)
        manager.handleInbound(json)
        assertThat(listenerEvents.any { it.startsWith("videoack:1:1") }).isTrue()
    }

    // ── job_status ────────────────────────────────────────────────

    @Test
    fun `job status frame is dispatched with all five fields mapped`() {
        manager.startSession()
        manager.handleInbound(
            buildJobStatusJson(seq = 2, jobs = listOf(
                """{"job_id":"job-1","agent_name":"hermes","summary":"查询机票","started_at_ms":5000,"running_time_ms":47000}""",
                """{"job_id":"job-2","agent_name":"hermes","summary":"汇总纪要","started_at_ms":8000,"running_time_ms":12000}""",
            )),
        )
        val event = listenerEvents.first { it.startsWith("jobstatus:") }
        assertThat(event).contains("job-1,hermes,查询机票,5000,47000")
        assertThat(event).contains("job-2,hermes,汇总纪要,8000,12000")
    }

    @Test
    fun `job status empty list is passed through`() {
        manager.startSession()
        manager.handleInbound(buildJobStatusJson(seq = 2, jobs = emptyList()))
        assertThat(listenerEvents.any { it == "jobstatus:" }).isTrue()
    }

    @Test
    fun `job status preserves server order`() {
        manager.startSession()
        manager.handleInbound(
            buildJobStatusJson(seq = 2, jobs = listOf(
                """{"job_id":"job-a","agent_name":"hermes","summary":"a","started_at_ms":1000,"running_time_ms":1}""",
                """{"job_id":"job-b","agent_name":"hermes","summary":"b","started_at_ms":2000,"running_time_ms":2}""",
                """{"job_id":"job-c","agent_name":"hermes","summary":"c","started_at_ms":3000,"running_time_ms":3}""",
            )),
        )
        val event = listenerEvents.first { it.startsWith("jobstatus:") }
        val jobs = event.removePrefix("jobstatus:").split("|")
        assertThat(jobs).hasSize(3)
        assertThat(jobs[0]).startsWith("job-a,")
        assertThat(jobs[1]).startsWith("job-b,")
        assertThat(jobs[2]).startsWith("job-c,")
    }

    @Test
    fun `job status startedAtMs converted via clock offset`() {
        manager.startSession()
        // session.ready with serverTimeMs = 5000 while local clock = 1000 → offset 4000.
        manager.handleInbound(buildSessionReadyJson(seq = 1, sessionId = "sess-abc", restored = false, serverTimeMs = 5000))
        listenerEvents.clear()
        manager.handleInbound(
            buildJobStatusJson(seq = 2, jobs = listOf(
                """{"job_id":"job-1","agent_name":"hermes","summary":"s","started_at_ms":5000,"running_time_ms":1000}""",
            )),
        )
        val event = listenerEvents.first { it.startsWith("jobstatus:") }
        // startedAtMs = 5000 - 4000 = 1000; runningTimeMs passes through.
        assertThat(event).contains("job-1,hermes,s,1000,1000")
    }

    @Test
    fun `job status logInbound handles JobStatus without throwing`() {
        manager.startSession()
        // Exhaustiveness regression for the logInbound `when` expression (§2.2).
        manager.handleInbound(
            buildJobStatusJson(seq = 2, jobs = listOf(
                """{"job_id":"job-1","agent_name":"hermes","summary":"s","started_at_ms":5000,"running_time_ms":1000}""",
            )),
        )
        assertThat(listenerEvents.any { it.startsWith("jobstatus:") }).isTrue()
    }

    // ── session.change ────────────────────────────────────────────

    @Test
    fun `sendSessionChange transcribe is not dropped and carries transcribe mode`() {
        // fd_pipeline_transcribe_mode.md §6.5：mode 字符串 → 生成枚举映射。
        manager.startSession()
        sentMessages.clear()
        manager.sendSessionChange("transcribe", null, null)
        assertThat(sentMessages).hasSize(1)
        assertThat(sentMessages[0]).contains(""""type":"session.change"""")
        assertThat(sentMessages[0]).contains(""""mode":"transcribe"""")
        assertThat(sentMessages[0]).doesNotContain(""""mode":"chat"""")
    }

    @Test
    fun `sendSessionChange chat and translate still map`() {
        manager.startSession()
        sentMessages.clear()
        manager.sendSessionChange("chat", null, null)
        assertThat(sentMessages.last()).contains(""""mode":"chat"""")
        manager.sendSessionChange("translate", "zh", "en")
        assertThat(sentMessages.last()).contains(""""mode":"translate"""")
        assertThat(sentMessages.last()).contains(""""my_language":"zh"""")
    }

    // ── Helpers ───────────────────────────────────────────────────

    private fun buildSessionReadyJson(seq: Long, sessionId: String, restored: Boolean, serverTimeMs: Long = currentTime): String {
        return """{"type":"session.ready","seq":$seq,"timestamp":$currentTime,"payload":{"session_id":"$sessionId","session_restored":$restored,"server_time_ms":$serverTimeMs,"keepalive_interval_s":15,"server_version":"0.1.0"}}"""
    }

    private fun buildJobStatusJson(seq: Long, jobs: List<String>): String {
        return """{"type":"job_status","seq":$seq,"timestamp":$currentTime,"payload":{"jobs":[${jobs.joinToString(",")}]}}"""
    }

    private fun buildSessionEndedJson(seq: Long, sessionId: String, reason: String): String {
        return """{"type":"session.ended","seq":$seq,"timestamp":$currentTime,"payload":{"reason":"$reason","session_id":"$sessionId"}}"""
    }

    private fun buildStatusJson(seq: Long, status: SessionStatusCode, reason: StatusReasonCode): String {
        return """{"type":"status","seq":$seq,"timestamp":$currentTime,"payload":{"session_status":"${status.name.lowercase()}","reason":"${reason.name.lowercase()}"}}"""
    }

    private fun buildErrorJson(seq: Long, code: Int, message: String, fatal: Boolean): String {
        return """{"type":"error","seq":$seq,"timestamp":$currentTime,"payload":{"code":$code,"message":"$message","fatal":$fatal,"recoverable":true}}"""
    }

    private fun buildServerPingJson(seq: Long): String {
        return """{"type":"ping","seq":$seq,"timestamp":$currentTime,"payload":{}}"""
    }

    private fun buildTranscriptionJson(seq: Long, text: String, isFinal: Boolean, id: String): String {
        return """{"type":"transcription","seq":$seq,"timestamp":$currentTime,"payload":{"text":"$text","is_final":$isFinal,"transcription_id":"$id"}}"""
    }

    private fun buildAiTextJson(seq: Long, text: String, messageId: String, index: Long, isFinal: Boolean): String {
        return """{"type":"ai_text","seq":$seq,"timestamp":$currentTime,"payload":{"text":"$text","message_id":"$messageId","index":$index,"is_final":$isFinal}}"""
    }

    private fun buildAiAudioJson(seq: Long, data: String, messageId: String, index: Long, durationMs: Int, isFinal: Boolean): String {
        return """{"type":"ai_audio","seq":$seq,"timestamp":$currentTime,"payload":{"data":"$data","codec":"opus","message_id":"$messageId","index":$index,"duration_ms":$durationMs,"is_final":$isFinal}}"""
    }

    private fun buildAiVideoAckJson(seq: Long, frameId: Long, ackFrameId: Long, processed: Boolean): String {
        return """{"type":"ai_video_ack","seq":$seq,"timestamp":$currentTime,"payload":{"frame_id":$frameId,"ack_frame_id":$ackFrameId,"processed":$processed}}"""
    }
}

class SessionManagerAvdetectorConfigTest {
    private val sentMessages = java.util.concurrent.CopyOnWriteArrayList<String>()
    private var errors = 0
    private var sessionChanges = 0
    private val avdetector = java.util.concurrent.atomic.AtomicBoolean(true)

    private val dispatcher = object : ListenerDispatcher {
        override fun onSessionReady(sessionId: String, sessionRestored: Boolean) {}
        override fun onSessionEnded(sessionId: String, reason: String) {}
        override fun onJobStatus(jobs: List<dev.livachat.sdk.model.AgentJob>) {}
        override fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {}
        override fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean, image: dev.livachat.sdk.model.AiImage?, source: String?) {}
        override fun onAiAudio(data: String, codec: String, durationMs: Int) {}
        override fun onAiVideoAck(frameId: Long, ackFrameId: Long, processed: Boolean) {}
        override fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) { errors++ }
        override fun onSessionChanged(mode: String, myLanguage: String?, localLanguage: String?, usecase: String?) { sessionChanges++ }
        override fun onPlaybackShouldFlush(reason: dev.livachat.sdk.model.PlaybackFlushReason) {}
    }

    private fun manager(): SessionManager = SessionManager(
        codec = MessageCodec,
        sendRaw = { sentMessages.add(it) },
        listenerDispatcher = dispatcher,
        now = { 1000L },
        configProvider = {
            ClientSessionStartConfig(
                enableVideo = false,
                audioSampleRate = 16000,
                vadSensitivity = VadSensitivity.MEDIUM,
                clientVersion = "android-1.0.0.1",
                avdetector = if (avdetector.get()) {
                    dev.livachat.protocol.generated.AvDetectorSwitch.ENABLE
                } else {
                    dev.livachat.protocol.generated.AvDetectorSwitch.DISABLE
                },
            )
        },
        avdetectorEnabled = avdetector,
    )

    private fun ready(m: SessionManager) = m.handleInbound(
        """{"type":"session.ready","seq":1,"timestamp":1000,"payload":{"session_id":"sess_aaaaaaaaaaaaaaaaaa","session_restored":false,"server_time_ms":1000,"keepalive_interval_s":15}}"""
    )

    private fun error(m: SessionManager, code: Int) = m.handleInbound(
        """{"type":"error","seq":2,"timestamp":1000,"payload":{"code":$code,"message":"x","fatal":false,"recoverable":true}}"""
    )

    @Test
    fun `start carries current avdetector value`() {
        val m = manager()
        m.startSession()
        assertThat(sentMessages.single()).contains(""""avdetector":"enable"""")
    }

    @Test
    fun `toggle before ready is flushed after session ready`() {
        val m = manager()
        m.startSession()
        m.setAvdetectorEnabled(false)
        assertThat(sentMessages).hasSize(1)          // only session.start
        ready(m)
        assertThat(sentMessages).hasSize(2)
        assertThat(sentMessages[1]).contains(""""type":"session.change"""")
        assertThat(sentMessages[1]).contains(""""mode":"config"""")
        assertThat(sentMessages[1]).contains(""""avdetector":"disable"""")
    }

    @Test
    fun `config echo is not dispatched as mode change`() {
        val m = manager()
        m.startSession()
        ready(m)
        m.setAvdetectorEnabled(false)
        m.handleInbound(
            """{"type":"session.changed","seq":3,"timestamp":1000,"payload":{"mode":"config","avdetector":"disable"}}"""
        )
        assertThat(sessionChanges).isEqualTo(0)
    }

    @Test
    fun `1011 is swallowed and does not reach app error handler`() {
        val m = manager()
        m.startSession()
        ready(m)
        m.setAvdetectorEnabled(false)
        error(m, 1011)
        assertThat(errors).isEqualTo(0)
    }

    @Test
    fun `1006 while config in flight marks unsupported and stops retrying`() {
        val m = manager()
        m.startSession()
        ready(m)
        m.setAvdetectorEnabled(false)
        val before = sentMessages.size
        error(m, 1006)
        assertThat(errors).isEqualTo(0)
        m.setAvdetectorEnabled(true)
        assertThat(sentMessages.size).isEqualTo(before)
    }

    @Test
    fun `toggle after endSession does not send config`() {
        val m = manager()
        m.startSession()
        ready(m)
        m.endSession(SessionEndReason.USER_HANGUP)
        val afterEnd = sentMessages.size
        assertThat(sentMessages.last()).contains(""""type":"session.end"""")

        m.setAvdetectorEnabled(false)

        assertThat(sentMessages).hasSize(afterEnd)
    }
}
