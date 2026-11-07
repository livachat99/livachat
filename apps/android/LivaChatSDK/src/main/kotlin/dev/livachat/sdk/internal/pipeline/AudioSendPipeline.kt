package dev.livachat.sdk.internal.pipeline

import dev.livachat.sdk.internal.processors.OpusEncoder
import dev.livachat.sdk.internal.processors.VadDetector
import dev.livachat.sdk.model.VadState

/**
 * Upstream audio pipeline: PCM → FrameAssembler → VAD → AGC → Opus encode → output.
 *
 * Does NOT hold AudioRecord; App pushes PCM via [pushPcm].
 * Not thread-safe; single-threaded on the audio send dispatcher.
 *
 * [uplinkAgc] 开启时在 VAD **之后**、编码之前做电平补偿（docs/android_audio_capture.md §4.3）：
 * VAD/onVad 看到原信号（服务端消费 vad_state 驱动轮次与打断），只有送编码器的 PCM 被提升。
 * [isAiSpeaking] 为 true（AI 说话中）时保持 unity，避免把 AEC 残留回声放大成假打断。
 */
internal class AudioSendPipeline(
    sampleRate: Int,
    frameMs: Int = 20,
    private val vad: VadDetector,
    private val encoder: OpusEncoder,
    private val onEncoded: (opus: ByteArray, durationMs: Int, vad: VadState, muted: Boolean) -> Unit,
    private val onVad: (VadState) -> Unit,
    uplinkAgc: Boolean = false,
    private val isAiSpeaking: () -> Boolean = { false },
) {
    private val agc: UplinkAgc? = if (uplinkAgc) UplinkAgc(frameMs = frameMs) else null

    private val assembler = FrameAssembler(sampleRate, frameMs) { pcm ->
        processFrame(pcm, frameMs)
    }

    // 本帧 muted 标记：取"完成该帧的那次 push"的值——帧可跨 push 组装，mute 沿处至多一帧
    // 混合（真实尾样本+静音头样本）取静音侧。docs/android_audio_capture.md §5.2
    private var currentPushMuted: Boolean = false

    /**
     * Push a chunk of 16-bit mono PCM. Internally assembled into [frameMs] frames,
     * each frame gets VAD + Opus encode + callbacks.
     *
     * @param muted true = mic 静音补发帧：payload 将标 `vad_state="muted"`，
     *   服务端替换为零 PCM（lecture 分支 5×，omni/dialogue 1:1；docs/android_audio_capture.md §5.2）
     */
    fun pushPcm(pcm: ShortArray, offset: Int = 0, len: Int = pcm.size, muted: Boolean = false) {
        currentPushMuted = muted
        assembler.push(pcm, offset, len)
    }

    /** Flush any partially assembled frame and reset encoder state. */
    fun reset() {
        assembler.reset()
        agc?.reset()
    }

    private fun processFrame(pcm: ShortArray, durationMs: Int) {
        val vadState = vad.detect(pcm, 0, pcm.size)
        onVad(vadState)

        // 落点承重约束：VAD 之后（见类注释）。原帧由 VAD/onVad 消费完毕，此处原位提升给编码器。
        if (agc != null && !isAiSpeaking()) {
            agc.process(pcm, 0, pcm.size)
        }

        val opus = encoder.encode(pcm, 0, pcm.size)
        onEncoded(opus, durationMs, vadState, currentPushMuted)
    }
}
