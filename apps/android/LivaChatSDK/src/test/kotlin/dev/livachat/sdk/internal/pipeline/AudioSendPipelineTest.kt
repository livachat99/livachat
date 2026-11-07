package dev.livachat.sdk.internal.pipeline

import dev.livachat.sdk.internal.processors.OpusEncoder
import dev.livachat.sdk.internal.processors.VadDetector
import dev.livachat.sdk.model.VadState
import kotlin.math.PI
import kotlin.math.log10
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioSendPipelineTest {

    private class FakeVad(var state: VadState = VadState.SILENCE) : VadDetector {
        var detectCalls = 0
        override fun detect(pcm: ShortArray, offset: Int, len: Int): VadState {
            detectCalls++
            return state
        }
        override fun close() {}
    }

    private class FakeEncoder : OpusEncoder {
        var encodeCalls = 0
        var seq = 0
        override fun encode(pcm: ShortArray, offset: Int, len: Int): ByteArray {
            encodeCalls++
            return byteArrayOf(seq++.toByte())
        }
        override fun close() {}
    }

    @Test
    fun pushPcm_exactOneFrame_callsVadAndEncode() {
        val encoded = mutableListOf<ByteArray>()
        val vadResults = mutableListOf<VadState>()
        val vad = FakeVad(VadState.SPEECH)
        val encoder = FakeEncoder()

        val pipeline = AudioSendPipeline(
            sampleRate = 16000,
            frameMs = 20,
            vad = vad,
            encoder = encoder,
            onEncoded = { opus, _, _, _ -> encoded.add(opus) },
            onVad = { v -> vadResults.add(v) },
        )

        pipeline.pushPcm(ShortArray(320)) // exactly 20ms

        assertEquals(1, vad.detectCalls)
        assertEquals(1, encoder.encodeCalls)
        assertEquals(1, encoded.size)
        assertEquals(1, vadResults.size)
        assertEquals(VadState.SPEECH, vadResults[0])
    }

    @Test
    fun pushPcm_twoFrames_callsTwice() {
        val encoded = mutableListOf<ByteArray>()
        val vadResults = mutableListOf<VadState>()
        val vad = FakeVad()
        val encoder = FakeEncoder()

        val pipeline = AudioSendPipeline(
            sampleRate = 16000, frameMs = 20,
            vad = vad, encoder = encoder,
            onEncoded = { opus, _, _, _ -> encoded.add(opus) },
            onVad = { v -> vadResults.add(v) },
        )

        pipeline.pushPcm(ShortArray(640)) // 40ms = 2 frames

        assertEquals(2, encoded.size)
        assertEquals(2, vadResults.size)
    }

    @Test
    fun pushPcm_partialFrames_emitsWhenComplete() {
        val encoded = mutableListOf<ByteArray>()
        val vad = FakeVad()
        val encoder = FakeEncoder()

        val pipeline = AudioSendPipeline(
            sampleRate = 16000, frameMs = 20,
            vad = vad, encoder = encoder,
            onEncoded = { opus, _, _, _ -> encoded.add(opus) },
            onVad = {},
        )

        pipeline.pushPcm(ShortArray(160)) // 10ms → pending
        assertEquals(0, encoded.size)

        pipeline.pushPcm(ShortArray(160)) // 10ms → completes frame
        assertEquals(1, encoded.size)
    }

    @Test
    fun reset_clearsPending() {
        val encoded = mutableListOf<ByteArray>()
        val vad = FakeVad()
        val encoder = FakeEncoder()

        val pipeline = AudioSendPipeline(
            sampleRate = 16000, frameMs = 20,
            vad = vad, encoder = encoder,
            onEncoded = { opus, _, _, _ -> encoded.add(opus) },
            onVad = {},
        )

        pipeline.pushPcm(ShortArray(160)) // 10ms pending
        pipeline.reset()
        pipeline.pushPcm(ShortArray(320)) // fresh 20ms

        assertEquals(1, encoded.size) // only 1 frame emitted after pending was discarded
    }

    @Test
    fun pushPcm_vadStatePropagatesToCallback() {
        val vadResults = mutableListOf<VadState>()
        val vad = FakeVad(VadState.SPEECH)
        val encoder = FakeEncoder()

        val pipeline = AudioSendPipeline(
            sampleRate = 16000, frameMs = 20,
            vad = vad, encoder = encoder,
            onEncoded = { _, _, _, _ -> },
            onVad = { v -> vadResults.add(v) },
        )

        vad.state = VadState.SILENCE
        pipeline.pushPcm(ShortArray(320))

        vad.state = VadState.SPEECH
        pipeline.pushPcm(ShortArray(320))

        assertEquals(listOf(VadState.SILENCE, VadState.SPEECH), vadResults)
    }

    @Test
    fun pushPcm_24kHz_frameSize() {
        val encoded = mutableListOf<ByteArray>()
        val vad = FakeVad()
        val encoder = FakeEncoder()

        val pipeline = AudioSendPipeline(
            sampleRate = 24000, frameMs = 20,
            vad = vad, encoder = encoder,
            onEncoded = { opus, _, _, _ -> encoded.add(opus) },
            onVad = {},
        )

        pipeline.pushPcm(ShortArray(480)) // 20ms at 24kHz

        assertEquals(1, encoded.size)
    }

    // ── 上行 AGC 落点（docs/android_audio_capture.md §4.3/§7.1）──

    private class PcmRecordingVad : VadDetector {
        var lastPcm: ShortArray? = null
        override fun detect(pcm: ShortArray, offset: Int, len: Int): VadState {
            lastPcm = pcm.copyOfRange(offset, offset + len)
            return VadState.SPEECH
        }
        override fun close() {}
    }

    private class PcmRecordingEncoder : OpusEncoder {
        var lastPcm: ShortArray? = null
        override fun encode(pcm: ShortArray, offset: Int, len: Int): ByteArray {
            lastPcm = pcm.copyOfRange(offset, offset + len)
            return byteArrayOf(0)
        }
        override fun close() {}
    }

    /** -40 dBFS 整周期正弦（250Hz@16k，320 样本/帧）。 */
    private fun quietFrame(): ShortArray {
        val amp = 32768.0 * 10.0.pow(-40.0 / 20.0) * sqrt(2.0)
        return ShortArray(320) { i ->
            (amp * sin(2 * PI * 250.0 * i / 16000.0)).toInt().toShort()
        }
    }

    private fun rmsDbfs(f: ShortArray): Double {
        var sum = 0.0
        for (s in f) sum += s.toDouble() * s
        return 20 * log10(sqrt(sum / f.size) / 32768.0)
    }

    // 8. 开 AGC：编码器拿到已提升的帧，VAD 拿到的仍是原帧（落点承重约束）
    @Test
    fun uplinkAgc_boostsEncoderInput_butNotVadInput() {
        val vad = PcmRecordingVad()
        val encoder = PcmRecordingEncoder()
        val source = quietFrame()
        val original = source.copyOf()

        val pipeline = AudioSendPipeline(
            sampleRate = 16000, frameMs = 20,
            vad = vad, encoder = encoder,
            onEncoded = { _, _, _, _ -> },
            onVad = {},
            uplinkAgc = true,
        )

        pipeline.pushPcm(source)

        assertTrue("VAD 必须看到原信号", vad.lastPcm!!.contentEquals(original))
        val boosted = encoder.lastPcm!!
        assertTrue("编码器输入应被提升", rmsDbfs(boosted) > rmsDbfs(original) + 6.0)
    }

    // 9. AI 说话期间：保持 unity（不放大残留回声）
    @Test
    fun uplinkAgc_aiSpeaking_keepsUnity() {
        val vad = PcmRecordingVad()
        val encoder = PcmRecordingEncoder()
        val source = quietFrame()
        val original = source.copyOf()

        val pipeline = AudioSendPipeline(
            sampleRate = 16000, frameMs = 20,
            vad = vad, encoder = encoder,
            onEncoded = { _, _, _, _ -> },
            onVad = {},
            uplinkAgc = true,
            isAiSpeaking = { true },
        )

        pipeline.pushPcm(source)

        assertTrue(encoder.lastPcm!!.contentEquals(original))
    }

    // 10. 默认（不开 AGC）：编码器逐样本收到原帧——默认零行为变化
    @Test
    fun uplinkAgcDisabled_encoderGetsOriginal() {
        val vad = PcmRecordingVad()
        val encoder = PcmRecordingEncoder()
        val source = quietFrame()
        val original = source.copyOf()

        val pipeline = AudioSendPipeline(
            sampleRate = 16000, frameMs = 20,
            vad = vad, encoder = encoder,
            onEncoded = { _, _, _, _ -> },
            onVad = {},
        )

        pipeline.pushPcm(source)

        assertTrue(encoder.lastPcm!!.contentEquals(original))
    }

    // 11. muted 标记穿透（docs/android_audio_capture.md §5.2）：
    //     push(muted=true) → onEncoded 收到 muted=true；默认 push → false
    @Test
    fun pushPcm_mutedFlag_forwardedToOnEncoded() {
        val mutedFlags = mutableListOf<Boolean>()
        val pipeline = AudioSendPipeline(
            sampleRate = 16000, frameMs = 20,
            vad = FakeVad(),
            encoder = FakeEncoder(),
            onEncoded = { _, _, _, muted -> mutedFlags.add(muted) },
            onVad = {},
        )

        pipeline.pushPcm(ShortArray(320), muted = true)
        pipeline.pushPcm(ShortArray(320))

        assertEquals(listOf(true, false), mutedFlags)
    }
}
