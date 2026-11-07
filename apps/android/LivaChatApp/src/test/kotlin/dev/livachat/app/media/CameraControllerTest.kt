package dev.livachat.app.media

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class CameraControllerTest {

    // ── scaledMaxLongSide（长边 ≤1280 硬闸：等比、只缩不放） ──────────

    @Test
    fun `max long side - 1280x960 target resolution never scales`() {
        assertNull(CameraController.scaledMaxLongSide(1280, 960))
    }

    @Test
    fun `max long side - smaller fallback resolution never scales`() {
        assertNull(CameraController.scaledMaxLongSide(640, 480))
    }

    @Test
    fun `max long side - exactly at limit is no-op`() {
        assertNull(CameraController.scaledMaxLongSide(1280, 720))
        assertNull(CameraController.scaledMaxLongSide(720, 1280))
    }

    @Test
    fun `max long side - landscape fallback 1600x1200 scales to 1280x960`() {
        assertEquals(CaptureSize(1280, 960), CameraController.scaledMaxLongSide(1600, 1200))
    }

    @Test
    fun `max long side - portrait after rotation 1200x1600 scales to 960x1280`() {
        assertEquals(CaptureSize(960, 1280), CameraController.scaledMaxLongSide(1200, 1600))
    }

    @Test
    fun `max long side - 1920x1080 keeps aspect ratio`() {
        assertEquals(CaptureSize(1280, 720), CameraController.scaledMaxLongSide(1920, 1080))
    }

    @Test
    fun `max long side - extreme ratio clamped to at least 1 pixel`() {
        assertEquals(CaptureSize(1280, 1), CameraController.scaledMaxLongSide(4000, 3))
    }

    // ── shouldSkipWarmup（§8.3 首帧暖机：距流开始 < 1000ms 丢帧） ──────

    @Test
    fun `warmup - elapsed 0 (first frame of stream) is skipped`() {
        assertTrue(CameraController.shouldSkipWarmup(0))
    }

    @Test
    fun `warmup - just inside the window is skipped`() {
        assertTrue(CameraController.shouldSkipWarmup(999))
    }

    @Test
    fun `warmup - at boundary of default window (1000ms) passes`() {
        assertFalse(CameraController.shouldSkipWarmup(1000))
    }

    @Test
    fun `warmup - beyond the window passes`() {
        assertFalse(CameraController.shouldSkipWarmup(1001))
    }

    // ── shouldBumpTurn（§8.2.2 P2-1：镜头切换 = 新采集轮，与切换后首帧同帧递增） ──

    @Test
    fun `turn bump - first pushed frame (null) never bumps`() {
        assertFalse(CameraController.shouldBumpTurn(null, "front"))
        assertFalse(CameraController.shouldBumpTurn(null, "back"))
    }

    @Test
    fun `turn bump - same lens never bumps (auto rebind, camera reopen same lens)`() {
        assertFalse(CameraController.shouldBumpTurn("front", "front"))
        assertFalse(CameraController.shouldBumpTurn("back", "back"))
    }

    @Test
    fun `turn bump - lens change bumps (switch)`() {
        assertTrue(CameraController.shouldBumpTurn("front", "back"))
        assertTrue(CameraController.shouldBumpTurn("back", "front"))
    }
}
