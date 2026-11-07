package dev.livachat.sdk

import dev.livachat.protocol.generated.CameraFacing
import dev.livachat.sdk.model.Orientation
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * video 帧 payload 组装（fd_pipeline_video_process.md §8）：turnId 参数直通落线（App 层维护、
 * push 逐帧传入；0 = 未启用）+ camera_facing 四值映射（"screen" = 屏幕采集、"gallery" = 图库照片）。
 */
class VideoTurnPayloadTest {

    @Test
    fun mapCameraFacing_fourValues() {
        assertEquals(CameraFacing.FRONT, mapCameraFacing("front"))
        assertEquals(CameraFacing.BACK, mapCameraFacing("back"))
        assertEquals(CameraFacing.SCREEN, mapCameraFacing("screen"))
        assertEquals(CameraFacing.GALLERY, mapCameraFacing("gallery"))
    }

    @Test
    fun mapCameraFacing_unknownFallsBackToFront() {
        // 旧行为保留：未知值兜底 FRONT（历史占位逻辑）
        assertEquals(CameraFacing.FRONT, mapCameraFacing("whatever"))
    }

    @Test
    fun buildVideoPayload_carriesTurnIdAndScreenFacing() {
        val payload = buildVideoPayload(
            jpegB64 = "AAAA", frameId = 7L, width = 640, height = 480,
            orientation = Orientation.PORTRAIT_0, cameraFacing = "screen",
            quality = 70, turnId = 3L, text = null,
        )
        assertEquals(3L, payload.turnId)
        assertEquals(CameraFacing.SCREEN, payload.cameraFacing)
        assertEquals(70, payload.quality)
        assertNull(payload.text)
    }

    @Test
    fun buildVideoPayload_keepsOrientationDegreesAndText() {
        val payload = buildVideoPayload(
            jpegB64 = "AAAA", frameId = 8L, width = 640, height = 480,
            orientation = Orientation.LANDSCAPE_90, cameraFacing = "back",
            quality = 70, turnId = 4L, text = "描述图片内容",
        )
        assertEquals(90, payload.orientation)
        assertEquals("描述图片内容", payload.text)
        assertEquals(CameraFacing.BACK, payload.cameraFacing)
    }

    @Test
    fun buildVideoPayload_carriesGalleryFacing() {
        val payload = buildVideoPayload(
            jpegB64 = "AAAA", frameId = 9L, width = 640, height = 480,
            orientation = Orientation.PORTRAIT_0, cameraFacing = "gallery",
            quality = 70, turnId = 0L, text = null,
        )
        assertEquals(CameraFacing.GALLERY, payload.cameraFacing)
    }
}
