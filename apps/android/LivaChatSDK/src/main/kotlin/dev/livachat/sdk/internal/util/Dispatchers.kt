package dev.livachat.sdk.internal.util

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.asCoroutineDispatcher
import java.util.concurrent.Executors

/**
 * SDK-internal dispatchers — one dedicated single-thread executor per pipeline
 * so that blocking native calls (Opus/VAD) never contend with each other.
 */
internal object SdkDispatchers {

    /** Audio send: PCM assembly + VAD + Opus encode. Single-threaded. */
    val audioSend: CoroutineDispatcher = Executors
        .newSingleThreadExecutor { Thread(it, "LivaChat-AudioSend").apply { isDaemon = true } }
        .asCoroutineDispatcher()

    /** Audio receive: Opus decode. Single-threaded. */
    val audioRecv: CoroutineDispatcher = Executors
        .newSingleThreadExecutor { Thread(it, "LivaChat-AudioRecv").apply { isDaemon = true } }
        .asCoroutineDispatcher()

    /** Video send: JPEG compress. Single-threaded. */
    val videoSend: CoroutineDispatcher = Executors
        .newSingleThreadExecutor { Thread(it, "LivaChat-VideoSend").apply { isDaemon = true } }
        .asCoroutineDispatcher()

    /** Logging: file I/O. Single-threaded to serialize writes. */
    val logging: CoroutineDispatcher = Executors
        .newSingleThreadExecutor { Thread(it, "LivaChat-Logging").apply { isDaemon = true } }
        .asCoroutineDispatcher()
}
