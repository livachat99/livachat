package dev.livachat.app.media

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.Assert.assertNotNull
import dev.livachat.app.R
import org.robolectric.RobolectricTestRunner

/**
 * docs/android_screen_recording.md §4.2/§8.1：浮窗纯函数——
 * 吸附（snapSide/snapX）、纵向夹取（clampY）、点按判定（isTap）、提示文案。
 *
 * 窗口加入/移除、触摸手势、抓帧序列的 platform 部分留给真机（§9.2）。
 */
@RunWith(RobolectricTestRunner::class)
class ScreenCaptureBubbleTest {

    // ── snapSide：拖拽松手贴最近边 ─────────────────────────────────

    @Test
    fun `snap side - left half snaps to left`() {
        assertEquals(Side.LEFT, ScreenCaptureBubble.snapSide(centerX = 100, screenWidth = 1080))
        assertEquals(Side.LEFT, ScreenCaptureBubble.snapSide(centerX = 540, screenWidth = 1080))
    }

    @Test
    fun `snap side - right half snaps to right`() {
        assertEquals(Side.RIGHT, ScreenCaptureBubble.snapSide(centerX = 541, screenWidth = 1080))
        assertEquals(Side.RIGHT, ScreenCaptureBubble.snapSide(centerX = 1080, screenWidth = 1080))
    }

    // ── snapX：贴边落位（含边距） ──────────────────────────────────

    @Test
    fun `snap x - left edge honors margin`() {
        assertEquals(
            12,
            ScreenCaptureBubble.snapX(Side.LEFT, screenWidth = 1080, bubbleWidth = 168, edgeMargin = 12),
        )
    }

    @Test
    fun `snap x - right edge hugs screen minus bubble and margin`() {
        assertEquals(
            900,
            ScreenCaptureBubble.snapX(Side.RIGHT, screenWidth = 1080, bubbleWidth = 168, edgeMargin = 12),
        )
    }

    @Test
    fun `snap x - tiny screen degrades to margin without negative`() {
        assertEquals(
            12,
            ScreenCaptureBubble.snapX(Side.RIGHT, screenWidth = 100, bubbleWidth = 168, edgeMargin = 12),
        )
    }

    // ── clampY：纵向可停区间 ──────────────────────────────────────

    @Test
    fun `clamp y - clamps into top and bottom margins`() {
        assertEquals(
            24,
            ScreenCaptureBubble.clampY(-50, screenHeight = 2400, bubbleHeight = 168, topMargin = 24, bottomMargin = 24),
        )
        assertEquals(
            2208,
            ScreenCaptureBubble.clampY(9999, screenHeight = 2400, bubbleHeight = 168, topMargin = 24, bottomMargin = 24),
        )
    }

    @Test
    fun `clamp y - keeps value inside range`() {
        assertEquals(
            1200,
            ScreenCaptureBubble.clampY(1200, screenHeight = 2400, bubbleHeight = 168, topMargin = 24, bottomMargin = 24),
        )
    }

    @Test
    fun `clamp y - degenerate screen collapses to top margin`() {
        assertEquals(
            24,
            ScreenCaptureBubble.clampY(500, screenHeight = 100, bubbleHeight = 168, topMargin = 24, bottomMargin = 24),
        )
    }

    // ── isTap：点击 / 拖拽判定（touch-slop） ───────────────────────

    @Test
    fun `is tap - within slop counts as tap`() {
        assertTrue(ScreenCaptureBubble.isTap(0, 0, slop = 8))
        assertTrue(ScreenCaptureBubble.isTap(8, -8, slop = 8))
    }

    @Test
    fun `is tap - beyond slop is a drag`() {
        assertFalse(ScreenCaptureBubble.isTap(9, 0, slop = 8))
        assertFalse(ScreenCaptureBubble.isTap(0, -40, slop = 8))
    }

    // ── 脉动相位 pulseFraction（v1.17：1fps 下的均匀步进） ──────────

    private fun fraction(t: Long, rampMs: Long = 2_000L, enabled: Boolean = true) =
        ScreenCaptureBubble.pulseFraction(t, rampMs, enabled)

    @Test
    fun `pulse - 1fps sampling lands on four even steps`() {
        // tick=1000ms、单程 2000ms：采样恰为 白(f=0) → 中(0.5) → 紫(1) → 中(0.5) → 白
        assertEquals(0f, fraction(0), 1e-4f)
        assertEquals(0.5f, fraction(1_000), 1e-4f)
        assertEquals(1f, fraction(2_000), 1e-4f)
        assertEquals(0.5f, fraction(3_000), 1e-4f)
        assertEquals(0f, fraction(4_000), 1e-4f)
    }

    @Test
    fun `pulse - monotonic within each ramp half`() {
        val up = listOf(0L, 500L, 1_000L, 1_500L, 2_000L).map { fraction(it) }
        assertEquals(up.sorted(), up)
        val down = listOf(2_000L, 2_500L, 3_000L, 3_500L, 4_000L).map { fraction(it) }
        assertEquals(down.sortedDescending(), down)
    }

    @Test
    fun `pulse - disabled (animator scale 0) stays white`() {
        listOf(0L, 1_000L, 2_000L, 3_000L, 7_777L).forEach {
            assertEquals(0f, fraction(it, enabled = false), 1e-4f)
        }
    }

    @Test
    fun `pulse - wraps negative and huge timestamps safely`() {
        assertEquals(fraction(0), fraction(4_000), 1e-4f)
        assertEquals(fraction(1_000), fraction(1_000 + 4_000 * 1_000), 1e-4f)
        // 负时间戳按周期回绕（-1000 ≡ 3000，落在下降段中点）
        assertEquals(fraction(3_000), fraction(-1_000), 1e-4f)
        assertTrue(fraction(-1_000) in 0f..1f)
    }

    // ── 等待态旋转相位 spinAngleDeg（v4 §4.2） ─────────────────────

    private fun angle(t: Long, period: Long = 1_200L) = ScreenCaptureBubble.spinAngleDeg(t, period)

    @Test
    fun `spin - linear quarters map to 0 90 180 270`() {
        assertEquals(0f, angle(0), 1e-3f)
        assertEquals(90f, angle(300), 1e-3f)
        assertEquals(180f, angle(600), 1e-3f)
        assertEquals(270f, angle(900), 1e-3f)
    }

    @Test
    fun `spin - tick step is even (period = 24 ticks)`() {
        // 周期 1200ms = 24 × 50ms → 每 tick 恰 +15°（低帧率下步进均匀，同 pulseFraction 的要求）
        val up = listOf(0L, 50L, 100L, 1_100L, 1_150L).map { angle(it) }
        assertEquals(up.sorted(), up)
        // 逐步长（不含回绕点：t=1150 的下一 tick 是周期边界）
        listOf(0L, 50L, 100L, 1_100L).forEach { t ->
            assertEquals(15f, angle(t + 50) - angle(t), 1e-3f)
        }
        assertEquals(345f, angle(1_150), 1e-3f)
    }

    @Test
    fun `spin - wraps at period and stays in range`() {
        assertEquals(0f, angle(1_200), 1e-3f)
        assertEquals(angle(300), angle(300 + 1_200 * 1_000), 1e-3f)
        listOf(0L, 1L, 1_199L, 1_200L, 99_999L).forEach { t ->
            assertTrue("t=$t → ${angle(t)}", angle(t) in 0f..360f)
        }
    }

    @Test
    fun `spin - negative timestamps wrap and degenerate period is safe`() {
        // 负时间戳按周期回绕（-300 ≡ 900 → 270°）
        assertEquals(angle(900), angle(-300), 1e-3f)
        assertTrue(angle(-1) in 0f..360f)
        assertEquals(0f, angle(123, period = 0), 1e-3f)
        assertEquals(0f, angle(123, period = -5), 1e-3f)
    }

    // ── 提示文案（§5.2） ──────────────────────────────────────────

    @Test
    fun `pill text - every feedback has copy and sent is the v3 copy`() {
        CaptureFeedback.entries.forEach { feedback ->
            assertNotNull("$feedback 必须有文案资源", ScreenCaptureBubble.pillTextRes(feedback))
        }
        // v3 §4.3 D8：文本随帧即时消费，5s 措辞作废
        assertEquals(R.string.cap_bubble_sent, ScreenCaptureBubble.pillTextRes(CaptureFeedback.SENT))
    }
}
