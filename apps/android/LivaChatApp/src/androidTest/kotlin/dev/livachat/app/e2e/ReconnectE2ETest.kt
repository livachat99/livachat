package dev.livachat.app.e2e

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
    fun connect_disconnect_cycle() {
        val connectedLatch = CountDownLatch(1)
        val disconnectedLatch = CountDownLatch(1)

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("session.start")) {
                    webSocket.send(MockServerHarness.sessionReady())
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {
            override fun onConnectionStateChanged(state: ConnectionState) {
                when (state) {
                    ConnectionState.CONNECTED -> connectedLatch.countDown()
                    ConnectionState.DISCONNECTED -> disconnectedLatch.countDown()
                    else -> {}
                }
            }
        })

        sdk.connect()
        assertTrue(connectedLatch.await(5, TimeUnit.SECONDS))

        sdk.disconnect()
        assertTrue(disconnectedLatch.await(5, TimeUnit.SECONDS))
    }
}
