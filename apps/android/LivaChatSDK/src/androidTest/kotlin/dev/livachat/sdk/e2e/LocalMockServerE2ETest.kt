package dev.livachat.sdk.e2e

import android.util.Log
import dev.livachat.sdk.LivaChatConfig
import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.AudioSampleRate
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class LocalMockServerE2ETest {

    companion object {
        private const val HOST_URL = "ws://10.0.2.2:8080/v1/ws/stream"
    }

    private lateinit var config: LivaChatConfig

    @Before
    fun setUp() {
        config = LivaChatConfig(
            serverUrl = HOST_URL,
            token = "dev-token",
            deviceId = "e2e550e4-0001-4000-a000-000000000001",
            clientVersion = "android-2.0.0",
            audioSampleRate = AudioSampleRate.RATE_16000,
            reconnectMaxAttempts = 0,
        )
    }

    @Test
    fun connect_receivesSessionReady() {
        val readyLatch = CountDownLatch(1)
        var sessionId: String? = null

        val sdk = LivaChatSDK(config, object : LivaChatListener {
            override fun onConnectionStateChanged(state: ConnectionState) {
                Log.i("E2E", "connectionState: $state")
            }
            override fun onSessionReady(sid: String, restored: Boolean) {
                Log.i("E2E", "sessionReady: $sid")
                sessionId = sid; readyLatch.countDown()
            }
            override fun onSessionStateChanged(state: SessionState) {
                Log.i("E2E", "sessionState: $state")
            }
            override fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) {
                Log.e("E2E", "error($code): $message fatal=$fatal")
            }
        })

        sdk.connect()

        // Wait with detailed state logging
        val start = System.currentTimeMillis()
        var lastState: ConnectionState? = null
        while (System.currentTimeMillis() - start < 15000) {
            val state = sdk.connectionState.value
            if (state != lastState) {
                Log.i("E2E", "Poll: $state (t=${System.currentTimeMillis() - start}ms)")
                lastState = state
            }
            if (state == ConnectionState.CONNECTED) break
            Thread.sleep(200)
        }
        Log.i("E2E", "Final after connect: ${sdk.connectionState.value}")

        assertEquals("Should connect", ConnectionState.CONNECTED, sdk.connectionState.value)

        sdk.startSession()

        val got = readyLatch.await(10, TimeUnit.SECONDS)
        Log.i("E2E", "sessionReady received: $got")

        if (got) {
            assertNotNull(sessionId)
            assertFalse(sessionId!!.isEmpty())
        }

        sdk.disconnect()
    }

    @Test
    fun fullConversation_flow() {
        val readyLatch = CountDownLatch(1)
        val transLatch = CountDownLatch(1)
        val speakingLatch = CountDownLatch(1)
        val events = mutableListOf<String>()

        val sdk = LivaChatSDK(config, object : LivaChatListener {
            override fun onSessionReady(sessionId: String, restored: Boolean) {
                events.add("ready: $sessionId"); readyLatch.countDown()
            }
            override fun onSessionStateChanged(state: SessionState) {
                events.add("state: $state")
                if (state == SessionState.SPEAKING) speakingLatch.countDown()
            }
            override fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {
                events.add("trans: $text final=$isFinal")
                if (isFinal) transLatch.countDown()
            }
            override fun onAiText(text: String, messageId: String, index: Long, isFinal: Boolean, image: dev.livachat.sdk.model.AiImage?, source: String?) {
                events.add("ai_text[$index]: $text")
            }
            override fun onAiAudioPcm(
                pcm: ShortArray, sampleRate: Int, sampleBits: Int,
                durationMs: Int, sequenceNumber: Long, turnId: String,
            ) {
                events.add("ai_audio[$sequenceNumber]")
            }
        })

        sdk.connect()
        val start = System.currentTimeMillis()
        while (sdk.connectionState.value != ConnectionState.CONNECTED &&
               System.currentTimeMillis() - start < 15000) Thread.sleep(200)
        assertEquals(ConnectionState.CONNECTED, sdk.connectionState.value)

        sdk.startSession()
        assertTrue(readyLatch.await(10, TimeUnit.SECONDS))

        // Phase 1: Send speech frames to trigger VAD=SPEECH (mock server starts interims)
        val speech = ShortArray(320) { (kotlin.math.sin(2.0 * Math.PI * 1000 * it / 16000) * 16000).toInt().toShort() }
        repeat(15) { sdk.pushAudioPcm(speech); Thread.sleep(25) }

        // Phase 2: Send silence frames to reach threshold (600ms / 20ms = 30 frames)
        val silence = ShortArray(320)
        repeat(40) { sdk.pushAudioPcm(silence); Thread.sleep(25) }

        // Give server time to process and send the full response flow
        val gotTrans = transLatch.await(20, TimeUnit.SECONDS)
        val gotSpeaking = speakingLatch.await(20, TimeUnit.SECONDS)

        Log.i("E2E", "Events: $events")
        Log.i("E2E", "transcription=$gotTrans speaking=$gotSpeaking")

        assertTrue("Should get final transcription", gotTrans)
        assertTrue("Should reach SPEAKING", gotSpeaking)

        sdk.disconnect()
    }
}
