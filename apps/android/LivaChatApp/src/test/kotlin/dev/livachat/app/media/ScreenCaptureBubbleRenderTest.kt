package dev.livachat.app.media

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.os.SystemClock
import android.provider.Settings
import android.view.View
import kotlin.math.cos
import kotlin.math.sin
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * v4 等待态环的**渲染级**验证（docs/android_screen_recording.md §8.1）：
 * 直绘 [ScreenCaptureBubble.BubbleView] 到 Bitmap 后按角度采样像素——缺口弧的
 * "有绿/无绿"分布、动画缩放=0 的静止整圈、非等待态无环、以及"等待态不吞胶囊"
 * （§4.2 注意⑤）四条。
 *
 * ⚠ 必须 NATIVE graphics：LEGACY 模式不真画，像素断言无意义。
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
// 像素级渲染几何断言依赖胶囊文案宽度：固定 zh 资源，避免宿主/Robolectric 默认 locale 漂移（i18n 后须钉）
@Config(qualifiers = "b+zh+Hans")
class ScreenCaptureBubbleRenderTest {

    private val context: Context get() = RuntimeEnvironment.getApplication()
    private val density: Float get() = context.resources.displayMetrics.density

    /** 环几何（与实现同源常量：标记 32dp、环线宽 3dp、热区 56dp） */
    private val centerPx: Float get() = 56f * density / 2f
    private val ringRadiusPx: Float get() = (32f * density) / 2f + (3f * density) / 2f

    private fun render(
        animatorScale: Float = 1f,
        configure: (ScreenCaptureBubble.BubbleView) -> Unit = {},
    ): Pair<ScreenCaptureBubble.BubbleView, Bitmap> {
        Settings.Global.putFloat(
            context.contentResolver,
            Settings.Global.ANIMATOR_DURATION_SCALE,
            animatorScale,
        )
        val bubble = ScreenCaptureBubble(context)
        val view = bubble.BubbleView()
        configure(view)
        view.measure(
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
            View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED),
        )
        view.layout(0, 0, view.measuredWidth, view.measuredHeight)
        val bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        view.draw(Canvas(bitmap))
        return view to bitmap
    }

    /** 环上某角度（Canvas 约定：0° = 3 点钟方向，顺时针为正）的像素。 */
    private fun ringPixel(bitmap: Bitmap, angleDeg: Float): Int {
        val rad = Math.toRadians(angleDeg.toDouble())
        val x = (centerPx + ringRadiusPx * cos(rad)).toInt()
        val y = (centerPx + ringRadiusPx * sin(rad)).toInt()
        return bitmap.getPixel(x, y)
    }

    /** 该角度是否落在环上：环是不透明的，标记黑底只有 0x59 alpha。 */
    private fun isRingAt(bitmap: Bitmap, angleDeg: Float): Boolean =
        Color.alpha(ringPixel(bitmap, angleDeg)) > 200

    private fun ringSamples(bitmap: Bitmap): Int =
        (0 until 72).count { isRingAt(bitmap, it * 5f) }

    @Test
    fun `waiting ring - 270 degree arc with a 90 degree gap, phase from wall clock`() {
        val (_, bitmap) = render { it.waiting = true }

        val covered = ringSamples(bitmap)
        // 270° 弧 → 覆盖 54/72 个采样点（±2 容忍圆头端点与取整）
        assertTrue("covered=$covered/72", covered in 52..56)

        // 相位与实现同源（同一墙钟纯函数；绘制与采样相隔毫秒级，135°/45° 的余量足够）
        val phase = ScreenCaptureBubble.spinAngleDeg(SystemClock.uptimeMillis())
        assertTrue("弧中段（相位+135°）应为绿", isRingAt(bitmap, phase + 135f))
        assertTrue("缺口正中（相位+315°）应无绿", !isRingAt(bitmap, phase + 315f))
    }

    @Test
    fun `waiting ring - animator scale zero degrades to a static full circle`() {
        // D5：系统关动画 → 静止整圈（仍区别于常态：环在且持久）
        val (_, bitmap) = render(animatorScale = 0f) { it.waiting = true }
        assertEquals(72, ringSamples(bitmap))
    }

    @Test
    fun `idle bubble draws no ring`() {
        val (_, bitmap) = render()
        assertEquals(0, ringSamples(bitmap))
    }

    @Test
    fun `waiting does not swallow the sent pill`() {
        // §4.2 注意⑤：环与胶囊拆开画——等待态与 SENT 反馈同刻，胶囊必须照常显示
        val (_, bitmap) = render {
            it.maxPillTextWidth = 400
            it.feedback = CaptureFeedback.SENT
            it.waiting = true
        }

        // 胶囊在标记右侧：left = (56 - 12)dp + 8dp = 52dp，高 32dp 居中
        val pillX = (52f * density + 20f).toInt()
        val pillY = (28f * density).toInt()
        val pillPixel = bitmap.getPixel(pillX, pillY)
        assertTrue("胶囊底色应为不透明深色（alpha=${Color.alpha(pillPixel)}）", Color.alpha(pillPixel) > 200)
        assertTrue("胶囊底色应为深色", Color.red(pillPixel) < 80 && Color.green(pillPixel) < 80)

        // 同帧环仍在转（弧覆盖约 3/4 圈）
        val covered = ringSamples(bitmap)
        assertTrue("covered=$covered/72", covered in 52..56)
    }
}
