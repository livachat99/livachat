package dev.livachat.sdk.internal.processors

import android.graphics.Bitmap

/**
 * Instrumentation test for JpegEncoder (pure Kotlin, but needs android.graphics.Bitmap).
 */
class JpegEncoderTest {

    private fun createTestBitmap(width: Int, height: Int, color: Int = 0xFF336699.toInt()): Bitmap {
        val bmp = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        bmp.eraseColor(color)
        return bmp
    }

    @org.junit.Test
    fun encode_smallBitmap_returnsNonEmptyJpeg() {
        val encoder = JpegEncoderImpl()
        val bmp = createTestBitmap(100, 100)

        val jpeg = encoder.encode(bmp, quality = 70)

        assert(jpeg.isNotEmpty()) { "JPEG output should not be empty" }
        assert(jpeg.size < 50 * 1024) { "JPEG for 100x100 should be < 50 KB, was ${jpeg.size}" }

        bmp.recycle()
    }

    @org.junit.Test
    fun encode_1080p_quality70_under200KB() {
        val encoder = JpegEncoderImpl()
        val bmp = createTestBitmap(1080, 1920)

        val jpeg = encoder.encode(bmp, quality = 70)

        assert(jpeg.isNotEmpty())
        assert(jpeg.size < 200 * 1024) {
            "1080p JPEG at q=70 should be < 200 KB, was ${jpeg.size}"
        }

        bmp.recycle()
    }

    @org.junit.Test
    fun encode_maxLongSide_scalesDown() {
        val encoder = JpegEncoderImpl()
        val bmp = createTestBitmap(1080, 1920)

        val jpeg = encoder.encode(bmp, quality = 70, maxLongSide = 540)

        assert(jpeg.isNotEmpty())
        // Scaled version should be significantly smaller
        assert(jpeg.size < 100 * 1024) {
            "540p JPEG should be < 100 KB, was ${jpeg.size}"
        }

        bmp.recycle()
    }

    @org.junit.Test
    fun encode_maxLongSide_smallerThanSource_noChange() {
        val encoder = JpegEncoderImpl()
        val bmp = createTestBitmap(100, 100)

        // maxLongSide=1080 is larger than 100x100, so no scaling needed
        val jpeg1 = encoder.encode(bmp, quality = 70, maxLongSide = 1080)
        val jpeg2 = encoder.encode(bmp, quality = 70)

        // Both should be similar size (no scaling applied)
        assert(kotlin.math.abs(jpeg1.size - jpeg2.size) < 200) {
            "Unscaled JPEGs should have similar sizes: ${jpeg1.size} vs ${jpeg2.size}"
        }

        bmp.recycle()
    }

    @org.junit.Test
    fun encode_differentQualities() {
        val encoder = JpegEncoderImpl()
        val bmp = createTestBitmap(640, 480)

        val jpegLow = encoder.encode(bmp, quality = 10)
        val jpegHigh = encoder.encode(bmp, quality = 90)

        assert(jpegLow.isNotEmpty())
        assert(jpegHigh.isNotEmpty())
        assert(jpegLow.size < jpegHigh.size) {
            "Low quality JPEG (${jpegLow.size}) should be smaller than high quality (${jpegHigh.size})"
        }

        bmp.recycle()
    }

    @org.junit.Test
    fun encode_validJpegHeader() {
        val encoder = JpegEncoderImpl()
        val bmp = createTestBitmap(64, 64)

        val jpeg = encoder.encode(bmp, quality = 70)

        // JPEG starts with FF D8 FF
        assert(jpeg.size >= 3)
        assert(jpeg[0] == 0xFF.toByte()) { "JPEG header byte 0 should be 0xFF" }
        assert(jpeg[1] == 0xD8.toByte()) { "JPEG header byte 1 should be 0xD8" }

        bmp.recycle()
    }
}
