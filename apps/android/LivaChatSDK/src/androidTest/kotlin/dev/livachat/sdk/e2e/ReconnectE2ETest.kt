package dev.livachat.sdk.e2e

import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.ConnectionState
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class ReconnectE2ETest {

    private lateinit var harness: MockServerHarness

    @Before fun setUp() { harness = MockServerHarness().also { it.start() } }
    @After fun tearDown() { harness.shutdown() }

    @Test
    fun sessionReady_restoredFalse_forNewSession() {
        val latch = CountDownLatch(1)
        var wasRestored: Boolean? = null

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("session.start")) {
                    // session_id is null in the payload = new session
                    webSocket.send(MockServerHarness.sessionReady("sess-new", restored = false))
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {
            override fun onSessionReady(sessionId: String, restored: Boolean) {
                wasRestored = restored; latch.countDown()
            }
        })

        sdk.connect(); waitForConnection(sdk); sdk.startSession()

        assertTrue(latch.await(5, TimeUnit.SECONDS))
        assertTrue(wasRestored == false)
        sdk.disconnect()
    }

    private fun waitForConnection(sdk: LivaChatSDK) {
        val start = System.currentTimeMillis()
        while (sdk.connectionState.value != ConnectionState.CONNECTED &&
               System.currentTimeMillis() - start < 5000) Thread.sleep(100)
    }
}
