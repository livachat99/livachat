package dev.livachat.sdk.internal.pipeline

import android.graphics.Bitmap
import dev.livachat.sdk.internal.processors.JpegEncoder
import dev.livachat.sdk.model.Orientation
import java.io.ByteArrayOutputStream

/**
 * Upstream video pipeline: Bitmap → JPEG compress → output, or pre-encoded JPEG passthrough.
 *
 * Does NOT hold Camera; App pushes Bitmap frames via [pushBitmap] or JPEG bytes via [pushJpeg].
 * Not thread-safe; single-threaded on the video send dispatcher.
 */
internal class VideoSendPipeline(
    private val defaultQuality: Int,
    private val defaultMaxLongSide: Int,
    private val jpegEncoder: JpegEncoder,
    private val onEncoded: (jpeg: ByteArray, frameId: Long, width: Int, height: Int, orientation: Orientation, cameraFacing: String, turnId: Long, text: String?) -> Unit,
) {
    /**
     * Compress [bitmap] to JPEG and emit through [onEncoded].
     *
     * @param quality overrides defaultQuality; clamped 1..100
     * @param turnId 采集轮标识（App 层维护，push 逐帧传入；默认 0 = 未启用，SDK 直通落线）
     * @param text 可选指令文本（语义见 [pushJpeg]）
     */
    fun pushBitmap(
        bitmap: Bitmap,
        frameId: Long,
        orientation: Orientation,
        quality: Int = defaultQuality,
        cameraFacing: String = "front",
        turnId: Long = 0,
        text: String? = null,
    ) {
        val jpeg = jpegEncoder.encode(bitmap, quality.coerceIn(1, 100), defaultMaxLongSide)
        onEncoded(jpeg, frameId, bitmap.width, bitmap.height, orientation, cameraFacing, turnId, text)
    }

    /**
     * Pass through pre-encoded JPEG bytes directly to [onEncoded], bypassing [JpegEncoder].
     * Width and height are informational; no scaling is applied.
     *
     * @param turnId 采集轮标识（App 层维护，push 逐帧传入；默认 0 = 未启用，SDK 直通落线）
     * @param text 可选：与该帧一同上行的用户指令（屏幕抓取点按传"描述图片内容"）。
     *   服务端在 text 非空时**不等 ASR**、直接以该文本 + 本帧起一轮 LLM/TTS
     *   （docs/android_screen_recording.md §5.2）；null/空 = 仅入帧缓冲（现状语义）。
     */
    fun pushJpeg(
        jpeg: ByteArray,
        frameId: Long,
        width: Int,
        height: Int,
        orientation: Orientation,
        cameraFacing: String = "front",
        turnId: Long = 0,
        text: String? = null,
    ) {
        onEncoded(jpeg, frameId, width, height, orientation, cameraFacing, turnId, text)
    }
}
