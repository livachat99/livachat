package dev.livachat.app.mock.media

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.model.PlaybackFlushReason
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.isActive
import kotlin.coroutines.coroutineContext

class AudioPlaybackController(
    private val sampleRate: Int = 16000,
) : LivaChatListener {

    private val jitterBuffer = JitterBuffer(targetFrames = 3, maxLatencyFrames = 10)
    private val playbackScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @Volatile
    private var playbackJob: Job? = null

    @Volatile
    private var audioTrack: AudioTrack? = null

    @Volatile
    var isPlaying: Boolean = false
        private set

    fun start() {
        if (isPlaying) return

        val minBuf = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = if (minBuf > 0) minBuf * 2 else sampleRate * 2

        audioTrack = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .build()
            )
            .setBufferSizeInBytes(bufferSize)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()

        isPlaying = true
        jitterBuffer.reset()
        startPlaybackLoop()
    }

    private fun startPlaybackLoop() {
        playbackJob = playbackScope.launch {
            val track = audioTrack ?: return@launch
            try {
                runLoop(track)
            } finally {
                withContext(NonCancellable) {
                    try { track.pause() } catch (_: Exception) {}
                    try { track.flush() } catch (_: Exception) {}
                    try { track.stop() } catch (_: Exception) {}
                    try { track.release() } catch (_: Exception) {}
                    audioTrack = null
                    isPlaying = false
                }
            }
        }
    }

    private suspend fun runLoop(track: AudioTrack) {
        val tickIntervalMs = 10L

        while (coroutineContext.isActive && !jitterBuffer.isReady()) {
            delay(10)
        }
        if (!coroutineContext.isActive) return
        track.play()

        while (coroutineContext.isActive) {
            val tickStart = System.nanoTime()

            val frame = jitterBuffer.pull()
            if (frame != null) {
                track.write(frame.pcm, 0, frame.pcm.size, AudioTrack.WRITE_BLOCKING)
            }

            val elapsedMs = (System.nanoTime() - tickStart) / 1_000_000
            val remainingMs = tickIntervalMs - elapsedMs
            if (remainingMs > 0) delay(remainingMs)
        }
    }

    fun stop() {
        isPlaying = false
        playbackJob?.cancel()
        playbackJob = null
        jitterBuffer.reset()
        // AudioTrack cleaned up in finally block
    }

    fun flush() {
        jitterBuffer.reset()
    }

    fun release() {
        stop()
    }

    override fun onAiAudioPcm(
        pcm: ShortArray,
        sampleRate: Int,
        sampleBits: Int,
        durationMs: Int,
        sequenceNumber: Long,
        turnId: String,
    ) {
        jitterBuffer.push(
            JitterBuffer.PcmFrame(
                pcm = pcm,
                sampleRate = sampleRate,
                sampleBits = sampleBits,
                durationMs = durationMs,
                sequenceNumber = sequenceNumber,
                turnId = turnId,
            )
        )
    }

    override fun onPlaybackShouldFlush(reason: PlaybackFlushReason) {
        // No-op
    }
}
