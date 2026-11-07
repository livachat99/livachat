package dev.livachat.app.media

import android.content.ContentValues
import android.content.Context
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import android.util.Base64
import android.util.Log
import androidx.annotation.RequiresApi
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.sdk.model.AiImage
import java.io.File
import java.text.SimpleDateFormat
import java.time.Instant
import java.util.Date
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * 查看器「保存到相册」：JPEG 原始字节直存（不 decode/rotate/re-encode，字节即所见）。
 * 成功 true；一切失败 false（不抛异常、不留半成品）。线程归 saver（IO），调用方只 await。
 */
@Singleton
class ImageGallerySaver @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    // withContext(IO) 必须包住整个入口：Base64 解码（最大 ~1.4MB 字符串）也属 IO 负载，
    // 漏在外面会回到调用方线程（conversation 侧是 Main）执行（2026-09-20 review 修复）
    suspend fun save(image: AiImage): Boolean = withContext(Dispatchers.IO) {
        val bytes = decodeJpegOrNull(image.data) ?: return@withContext false
        saveJpegBytes(bytes, image.mime)
    }

    /**
     * 字节入口（gallery 查看器帧图下载后直存，docs/android_timeline_image_gallery.md §3.5）：
     * 同一 JPEG magic 校验 + 同一 MediaStore/legacy 写路径；成功 true、一切失败 false
     * （不抛异常、不留半成品）；文件名口径与 conversation 侧一致（buildDisplayName）。
     */
    suspend fun saveJpegBytes(bytes: ByteArray, mime: String = DEFAULT_MIME): Boolean =
        withContext(Dispatchers.IO) {
            if (!isJpeg(bytes)) return@withContext false
            val displayName = buildDisplayName(Instant.now())
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                saveViaMediaStore(bytes, displayName, mime)
            } else {
                saveViaLegacyStorage(bytes, displayName, mime)
            }
        }

    /** Scoped Storage（API 29+，免权限）：insert(IS_PENDING) → 写流 → 置非 pending。 */
    @RequiresApi(Build.VERSION_CODES.Q)
    private fun saveViaMediaStore(bytes: ByteArray, displayName: String, mime: String): Boolean {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, displayName)
            put(MediaStore.Images.Media.MIME_TYPE, mime.ifBlank { DEFAULT_MIME })
            put(
                MediaStore.Images.Media.RELATIVE_PATH,
                Environment.DIRECTORY_PICTURES + "/LivaChat",
            )
            put(MediaStore.Images.Media.IS_PENDING, 1)
        }
        val uri = runCatching {
            resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
        }.getOrNull() ?: return false
        return try {
            val stream = resolver.openOutputStream(uri) ?: return deletePendingRow(uri)
            stream.use { it.write(bytes) }
            val done = ContentValues().apply { put(MediaStore.Images.Media.IS_PENDING, 0) }
            val updated = runCatching { resolver.update(uri, done, null, null) }.getOrDefault(0)
            // 置回失败则文件永远 pending 不可见——同样按失败清理，不留半成品
            if (updated <= 0) deletePendingRow(uri) else true
        } catch (e: Exception) {
            deletePendingRow(uri)
        }
    }

    /** 删除 pending 行，返回 false（失败路径统一出口）。 */
    private fun deletePendingRow(uri: Uri): Boolean {
        runCatching { context.contentResolver.delete(uri, null, null) }
        return false
    }

    /**
     * API 26–28：公共 Pictures/LivaChat 目录裸写 + MediaScanner 入册。scanFile 异步，
     * 返回 true 仅代表文件写入成功（是否已入册不可知，接受）。
     */
    private fun saveViaLegacyStorage(bytes: ByteArray, displayName: String, mime: String): Boolean {
        return try {
            @Suppress("DEPRECATION")
            val pictures = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_PICTURES)
            val dir = File(pictures, "LivaChat")
            if (!dir.exists() && !dir.mkdirs()) return false
            val file = File(dir, displayName)
            file.outputStream().use { it.write(bytes) }
            MediaScannerConnection.scanFile(
                context,
                arrayOf(file.absolutePath),
                arrayOf(mime.ifBlank { DEFAULT_MIME }),
            ) { path, uri ->
                Log.d(TAG, "gallery scan completed: path=$path uri=$uri")
            }
            true
        } catch (e: Exception) {
            false
        }
    }

    private companion object {
        const val TAG = "ImageGallerySaver"
        const val DEFAULT_MIME = "image/jpeg"
    }
}

/**
 * 文件名毫秒级时间戳：重入守卫只防重叠保存，同一秒内顺序保存两张会撞名——
 * API ≤ 28 裸文件写同名静默覆盖，API 29+ 的同名去重是 MediaProvider 实现细节，不应依赖。
 */
internal fun buildDisplayName(now: Instant): String =
    "LivaChat_${SimpleDateFormat("yyyyMMdd_HHmmss_SSS", Locale.US).format(Date.from(now))}.jpg"

/**
 * Base64 → JPEG 原始字节；非法/截断 Base64 或非 JPEG magic（FF D8 FF）一律 null，
 * 不把非图片数据写进用户相册。
 */
internal fun decodeJpegOrNull(data: String): ByteArray? {
    val bytes = runCatching { Base64.decode(data, Base64.DEFAULT) }.getOrNull() ?: return null
    return if (isJpeg(bytes)) bytes else null
}

/** JPEG magic（FF D8 FF）：解码字节与下载字节共用同一道闸，不把非图片数据写进用户相册。 */
internal fun isJpeg(bytes: ByteArray): Boolean =
    bytes.size >= 3 && bytes[0] == 0xFF.toByte() &&
        bytes[1] == 0xD8.toByte() && bytes[2] == 0xFF.toByte()
