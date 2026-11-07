package dev.livachat.app.mock.media

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import dev.livachat.sdk.LivaChatSDK
import kotlinx.coroutines.*

class AudioCaptureController(
    private val sdk: LivaChatSDK,
    private val sampleRate: Int = 16000,
) {
    private var audioRecord: AudioRecord? = null
    private var captureJob: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var restartRequested = false

    @Volatile
    var isCapturing: Boolean = false
        private set

    /** Error callback for UI notification on capture failure. */
    var onError: ((String) -> Unit)? = null

    fun start() {
        if (isCapturing) return

        if (!initAudioRecord()) {
            onError?.invoke("Microphone unavailable")
            return
        }

        isCapturing = true
        restartRequested = false

        captureJob = scope.launch {
            val frameSize = sampleRate * 20 / 1000
            val frame = ShortArray(frameSize)

            while (isActive && isCapturing) {
                val rec = audioRecord
                if (rec == null || rec.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                    // Attempt recovery
                    if (!restartRequested && isCapturing) {
                        restartRequested = true
                        launch(Dispatchers.Main) {
                            onError?.invoke("AudioRecord error, attempting restart...")
                        }
                        delay(300)
                        closeRecord()
                        if (initAudioRecord()) {
                            restartRequested = false
                            launch(Dispatchers.Main) {
                                onError?.invoke("AudioRecord recovered")
                            }
                            continue
                        }
                    }
                    break
                }

                val read = rec.read(frame, 0, frame.size)
                when {
                    read > 0 -> sdk.pushAudioPcm(frame, 0, read)
                    read == AudioRecord.ERROR_INVALID_OPERATION -> {
                        onError?.invoke("AudioRecord ERROR_INVALID_OPERATION")
                    }
                    read == AudioRecord.ERROR_DEAD_OBJECT -> {
                        onError?.invoke("AudioRecord ERROR_DEAD_OBJECT")
                        break
                    }
                    read == AudioRecord.ERROR_BAD_VALUE -> {
                        onError?.invoke("AudioRecord ERROR_BAD_VALUE")
                    }
                }
            }

            if (isCapturing && !restartRequested) {
                // Abnormal exit, notify
                launch(Dispatchers.Main) {
                    onError?.invoke("Capture stopped unexpectedly")
                }
            }
        }
    }

    fun stop() {
        isCapturing = false
        captureJob?.cancel()
        captureJob = null
        closeRecord()
    }

    private fun initAudioRecord(): Boolean {
        val bufSize = AudioRecord.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        ) * 2

        if (bufSize <= 0) return false

        return try {
            audioRecord = AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufSize,
            )

            if (audioRecord?.state != AudioRecord.STATE_INITIALIZED) {
                closeRecord()
                false
            } else {
                audioRecord?.startRecording()
                audioRecord?.recordingState == AudioRecord.RECORDSTATE_RECORDING
            }
        } catch (e: SecurityException) {
            onError?.invoke("Microphone permission denied")
            false
        } catch (e: Exception) {
            onError?.invoke("AudioRecord init failed: ${e.message}")
            false
        }
    }

    private fun closeRecord() {
        try {
            audioRecord?.apply {
                if (recordingState == AudioRecord.RECORDSTATE_RECORDING) stop()
                release()
            }
        } catch (_: Exception) {}
        audioRecord = null
    }
}
