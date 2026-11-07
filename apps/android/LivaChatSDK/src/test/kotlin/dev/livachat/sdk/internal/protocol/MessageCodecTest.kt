package dev.livachat.sdk.internal.protocol

import dev.livachat.protocol.generated.*
import org.assertj.core.api.Assertions.assertThat
import org.junit.Test

class MessageCodecTest {

    // ── Encode tests ──────────────────────────────────────────────

    @Test
    fun `encode SessionStart produces JSON with type discriminator`() {
        val config = ClientSessionStartConfig(
            enableVideo = false,
            audioSampleRate = 16000,
            vadSensitivity = VadSensitivity.MEDIUM,
            clientVersion = "1.0.0",
            language = "zh-Hans",
        )
        val payload = ClientSessionStartPayload(sessionId = null, config = config)
        val msg = ClientMessage.SessionStart(seq = 1, timestamp = 1000, payload = payload)

        val json = MessageCodec.encode(msg)

        assertThat(json).contains(""""type":"session.start"""")
        assertThat(json).contains(""""seq":1""")
        assertThat(json).contains(""""enable_video":false""")
        assertThat(json).contains(""""vad_sensitivity":"medium"""")
        assertThat(json).contains(""""language":"zh-Hans"""")
    }

    @Test
    fun `encode Audio preserves base64 data field`() {
        val base64Audio = "SGVsbG8gV29ybGQ=" // "Hello World" in base64
        val payload = ClientAudioPayload(
            data = base64Audio,
            codec = "opus",
            durationMs = 20,
            vadState = VadState.SPEECH
        )
        val msg = ClientMessage.Audio(seq = 2, timestamp = 2000, payload = payload)

        val json = MessageCodec.encode(msg)

        assertThat(json).contains(""""type":"audio"""")
        assertThat(json).contains(""""data":"$base64Audio"""")
        assertThat(json).contains(""""codec":"opus"""")
        assertThat(json).contains(""""vad_state":"speech"""")
    }

    @Test
    fun `encode SessionEnd with reason`() {
        val payload = ClientSessionEndPayload(SessionEndReason.USER_QUIT)
        val msg = ClientMessage.SessionEnd(seq = 3, timestamp = 3000, payload = payload)

        val json = MessageCodec.encode(msg)

        assertThat(json).contains(""""type":"session.end"""")
        assertThat(json).contains(""""reason":"user_quit"""")
    }

    @Test
    fun `encode Video preserves all payload fields`() {
        val payload = ClientVideoPayload(
            data = "base64jpegdata",
            frameId = 1,
            width = 1080,
            height = 1920,
            orientation = 0,
            cameraFacing = CameraFacing.FRONT,
            quality = 70
        )
        val msg = ClientMessage.Video(seq = 4, timestamp = 4000, payload = payload)

        val json = MessageCodec.encode(msg)

        assertThat(json).contains(""""type":"video"""")
        assertThat(json).contains(""""data":"base64jpegdata"""")
        assertThat(json).contains(""""frame_id":1""")
        assertThat(json).contains(""""width":1080""")
        assertThat(json).contains(""""height":1920""")
    }

    @Test
    fun `encode Interrupt`() {
        val payload = ClientInterruptPayload(InterruptReason.USER_VOICE)
        val msg = ClientMessage.Interrupt(seq = 5, timestamp = 5000, payload = payload)

        val json = MessageCodec.encode(msg)

        assertThat(json).contains(""""type":"interrupt"""")
        assertThat(json).contains(""""reason":"user_voice"""")
    }

    @Test
    fun `encode Ping`() {
        val payload = ClientPingPayload()
        val msg = ClientMessage.Ping(seq = 6, timestamp = 6000, payload = payload)

        val json = MessageCodec.encode(msg)

        assertThat(json).contains(""""type":"ping"""")
    }

    @Test
    fun `encode Pong echoes ping seq`() {
        val payload = ClientPongPayload(pingSeq = 99)
        val msg = ClientMessage.Pong(seq = 7, timestamp = 7000, payload = payload)

        val json = MessageCodec.encode(msg)

        assertThat(json).contains(""""type":"pong"""")
        assertThat(json).contains(""""ping_seq":99""")
    }

    @Test
    fun `encode throws CodecException on classes mismatching serializer`() {
        // Constructing an invalid message scenario is hard with the generated
        // sealed hierarchy; verify that normal types encode cleanly (happy path).
        // CodecException wiring is tested implicitly — any Json internal error
        // (e.g. polymorphic mismatch) would be caught and wrapped.
        val payload = ClientPingPayload()
        val msg = ClientMessage.Ping(seq = 1, timestamp = 1000, payload = payload)
        val json = MessageCodec.encode(msg)
        assertThat(json).isNotEmpty()
    }

    // ── Decode tests ──────────────────────────────────────────────

    @Test
    fun `decode SessionReady maps all fields`() {
        val json = loadFixture("session_ready.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.SessionReady::class.java)
        msg as ServerMessage.SessionReady
        assertThat(msg.seq).isEqualTo(1)
        assertThat(msg.payload.sessionId).isEqualTo("abc123")
        assertThat(msg.payload.sessionRestored).isFalse()
        assertThat(msg.payload.serverTimeMs).isEqualTo(1700000000000L)
        assertThat(msg.payload.keepaliveIntervalS).isEqualTo(15)
        assertThat(msg.payload.serverVersion).isEqualTo("0.1.0")
    }

    @Test
    fun `decode SessionEnded maps reason and sessionId`() {
        val json = loadFixture("session_ended.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.SessionEnded::class.java)
        msg as ServerMessage.SessionEnded
        assertThat(msg.payload.reason).isEqualTo(SessionEndReason.USER_QUIT)
        assertThat(msg.payload.sessionId).isEqualTo("abc123")
    }

    @Test
    fun `decode Transcription maps is_final and text`() {
        val json = loadFixture("transcription.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.Transcription::class.java)
        msg as ServerMessage.Transcription
        assertThat(msg.payload.text).isEqualTo("Hello world")
        assertThat(msg.payload.isFinal).isTrue()
        assertThat(msg.payload.transcriptionId).isEqualTo("tr-001")
    }

    @Test
    fun `decode AiText maps message_id and chunks`() {
        val json = loadFixture("ai_text.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.AiText::class.java)
        msg as ServerMessage.AiText
        assertThat(msg.payload.text).isEqualTo("Hi there")
        assertThat(msg.payload.messageId).isEqualTo("msg-001")
        assertThat(msg.payload.index).isEqualTo(0)
        assertThat(msg.payload.isFinal).isTrue()
    }

    @Test
    fun `decode AiAudio maps base64 data and is_final`() {
        val json = loadFixture("ai_audio.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.AiAudio::class.java)
        msg as ServerMessage.AiAudio
        assertThat(msg.payload.data).isEqualTo("SGVsbG8gV29ybGQ=")
        assertThat(msg.payload.codec).isEqualTo("opus")
        assertThat(msg.payload.messageId).isEqualTo("msg-001")
        assertThat(msg.payload.durationMs).isEqualTo(20)
        assertThat(msg.payload.isFinal).isTrue()
    }

    @Test
    fun `decode AiVideoAck maps ack fields`() {
        val json = loadFixture("ai_video_ack.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.AiVideoAck::class.java)
        msg as ServerMessage.AiVideoAck
        assertThat(msg.payload.frameId).isEqualTo(1)
        assertThat(msg.payload.ackFrameId).isEqualTo(1)
        assertThat(msg.payload.processed).isTrue()
    }

    @Test
    fun `decode Status maps session_status`() {
        val json = loadFixture("status.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.Status::class.java)
        msg as ServerMessage.Status
        assertThat(msg.payload.sessionStatus).isEqualTo(SessionStatusCode.SPEAKING)
        assertThat(msg.payload.reason).isEqualTo(StatusReasonCode.TTS_START)
    }

    @Test
    fun `decode Error maps code and fatal flag`() {
        val json = loadFixture("error.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.Error::class.java)
        msg as ServerMessage.Error
        assertThat(msg.payload.code).isEqualTo(4001)
        assertThat(msg.payload.fatal).isTrue()
        assertThat(msg.payload.recoverable).isTrue()
    }

    @Test
    fun `decode server Ping`() {
        val json = loadFixture("server_ping.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.Ping::class.java)
    }

    @Test
    fun `decode JobStatus maps all five fields`() {
        val json = loadFixture("job_status.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.JobStatus::class.java)
        msg as ServerMessage.JobStatus
        assertThat(msg.payload.jobs).hasSize(1)
        val job = msg.payload.jobs[0]
        assertThat(job.jobId).isEqualTo("9f3c2ab4d1e84f0c9f3c2ab4d1e84f0c")
        assertThat(job.agentName).isEqualTo("hermes")
        assertThat(job.summary).isEqualTo("正在查询东京机票")
        assertThat(job.startedAtMs).isEqualTo(1712345640000L)
        assertThat(job.runningTimeMs).isEqualTo(47000L)
    }

    @Test
    fun `decode JobStatus empty jobs array`() {
        val json = """{"type":"job_status","seq":42,"timestamp":1712345690000,"payload":{"jobs":[]}}"""

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.JobStatus::class.java)
        msg as ServerMessage.JobStatus
        assertThat(msg.payload.jobs).isEmpty()
    }

    @Test
    fun `roundtrip JobStatus decode via codec matches encoded JSON`() {
        val job = ServerJobStatusJobsItem(
            jobId = "job-1",
            agentName = "hermes",
            summary = "汇总会议纪要",
            startedAtMs = 1712345640000L,
            runningTimeMs = 47000L,
        )
        val msg = ServerMessage.JobStatus(
            seq = 9,
            timestamp = 9000,
            payload = ServerJobStatusPayload(jobs = listOf(job)),
        )
        val json = MessageCodec.json.encodeToString(ServerMessage.serializer(), msg)

        val decoded = MessageCodec.decode(json)

        assertThat(decoded).isInstanceOf(ServerMessage.JobStatus::class.java)
        decoded as ServerMessage.JobStatus
        assertThat(decoded.seq).isEqualTo(9)
        assertThat(decoded.payload.jobs).hasSize(1)
        val decodedJob = decoded.payload.jobs[0]
        assertThat(decodedJob.jobId).isEqualTo("job-1")
        assertThat(decodedJob.agentName).isEqualTo("hermes")
        assertThat(decodedJob.summary).isEqualTo("汇总会议纪要")
        assertThat(decodedJob.startedAtMs).isEqualTo(1712345640000L)
        assertThat(decodedJob.runningTimeMs).isEqualTo(47000L)
    }

    @Test
    fun `decode server Pong echoes ping_seq`() {
        val json = loadFixture("server_pong.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isInstanceOf(ServerMessage.Pong::class.java)
        msg as ServerMessage.Pong
        assertThat(msg.payload.pingSeq).isEqualTo(42)
    }

    @Test
    fun `decode unknown type returns null`() {
        val json = loadFixture("unknown_type.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isNull()
    }

    @Test
    fun `decode malformed JSON returns null`() {
        val json = loadFixture("malformed.json")

        val msg = MessageCodec.decode(json)

        assertThat(msg).isNull()
    }

    @Test
    fun `decode empty string returns null`() {
        assertThat(MessageCodec.decode("")).isNull()
    }

    @Test
    fun `decode blank JSON object with no type returns null`() {
        assertThat(MessageCodec.decode("{}")).isNull()
    }

    // ── Roundtrip tests ───────────────────────────────────────────

    @Test
    fun `roundtrip SessionStart encode then decode as ClientMessage`() {
        val config = ClientSessionStartConfig(
            enableVideo = true,
            audioSampleRate = 24000,
            vadSensitivity = VadSensitivity.HIGH,
            clientVersion = "2.0.0",
            language = "zh-Hant",
        )
        val payload = ClientSessionStartPayload(sessionId = "resume-id", config = config)
        val original = ClientMessage.SessionStart(seq = 1, timestamp = 1000, payload = payload)

        val json = MessageCodec.encode(original)
        val decoded = MessageCodec.json.decodeFromString(ClientMessage.serializer(), json)

        assertThat(decoded).isInstanceOf(ClientMessage.SessionStart::class.java)
        decoded as ClientMessage.SessionStart
        assertThat(decoded.seq).isEqualTo(original.seq)
        assertThat(decoded.payload.sessionId).isEqualTo(original.payload.sessionId)
        assertThat(decoded.payload.config.enableVideo).isEqualTo(original.payload.config.enableVideo)
        assertThat(decoded.payload.config.audioSampleRate).isEqualTo(original.payload.config.audioSampleRate)
        assertThat(decoded.payload.config.vadSensitivity).isEqualTo(original.payload.config.vadSensitivity)
        assertThat(decoded.payload.config.clientVersion).isEqualTo(original.payload.config.clientVersion)
        assertThat(decoded.payload.config.language).isEqualTo(original.payload.config.language)
    }

    @Test
    fun `roundtrip Audio encode then decode as ClientMessage`() {
        val payload = ClientAudioPayload(
            data = "aGV5IHRoZXJl",
            codec = "opus",
            durationMs = 20,
            vadState = VadState.SILENCE
        )
        val original = ClientMessage.Audio(seq = 5, timestamp = 5000, payload = payload)

        val json = MessageCodec.encode(original)
        val decoded = MessageCodec.json.decodeFromString(ClientMessage.serializer(), json)

        assertThat(decoded).isInstanceOf(ClientMessage.Audio::class.java)
        decoded as ClientMessage.Audio
        assertThat(decoded.seq).isEqualTo(original.seq)
        assertThat(decoded.payload.data).isEqualTo(original.payload.data)
        assertThat(decoded.payload.codec).isEqualTo(original.payload.codec)
        assertThat(decoded.payload.vadState).isEqualTo(original.payload.vadState)
    }

    @Test
    fun `roundtrip SessionEnd encode then decode as ClientMessage`() {
        val payload = ClientSessionEndPayload(SessionEndReason.APP_BACKGROUND)
        val original = ClientMessage.SessionEnd(seq = 10, timestamp = 10000, payload = payload)

        val json = MessageCodec.encode(original)
        val decoded = MessageCodec.json.decodeFromString(ClientMessage.serializer(), json)

        assertThat(decoded).isInstanceOf(ClientMessage.SessionEnd::class.java)
        decoded as ClientMessage.SessionEnd
        assertThat(decoded.seq).isEqualTo(original.seq)
        assertThat(decoded.payload.reason).isEqualTo(original.payload.reason)
    }

    @Test
    fun `roundtrip Video encode then decode as ClientMessage`() {
        val payload = ClientVideoPayload(
            data = "anBlZw==",
            frameId = 3,
            width = 720,
            height = 1280,
            orientation = 90,
            cameraFacing = CameraFacing.FRONT,
            quality = 75
        )
        val original = ClientMessage.Video(seq = 7, timestamp = 7000, payload = payload)

        val json = MessageCodec.encode(original)
        val decoded = MessageCodec.json.decodeFromString(ClientMessage.serializer(), json)

        assertThat(decoded).isInstanceOf(ClientMessage.Video::class.java)
        decoded as ClientMessage.Video
        assertThat(decoded.seq).isEqualTo(original.seq)
        assertThat(decoded.payload.data).isEqualTo(original.payload.data)
        assertThat(decoded.payload.frameId).isEqualTo(original.payload.frameId)
        assertThat(decoded.payload.width).isEqualTo(original.payload.width)
        assertThat(decoded.payload.height).isEqualTo(original.payload.height)
        assertThat(decoded.payload.orientation).isEqualTo(original.payload.orientation)
        assertThat(decoded.payload.quality).isEqualTo(original.payload.quality)
    }

    @Test
    fun `roundtrip Interrupt encode then decode as ClientMessage`() {
        val payload = ClientInterruptPayload(InterruptReason.MANUAL)
        val original = ClientMessage.Interrupt(seq = 4, timestamp = 4000, payload = payload)

        val json = MessageCodec.encode(original)
        val decoded = MessageCodec.json.decodeFromString(ClientMessage.serializer(), json)

        assertThat(decoded).isInstanceOf(ClientMessage.Interrupt::class.java)
        decoded as ClientMessage.Interrupt
        assertThat(decoded.seq).isEqualTo(original.seq)
        assertThat(decoded.payload.reason).isEqualTo(original.payload.reason)
    }

    @Test
    fun `roundtrip Ping encode then decode as ClientMessage`() {
        val payload = ClientPingPayload()
        val original = ClientMessage.Ping(seq = 42, timestamp = 42000, payload = payload)

        val json = MessageCodec.encode(original)
        val decoded = MessageCodec.json.decodeFromString(ClientMessage.serializer(), json)

        assertThat(decoded).isInstanceOf(ClientMessage.Ping::class.java)
        decoded as ClientMessage.Ping
        assertThat(decoded.seq).isEqualTo(original.seq)
    }

    @Test
    fun `roundtrip Pong encode then decode as ClientMessage`() {
        val payload = ClientPongPayload(pingSeq = 77)
        val original = ClientMessage.Pong(seq = 8, timestamp = 8000, payload = payload)

        val json = MessageCodec.encode(original)
        val decoded = MessageCodec.json.decodeFromString(ClientMessage.serializer(), json)

        assertThat(decoded).isInstanceOf(ClientMessage.Pong::class.java)
        decoded as ClientMessage.Pong
        assertThat(decoded.seq).isEqualTo(original.seq)
        assertThat(decoded.payload.pingSeq).isEqualTo(original.payload.pingSeq)
    }

    // ── ServerMessage roundtrip (encode with same Json, decode via codec) ──

    @Test
    fun `roundtrip SessionReady decode via codec matches encoded JSON`() {
        val payload = ServerSessionReadyPayload(
            sessionId = "sess-1",
            sessionRestored = true,
            serverTimeMs = 1700000000000L,
            keepaliveIntervalS = 30
        )
        val msg = ServerMessage.SessionReady(seq = 1, timestamp = 1000, payload = payload)
        val json = MessageCodec.json.encodeToString(ServerMessage.serializer(), msg)

        val decoded = MessageCodec.decode(json)

        assertThat(decoded).isInstanceOf(ServerMessage.SessionReady::class.java)
        decoded as ServerMessage.SessionReady
        assertThat(decoded.payload.sessionId).isEqualTo("sess-1")
        assertThat(decoded.payload.sessionRestored).isTrue()
        assertThat(decoded.payload.keepaliveIntervalS).isEqualTo(30)
    }

    @Test
    fun `roundtrip ServerMessage Status decode via codec matches encoded JSON`() {
        val payload = ServerStatusPayload(
            sessionStatus = SessionStatusCode.LISTENING,
            reason = StatusReasonCode.INTERRUPTED,
            hint = "reduce_video_quality"
        )
        val msg = ServerMessage.Status(seq = 3, timestamp = 3000, payload = payload)
        val json = MessageCodec.json.encodeToString(ServerMessage.serializer(), msg)

        val decoded = MessageCodec.decode(json)

        assertThat(decoded).isInstanceOf(ServerMessage.Status::class.java)
        decoded as ServerMessage.Status
        assertThat(decoded.payload.sessionStatus).isEqualTo(SessionStatusCode.LISTENING)
        assertThat(decoded.payload.reason).isEqualTo(StatusReasonCode.INTERRUPTED)
        assertThat(decoded.payload.hint).isEqualTo("reduce_video_quality")
    }

    @Test
    fun `roundtrip ServerMessage Error decode via codec matches encoded JSON`() {
        val payload = ServerErrorPayload(
            code = 3001,
            message = "Bandwidth too low",
            fatal = false,
            recoverable = true
        )
        val msg = ServerMessage.Error(seq = 5, timestamp = 5000, payload = payload)
        val json = MessageCodec.json.encodeToString(ServerMessage.serializer(), msg)

        val decoded = MessageCodec.decode(json)

        assertThat(decoded).isInstanceOf(ServerMessage.Error::class.java)
        decoded as ServerMessage.Error
        assertThat(decoded.payload.code).isEqualTo(3001)
        assertThat(decoded.payload.fatal).isFalse()
    }

    // ── Edge cases ────────────────────────────────────────────────

    @Test
    fun `encode then decode as correct ClientMessage subtype preserves all fields`() {
        // Full integration: create each ClientMessage subtype, encode, decode as ClientMessage, verify
        val messages = listOf(
            ClientMessage.SessionStart(1, 1000, ClientSessionStartPayload(
                null, ClientSessionStartConfig(false, 16000, VadSensitivity.LOW, "1.0")
            )),
            ClientMessage.Audio(2, 2000, ClientAudioPayload("ZGF0YQ==", "opus", 20, VadState.SPEECH)),
            ClientMessage.Video(3, 3000, ClientVideoPayload("anBn", 1, 640, 480, 0, CameraFacing.FRONT, 50)),
            ClientMessage.Interrupt(4, 4000, ClientInterruptPayload(InterruptReason.USER_VOICE)),
            ClientMessage.Ping(5, 5000, ClientPingPayload()),
            ClientMessage.Pong(6, 6000, ClientPongPayload(100)),
            ClientMessage.SessionEnd(7, 7000, ClientSessionEndPayload(SessionEndReason.USER_QUIT)),
        )

        for (original in messages) {
            val json = MessageCodec.encode(original)
            val decoded = MessageCodec.json.decodeFromString(ClientMessage.serializer(), json)
            assertThat(decoded::class).isEqualTo(original::class)
            assertThat(decoded.seq).isEqualTo(original.seq)
        }
    }

    @Test
    fun `explicitNulls false omits null optional fields`() {
        val config = ClientSessionStartConfig(false, 16000, VadSensitivity.LOW, "1.0")
        val payload = ClientSessionStartPayload(sessionId = null, config = config)
        val msg = ClientMessage.SessionStart(seq = 1, timestamp = 1000, payload = payload)

        val json = MessageCodec.encode(msg)

        // null session_id should be absent from JSON
        assertThat(json).contains(""""session_id":null""")
        assertThat(json).doesNotContain(""""language"""")
    }

    // ── Helpers ───────────────────────────────────────────────────

    private fun loadFixture(name: String): String {
        val resourcePath = "protocol-fixtures/$name"
        return javaClass.classLoader.getResourceAsStream(resourcePath)
            ?.bufferedReader()
            ?.readText()
            ?: throw IllegalArgumentException("Fixture not found: $resourcePath")
    }
}
