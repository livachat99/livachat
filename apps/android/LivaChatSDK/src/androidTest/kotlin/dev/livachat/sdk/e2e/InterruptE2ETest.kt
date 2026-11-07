package dev.livachat.sdk.e2e

import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.VadState
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class InterruptE2ETest {

    private lateinit var harness: MockServerHarness

    @Before fun setUp() { harness = MockServerHarness().also { it.start() } }
    @After fun tearDown() { harness.shutdown() }

    @Test
    fun interrupt_reachesServer() {
        val interruptLatch = CountDownLatch(1)

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("session.start")) {
                    webSocket.send(MockServerHarness.sessionReady())
                    webSocket.send(MockServerHarness.status("speaking", "tts_start"))
                }
                if (text.contains("interrupt")) {
                    interruptLatch.countDown()
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {})
        sdk.connect(); waitForConnection(sdk); sdk.startSession()
        Thread.sleep(200)

        sdk.sendInterrupt()

        assertTrue(interruptLatch.await(5, TimeUnit.SECONDS))
        sdk.disconnect()
    }

    @Test
    fun localVadState_updatesOnPcmInput() {
        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                if (text.contains("session.start")) {
                    webSocket.send(MockServerHarness.sessionReady())
                    webSocket.send(MockServerHarness.status("listening"))
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {})
        sdk.connect(); waitForConnection(sdk); sdk.startSession()
        Thread.sleep(300)

        val states = mutableListOf<VadState>()
        sdk.pushAudioPcm(ShortArray(320))
        Thread.sleep(200)
        states.add(sdk.localVadState.value)

        val loud = ShortArray(320) { (it * 3000 % 32767).toShort() }
        sdk.pushAudioPcm(loud)
        Thread.sleep(200)
        states.add(sdk.localVadState.value)

        assertTrue(states.any { it != VadState.UNKNOWN })
        sdk.disconnect()
    }

    private fun waitForConnection(sdk: LivaChatSDK) {
        val start = System.currentTimeMillis()
        while (sdk.connectionState.value != ConnectionState.CONNECTED &&
               System.currentTimeMillis() - start < 5000) Thread.sleep(100)
    }
}
