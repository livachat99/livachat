package dev.livachat.app.media

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.livachat.sdk.model.PlaybackFlushReason
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AudioPlaybackControllerTest {

    private lateinit var controller: AudioPlaybackController

    @Before
    fun setUp() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val audioRouteManager = AudioRouteManager(context)
        // 输出设备选择特性新增 context 注入（设备解析用，§5.4）+ 设备表注入（D10 默认选择/断连兜底）
        controller = AudioPlaybackController(audioRouteManager, AudioDeviceManager(context), context)
    }

    @After
    fun tearDown() {
        controller.stop()
    }

    @Test
    fun startAndStop() = runBlocking {
        assertFalse(controller.isPlaying.first())

        controller.start()
        assertTrue(controller.isPlaying.first())

        controller.stop()
        assertFalse(controller.isPlaying.first())
    }

    @Test
    fun flushIsIdempotent() = runBlocking {
        controller.start()
        assertTrue(controller.isPlaying.first())

        controller.flush()
        assertTrue(controller.isPlaying.first())

        controller.flush()
        controller.flush()
        assertTrue(controller.isPlaying.first())

        controller.stop()
    }

    @Test
    fun flushWhenStoppedDoesNotCrash() {
        controller.flush()
        controller.flush()
    }

    @Test
    fun setMuted() = runBlocking {
        assertFalse(controller.isMuted.first())
        controller.setMuted(true)
        assertTrue(controller.isMuted.first())
        controller.setMuted(false)
        assertFalse(controller.isMuted.first())
    }

    @Test
    fun writeSilencePcm() = runBlocking {
        controller.start()
        val silence = ShortArray(480)

        controller.onAiAudioPcm(silence, 24000, 16, 20, 1, "")
        controller.onAiAudioPcm(silence, 24000, 16, 20, 2, "")
        controller.onAiAudioPcm(silence, 24000, 16, 20, 3, "")

        // Give playback loop time to consume the frames
        delay(100)

        assertTrue(controller.isPlaying.first())
        controller.stop()
    }

    @Test
    fun mutedDoesNotCrash() = runBlocking {
        controller.start()
        controller.setMuted(true)

        val pcm = ShortArray(480) { 1 }
        controller.onAiAudioPcm(pcm, 24000, 16, 20, 1, "")

        delay(100)
        assertTrue(controller.isPlaying.first())
        controller.stop()
    }

    @Test
    fun playbackFlushCallback() = runBlocking {
        controller.start()
        assertTrue(controller.isPlaying.first())

        controller.onPlaybackShouldFlush(PlaybackFlushReason.INTERRUPT_ACK)
        assertTrue(controller.isPlaying.first())

        controller.stop()
    }

    @Test
    fun doubleStartDoesNotCrash() = runBlocking {
        controller.start()
        assertTrue(controller.isPlaying.first())
        controller.start()
        assertTrue(controller.isPlaying.first())
        controller.stop()
    }
}
