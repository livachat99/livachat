package dev.livachat.sdk.internal.pipeline

import android.graphics.Bitmap
import dev.livachat.sdk.internal.processors.JpegEncoderImpl
import dev.livachat.sdk.model.Orientation
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue

class VideoSendPipelineTest {

    private fun createBitmap(w: Int, h: Int, color: Int = 0xFF336699.toInt()): Bitmap {
        return Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888).apply { eraseColor(color) }
    }

    @org.junit.Test
    fun pushBitmap_emitsJpegWithProvidedFrameId() {
        val jpegs = mutableListOf<ByteArray>()
        val frameIds = mutableListOf<Long>()

        val pipeline = VideoSendPipeline(
            defaultQuality = 70,
            defaultMaxLongSide = 1080,
            jpegEncoder = JpegEncoderImpl(),
            onEncoded = { jpeg, frameId, _, _, _, _, _, _ ->
                jpegs.add(jpeg)
                frameIds.add(frameId)
            },
        )

        val bmp = createBitmap(640, 480)
        pipeline.pushBitmap(bmp, frameId = 1L, orientation = Orientation.PORTRAIT_0)
        pipeline.pushBitmap(bmp, frameId = 2L, orientation = Orientation.LANDSCAPE_90)
        bmp.recycle()

        assertEquals(2, jpegs.size)
        assertEquals(listOf(1L, 2L), frameIds)
        jpegs.forEach { jpeg ->
            assertTrue(jpeg.isNotEmpty())
            assertTrue(jpeg.size > 100)
        }
    }

    @org.junit.Test
    fun pushBitmap_orientationPropagates() {
        val orientations = mutableListOf<Orientation>()

        val pipeline = VideoSendPipeline(
            defaultQuality = 70,
            defaultMaxLongSide = 1080,
            jpegEncoder = JpegEncoderImpl(),
            onEncoded = { _, _, _, _, ori, _, _, _ -> orientations.add(ori) },
        )

        val bmp = createBitmap(100, 100)
        pipeline.pushBitmap(bmp, frameId = 1L, orientation = Orientation.PORTRAIT_0)
        pipeline.pushBitmap(bmp, frameId = 2L, orientation = Orientation.LANDSCAPE_90)
        pipeline.pushBitmap(bmp, frameId = 3L, orientation = Orientation.PORTRAIT_180)
        pipeline.pushBitmap(bmp, frameId = 4L, orientation = Orientation.LANDSCAPE_270)
        bmp.recycle()

        assertEquals(
            listOf(Orientation.PORTRAIT_0, Orientation.LANDSCAPE_90,
                   Orientation.PORTRAIT_180, Orientation.LANDSCAPE_270),
            orientations,
        )
    }

    @org.junit.Test
    fun pushBitmap_widthHeightPreserved() {
        val widths = mutableListOf<Int>()
        val heights = mutableListOf<Int>()

        val pipeline = VideoSendPipeline(
            defaultQuality = 70,
            defaultMaxLongSide = 1080,
            jpegEncoder = JpegEncoderImpl(),
            onEncoded = { _, _, w, h, _, _, _, _ ->
                widths.add(w)
                heights.add(h)
            },
        )

        val bmp = createBitmap(640, 480)
        pipeline.pushBitmap(bmp, frameId = 1L, orientation = Orientation.PORTRAIT_0)
        bmp.recycle()

        assertEquals(640, widths[0])
        assertEquals(480, heights[0])
    }

    @org.junit.Test
    fun pushBitmap_qualityClampsCorrectly() {
        val jpegs = mutableListOf<ByteArray>()

        val pipeline = VideoSendPipeline(
            defaultQuality = 70,
            defaultMaxLongSide = 1080,
            jpegEncoder = JpegEncoderImpl(),
            onEncoded = { jpeg, _, _, _, _, _, _, _ -> jpegs.add(jpeg) },
        )

        val bmp = createBitmap(320, 240)
        pipeline.pushBitmap(bmp, frameId = 1L, orientation = Orientation.PORTRAIT_0, quality = 5)
        val low = jpegs.size

        pipeline.pushBitmap(bmp, frameId = 2L, orientation = Orientation.PORTRAIT_0, quality = 95)
        bmp.recycle()

        // Low quality should produce smaller JPEG
        assertTrue(jpegs[0].size < jpegs[1].size)
    }

    @org.junit.Test
    fun pushJpeg_passesThroughBytesWithoutReencoding() {
        val results = mutableListOf<Triple<ByteArray, Int, Int>>()

        val pipeline = VideoSendPipeline(
            defaultQuality = 70,
            defaultMaxLongSide = 1080,
            jpegEncoder = JpegEncoderImpl(),
            onEncoded = { jpeg, _, w, h, _, _, _, _ ->
                results.add(Triple(jpeg, w, h))
            },
        )

        val raw = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 1, 2, 3)
        pipeline.pushJpeg(raw, frameId = 42L, width = 1920, height = 1080, orientation = Orientation.PORTRAIT_0)

        assertEquals(1, results.size)
        val (jpeg, w, h) = results[0]
        assertEquals(1920, w)
        assertEquals(1080, h)
        // Bytes must be identical — no re-encoding
        assertEquals(raw.size, jpeg.size)
        assertTrue(raw.contentEquals(jpeg))
    }
}
