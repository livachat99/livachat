package dev.livachat.app.ui.screens.conversation

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.net.Uri
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.pinch
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.ui.theme.LivaChatTheme
import java.io.File
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * docs/fd_avator_customization.md §6（真机验证）：裁剪页缩到最小档时图片短边仍覆盖裁剪圆
 * （`minScale = 2×cropCirclePx/minDim`）。修复前短边仅为圆直径的一半（=半径 R），
 * 圆内在短轴方向留空。用 3:1 纯红测试图捏合缩小后，裁剪圆中心 ±0.7R 的采样点应为图像色。
 * 裁剪圆圆心 = 屏幕内容中心（顶栏与底部按钮等高，各 80dp）。
 */
class AvatarCropScreenPinchTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun crop_image_still_covers_circle_at_min_zoom() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val radiusPx = 96f * context.resources.displayMetrics.density

        // 3:1 横图（短边=高=100px）：最小档放大 2R/100 后高度恰 2R；修复前只有 R
        val src = File(context.cacheDir, "crop_pinch_test.png")
        val bmp = Bitmap.createBitmap(300, 100, Bitmap.Config.ARGB_8888)
        Canvas(bmp).drawColor(Color.RED)
        src.outputStream().use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }

        composeTestRule.setContent {
            LivaChatTheme {
                AvatarCropScreen(sourceUri = Uri.fromFile(src), onConfirm = {}, onCancel = {})
            }
        }

        // 捏合缩小（净倍率 ≈ 0.15，必然触达 minScale 并被 coerce）；起点随屏宽自适应，窄屏不出界
        composeTestRule.onRoot().performTouchInput {
            val dx = minOf(400f, center.x * 0.6f)
            pinch(
                start0 = center + Offset(-dx, 0f),
                end0 = center + Offset(-dx * 0.15f, 0f),
                start1 = center + Offset(dx, 0f),
                end1 = center + Offset(dx * 0.15f, 0f),
            )
        }
        composeTestRule.waitForIdle()

        val pixels = composeTestRule.onRoot().captureToImage().toPixelMap()
        val cx = pixels.width / 2f
        val cy = pixels.height / 2f
        val d = radiusPx * 0.7f

        val samples = listOf(
            Offset(cx, cy - d), Offset(cx, cy + d),   // 短轴（判别点：修复前为背景色）
            Offset(cx - d, cy), Offset(cx + d, cy),   // 长轴
        )
        for (p in samples) {
            val c = pixels[p.x.toInt(), p.y.toInt()]
            // 图像为纯红；背景 Background(#0F172A) 与遮罩(0.6α 黑)均不含红色分量
            assertTrue(
                "裁剪圆内在 ($p) 留空：采样色 r=${c.red} g=${c.green} b=${c.blue}（应为图像红）",
                c.red > 0.7f && c.green < 0.3f && c.blue < 0.3f,
            )
        }
    }
}
