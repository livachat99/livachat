package dev.livachat.sdk.internal.pipeline

import dev.livachat.sdk.internal.processors.OpusDecoder
import org.junit.Assert.assertEquals
import org.junit.Test

class AudioRecvPipelineTest {

    private class FakeDecoder : OpusDecoder {
        var decodeCalls = 0
        var lastOpusSize = -1
        var pcmToReturn: ShortArray = ShortArray(320)

        override fun decode(opus: ByteArray, offset: Int, len: Int): ShortArray {
            decodeCalls++
            lastOpusSize = len
            return pcmToReturn
        }

        override fun close() {}
    }

    @Test
    fun handleAiAudio_decodesAndEmitsPcm() {
        val decoder = FakeDecoder()
        val results = mutableListOf<Pair<Int, Long>>() // durationMs, sequenceNumber

        val pipeline = AudioRecvPipeline(
            sampleRate = 16000,
            decoder = decoder,
            onPcm = { _, _, durationMs, sequenceNumber ->
                results.add(Pair(durationMs, sequenceNumber))
            },
        )

        val opus = byteArrayOf(0x01, 0x02, 0x03)
        pipeline.handleAiAudio(opus, durationMs = 20, sequenceNumber = 1)

        assertEquals(1, decoder.decodeCalls)
        assertEquals(3, decoder.lastOpusSize)
        assertEquals(1, results.size)
        assertEquals(20, results[0].first)
        assertEquals(1L, results[0].second)
    }

    @Test
    fun handleAiAudio_multipleChunks_monotonicSeq() {
        val decoder = FakeDecoder()
        val seqs = mutableListOf<Long>()

        val pipeline = AudioRecvPipeline(
            sampleRate = 24000,
            decoder = decoder,
            onPcm = { _, _, _, sequenceNumber -> seqs.add(sequenceNumber) },
        )

        pipeline.handleAiAudio(byteArrayOf(1), durationMs = 20, sequenceNumber = 0)
        pipeline.handleAiAudio(byteArrayOf(2), durationMs = 20, sequenceNumber = 1)
        pipeline.handleAiAudio(byteArrayOf(3), durationMs = 40, sequenceNumber = 2)

        assertEquals(listOf(0L, 1L, 2L), seqs)
    }

    @Test
    fun reset_doesNotThrow() {
        val pipeline = AudioRecvPipeline(
            sampleRate = 16000,
            decoder = FakeDecoder(),
            onPcm = { _, _, _, _ -> },
        )
        pipeline.reset()
    }
}
