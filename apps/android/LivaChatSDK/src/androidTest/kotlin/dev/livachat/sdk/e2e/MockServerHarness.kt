package dev.livachat.sdk.e2e

import android.util.Base64
import dev.livachat.sdk.LivaChatConfig
import dev.livachat.sdk.internal.processors.OpusEncoderImpl
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import java.net.InetAddress

class MockServerHarness {

    val server = MockWebServer()
    private var port = 0

    fun start() {
        server.start(InetAddress.getByName("127.0.0.1"), 0)
        port = server.port
    }

    fun shutdown() {
        try {
            server.shutdown()
        } catch (_: Exception) {
            // MockWebServer shutdown may throw on emulator
        }
    }

    fun config() = LivaChatConfig(
        serverUrl = "ws://127.0.0.1:$port/v1/ws/stream",
        token = "e2e-token",
        deviceId = "e2e-device-uuid",
        clientVersion = "android-e2e-test",
        reconnectMaxAttempts = 0, // disable reconnect for E2E tests
        reconnectBaseDelayMs = 100L,
        reconnectMaxDelayMs = 500L,
        keepaliveIntervalS = 15,
        keepaliveTimeoutS = 60,
    )

    fun enqueueWebSocket(listener: WebSocketListener) {
        server.enqueue(MockResponse().withWebSocketUpgrade(listener))
    }

    companion object {
        fun opusSilenceBase64(): String {
            val encoder = OpusEncoderImpl(16000)
            val silence = ShortArray(320)
            val opus = encoder.encode(silence, 0, 320)
            encoder.close()
            return Base64.encodeToString(opus, Base64.NO_WRAP)
        }

        fun sessionReady(sessionId: String = "e2e-sess-001", restored: Boolean = false): String {
            val now = System.currentTimeMillis()
            return """{"type":"session.ready","seq":1,"timestamp":$now,"payload":{"session_id":"$sessionId","session_restored":$restored,"server_time_ms":$now,"keepalive_interval_s":15,"server_version":"0.1.0"}}"""
        }

        fun status(status: String, reason: String = "connected"): String {
            val now = System.currentTimeMillis()
            return """{"type":"status","seq":2,"timestamp":$now,"payload":{"session_status":"$status","reason":"$reason"}}"""
        }

        fun transcription(text: String, isFinal: Boolean, id: String = "tr-1"): String {
            val now = System.currentTimeMillis()
            return """{"type":"transcription","seq":3,"timestamp":$now,"payload":{"text":"$text","is_final":$isFinal,"transcription_id":"$id"}}"""
        }

        fun aiText(text: String, msgId: String, index: Long, isFinal: Boolean): String {
            val now = System.currentTimeMillis()
            return """{"type":"ai_text","seq":4,"timestamp":$now,"payload":{"text":"$text","message_id":"$msgId","index":$index,"is_final":$isFinal}}"""
        }

        fun aiAudio(isFinal: Boolean = false, index: Long = 0, msgId: String = "msg-1"): String {
            val now = System.currentTimeMillis()
            val data = opusSilenceBase64()
            return """{"type":"ai_audio","seq":5,"timestamp":$now,"payload":{"data":"$data","codec":"opus","message_id":"$msgId","index":$index,"duration_ms":20,"is_final":$isFinal}}"""
        }

        fun error(code: Int, message: String, fatal: Boolean = false, recoverable: Boolean = true): String {
            val now = System.currentTimeMillis()
            return """{"type":"error","seq":6,"timestamp":$now,"payload":{"code":$code,"message":"$message","fatal":$fatal,"recoverable":$recoverable}}"""
        }
    }
}
