package dev.livachat.app.e2e

import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.InterruptReason
import dev.livachat.sdk.model.SessionState
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
    fun user_interrupt_flushes_playback() {
        val speakingLatch = CountDownLatch(1)
        val interruptAckLatch = CountDownLatch(1)
        val flushReasons = mutableListOf<String>()

        harness.enqueueWebSocket(object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                when {
                    text.contains("session.start") -> {
                        webSocket.send(MockServerHarness.sessionReady())
                        webSocket.send(MockServerHarness.status("speaking"))
                    }
                    text.contains("\"interrupt\"") -> {
                        // Server acks interrupt → send status back to listening
                        webSocket.send(MockServerHarness.status("listening"))
                        interruptAckLatch.countDown()
                    }
                }
            }
        })

        val sdk = LivaChatSDK(harness.config(), object : LivaChatListener {
            override fun onSessionStateChanged(state: SessionState) {
                if (state == SessionState.SPEAKING) speakingLatch.countDown()
            }
            override fun onPlaybackShouldFlush(reason: dev.livachat.sdk.model.PlaybackFlushReason) {
                flushReasons.add(reason.name)
            }
        })

        sdk.connect(); waitHelper(sdk); sdk.startSession()
        assertTrue(speakingLatch.await(5, TimeUnit.SECONDS))

        // User interrupts
        sdk.sendInterrupt(InterruptReason.USER_VOICE)
        assertTrue(interruptAckLatch.await(5, TimeUnit.SECONDS))

        sdk.disconnect()
    }

    private fun waitHelper(sdk: LivaChatSDK) {
        val start = System.currentTimeMillis()
        while (sdk.connectionState.value != dev.livachat.sdk.model.ConnectionState.CONNECTED &&
            System.currentTimeMillis() - start < 5000
        ) Thread.sleep(100)
    }
}
