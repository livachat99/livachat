package dev.livachat.sdk.internal.pipeline

import android.graphics.Bitmap
import dev.livachat.protocol.generated.CameraFacing
import dev.livachat.protocol.generated.ClientMessage
import dev.livachat.protocol.generated.ClientVideoPayload
import dev.livachat.sdk.internal.processors.JpegEncoder
import dev.livachat.sdk.internal.protocol.MessageCodec
import dev.livachat.sdk.model.Orientation
import io.mockk.every
import io.mockk.mockk
import org.assertj.core.api.Assertions.assertThat
import org.junit.Test

/**
 * video.payload.text 透传（docs/android_screen_recording.md §8.2）：
 * 管线把可选 text 原样交给 onEncoded；编码后的报文里 text **有则落线、缺省则字段整体省略**
 * （生成物 `text: String? = null` 带默认值 + kotlinx `encodeDefaults=false`——与 explicitNulls
 * 无关；服务端 `text is None` → 走现状分支）。
 */
class VideoSendPipelineTest {

    private data class Captured(
        val frameId: Long,
        val width: Int,
        val height: Int,
        val orientation: Orientation,
        val cameraFacing: String,
        val turnId: Long,
        val text: String?,
    )

    private fun pipelineWithRecorder(
        encoder: JpegEncoder = mockk(relaxed = true),
    ): Pair<VideoSendPipeline, MutableList<Captured>> {
        val captured = mutableListOf<Captured>()
        val pipeline = VideoSendPipeline(
            defaultQuality = 70,
            defaultMaxLongSide = 1080,
            jpegEncoder = encoder,
        ) { _, frameId, width, height, orientation, cameraFacing, turnId, text ->
            captured += Captured(frameId, width, height, orientation, cameraFacing, turnId, text)
        }
        return pipeline to captured
    }

    @Test
    fun `pushJpeg forwards text and turnId`() {
        val (pipeline, captured) = pipelineWithRecorder()

        pipeline.pushJpeg(
            jpeg = byteArrayOf(1, 2, 3), frameId = 7, width = 528, height = 1080,
            orientation = Orientation.PORTRAIT_0, cameraFacing = "front",
            turnId = 5, text = "描述图片内容",
        )

        assertThat(captured).hasSize(1)
        assertThat(captured[0].text).isEqualTo("描述图片内容")
        assertThat(captured[0].turnId).isEqualTo(5L)
        assertThat(captured[0].frameId).isEqualTo(7L)
        assertThat(captured[0].width).isEqualTo(528)
    }

    @Test
    fun `pushJpeg defaults turnId to zero when not provided`() {
        val (pipeline, captured) = pipelineWithRecorder()

        pipeline.pushJpeg(
            jpeg = byteArrayOf(1), frameId = 9, width = 640, height = 480,
            orientation = Orientation.PORTRAIT_0,
        )

        assertThat(captured).hasSize(1)
        assertThat(captured[0].turnId).isEqualTo(0L)
    }

    @Test
    fun `pushJpeg without text forwards null`() {
        val (pipeline, captured) = pipelineWithRecorder()

        pipeline.pushJpeg(
            jpeg = byteArrayOf(1), frameId = 8, width = 640, height = 480,
            orientation = Orientation.PORTRAIT_0,
        )

        assertThat(captured).hasSize(1)
        assertThat(captured[0].text).isNull()
    }

    @Test
    fun `pushBitmap forwards text`() {
        val encoder = mockk<JpegEncoder> {
            every { encode(any(), any(), any()) } returns byteArrayOf(9)
        }
        val bitmap = mockk<Bitmap> {
            every { width } returns 640
            every { height } returns 480
        }
        val (pipeline, captured) = pipelineWithRecorder(encoder)

        pipeline.pushBitmap(
            bitmap = bitmap, frameId = 3, orientation = Orientation.PORTRAIT_0,
            cameraFacing = "back", turnId = 6, text = "描述图片内容",
        )

        assertThat(captured).hasSize(1)
        assertThat(captured[0].text).isEqualTo("描述图片内容")
        assertThat(captured[0].cameraFacing).isEqualTo("back")
        assertThat(captured[0].turnId).isEqualTo(6L)
    }

    // ── 报文层：text 的有无都按契约上线（服务端 ClientVideoPayload 已含可选字段） ──

    private fun videoRaw(text: String?, turnId: Long? = null): String = MessageCodec.encode(
        ClientMessage.Video(
            seq = 12,
            timestamp = 1_712_345_682_000,
            payload = ClientVideoPayload(
                data = "AAAA", frameId = 7, width = 640, height = 480,
                orientation = 0, cameraFacing = CameraFacing.FRONT, quality = 70,
                turnId = turnId,
                text = text,
            ),
        ),
    )

    @Test
    fun `payload carries turn_id when set (explicit zero lands, values start at one)`() {
        // SDK 直通语义（docs/fd_pipeline_video_process.md §8.2.2）：显式 0 会落线（encodeDefaults=false
        // 只省略等于默认 null 的场合），由服务端视同缺省——默认 0 因此是 fail-inert。
        assertThat(videoRaw(null, turnId = 0)).contains("\"turn_id\":0")
        assertThat(videoRaw(null, turnId = 5)).contains("\"turn_id\":5")
    }

    @Test
    fun `payload carries text when set`() {
        assertThat(videoRaw("描述图片内容")).contains("\"text\":\"描述图片内容\"")
    }

    @Test
    fun `payload omits text when absent`() {
        // 相机帧不传 text：生成物该字段带默认值 + kotlinx `encodeDefaults=false`（MessageCodec
        // 未开该开关）→ 线上**完全不出现** text 字段；服务端按空值处理 → 仅入帧缓冲（行为与现状一致）。
        val raw = videoRaw(null)
        assertThat(raw).doesNotContain("\"text\"")
        assertThat(raw).contains("\"quality\":70")
    }
}
