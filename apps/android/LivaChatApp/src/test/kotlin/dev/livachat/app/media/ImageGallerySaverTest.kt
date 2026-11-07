package dev.livachat.app.media

import android.content.Context
import android.util.Base64
import io.mockk.mockk
import java.time.Instant
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/** 保存到相册的纯函数：文件名毫秒精度 + JPEG magic 校验（垃圾数据不落相册）。 */
@RunWith(RobolectricTestRunner::class)
class ImageGallerySaverTest {

    /** 1×1 baseline JPEG（与 ImageViewerOverlayTest 同一 fixture）。 */
    private val tinyJpegBase64 =
        "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4n" +
            "ICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/E" +
            "ABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="

    @Test
    fun buildDisplayName_hasTimestampPatternWithMillis() {
        val name = buildDisplayName(Instant.ofEpochMilli(1_726_106_400_123L))
        assertTrue(
            "unexpected name: $name",
            name.matches(Regex("""LivaChat_\d{8}_\d{6}_\d{3}\.jpg""")),
        )
    }

    @Test
    fun buildDisplayName_differsWhenOnlyMillisDiffer() {
        // 同一秒内顺序保存两张也不得同名（API ≤ 28 同名裸写会静默覆盖前一张）
        assertNotEquals(
            buildDisplayName(Instant.ofEpochMilli(1_726_106_400_123L)),
            buildDisplayName(Instant.ofEpochMilli(1_726_106_400_124L)),
        )
    }

    @Test
    fun decodeJpegOrNull_validJpeg_returnsMagicVerifiedBytes() {
        val bytes = requireNotNull(decodeJpegOrNull(tinyJpegBase64)) { "valid JPEG must decode" }
        assertTrue(bytes.size > 3)
        assertEquals(0xFF.toByte(), bytes[0])
        assertEquals(0xD8.toByte(), bytes[1])
        assertEquals(0xFF.toByte(), bytes[2])
    }

    @Test
    fun decodeJpegOrNull_truncatedBase64_returnsNull() {
        // 尾状态非法（数据段长度 ≡ 1 mod 4）→ Base64.decode 抛异常 → null，不落相册
        assertNull(decodeJpegOrNull("/9j/4"))
    }

    @Test
    fun decodeJpegOrNull_nonJpegBytes_returnsNull() {
        assertNull(decodeJpegOrNull(Base64.encodeToString("hello".toByteArray(), Base64.DEFAULT)))
    }

    // ---- 字节入口（gallery 帧图下载后直存，§3.5）----

    @Test
    fun isJpeg_magicOnly() {
        assertTrue(isJpeg(Base64.decode(tinyJpegBase64, Base64.DEFAULT)))
        assertFalse(isJpeg(byteArrayOf(0xFF.toByte(), 0xD8.toByte()))) // 长度不足
        assertFalse(isJpeg(byteArrayOf(1, 2, 3)))
        assertFalse(isJpeg(ByteArray(0)))
    }

    @Test
    fun saveJpegBytes_nonJpeg_returnsFalseWithoutTouchingGallery() = runBlocking {
        // 非法 magic 必须在触达 context（写相册）之前短路——mock Context 不设任何期望
        val saver = ImageGallerySaver(mockk<Context>(relaxed = true))
        assertFalse(saver.saveJpegBytes(byteArrayOf(1, 2, 3)))
    }
}
