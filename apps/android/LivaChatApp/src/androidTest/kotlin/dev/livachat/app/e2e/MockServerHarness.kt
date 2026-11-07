package dev.livachat.app.e2e

import dev.livachat.sdk.LivaChatConfig
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.WebSocketListener
import java.net.InetAddress

class MockServerHarness {

    val server = MockWebServer()
    private var port = 0

    fun start() {
        server.start(InetAddress.getByName("127.0.0.1"), 0)
        port = server.port
    }

    fun shutdown() {
        try { server.shutdown() } catch (_: Exception) {}
    }

    fun config() = LivaChatConfig(
        serverUrl = "ws://127.0.0.1:$port/v1/ws/stream",
        token = "app-e2e-token",
        deviceId = "app-e2e-device",
        clientVersion = "android-app-e2e",
        reconnectMaxAttempts = 2,
        reconnectBaseDelayMs = 100L,
        reconnectMaxDelayMs = 300L,
        keepaliveIntervalS = 15,
        keepaliveTimeoutS = 60,
    )

    fun enqueueWebSocket(listener: WebSocketListener) {
        server.enqueue(MockResponse().withWebSocketUpgrade(listener))
    }

    companion object {
        fun sessionReady(sessionId: String = "e2e-sess-001", restored: Boolean = false): String {
            val now = System.currentTimeMillis()
            return """{"type":"session.ready","seq":1,"timestamp":$now,"payload":{"session_id":"$sessionId","session_restored":$restored,"server_time_ms":$now,"keepalive_interval_s":15,"server_version":"0.1.0"}}"""
        }

        fun status(status: String): String {
            val now = System.currentTimeMillis()
            return """{"type":"status","seq":2,"timestamp":$now,"payload":{"session_status":"$status","reason":"connected"}}"""
        }

        fun transcription(text: String, isFinal: Boolean, id: String = "tr-1"): String {
            val now = System.currentTimeMillis()
            return """{"type":"transcription","seq":3,"timestamp":$now,"payload":{"text":"$text","is_final":$isFinal,"transcription_id":"$id"}}"""
        }

        fun aiText(text: String, msgId: String, index: Long, isFinal: Boolean): String {
            val now = System.currentTimeMillis()
            return """{"type":"ai_text","seq":4,"timestamp":$now,"payload":{"text":"$text","message_id":"$msgId","index":$index,"is_final":$isFinal}}"""
        }

        fun aiAudio(isFinal: Boolean = false): String {
            val now = System.currentTimeMillis()
            val silenceBase64 = android.util.Base64.encodeToString(ByteArray(40), android.util.Base64.NO_WRAP)
            return """{"type":"ai_audio","seq":5,"timestamp":$now,"payload":{"data":"$silenceBase64","codec":"opus","message_id":"msg-1","index":0,"duration_ms":20,"is_final":$isFinal}}"""
        }

        fun error(code: Int, message: String, fatal: Boolean = false): String {
            val now = System.currentTimeMillis()
            return """{"type":"error","seq":6,"timestamp":$now,"payload":{"code":$code,"message":"$message","fatal":$fatal,"recoverable":true}}"""
        }
    }
}
