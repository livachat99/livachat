package dev.livachat.sdk.e2e

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
    fun sessionReady_sessionIdPropagated() {
        val latch = CountDownLatch(1)
        var sessionId: String? = null
        var restored: Boolean? = null

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("session.start")) {
                    webSocket.send(MockServerHarness.sessionReady("my-sess-42", false))
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
        assertEquals("my-sess-42", sessionId)
        assertFalse(restored!!)
        sdk.disconnect()
    }

    @Test
    fun pushAudioPcm_triggersTranscription() {
        val transLatch = CountDownLatch(1)
        var transText: String? = null

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                when {
                    text.contains("session.start") -> {
                        webSocket.send(MockServerHarness.sessionReady())
                        webSocket.send(MockServerHarness.status("listening"))
                    }
                    text.contains("\"audio\"") -> {
                        webSocket.send(MockServerHarness.transcription("Hello world", true))
                    }
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {
            override fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {
                transText = text; transLatch.countDown()
            }
        })

        sdk.connect(); waitForConnection(sdk); sdk.startSession()
        sdk.pushAudioPcm(ShortArray(320))

        assertTrue(transLatch.await(5, TimeUnit.SECONDS))
        assertEquals("Hello world", transText)
        sdk.disconnect()
    }

    @Test
    fun statusMessages_changeSessionState() {
        val states = mutableListOf<SessionState>()
        val speakingLatch = CountDownLatch(1)

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("session.start")) {
                    webSocket.send(MockServerHarness.sessionReady())
                    webSocket.send(MockServerHarness.status("listening"))
                    Thread.sleep(100)
                    webSocket.send(MockServerHarness.status("thinking", "asr_final"))
                    Thread.sleep(100)
                    webSocket.send(MockServerHarness.status("speaking", "tts_start"))
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

        assertTrue(speakingLatch.await(5, TimeUnit.SECONDS))
        assertTrue(states.contains(SessionState.LISTENING))
        assertTrue(states.contains(SessionState.SPEAKING))
        sdk.disconnect()
    }

    private fun waitForConnection(sdk: LivaChatSDK) {
        val start = System.currentTimeMillis()
        while (sdk.connectionState.value != ConnectionState.CONNECTED &&
               System.currentTimeMillis() - start < 5000) Thread.sleep(100)
        assertEquals(ConnectionState.CONNECTED, sdk.connectionState.value)
    }
}
