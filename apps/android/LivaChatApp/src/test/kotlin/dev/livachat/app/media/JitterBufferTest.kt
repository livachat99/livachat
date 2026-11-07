package dev.livachat.app.media

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicBoolean

class JitterBufferTest {

    private fun makeFrame(seq: Long): JitterBuffer.PcmFrame = JitterBuffer.PcmFrame(
        pcm = ShortArray(320) { (seq % 100).toShort() },
        sampleRate = 16000,
        sampleBits = 16,
        durationMs = 20,
        sequenceNumber = seq,
        turnId = "",
    )

    @Test
    fun `pushPull contiguous seq returns frames in order`() {
        val jb = JitterBuffer(targetFrames = 3, maxLatencyFrames = 10)
        jb.push(makeFrame(0))
        jb.push(makeFrame(1))
        jb.push(makeFrame(2))

        val f0 = jb.pull()
        val f1 = jb.pull()
        val f2 = jb.pull()

        assertNotNull(f0)
        assertNotNull(f1)
        assertNotNull(f2)
        assertEquals(0L, f0!!.sequenceNumber)
        assertEquals(1L, f1!!.sequenceNumber)
        assertEquals(2L, f2!!.sequenceNumber)
    }

    @Test
    fun `pushPull out of order reorders`() {
        val jb = JitterBuffer(targetFrames = 3, maxLatencyFrames = 10)
        jb.push(makeFrame(2))
        jb.push(makeFrame(0))
        jb.push(makeFrame(1))

        assertEquals(0L, jb.pull()?.sequenceNumber)
        assertEquals(1L, jb.pull()?.sequenceNumber)
        assertEquals(2L, jb.pull()?.sequenceNumber)
    }

    @Test
    fun `late frame dropped`() {
        val jb = JitterBuffer(targetFrames = 1, maxLatencyFrames = 10)
        jb.push(makeFrame(5))
        jb.pull() // nextExpected becomes 6

        jb.push(makeFrame(3)) // late, seq < 6

        assertNull(jb.pull())
    }

    @Test
    fun `isReady false under target`() {
        val jb = JitterBuffer(targetFrames = 3, maxLatencyFrames = 10)
        jb.push(makeFrame(0))
        jb.push(makeFrame(1))

        assertFalse(jb.isReady())
    }

    @Test
    fun `isReady true at target`() {
        val jb = JitterBuffer(targetFrames = 3, maxLatencyFrames = 10)
        jb.push(makeFrame(0))
        jb.push(makeFrame(1))
        jb.push(makeFrame(2))

        assertTrue(jb.isReady())
    }

    @Test
    fun `reset clears state`() {
        val jb = JitterBuffer(targetFrames = 3, maxLatencyFrames = 10)
        jb.push(makeFrame(0))
        jb.push(makeFrame(1))
        jb.push(makeFrame(2))

        jb.reset()

        assertTrue(jb.isEmpty())
        assertFalse(jb.isReady())
        assertNull(jb.pull())
    }

    @Test
    fun `maxLatency rejects overflow`() {
        val jb = JitterBuffer(targetFrames = 1, maxLatencyFrames = 2)
        jb.push(makeFrame(100))
        jb.push(makeFrame(101))
        jb.push(makeFrame(102)) // rejected — buffer full

        // First two frames still in order
        val first = jb.pull()
        assertNotNull(first)
        assertEquals(100L, first!!.sequenceNumber)
        assertEquals(101L, jb.pull()?.sequenceNumber)
        assertNull(jb.pull()) // frame 102 was rejected
    }

    @Test
    fun `pull when empty returns null`() {
        val jb = JitterBuffer(targetFrames = 3, maxLatencyFrames = 10)
        assertNull(jb.pull())
    }

    @Test
    fun `push initializes nextExpected from first frame`() {
        val jb = JitterBuffer(targetFrames = 1, maxLatencyFrames = 10)

        jb.push(makeFrame(500))

        val frame = jb.pull()
        assertNotNull(frame)
        assertEquals(500L, frame!!.sequenceNumber)
    }

    @Test
    fun `reset re-accepts lower seq`() {
        val jb = JitterBuffer(targetFrames = 1, maxLatencyFrames = 10)
        jb.push(makeFrame(100))
        jb.pull() // nextExpected = 101

        jb.reset()

        jb.push(makeFrame(0))
        assertEquals(0L, jb.pull()?.sequenceNumber)
    }

    @Test
    fun `concurrent push pull does not crash`() {
        val jb = JitterBuffer(targetFrames = 1, maxLatencyFrames = 100)
        val done = AtomicBoolean(false)

        val producer = Thread {
            var seq = 0L
            while (!done.get()) {
                jb.push(makeFrame(seq))
                seq++
                if (seq > 1000) seq = 0
            }
        }.apply { name = "test-producer" }

        val consumer = Thread {
            while (!done.get()) {
                jb.pull()
            }
        }.apply { name = "test-consumer" }

        producer.start()
        consumer.start()
        Thread.sleep(500)
        done.set(true)
        producer.join(2000)
        consumer.join(2000)
    }
}
