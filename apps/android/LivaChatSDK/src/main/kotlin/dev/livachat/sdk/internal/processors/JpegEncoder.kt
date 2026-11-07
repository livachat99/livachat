package dev.livachat.sdk.internal.processors

import android.graphics.Bitmap
import java.io.ByteArrayOutputStream

/**
 * Internal interface for JPEG compression. SDK-internal only.
 *
 * Uses Android's built-in [Bitmap.compress] (Skia-backed);
 * no native JNI dependency needed for JPEG encoding.
 */
internal interface JpegEncoder {
    /**
     * Compress a Bitmap to JPEG bytes.
     *
     * @param bitmap       source bitmap (any resolution / color space)
     * @param quality      JPEG quality 1–100, default 70
     * @param maxLongSide  if non-null, scale the bitmap so the longer side ≤ this value
     * @return JPEG byte array
     */
    fun encode(bitmap: Bitmap, quality: Int = 70, maxLongSide: Int? = null): ByteArray
}

internal class JpegEncoderImpl : JpegEncoder {

    override fun encode(
        bitmap: Bitmap,
        quality: Int,
        maxLongSide: Int?,
    ): ByteArray {
        val src = if (maxLongSide != null) {
            scaleIfNeeded(bitmap, maxLongSide)
        } else {
            bitmap
        }

        val bos = ByteArrayOutputStream(src.byteCount / 8)
        src.compress(Bitmap.CompressFormat.JPEG, quality, bos)
        val result = bos.toByteArray()

        // Don't leak the scaled bitmap
        if (src !== bitmap) {
            src.recycle()
        }

        return result
    }

    private fun scaleIfNeeded(
        bitmap: Bitmap,
        maxLongSide: Int,
    ): Bitmap {
        val w = bitmap.width
        val h = bitmap.height
        val longSide = maxOf(w, h)
        if (longSide <= maxLongSide) return bitmap

        val scale = maxLongSide.toFloat() / longSide
        val newW = (w * scale).toInt()
        val newH = (h * scale).toInt()
        return Bitmap.createScaledBitmap(bitmap, newW, newH, true)
    }
}
