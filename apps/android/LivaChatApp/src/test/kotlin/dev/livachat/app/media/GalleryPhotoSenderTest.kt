package dev.livachat.app.media

import android.content.ContentResolver
import android.content.Context
import android.content.ContextWrapper
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.net.Uri
import androidx.exifinterface.media.ExifInterface
import dev.livachat.app.R
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.Orientation
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.GraphicsMode
import java.io.ByteArrayInputStream
import java.io.File
import java.io.FileOutputStream

@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class GalleryPhotoSenderTest {

    private lateinit var context: Context
    private val tempFiles = mutableListOf<File>()

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
    }

    @After
    fun tearDown() {
        tempFiles.forEach { it.delete() }
    }

    // ── 纯函数：采样与缩放 ──────────────────────────────────────────

    @Test
    fun `sample size - small image stays 1`() {
        assertEquals(1, GalleryPhotoSender.sampleSizeFor(1920, 1080))
        assertEquals(1, GalleryPhotoSender.sampleSizeFor(1000, 800))
    }

    @Test
    fun `sample size - large images use largest power of two`() {
        assertEquals(2, GalleryPhotoSender.sampleSizeFor(3840, 2160))
        assertEquals(2, GalleryPhotoSender.sampleSizeFor(4000, 3000))
        assertEquals(4, GalleryPhotoSender.sampleSizeFor(7680, 4320))
        assertEquals(4, GalleryPhotoSender.sampleSizeFor(12000, 1000))
    }

    @Test
    fun `sample size - just above limit still samples once`() {
        assertEquals(1, GalleryPhotoSender.sampleSizeFor(1921, 1081))
        assertEquals(2, GalleryPhotoSender.sampleSizeFor(3840, 1000))
    }

    @Test
    fun `scaled size - at or below limit is null`() {
        assertEquals(null, GalleryPhotoSender.scaledSizeFor(1920, 1080))
        assertEquals(null, GalleryPhotoSender.scaledSizeFor(1000, 800))
    }

    @Test
    fun `scaled size - landscape and portrait keep aspect ratio`() {
        assertEquals(CaptureSize(1920, 1440), GalleryPhotoSender.scaledSizeFor(4000, 3000))
        assertEquals(CaptureSize(1440, 1920), GalleryPhotoSender.scaledSizeFor(3000, 4000))
        assertEquals(CaptureSize(1920, 1), GalleryPhotoSender.scaledSizeFor(4000, 3))
    }

    // ── 编码：采样 / 缩放 / EXIF ────────────────────────────────────

    @Test
    fun `encode photo - samples to long side 1920`() {
        val uri = writeJpeg(3840, 2160)
        val encoded = newSender().encodePhoto(uri)

        assertNotNull(encoded)
        assertEquals(1920, encoded!!.width)
        assertEquals(1080, encoded.height)
        assertJpeg(encoded.jpeg)
    }

    @Test
    fun `encode photo - scales to long side 1920`() {
        val uri = writeJpeg(3000, 2000)
        val encoded = newSender().encodePhoto(uri)

        assertNotNull(encoded)
        assertEquals(1920, encoded!!.width)
        assertEquals(1280, encoded.height)
        assertJpeg(encoded.jpeg)
    }

    @Test
    fun `encode photo - applies exif rotation after scaling`() {
        val uri = writeJpeg(2000, 1000, ExifInterface.ORIENTATION_ROTATE_90)
        val encoded = newSender().encodePhoto(uri)

        assertNotNull(encoded)
        // 2000x1000 -> 1920x960 -> 旋正 960x1920
        assertEquals(960, encoded!!.width)
        assertEquals(1920, encoded.height)
        assertJpeg(encoded.jpeg)
    }

    @Test
    fun `encode photo - all eight exif orientations map quadrants`() {
        val red = Color.RED
        val green = Color.GREEN
        val blue = Color.BLUE
        val yellow = Color.YELLOW
        data class Case(
            val name: String,
            val orientation: Int,
            val outW: Int,
            val outH: Int,
            val quadrants: List<Int>, // TL, TR, BL, BR
        )
        val cases = listOf(
            Case("normal", ExifInterface.ORIENTATION_NORMAL, 200, 100, listOf(red, green, blue, yellow)),
            Case(
                "flip-h", ExifInterface.ORIENTATION_FLIP_HORIZONTAL, 200, 100,
                listOf(green, red, yellow, blue),
            ),
            Case("rotate180", ExifInterface.ORIENTATION_ROTATE_180, 200, 100, listOf(yellow, blue, green, red)),
            Case(
                "flip-v", ExifInterface.ORIENTATION_FLIP_VERTICAL, 200, 100,
                listOf(blue, yellow, red, green),
            ),
            Case("transpose", ExifInterface.ORIENTATION_TRANSPOSE, 100, 200, listOf(red, blue, green, yellow)),
            Case("rotate90", ExifInterface.ORIENTATION_ROTATE_90, 100, 200, listOf(blue, red, yellow, green)),
            Case(
                "transverse", ExifInterface.ORIENTATION_TRANSVERSE, 100, 200,
                listOf(yellow, green, blue, red),
            ),
            Case("rotate270", ExifInterface.ORIENTATION_ROTATE_270, 100, 200, listOf(green, yellow, red, blue)),
        )

        for (case in cases) {
            val encoded = newSender().encodePhoto(writeQuadrantJpeg(case.orientation))
            assertNotNull(case.name, encoded)
            assertEquals(case.name, case.outW, encoded!!.width)
            assertEquals(case.name, case.outH, encoded.height)

            val decoded = BitmapFactory.decodeByteArray(encoded.jpeg, 0, encoded.jpeg.size)
            try {
                val x1 = decoded.width / 4
                val x2 = decoded.width * 3 / 4
                val y1 = decoded.height / 4
                val y2 = decoded.height * 3 / 4
                val got = listOf(
                    nearestReference(decoded.getPixel(x1, y1)),
                    nearestReference(decoded.getPixel(x2, y1)),
                    nearestReference(decoded.getPixel(x1, y2)),
                    nearestReference(decoded.getPixel(x2, y2)),
                )
                assertEquals(case.name, case.quadrants, got)
            } finally {
                decoded.recycle()
            }
        }
    }

    // ── 上行参数与三态 ───────────────────────────────────────────────

    @Test
    fun `send - chat pushes localized instruction and gallery facing`() {
        val sdk = mockk<LivaChatSDK>(relaxed = true)
        val sender = newSender(sdk)
        val uri = writeJpeg(100, 100)

        val result = runBlocking { sender.send(uri, omniMode = false, isStillAllowed = { true }) }

        assertEquals(GallerySendResult.SENT, result)
        verify {
            sdk.pushVideoJpeg(
                jpeg = any(),
                width = 100,
                height = 100,
                orientation = Orientation.PORTRAIT_0,
                cameraFacing = "gallery",
                turnId = 0L,
                text = context.getString(R.string.conv_image_action_text),
            )
        }
    }

    @Test
    fun `send - omni pushes null text`() {
        val sdk = mockk<LivaChatSDK>(relaxed = true)
        val sender = newSender(sdk)
        val uri = writeJpeg(100, 100)

        val result = runBlocking { sender.send(uri, omniMode = true, isStillAllowed = { true }) }

        assertEquals(GallerySendResult.SENT, result)
        verify {
            sdk.pushVideoJpeg(
                jpeg = any(),
                width = 100,
                height = 100,
                orientation = Orientation.PORTRAIT_0,
                cameraFacing = "gallery",
                turnId = 0L,
                text = null,
            )
        }
    }

    @Test
    fun `send - camera turned on during encode is rejected without push`() {
        val sdk = mockk<LivaChatSDK>(relaxed = true)
        val sender = newSender(sdk)
        val uri = writeJpeg(100, 100)

        val result = runBlocking { sender.send(uri, omniMode = false, isStillAllowed = { false }) }

        assertEquals(GallerySendResult.REJECTED, result)
        verify(exactly = 0) { sdk.pushVideoJpeg(any(), any(), any(), any(), any(), any(), any()) }
    }

    @Test
    fun `send - pixel decode OOM fails without crash`() {
        val sdk = mockk<LivaChatSDK>(relaxed = true)
        val uri = writeJpeg(10, 10)
        val jpegBytes = File(uri.path!!).readBytes()
        var calls = 0
        val resolver = mockk<ContentResolver>()
        // 第 1 次 bounds（inJustDecodeBounds，配真实字节）成功；第 2 次像素解码抛 OOM
        every { resolver.openInputStream(uri) } answers {
            calls++
            if (calls == 1) ByteArrayInputStream(jpegBytes) else throw OutOfMemoryError("decode OOM")
        }
        val wrapped = object : ContextWrapper(context) {
            override fun getContentResolver(): ContentResolver = resolver
        }
        val sender = GalleryPhotoSender(sdk, wrapped, CaptureTurnCounter())

        val result = runBlocking { sender.send(uri, omniMode = false, isStillAllowed = { true }) }

        assertEquals(GallerySendResult.FAILED, result)
        verify(exactly = 0) { sdk.pushVideoJpeg(any(), any(), any(), any(), any(), any(), any()) }
    }

    @Test
    fun `send - unreadable uri fails without push`() {
        val sdk = mockk<LivaChatSDK>(relaxed = true)
        val sender = newSender(sdk)

        val result = runBlocking {
            sender.send(
                Uri.fromFile(File(context.cacheDir, "missing-${System.nanoTime()}.jpg")),
                omniMode = false,
                isStillAllowed = { true },
            )
        }

        assertEquals(GallerySendResult.FAILED, result)
        verify(exactly = 0) { sdk.pushVideoJpeg(any(), any(), any(), any(), any(), any(), any()) }
    }

    // ── helpers ─────────────────────────────────────────────────────

    private fun newSender(sdk: LivaChatSDK = mockk(relaxed = true)) =
        GalleryPhotoSender(sdk, context, CaptureTurnCounter())

    @Test
    fun `gallery and screen capture share the localized image action resource`() {
        val gallery = sourceFile("dev/livachat/app/media/GalleryPhotoSender.kt").readText()
        val screen = sourceFile("dev/livachat/app/media/ScreenCaptureController.kt").readText()
        assertTrue(gallery.contains("R.string.conv_image_action_text"))
        assertTrue(screen.contains("R.string.conv_image_action_text"))
        assertFalse(gallery.contains("GALLERY_ACTION_TEXT"))
        assertFalse(screen.contains("TAP_CAPTURE_TEXT"))
    }

    private fun sourceFile(relative: String): File {
        var dir: File? = File(System.getProperty("user.dir")!!)
        while (dir != null) {
            val candidate = File(dir, "src/main/kotlin/$relative")
            if (candidate.isFile) return candidate
            dir = dir.parentFile
        }
        error("source not found: $relative")
    }

    private fun writeJpeg(width: Int, height: Int, orientation: Int? = null): Uri {
        val file = File.createTempFile("gallery_", ".jpg", context.cacheDir).also { tempFiles += it }
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        try {
            FileOutputStream(file).use { out ->
                assertTrue(bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out))
            }
        } finally {
            bitmap.recycle()
        }
        if (orientation != null) {
            ExifInterface(file.absolutePath).apply {
                setAttribute(ExifInterface.TAG_ORIENTATION, orientation.toString())
                saveAttributes()
            }
        }
        return Uri.fromFile(file)
    }

    private fun assertJpeg(bytes: ByteArray) {
        assertTrue("JPEG magic", bytes.size >= 2 && bytes[0] == 0xFF.toByte() && bytes[1] == 0xD8.toByte())
    }

    /** 四象限纯色大图（TL 红 / TR 绿 / BL 蓝 / BR 黄），用于八朝向像素归属断言。 */
    private fun writeQuadrantJpeg(orientation: Int): Uri {
        val file = File.createTempFile("gallery_quad_", ".jpg", context.cacheDir).also { tempFiles += it }
        val bitmap = Bitmap.createBitmap(200, 100, Bitmap.Config.ARGB_8888)
        try {
            val canvas = Canvas(bitmap)
            val paint = Paint()
            paint.color = Color.RED
            canvas.drawRect(0f, 0f, 100f, 50f, paint)
            paint.color = Color.GREEN
            canvas.drawRect(100f, 0f, 200f, 50f, paint)
            paint.color = Color.BLUE
            canvas.drawRect(0f, 50f, 100f, 100f, paint)
            paint.color = Color.YELLOW
            canvas.drawRect(100f, 50f, 200f, 100f, paint)
            FileOutputStream(file).use { out ->
                assertTrue(bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out))
            }
        } finally {
            bitmap.recycle()
        }
        ExifInterface(file.absolutePath).apply {
            setAttribute(ExifInterface.TAG_ORIENTATION, orientation.toString())
            saveAttributes()
        }
        return Uri.fromFile(file)
    }

    /** JPEG 有损：用最近参考色分类，避免逐通道阈值。 */
    private fun nearestReference(pixel: Int): Int {
        val refs = listOf(Color.RED, Color.GREEN, Color.BLUE, Color.YELLOW)
        return refs.minBy { ref ->
            kotlin.math.abs(Color.red(pixel) - Color.red(ref)) +
                kotlin.math.abs(Color.green(pixel) - Color.green(ref)) +
                kotlin.math.abs(Color.blue(pixel) - Color.blue(ref))
        }
    }
}
