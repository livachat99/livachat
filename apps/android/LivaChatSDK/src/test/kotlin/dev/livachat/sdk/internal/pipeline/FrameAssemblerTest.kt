package dev.livachat.sdk.internal.pipeline

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class FrameAssemblerTest {

    private val FORTY_MS = ShortArray(40)

    @Test
    fun push_exactFrame_emitsOneFrame() {
        val frames = mutableListOf<ShortArray>()
        val assembler = FrameAssembler(16000, 20) { frames.add(it) }

        assembler.push(ShortArray(320)) // exactly 20ms at 16kHz

        assertEquals(1, frames.size)
        assertEquals(320, frames[0].size)
    }

    @Test
    fun push_halfFrame_then_halfFrame_emitsOneFrame() {
        val frames = mutableListOf<ShortArray>()
        val assembler = FrameAssembler(16000, 20) { frames.add(it) }

        assembler.push(ShortArray(160)) // 10ms
        assertEquals(0, frames.size)

        assembler.push(ShortArray(160)) // 10ms → completes frame
        assertEquals(1, frames.size)
        assertEquals(320, frames[0].size)
    }

    @Test
    fun push_30ms_emitsOneFrame_leaves10ms() {
        val frames = mutableListOf<ShortArray>()
        val assembler = FrameAssembler(16000, 20) { frames.add(it) }

        assembler.push(ShortArray(480)) // 30ms

        assertEquals(1, frames.size)
        assertEquals(320, frames[0].size)

        // Remaining 160 samples (10ms) should be pending
        assembler.push(ShortArray(160)) // 10ms → completes second frame
        assertEquals(2, frames.size)
    }

    @Test
    fun push_40ms_emitsTwoFrames() {
        val frames = mutableListOf<ShortArray>()
        val assembler = FrameAssembler(16000, 20) { frames.add(it) }

        assembler.push(ShortArray(640)) // 40ms = 2 complete frames

        assertEquals(2, frames.size)
        assertEquals(320, frames[0].size)
        assertEquals(320, frames[1].size)
    }

    @Test
    fun push_largeChunk_emitsMultipleFrames() {
        val frames = mutableListOf<ShortArray>()
        val assembler = FrameAssembler(16000, 20) { frames.add(it) }

        val chunk = ShortArray(320 * 5) // 100ms = 5 frames
        assembler.push(chunk)

        assertEquals(5, frames.size)
        frames.forEach { assertEquals(320, it.size) }
    }

    @Test
    fun reset_clearsPending() {
        val frames = mutableListOf<ShortArray>()
        val assembler = FrameAssembler(16000, 20) { frames.add(it) }

        assembler.push(ShortArray(160)) // 10ms pending
        assertEquals(0, frames.size)

        assembler.reset()

        // After reset, old data should be discarded
        assembler.push(ShortArray(320)) // 20ms fresh
        assertEquals(1, frames.size)
        assertEquals(320, frames[0].size)
    }

    @Test
    fun push_24kHz_20ms_frame() {
        val frames = mutableListOf<ShortArray>()
        val assembler = FrameAssembler(24000, 20) { frames.add(it) }

        assembler.push(ShortArray(480)) // 20ms at 24kHz

        assertEquals(1, frames.size)
        assertEquals(480, frames[0].size)
    }

    @Test
    fun push_40ms_frameSize() {
        val frames = mutableListOf<ShortArray>()
        val assembler = FrameAssembler(16000, 40) { frames.add(it) }

        assembler.push(ShortArray(640)) // 40ms at 16kHz

        assertEquals(1, frames.size)
        assertEquals(640, frames[0].size)
    }

    @Test
    fun push_onFrameThrows_nextPushAssemblesFreshFrame() {
        val frames = mutableListOf<ShortArray>()
        var throwOnce = true
        val assembler = FrameAssembler(16000, 20) { frame ->
            frames.add(frame)
            if (throwOnce) {
                throwOnce = false
                throw IllegalStateException("boom")
            }
        }
        val first = ShortArray(320) { 11 }
        val second = ShortArray(320) { 22 }

        var threw = false
        try {
            assembler.push(first)
        } catch (_: IllegalStateException) {
            threw = true
        }
        assertTrue(threw)

        assembler.push(second)

        // 不卡死：异常已复位 pendingLen，下一次 push 组装新帧而非重发旧帧
        assertEquals(2, frames.size)
        assertEquals(11, frames[0][0].toInt())
        assertEquals(22, frames[1][0].toInt())
    }

    @Test
    fun push_nonIntegerBlocks_convergesCorrectly() {
        val frames = mutableListOf<ShortArray>()
        val assembler = FrameAssembler(16000, 20) { frames.add(it) }

        // Push 7 chunks of 100 samples each = 700 total = 2 frames + 60 remainder
        repeat(7) { assembler.push(ShortArray(100)) }

        assertEquals(2, frames.size)
        // 60 samples should be pending
        assembler.push(ShortArray(260)) // completes the pending 60 + 260 → 320
        assertEquals(3, frames.size)
    }
}
