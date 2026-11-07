package dev.livachat.sdk

import dev.livachat.sdk.model.AudioSampleRate
import dev.livachat.sdk.model.BandwidthHint
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.Orientation
import dev.livachat.sdk.model.SessionEndReason
import dev.livachat.sdk.model.SessionState
import dev.livachat.sdk.model.VadSensitivity
import dev.livachat.sdk.model.VadState
import java.util.TimeZone
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Tests for public API surface: config, listener, model enums, and the
 * wiring contract between SDK components.
 *
 * Full E2E with MockWebServer is deferred to Phase 7 (E2E with Mock Server),
 * which runs as an instrumentation test on emulator/device.
 */
class LivaChatSDKIntegrationTest {

    // ── Config ──────────────────────────────────────────────────────

    @Test
    fun config_defaultValues() {
        val config = LivaChatConfig(
            serverUrl = "wss://example.com/v1/ws/stream",
            token = "jwt",
            deviceId = "uuid",
            clientVersion = "android-2.0.0",
        )
        assertEquals(AudioSampleRate.RATE_16000, config.audioSampleRate)
        assertEquals(20, config.audioFrameMs)
        assertEquals(VadSensitivity.MEDIUM, config.vadSensitivity)
        assertEquals(70, config.videoQuality)
        assertEquals(1080, config.videoMaxLongSide)
        assertEquals(true, config.enableVideo)
        assertEquals(5, config.reconnectMaxAttempts)
        assertEquals(1000L, config.reconnectBaseDelayMs)
        assertEquals(30000L, config.reconnectMaxDelayMs)
        assertEquals(15, config.keepaliveIntervalS)
        assertEquals(35, config.keepaliveTimeoutS)
        assertEquals(null, config.timezone)  // 缺省 = 每次现取系统时区（§4）
        assertEquals(null, config.language)  // 缺省 = 不上报语言
        assertEquals(null, config.languageProvider)
    }

    @Test
    fun config_explicitTimezonePreserved() {
        val config = LivaChatConfig(
            serverUrl = "wss://example.com/v1/ws/stream",
            token = "jwt",
            deviceId = "uuid",
            clientVersion = "android-2.0.0",
            timezone = "America/New_York",
        )
        assertEquals("America/New_York", config.timezone)
    }

    @Test
    fun config_languageAndProviderPreserved() {
        val config = LivaChatConfig(
            serverUrl = "wss://example.com/v1/ws/stream",
            token = "jwt",
            deviceId = "uuid",
            clientVersion = "android-2.0.0",
            language = "zh-Hans",
            languageProvider = { "ja" },
        )
        assertEquals("zh-Hans", config.language)
        assertEquals("ja", config.languageProvider?.invoke())
    }

    // ── session.start 时区上报（timezone-support.md §4）────────────────

    @Test
    fun timezone_resolution_defaults_to_system() {
        assertEquals(TimeZone.getDefault().id, resolveTimezone(null))
    }

    @Test
    fun timezone_resolution_explicit_override_wins() {
        assertEquals("America/New_York", resolveTimezone("America/New_York"))
    }

    @Test
    fun config_allFieldsExplicit() {
        val config = LivaChatConfig(
            serverUrl = "wss://host/v1/ws/stream",
            token = "token",
            deviceId = "device",
            clientVersion = "v1",
            audioSampleRate = AudioSampleRate.RATE_24000,
            audioFrameMs = 40,
            vadSensitivity = VadSensitivity.HIGH,
            videoQuality = 90,
            videoMaxLongSide = 720,
            enableVideo = false,
            reconnectMaxAttempts = 3,
            reconnectBaseDelayMs = 2000L,
            reconnectMaxDelayMs = 15000L,
            keepaliveIntervalS = 30,
            keepaliveTimeoutS = 60,
        )
        assertEquals(AudioSampleRate.RATE_24000, config.audioSampleRate)
        assertEquals(40, config.audioFrameMs)
        assertEquals(VadSensitivity.HIGH, config.vadSensitivity)
        assertEquals(90, config.videoQuality)
        assertEquals(false, config.enableVideo)
        assertEquals(3, config.reconnectMaxAttempts)
        assertEquals(2000L, config.reconnectBaseDelayMs)
        assertEquals(15000L, config.reconnectMaxDelayMs)
    }

    // ── Listener ────────────────────────────────────────────────────

    @Test
    fun listener_defaultImplementationsDoNotThrow() {
        val listener = object : LivaChatListener {}
        // All default methods should be no-ops
        listener.onConnectionStateChanged(ConnectionState.CONNECTED)
        listener.onSessionStateChanged(SessionState.LISTENING)
        listener.onSessionReady("sess-1", false)
        listener.onSessionEnded("user_quit", "sess-1")
        listener.onTranscription("hello", true, "tr-1")
        listener.onAiText("text", "msg-1", 0, false)
        listener.onAiAudioPcm(ShortArray(320), 16000, 16, 20, 0, "")
        listener.onPlaybackShouldFlush(dev.livachat.sdk.model.PlaybackFlushReason.INTERRUPT_ACK)
        listener.onVideoAck(1, true)
        listener.onError(500, "oops", true, false)
    }

    @Test
    fun listener_canOverrideSelectively() {
        var readyCalled = false
        val listener = object : LivaChatListener {
            override fun onSessionReady(sessionId: String, restored: Boolean) {
                readyCalled = true
            }
        }
        listener.onSessionReady("id", true)
        assertTrue(readyCalled)
    }

    // ── Model enums ─────────────────────────────────────────────────

    @Test
    fun audioSampleRate_values() {
        assertEquals(16000, AudioSampleRate.RATE_16000.hz)
        assertEquals(24000, AudioSampleRate.RATE_24000.hz)
    }

    @Test
    fun vadSensitivity_values() {
        assertEquals(0, VadSensitivity.LOW.mode)
        assertEquals(1, VadSensitivity.MEDIUM.mode)
        assertEquals(2, VadSensitivity.HIGH.mode)
    }

    @Test
    fun connectionState_values() {
        assertEquals(4, ConnectionState.entries.size)
    }

    @Test
    fun sessionState_values() {
        assertEquals(4, SessionState.entries.size)
    }

    @Test
    fun orientation_values() {
        assertEquals(4, Orientation.entries.size)
    }

    @Test
    fun bandwidthHint_values() {
        assertEquals(4, BandwidthHint.entries.size)
    }

    @Test
    fun sessionEndReason_values() {
        assertNotNull(SessionEndReason.USER_HANGUP)
        assertNotNull(SessionEndReason.APP_BACKGROUND)
        assertNotNull(SessionEndReason.ERROR)
    }

    @Test
    fun vadState_values() {
        assertNotNull(VadState.SPEECH)
        assertNotNull(VadState.SILENCE)
        assertNotNull(VadState.UNKNOWN)
    }
}
