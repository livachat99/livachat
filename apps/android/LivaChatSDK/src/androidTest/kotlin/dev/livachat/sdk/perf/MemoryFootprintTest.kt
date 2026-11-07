package dev.livachat.sdk.perf

import android.os.Debug
import android.util.Log
import dev.livachat.sdk.LivaChatConfig
import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.AudioSampleRate
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Memory footprint benchmark.
 *
 * Target (design §10): SDK static memory < 30 MB.
 */
class MemoryFootprintTest {

    companion object {
        private const val TAG = "MemPerf"
        private const val TARGET_MB = 30.0
    }

    @Test
    fun sdkMemory_under30MB() {
        // Force GC before measuring
        Runtime.getRuntime().gc()
        Thread.sleep(200)

        val beforeMem = Debug.getNativeHeapAllocatedSize()

        val config = LivaChatConfig(
            serverUrl = "ws://127.0.0.1:1/v1/ws/stream",
            token = "t", deviceId = "d", clientVersion = "android-2.0.0",
            audioSampleRate = AudioSampleRate.RATE_16000,
            reconnectMaxAttempts = 0,
        )

        val sdk = LivaChatSDK(config, object : LivaChatListener {})
        // Let constructors settle
        Thread.sleep(100)

        Runtime.getRuntime().gc()
        Thread.sleep(200)

        val afterMem = Debug.getNativeHeapAllocatedSize()
        val deltaBytes = afterMem - beforeMem
        val deltaMB = deltaBytes / (1024.0 * 1024.0)

        Log.i(TAG, "Memory delta: %.1f MB (native heap: before=%d after=%d)".format(
            deltaMB, beforeMem, afterMem))

        sdk.disconnect()

        assertTrue("SDK memory delta %.1f MB exceeds %.0f MB target".format(deltaMB, TARGET_MB),
                   deltaMB <= TARGET_MB * 1024 * 1024)
    }
}
