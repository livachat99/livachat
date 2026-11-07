package dev.livachat.app.media

import android.content.ContentUris
import android.os.Build
import android.provider.MediaStore
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.sdk.model.AiImage
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayOutputStream

/**
 * 真机端到端探针：`ImageGallerySaver.save` 真实 MediaStore 写路径（设计 §9.6 原列「不做自动化」，
 * 此处按「自清理不污染」修订——只删除本探针新建的行，用 save 前后 id 差集识别，绝不触碰相册
 * 既有 LivaChat 文件）。
 *
 * API ≤ 28 走 legacy 裸写 + MediaScanner 异步入册：本探针的同步查询会闪断，且删除行不回收文件
 * （可能残留）→ 予以跳过，维持文档「API 26–28 残项」结论。
 */
@RunWith(AndroidJUnit4::class)
class ImageGallerySaverDeviceProbeTest {

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val resolver = context.contentResolver

    @Before
    fun requireScopedStoragePath() {
        Assume.assumeTrue(
            "API ≤ 28 legacy 扫描路径异步入册，探针不覆盖（残项）",
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q,
        )
    }

    /** 1×1 baseline JPEG（与 ImageViewerOverlayTest 同一 fixture）。 */
    private val tinyJpegBase64 =
        "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4n" +
            "ICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/E" +
            "ABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="

    private fun livaChatRowIds(): Set<Long> {
        val ids = mutableSetOf<Long>()
        resolver.query(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            arrayOf(MediaStore.Images.Media._ID),
            "${MediaStore.Images.Media.DISPLAY_NAME} LIKE ?",
            arrayOf("LivaChat_%"),
            null,
        )?.use { c ->
            while (c.moveToNext()) ids += c.getLong(0)
        }
        return ids
    }

    @Test
    fun save_writesRealGalleryRow_pendingCleared_bytesIntact() = runBlocking {
        val before = livaChatRowIds()
        val saver = ImageGallerySaver(context)

        val ok = saver.save(
            AiImage(data = tinyJpegBase64, mime = "image/jpeg", width = 1, height = 1),
        )
        assertTrue("save() 在 API ${Build.VERSION.SDK_INT} 真机上应成功", ok)

        val after = livaChatRowIds()
        val newIds = after - before
        try {
            assertEquals("应恰好多出 1 行", 1, newIds.size)
            val id = newIds.single()
            val uri = ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id)

            resolver.query(
                uri,
                arrayOf(
                    MediaStore.Images.Media.DISPLAY_NAME,
                    MediaStore.Images.Media.IS_PENDING,
                    MediaStore.Images.Media.RELATIVE_PATH,
                    MediaStore.Images.Media.MIME_TYPE,
                ),
                null, null, null,
            )?.use { c ->
                assertTrue("行应可查询", c.moveToFirst())
                val name = c.getString(0)
                assertTrue("文件名格式: $name", name.matches(Regex("""LivaChat_\d{8}_\d{6}_\d{3}\.jpg""")))
                assertEquals("IS_PENDING 应已置 0", 0, c.getInt(1))
                // MediaStore 规范化为尾斜杠形式
                assertEquals("Pictures/LivaChat/", c.getString(2))
                assertEquals("image/jpeg", c.getString(3))
            } ?: throw AssertionError("新行查询失败")

            val bytes = resolver.openInputStream(uri)!!.use { input ->
                ByteArrayOutputStream().also { input.copyTo(it) }.toByteArray()
            }
            val expected = android.util.Base64.decode(tinyJpegBase64, android.util.Base64.DEFAULT)
            assertEquals("写入字节数应与 JPEG 一致", expected.size, bytes.size)
            assertEquals(0xFF.toByte(), bytes[0])
            assertEquals(0xD8.toByte(), bytes[1])
            assertEquals(0xFF.toByte(), bytes[2])
        } finally {
            newIds.forEach { resolver.delete(ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, it), null, null) }
        }
    }

    @Test
    fun save_garbageData_returnsFalse_leavesNoRow() = runBlocking {
        val before = livaChatRowIds()
        val saver = ImageGallerySaver(context)

        val ok = saver.save(AiImage(data = "aGVsbG8=", mime = "image/jpeg", width = 1, height = 1))

        assertFalse("非 JPEG magic 必须保存失败", ok)
        assertEquals("不得新增任何行", before, livaChatRowIds())
    }
}
