package dev.livachat.app.ui.screens.conversation

import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.unit.IntSize
import org.junit.Assert.assertEquals
import org.junit.Test

class ImageViewerMathTest {

    private val eps = 0.001f

    // ── clampScale ────────────────────────────────────────────────

    @Test
    fun clampScale_below_min_clamps_to_fit() {
        assertEquals(1f, ImageViewerMath.clampScale(0.3f), eps)
    }

    @Test
    fun clampScale_boundaries_stay() {
        assertEquals(1f, ImageViewerMath.clampScale(ImageViewerMath.MIN_SCALE), eps)
        assertEquals(5f, ImageViewerMath.clampScale(ImageViewerMath.MAX_SCALE), eps)
    }

    @Test
    fun clampScale_above_max_clamps_to_five() {
        assertEquals(5f, ImageViewerMath.clampScale(8f), eps)
    }

    // ── fitSize ───────────────────────────────────────────────────

    @Test
    fun fitSize_landscape_image_width_limited() {
        // 640x480 横图进 1000x2000 竖屏视口 → 宽度贴满，高度 750（上下留黑）
        assertEquals(Size(1000f, 750f), ImageViewerMath.fitSize(640, 480, IntSize(1000, 2000)))
    }

    @Test
    fun fitSize_portrait_image_width_limited() {
        // 480x640 竖图进 1000x2000 → 宽度贴满，高度 1333.33
        val size = ImageViewerMath.fitSize(480, 640, IntSize(1000, 2000))
        assertEquals(1000f, size.width, eps)
        assertEquals(1333.333f, size.height, eps)
    }

    @Test
    fun fitSize_tall_viewport_height_limited() {
        // 800x800 方图进 1000x500 扁视口 → 高度受限，左右留黑
        assertEquals(Size(500f, 500f), ImageViewerMath.fitSize(800, 800, IntSize(1000, 500)))
    }

    @Test
    fun fitSize_unlaid_out_viewport_degrades_to_zero() {
        assertEquals(Size.Zero, ImageViewerMath.fitSize(640, 480, IntSize.Zero))
    }

    @Test
    fun fitSize_invalid_image_degrades_to_zero() {
        assertEquals(Size.Zero, ImageViewerMath.fitSize(0, 480, IntSize(1000, 2000)))
    }

    // ── offsetAfterZoom ───────────────────────────────────────────

    @Test
    fun offsetAfterZoom_keeps_centroid_content_point_fixed() {
        val viewport = IntSize(1000, 2000)
        val center = Offset(500f, 1000f)
        val oldScale = 2f
        val newScale = 3f
        val oldOffset = Offset(100f, -200f)
        val centroid = Offset(300f, 800f)

        val newOffset = ImageViewerMath.offsetAfterZoom(
            oldOffset, centroid, viewport, oldScale, newScale, Offset.Zero,
        )

        // 由旧状态反推 centroid 下的内容点，缩放后应仍映射到 centroid
        val content = center + (centroid - center - oldOffset) / oldScale
        val screenAfter = center + (content - center) * newScale + newOffset
        assertEquals(centroid.x, screenAfter.x, eps)
        assertEquals(centroid.y, screenAfter.y, eps)
    }

    @Test
    fun offsetAfterZoom_same_scale_only_adds_pan() {
        val result = ImageViewerMath.offsetAfterZoom(
            Offset(10f, 20f), Offset(111f, 222f), IntSize(1000, 2000), 3f, 3f, Offset(5f, -7f),
        )
        assertEquals(15f, result.x, eps)
        assertEquals(13f, result.y, eps)
    }

    @Test
    fun offsetAfterZoom_zoom_out_to_fit_then_clamp_resets_to_zero() {
        // 缩回 1x（锚点在偏移后的位置）→ clampOffset 归位居中
        val viewport = IntSize(1000, 2000)
        val zoomedOut = ImageViewerMath.offsetAfterZoom(
            Offset(300f, 100f), Offset(200f, 300f), viewport, 2f, 1f, Offset.Zero,
        )
        val clamped = ImageViewerMath.clampOffset(
            zoomedOut, Size(1000f, 750f), ImageViewerMath.MIN_SCALE, viewport,
        )
        assertEquals(Offset.Zero, clamped)
    }

    @Test
    fun offsetAfterZoom_invalid_old_scale_degrades_to_pan() {
        val result = ImageViewerMath.offsetAfterZoom(
            Offset(1f, 2f), Offset.Zero, IntSize(10, 10), 0f, 2f, Offset(3f, 4f),
        )
        assertEquals(4f, result.x, eps)
        assertEquals(6f, result.y, eps)
    }

    // ── clampOffset ───────────────────────────────────────────────

    @Test
    fun clampOffset_at_fit_scale_is_zero() {
        val result = ImageViewerMath.clampOffset(
            Offset(50f, 50f), Size(1000f, 750f), ImageViewerMath.MIN_SCALE, IntSize(1000, 2000),
        )
        assertEquals(Offset.Zero, result)
    }

    @Test
    fun clampOffset_clamps_to_displayed_bounds() {
        // fit 1000x750 @2x → displayed 2000x1500；maxX = 500，maxY = 0（高度仍小于视口）
        val result = ImageViewerMath.clampOffset(
            Offset(900f, 300f), Size(1000f, 750f), 2f, IntSize(1000, 2000),
        )
        assertEquals(500f, result.x, eps)
        assertEquals(0f, result.y, eps)
    }

    @Test
    fun clampOffset_clamps_negative_side() {
        val result = ImageViewerMath.clampOffset(
            Offset(-900f, 0f), Size(1000f, 750f), 2f, IntSize(1000, 2000),
        )
        assertEquals(-500f, result.x, eps)
    }

    @Test
    fun clampOffset_displayed_smaller_than_viewport_has_zero_max() {
        // 400x300 @2x = 800x600，两轴均小于视口 → 恒居中
        val result = ImageViewerMath.clampOffset(
            Offset(-10f, 10f), Size(400f, 300f), 2f, IntSize(1000, 2000),
        )
        assertEquals(Offset.Zero, result)
    }
}
