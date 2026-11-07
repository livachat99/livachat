package dev.livachat.sdk.e2e

import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.PlaybackFlushReason
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class ErrorHandlingE2ETest {

    private lateinit var harness: MockServerHarness

    @Before fun setUp() { harness = MockServerHarness().also { it.start() } }
    @After fun tearDown() { harness.shutdown() }

    @Test
    fun error_recoverable() {
        val latch = CountDownLatch(1)
        var code = 0; var msg = ""; var fatal = true; var rec = false

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("session.start")) {
                    webSocket.send(MockServerHarness.sessionReady())
                    Thread.sleep(100)
                    webSocket.send(MockServerHarness.error(4001, "rate limited", fatal = false))
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {
            override fun onError(c: Int, m: String, f: Boolean, r: Boolean) {
                code = c; msg = m; fatal = f; rec = r; latch.countDown()
            }
        })

        sdk.connect(); waitForConnection(sdk); sdk.startSession()

        assertTrue(latch.await(5, TimeUnit.SECONDS))
        assertEquals(4001, code)
        assertEquals("rate limited", msg)
        assertTrue(!fatal); assertTrue(rec)
        sdk.disconnect()
    }

    @Test
    fun error_fatal_triggersFlush() {
        val latch = CountDownLatch(1)

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("session.start")) {
                    webSocket.send(MockServerHarness.sessionReady())
                    Thread.sleep(100)
                    webSocket.send(MockServerHarness.status("speaking"))
                    Thread.sleep(100)
                    webSocket.send(MockServerHarness.error(500, "server error", fatal = true, recoverable = false))
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {
            override fun onPlaybackShouldFlush(reason: PlaybackFlushReason) {
                if (reason == PlaybackFlushReason.SERVER_ABORT) latch.countDown()
            }
        })

        sdk.connect(); waitForConnection(sdk); sdk.startSession()
        assertTrue(latch.await(5, TimeUnit.SECONDS))
        sdk.disconnect()
    }

    private fun waitForConnection(sdk: LivaChatSDK) {
        val start = System.currentTimeMillis()
        while (sdk.connectionState.value != ConnectionState.CONNECTED &&
               System.currentTimeMillis() - start < 5000) Thread.sleep(100)
    }
}
