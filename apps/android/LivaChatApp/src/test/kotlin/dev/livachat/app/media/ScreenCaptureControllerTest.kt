package dev.livachat.app.media

import android.graphics.Bitmap
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import kotlin.math.abs

@RunWith(RobolectricTestRunner::class)
class ScreenCaptureControllerTest {

    // ── scaledCaptureSize（D6：等比缩放、长边 ≤ 默认上限、偶数） ────

    @Test
    fun `scaled size - device metrics downscaled to long side 1920 (default limit)`() {
        // 默认上限 1920：真机 1176×2400 → 940×1920（scale=0.8，抓屏长边上限提至 1920）
        val size = ScreenCaptureController.scaledCaptureSize(1176, 2400)
        assertEquals(940, size.width)
        assertEquals(1920, size.height)
        assertEvenAndWithinLimit(size, 1176, 2400, 1920)
    }

    @Test
    fun `scaled size - portrait 1080x2392 downscaled to long side 1920`() {
        val size = ScreenCaptureController.scaledCaptureSize(1080, 2392)
        assertEquals(866, size.width)
        assertEquals(1920, size.height)
        assertEvenAndWithinLimit(size, 1080, 2392, 1920)
    }

    @Test
    fun `scaled size - explicit smaller limit still honored`() {
        // 上限是参数：显式 1080 仍按 1080 缩放（算法与常量解耦）
        val size = ScreenCaptureController.scaledCaptureSize(720, 1280, maxLongSide = 1080)
        assertEquals(606, size.width)
        assertEquals(1080, size.height)
        assertEvenAndWithinLimit(size, 720, 1280, 1080)
    }

    @Test
    fun `scaled size - small screen keeps original size`() {
        val size = ScreenCaptureController.scaledCaptureSize(480, 640)
        assertEquals(480, size.width)
        assertEquals(640, size.height)
    }

    @Test
    fun `scaled size - landscape input uses max dimension as long side`() {
        val size = ScreenCaptureController.scaledCaptureSize(2400, 1080)
        assertEquals(1920, size.width)
        assertEquals(864, size.height)
    }

    @Test
    fun `scaled size - odd dimensions rounded down to even without upscaling`() {
        val size = ScreenCaptureController.scaledCaptureSize(641, 479)
        assertEquals(640, size.width)
        assertEquals(478, size.height)
    }

    @Test
    fun `scaled size - invalid input degrades to minimal capture`() {
        val size = ScreenCaptureController.scaledCaptureSize(0, 0)
        assertEquals(2, size.width)
        assertEquals(2, size.height)
    }

    private fun assertEvenAndWithinLimit(size: CaptureSize, realW: Int, realH: Int, limit: Int = 1920) {
        assertTrue("width even", size.width % 2 == 0)
        assertTrue("height even", size.height % 2 == 0)
        assertTrue("long side <= $limit", maxOf(size.width, size.height) <= limit)
        val realRatio = realW.toDouble() / realH
        val scaledRatio = size.width.toDouble() / size.height
        assertTrue("aspect ratio drift", abs(realRatio - scaledRatio) < 0.01)
    }

    // ── decideFrameToPush（D7/D8 keepalive 取舍） ──────────────────

    private fun frame(marker: Int) = ScreenFrame(byteArrayOf(marker.toByte()), marker, marker)

    @Test
    fun `tick with new frame pushes the new frame and promotes it to cache`() {
        val newFrame = frame(1)
        val cached = frame(2)
        var pushed: ScreenFrame? = null

        val newCache = ScreenCaptureController.decideFrameToPush(newFrame, cached) { pushed = it }

        assertSame(newFrame, pushed)
        assertSame(newFrame, newCache)
    }

    @Test
    fun `tick without new frame resends cached frame (keepalive)`() {
        val cached = frame(2)
        var pushed: ScreenFrame? = null

        val newCache = ScreenCaptureController.decideFrameToPush(null, cached) { pushed = it }

        assertSame(cached, pushed)
        assertSame(cached, newCache)
    }

    @Test
    fun `tick with no frame at all skips push`() {
        var pushCount = 0
        val newCache = ScreenCaptureController.decideFrameToPush(null, null) { pushCount++ }

        assertNull(newCache)
        assertEquals(0, pushCount)
    }

    // ── shouldPushFrame（v2 门控判定，D12/D14） ─────────────────────
    // 矩阵全覆盖：前台 × 任意 → false；后台 + 交互 + 未锁屏 → true；
    // 后台 + 非交互 → false；后台 + 交互 + 锁屏 → false

    private data class GateCase(
        val appForeground: Boolean,
        val isInteractive: Boolean,
        val isKeyguardLocked: Boolean,
        val expected: Boolean,
        val description: String,
    )

    @Test
    fun `gate - only background interactive unlocked screen may push (full matrix)`() {
        val cases = listOf(
            GateCase(true, true, false, false, "前台 + 交互 + 未锁屏"),
            GateCase(true, true, true, false, "前台 + 交互 + 锁屏"),
            GateCase(true, false, false, false, "前台 + 熄屏"),
            GateCase(true, false, true, false, "前台 + 熄屏 + keyguard 误报"),
            GateCase(false, true, false, true, "后台 + 交互 + 未锁屏（唯一放行）"),
            GateCase(false, true, true, false, "后台 + 交互 + 锁屏（含滑动锁屏 showing 语义）"),
            GateCase(false, false, false, false, "后台 + 熄屏/doze/AOD"),
            GateCase(false, false, true, false, "后台 + 熄屏 + keyguard 误报"),
        )
        cases.forEach { c ->
            val actual = ScreenCaptureController.shouldPushFrame(
                c.appForeground,
                c.isInteractive,
                c.isKeyguardLocked,
            )
            assertEquals("${c.description}（foreground=${c.appForeground}, " +
                "interactive=${c.isInteractive}, keyguardLocked=${c.isKeyguardLocked}）", c.expected, actual)
        }
    }

    // ── needsResize（v3 D15/D16：rotation 变化适配判定） ─────────────

    @Test
    fun `needs resize - null current size requires baseline init`() {
        assertTrue(ScreenCaptureController.needsResize(null, 486, 1080))
    }

    @Test
    fun `needs resize - same size is no-op`() {
        val current = CaptureSize(486, 1080)
        assertFalse(ScreenCaptureController.needsResize(current, 486, 1080))
    }

    @Test
    fun `needs resize - portrait to landscape swap requires resize`() {
        // 竖 1080×2392 → 横 2392×1080（scaledCaptureSize 后的 CaptureSize，默认上限 1920）
        val portrait = ScreenCaptureController.scaledCaptureSize(1080, 2392)
        val landscape = ScreenCaptureController.scaledCaptureSize(2392, 1080)
        assertEquals(866, portrait.width)
        assertEquals(1920, portrait.height)
        assertTrue(ScreenCaptureController.needsResize(portrait, landscape.width, landscape.height))
        assertTrue(ScreenCaptureController.needsResize(landscape, portrait.width, portrait.height))
    }

    @Test
    fun `needs resize - any dimension change triggers resize`() {
        val current = CaptureSize(1080, 486)
        assertTrue(ScreenCaptureController.needsResize(current, 1080, 488))
        assertTrue(ScreenCaptureController.needsResize(current, 1078, 486))
    }

    @Test
    fun `needs resize - inverted 180 rotation keeps size so no resize`() {
        // 180° 旋转前后逻辑尺寸相同（scaledCaptureSize 不变）→ 零动作（平台镜像自动跟随）
        val size = CaptureSize(486, 1080)
        assertFalse(ScreenCaptureController.needsResize(size, 486, 1080))
    }

    // ── nextResizeFailureCount（v3.1：resize 连续失败收敛） ───────────

    @Test
    fun `resize failure - consecutive failures count toward limit`() {
        assertEquals(1, ScreenCaptureController.nextResizeFailureCount(0))
        assertEquals(2, ScreenCaptureController.nextResizeFailureCount(1))
    }

    @Test
    fun `resize failure - third consecutive failure returns null to escalate`() {
        // 达限 → null 信号：调用方必须升级 stop + 错误上报（§8 不得静默无限重试）
        assertNull(ScreenCaptureController.nextResizeFailureCount(2))
    }

    @Test
    fun `resize failure - default threshold is three and custom threshold honored`() {
        assertNull(ScreenCaptureController.nextResizeFailureCount(2))
        assertNull(ScreenCaptureController.nextResizeFailureCount(0, maxFailures = 1))
        assertEquals(1, ScreenCaptureController.nextResizeFailureCount(0, maxFailures = 2))
    }

    @Test
    fun `resize failure - failures beyond limit keep signaling escalate`() {
        assertNull(ScreenCaptureController.nextResizeFailureCount(4))
        assertNull(ScreenCaptureController.nextResizeFailureCount(9))
    }

    // ── captureDecision（v2 点按抓取决策，§4.1/§8.1） ────────────────
    // 决策与副作用分离：本表驱动覆盖"未录制/在飞/防连点/门控拒绝（含原因文案）/
    // 放行"全部分支；隐藏/排空/取帧的 platform 部分留真机（§9.2）。

    private fun decision(
        isRecording: Boolean = true,
        capturing: Boolean = false,
        nowMs: Long = 10_000,
        lastCaptureMs: Long = 0,
        appForeground: Boolean = false,
        isInteractive: Boolean = true,
        isKeyguardLocked: Boolean = false,
        waiting: Boolean = false,
    ) = ScreenCaptureController.captureDecision(
        isRecording = isRecording,
        capturing = capturing,
        nowMs = nowMs,
        lastCaptureMs = lastCaptureMs,
        appForeground = appForeground,
        isInteractive = isInteractive,
        isKeyguardLocked = isKeyguardLocked,
        waiting = waiting,
    )

    // ── endsWait（v4 D6：本轮 ai_text 判据，§4.3） ──────────────────

    @Test
    fun `ends wait - non-agent first chunk hits`() {
        assertTrue(ScreenCaptureController.endsWait(null, 0L))
        assertTrue(ScreenCaptureController.endsWait("llm", 0L))
    }

    @Test
    fun `ends wait - agent pushes and later chunks miss`() {
        assertFalse(ScreenCaptureController.endsWait("agent", 0L))
        assertFalse(ScreenCaptureController.endsWait(null, 1L))
        assertFalse(ScreenCaptureController.endsWait("agent", 3L))
    }

    @Test
    fun `decision - not recording ignores tap`() {
        assertEquals(
            CaptureDecision.Ignore,
            decision(isRecording = false),
        )
    }

    @Test
    fun `decision - in-flight capture ignores tap`() {
        assertEquals(
            CaptureDecision.Ignore,
            decision(capturing = true),
        )
    }

    @Test
    fun `decision - waiting ignores tap and beats every other branch`() {
        // v4 D7：等待期不允许再次点击——压过在飞/防连点/门控拒绝（三者叠加仍是 IgnoreWaiting）
        assertEquals(CaptureDecision.IgnoreWaiting, decision(waiting = true))
        assertEquals(CaptureDecision.IgnoreWaiting, decision(waiting = true, capturing = true))
        assertEquals(
            CaptureDecision.IgnoreWaiting,
            decision(waiting = true, nowMs = 10_000, lastCaptureMs = 10_000),
        )
        assertEquals(CaptureDecision.IgnoreWaiting, decision(waiting = true, appForeground = true))
        assertEquals(
            CaptureDecision.IgnoreWaiting,
            decision(waiting = true, isInteractive = false, isKeyguardLocked = true),
        )
    }

    @Test
    fun `decision - not recording still wins over waiting`() {
        assertEquals(
            CaptureDecision.Ignore,
            decision(isRecording = false, waiting = true),
        )
    }

    @Test
    fun `decision - min interval ignores rapid second tap`() {
        assertEquals(
            CaptureDecision.Ignore,
            decision(nowMs = 10_000, lastCaptureMs = 10_000 - 599),
        )
        assertEquals(
            CaptureDecision.StartCapture,
            decision(nowMs = 10_000, lastCaptureMs = 10_000 - 600),
        )
    }

    @Test
    fun `decision - foreground app rejects with foreground copy`() {
        assertEquals(
            CaptureDecision.Reject(CaptureFeedback.REJECTED_FOREGROUND),
            decision(appForeground = true),
        )
    }

    @Test
    fun `decision - locked or non-interactive screen rejects with locked copy`() {
        assertEquals(
            CaptureDecision.Reject(CaptureFeedback.REJECTED_LOCKED),
            decision(isKeyguardLocked = true),
        )
        assertEquals(
            CaptureDecision.Reject(CaptureFeedback.REJECTED_LOCKED),
            decision(isInteractive = false),
        )
    }

    @Test
    fun `decision - background interactive unlocked screen starts capture`() {
        assertEquals(
            CaptureDecision.StartCapture,
            decision(),
        )
    }

    // ── rgbaToBitmap（§8：剥离 rowStride 行对齐 padding） ───────────

    @Test
    fun `rgba to bitmap - strips row padding and maps pixel channels`() {
        val width = 2
        val height = 2
        val rowStride = 12 // 8B 数据 + 4B padding
        val pixelStride = 4
        // pixelStride=4 时像素连续，行尾 4B 为 padding
        // row0: p(0,0), p(1,0) | pad；row1: p(0,1), p(1,1) | pad
        fun b(vararg values: Int) = ByteArray(values.size) { values[it].toByte() }
        val pixels = b(
            10, 20, 30, 255, 40, 50, 60, 255, 0, 0, 0, 0,
            70, 80, 90, 255, 100, 110, 120, 255, 0, 0, 0, 0,
        )

        val bitmap = ScreenCaptureController.rgbaToBitmap(pixels, width, height, rowStride, pixelStride)

        assertEquals(width, bitmap.width)
        assertEquals(height, bitmap.height)
        // 每像素 4 通道字节必须与平面输入一致（RGBA 序原样落位），行尾 padding 段被剥离
        assertArrayEquals(pixels.copyOfRange(0, 4), storedPixelBytes(bitmap, 0, 0))
        assertArrayEquals(pixels.copyOfRange(4, 8), storedPixelBytes(bitmap, 1, 0))
        assertArrayEquals(pixels.copyOfRange(12, 16), storedPixelBytes(bitmap, 0, 1))
        assertArrayEquals(pixels.copyOfRange(16, 20), storedPixelBytes(bitmap, 1, 1))
    }

    @Test
    fun `rgba to bitmap - full image round trip preserves pixels`() {
        val width = 4
        val height = 3
        val rowStride = 20 // 16B 数据 + 4B padding
        val pixels = ByteArray(rowStride * height) { (it % 256).toByte() }
        val bitmap = ScreenCaptureController.rgbaToBitmap(pixels, width, height, rowStride, 4)

        assertEquals(width, bitmap.width)
        assertEquals(height, bitmap.height)
        for (y in 0 until height) {
            for (x in 0 until width) {
                val i = y * rowStride + x * 4
                assertArrayEquals(pixels.copyOfRange(i, i + 4), storedPixelBytes(bitmap, x, y))
            }
        }
    }

    @Test
    fun `rgba to bitmap - source bytes are not mutated`() {
        val pixels = byteArrayOf(1, 2, 3, 4, 5, 6, 7, 8)
        val copy = pixels.copyOf()
        ScreenCaptureController.rgbaToBitmap(pixels, 1, 1, 8, 4)
        assertArrayEquals(copy, pixels)
    }

    private fun storedPixelBytes(bitmap: Bitmap, x: Int, y: Int): ByteArray =
        ByteBuffer.allocate(4).order(ByteOrder.BIG_ENDIAN)
            .putInt(bitmap.getPixel(x, y)).array()
}
