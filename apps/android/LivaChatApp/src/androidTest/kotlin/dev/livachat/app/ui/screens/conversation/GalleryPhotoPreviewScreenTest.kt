package dev.livachat.app.ui.screens.conversation

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.net.Uri
import androidx.activity.ComponentActivity
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.test.TouchInjectionScope
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.R
import dev.livachat.app.ui.theme.LivaChatTheme
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.io.FileOutputStream

class GalleryPhotoPreviewScreenTest {

    @get:Rule
    val composeTestRule = createAndroidComposeRule<ComponentActivity>()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val tempFiles = mutableListOf<File>()

    @After
    fun tearDown() {
        tempFiles.forEach { it.delete() }
    }

    private fun str(id: Int) = context.getString(id)
    private fun sendLabel() = str(R.string.conv_gallery_send)
    private fun cancelLabel() = str(R.string.common_cancel)
    private fun backLabel() = str(R.string.common_back)
    private fun failedLabel() = str(R.string.conv_gallery_preview_failed)

    @Test
    fun buttons_are_reachable_and_send_enabled_after_load() {
        var sent = 0
        var cancelled = 0
        var backToPicker = 0
        val imageUri = writeJpeg()
        composeTestRule.setContent {
            LivaChatTheme {
                GalleryPhotoPreviewScreen(
                    uri = imageUri,
                    onSend = { sent++ },
                    onCancel = { cancelled++ },
                    onBackToPicker = { backToPicker++ },
                )
            }
        }

        composeTestRule.waitUntil(5_000) {
            runCatching {
                composeTestRule.onNodeWithText(sendLabel()).assertIsEnabled()
            }.isSuccess
        }
        composeTestRule.onNodeWithText(sendLabel()).performClick()
        assertEquals(1, sent)

        composeTestRule.onNodeWithText(cancelLabel()).performClick()
        assertEquals(1, cancelled)

        composeTestRule.onNodeWithContentDescription(backLabel()).performClick()
        assertEquals(1, backToPicker)
    }

    @Test
    fun system_back_goes_to_gallery_not_cancel() {
        var cancelled = 0
        var backToPicker = 0
        val imageUri = writeJpeg()
        composeTestRule.setContent {
            LivaChatTheme {
                GalleryPhotoPreviewScreen(
                    uri = imageUri,
                    onSend = {},
                    onCancel = { cancelled++ },
                    onBackToPicker = { backToPicker++ },
                )
            }
        }
        composeTestRule.waitForIdle()

        composeTestRule.runOnUiThread {
            composeTestRule.activity.onBackPressedDispatcher.onBackPressed()
        }

        assertEquals(1, backToPicker)
        assertEquals(0, cancelled)
    }

    @Test
    fun load_failure_shows_error_and_disables_send() {
        val missing = Uri.fromFile(File(context.cacheDir, "missing-${System.nanoTime()}.jpg"))
        composeTestRule.setContent {
            LivaChatTheme {
                GalleryPhotoPreviewScreen(
                    uri = missing,
                    onSend = {},
                    onCancel = {},
                    onBackToPicker = {},
                )
            }
        }

        composeTestRule.waitUntil(5_000) {
            runCatching {
                composeTestRule.onNodeWithText(failedLabel()).assertIsDisplayed()
            }.isSuccess
        }
        composeTestRule.onNodeWithText(sendLabel()).assertIsNotEnabled()
    }

    @Test
    fun pinch_zooms_rendered_preview_and_send_still_works() {
        var sent = 0
        val imageUri = writeContrastJpeg()
        composeTestRule.setContent {
            LivaChatTheme {
                GalleryPhotoPreviewScreen(
                    uri = imageUri,
                    onSend = { sent++ },
                    onCancel = {},
                    onBackToPicker = {},
                )
            }
        }
        composeTestRule.waitUntil(5_000) {
            runCatching {
                composeTestRule.onNodeWithText(sendLabel()).assertIsEnabled()
            }.isSuccess
        }

        val before = composeTestRule.onRoot().captureToImage()
        composeTestRule.onRoot().performTouchInput { pinchOut() }
        composeTestRule.waitForIdle()
        val after = composeTestRule.onRoot().captureToImage()
        savePng(before, "gallery_pinch_before.png")
        savePng(after, "gallery_pinch_after.png")

        // 缩放量值不进语义树：用渲染帧采样差异证明捏合确实改变了预览
        val b = before.toPixelMap()
        val a = after.toPixelMap()
        var sampled = 0
        var changed = 0
        var y = 0
        while (y < b.height) {
            var x = 0
            while (x < b.width) {
                sampled++
                if (b[x, y] != a[x, y]) changed++
                x += 16
            }
            y += 16
        }
        assertTrue("pinch must change rendered frame: $changed/$sampled", changed > sampled / 100)

        composeTestRule.onNodeWithText(sendLabel()).assertIsEnabled().performClick()
        assertEquals(1, sent)
    }

    @Test
    fun uri_change_resets_send_enabled_until_new_image_loads() {
        val first = writeJpeg()
        val current = mutableStateOf(first)
        composeTestRule.setContent {
            LivaChatTheme {
                GalleryPhotoPreviewScreen(
                    uri = current.value,
                    onSend = {},
                    onCancel = {},
                    onBackToPicker = {},
                )
            }
        }
        composeTestRule.waitUntil(5_000) {
            runCatching {
                composeTestRule.onNodeWithText(sendLabel()).assertIsEnabled()
            }.isSuccess
        }

        val missing = Uri.fromFile(File(context.cacheDir, "missing-${System.nanoTime()}.jpg"))
        composeTestRule.runOnIdle { current.value = missing }

        composeTestRule.waitUntil(5_000) {
            runCatching {
                composeTestRule.onNodeWithText(sendLabel()).assertIsNotEnabled()
            }.isSuccess
        }
    }

    private fun writeJpeg(): Uri {
        val file = File.createTempFile("preview_", ".jpg", context.cacheDir).also { tempFiles += it }
        val bitmap = Bitmap.createBitmap(120, 90, Bitmap.Config.ARGB_8888)
        try {
            FileOutputStream(file).use { out ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)
            }
        } finally {
            bitmap.recycle()
        }
        return Uri.fromFile(file)
    }

    /** 左红右蓝的大图：捏合前后渲染帧采样差异可判别。 */
    private fun writeContrastJpeg(): Uri {
        val file = File.createTempFile("preview_pinch_", ".jpg", context.cacheDir).also { tempFiles += it }
        val bitmap = Bitmap.createBitmap(600, 400, Bitmap.Config.ARGB_8888)
        try {
            val canvas = Canvas(bitmap)
            canvas.drawColor(Color.RED)
            canvas.drawRect(
                0f, 0f, 300f, 400f,
                Paint().apply { color = Color.BLUE },
            )
            FileOutputStream(file).use { out ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 95, out)
            }
        } finally {
            bitmap.recycle()
        }
        return Uri.fromFile(file)
    }

    private fun savePng(image: ImageBitmap, name: String) {
        FileOutputStream(File(context.filesDir, name)).use { out ->
            image.asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, out)
        }
    }

    private fun TouchInjectionScope.pinchOut(steps: Int = 10) {
        val c = center
        val startPx = 60f
        down(0, c + Offset(-startPx, 0f))
        down(1, c + Offset(startPx, 0f))
        repeat(steps) { i ->
            val half = startPx + (i + 1) * 30f
            moveTo(0, c + Offset(-half, 0f))
            moveTo(1, c + Offset(half, 0f))
        }
        up(0)
        up(1)
    }
}
