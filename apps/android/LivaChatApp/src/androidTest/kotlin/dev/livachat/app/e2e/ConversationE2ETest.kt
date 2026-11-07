package dev.livachat.app.e2e

import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class ConversationE2ETest {

    private lateinit var harness: MockServerHarness

    @Before fun setUp() { harness = MockServerHarness().also { it.start() } }
    @After fun tearDown() { harness.shutdown() }

    @Test
    fun sessionReady_propagatesSessionId() {
        val latch = CountDownLatch(1)
        var sessionId: String? = null
        var restored: Boolean? = null

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("session.start")) {
                    webSocket.send(MockServerHarness.sessionReady("app-sess-001", false))
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {
            override fun onSessionReady(sid: String, r: Boolean) {
                sessionId = sid; restored = r; latch.countDown()
            }
        })

        sdk.connect(); waitForConnection(sdk); sdk.startSession()

        assertTrue(latch.await(5, TimeUnit.SECONDS))
        assertEquals("app-sess-001", sessionId)
        assertFalse(restored!!)
        sdk.disconnect()
    }

    @Test
    fun conversation_flow_listening_to_speaking() {
        val states = mutableListOf<SessionState>()
        val speakingLatch = CountDownLatch(1)

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                when {
                    text.contains("session.start") -> {
                        webSocket.send(MockServerHarness.sessionReady())
                        webSocket.send(MockServerHarness.status("listening"))
                    }
                    text.contains("\"audio\"") -> {
                        webSocket.send(MockServerHarness.transcription("Hello", false))
                        webSocket.send(MockServerHarness.status("thinking"))
                        webSocket.send(MockServerHarness.status("speaking"))
                    }
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {
            override fun onSessionStateChanged(state: SessionState) {
                states.add(state)
                if (state == SessionState.SPEAKING) speakingLatch.countDown()
            }
        })

        sdk.connect(); waitForConnection(sdk); sdk.startSession()
        sdk.pushAudioPcm(ShortArray(320))

        assertTrue(speakingLatch.await(5, TimeUnit.SECONDS))
        assertTrue(states.contains(SessionState.LISTENING))
        assertTrue(states.contains(SessionState.SPEAKING))
        sdk.disconnect()
    }

    @Test
    fun aiText_received_and_merged() {
        val texts = mutableListOf<String>()
        val finalLatch = CountDownLatch(1)

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                when {
                    text.contains("session.start") -> {
                        webSocket.send(MockServerHarness.sessionReady())
                        webSocket.send(MockServerHarness.status("speaking"))
                        webSocket.send(MockServerHarness.aiText("Hello", "msg-1", 0, false))
                        Thread.sleep(50)
                        webSocket.send(MockServerHarness.aiText("Hello world", "msg-1", 1, true))
                    }
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {
            override fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean, image: dev.livachat.sdk.model.AiImage?, source: String?) {
                texts.add(text)
                if (isFinal) finalLatch.countDown()
            }
        })

        sdk.connect(); waitForConnection(sdk); sdk.startSession()

        assertTrue(finalLatch.await(5, TimeUnit.SECONDS))
        assertTrue(texts.contains("Hello"))
        assertTrue(texts.contains("Hello world"))
        sdk.disconnect()
    }

    @Test
    fun error_propagates_to_listener() {
        val errorLatch = CountDownLatch(1)
        var errorCode = 0
        var errorMsg = ""

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("session.start")) {
                    webSocket.send(MockServerHarness.sessionReady())
                    webSocket.send(MockServerHarness.error(500, "server error", false))
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {
            override fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) {
                errorCode = code; errorMsg = message; errorLatch.countDown()
            }
        })

        sdk.connect(); waitForConnection(sdk); sdk.startSession()

        assertTrue(errorLatch.await(5, TimeUnit.SECONDS))
        assertEquals(500, errorCode)
        assertEquals("server error", errorMsg)
        sdk.disconnect()
    }

    private fun waitForConnection(sdk: LivaChatSDK) {
        val start = System.currentTimeMillis()
        while (sdk.connectionState.value != ConnectionState.CONNECTED &&
            System.currentTimeMillis() - start < 5000
        ) Thread.sleep(100)
        assertEquals(ConnectionState.CONNECTED, sdk.connectionState.value)
    }
}
