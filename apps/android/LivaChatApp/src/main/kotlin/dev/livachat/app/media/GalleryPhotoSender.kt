package dev.livachat.app.media

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.R
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.Orientation
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 图库照片规范化并上行（docs/camera_enhance.md §9）：
 * Uri → 采样解码 → 长边 ≤1920/q70 → EXIF 旋正 → LivaChatSDK.pushVideoJpeg。
 *
 * chat 下带本地化发图指令触发 v3 文本回合；omni 下 text=null，仅作视觉上下文；
 * translate/lecture/transcribe 由服务端模式门控排除（D5=常驻，无回合为接受行为）。
 */
@Singleton
class GalleryPhotoSender @Inject constructor(
    private val sdk: LivaChatSDK,
    @ApplicationContext private val context: Context,
    private val turnCounter: CaptureTurnCounter,
) {

    /**
     * @param omniMode true = 全模态：text=null（镜像屏幕抓取 D6b）；false = 带指令文本。
     * @param isStillAllowed 编码完成后、push 前复检（D9：摄像头开启即不再上行）。
     */
    internal suspend fun send(
        uri: Uri,
        omniMode: Boolean,
        isStillAllowed: () -> Boolean,
    ): GallerySendResult = withContext(Dispatchers.IO) {
        val encoded = encodePhoto(uri) ?: return@withContext GallerySendResult.FAILED
        if (!isStillAllowed()) {
            sdk.logDebug(TAG, "photo rejected: camera turned on during encode")
            return@withContext GallerySendResult.REJECTED
        }
        sdk.pushVideoJpeg(
            jpeg = encoded.jpeg,
            width = encoded.width,
            height = encoded.height,
            orientation = Orientation.PORTRAIT_0,
            cameraFacing = CAMERA_FACING,
            turnId = turnCounter.current,
            text = if (omniMode) null else context.getString(R.string.conv_image_action_text),
        )
        sdk.logDebug(
            TAG,
            "photo pushed ${encoded.width}x${encoded.height} bytes=${encoded.jpeg.size} omni=$omniMode",
        )
        GallerySendResult.SENT
    }

    /** 解码 + 旋正 + 缩放 + JPEG；失败收敛为 null（不抛）。 */
    internal fun encodePhoto(uri: Uri): EncodedPhoto? {
        val resolver = context.contentResolver
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        try {
            resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
        } catch (e: Exception) {
            sdk.logWarn(TAG, "photo bounds read failed", e)
            return null
        } catch (e: OutOfMemoryError) {
            sdk.logWarn(TAG, "photo bounds read OOM")
            return null
        }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

        val options = BitmapFactory.Options().apply {
            inSampleSize = sampleSizeFor(bounds.outWidth, bounds.outHeight)
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        var bitmap: Bitmap = try {
            resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
        } catch (e: Exception) {
            sdk.logWarn(TAG, "photo decode failed", e)
            null
        } catch (e: OutOfMemoryError) {
            // BitmapFactory 分配像素缓冲失败抛 Error（非 Exception）；不捕获会直达
            // 进程默认处理器崩溃。契约：OOM 收敛为 FAILED（§9.4）。
            sdk.logWarn(TAG, "photo decode OOM")
            null
        } ?: return null

        try {
            // 先缩放后旋正（相机路径同序）：90°/270° 下长边不变，先缩降低峰值内存
            val scaled = scaleToMaxLongSide(bitmap)
            if (scaled !== bitmap) {
                bitmap.recycle()
                bitmap = scaled
            }
            // 契约：需要旋正时返回新位图并回收输入；无需旋正时原样返回
            bitmap = applyExifOrientation(context, uri, bitmap)

            val out = ByteArrayOutputStream()
            if (!bitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)) {
                sdk.logWarn(TAG, "photo compress failed")
                return null
            }
            return EncodedPhoto(out.toByteArray(), bitmap.width, bitmap.height)
        } catch (e: Exception) {
            sdk.logWarn(TAG, "photo encode failed", e)
            return null
        } catch (e: OutOfMemoryError) {
            sdk.logWarn(TAG, "photo encode OOM")
            return null
        } finally {
            bitmap.recycle()
        }
    }

    private fun scaleToMaxLongSide(bitmap: Bitmap): Bitmap {
        val target = scaledSizeFor(bitmap.width, bitmap.height) ?: return bitmap
        sdk.logDebug(TAG, "photo scaled ${bitmap.width}x${bitmap.height} -> ${target.width}x${target.height}")
        return Bitmap.createScaledBitmap(bitmap, target.width, target.height, true)
    }

    companion object {
        private const val TAG = "GalleryPhoto"
        /** v7 D3=F1：图库照片来源（protocol CameraFacing=gallery；服务端仅存储/回显）。 */
        private const val CAMERA_FACING = "gallery"
        internal const val MAX_LONG_SIDE = 1920
        internal const val JPEG_QUALITY = 70

        /** 采样倍数：不超过 长边/1920 的最大 2 的幂（至少 1）；采样后长边仍 ≥1920。 */
        internal fun sampleSizeFor(
            width: Int,
            height: Int,
            maxLongSide: Int = MAX_LONG_SIDE,
        ): Int {
            val longSide = maxOf(width, height)
            if (longSide <= maxLongSide) return 1
            val ratio = longSide / maxLongSide
            var sample = 1
            while (sample * 2 <= ratio) sample *= 2
            return sample
        }

        /** 长边超限时返回等比缩放尺寸（只缩不放）；null = 无需缩放。 */
        internal fun scaledSizeFor(
            width: Int,
            height: Int,
            maxLongSide: Int = MAX_LONG_SIDE,
        ): CaptureSize? {
            val longSide = maxOf(width, height)
            if (longSide <= maxLongSide) return null
            val scale = maxLongSide.toFloat() / longSide
            return CaptureSize(
                (width * scale).toInt().coerceAtLeast(1),
                (height * scale).toInt().coerceAtLeast(1),
            )
        }
    }
}

internal data class EncodedPhoto(val jpeg: ByteArray, val width: Int, val height: Int)

/** 三态：REJECTED 是用户状态变化导致的静默丢弃，不得映射为错误横幅。 */
internal enum class GallerySendResult { SENT, REJECTED, FAILED }
