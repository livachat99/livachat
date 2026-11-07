package dev.livachat.app.ui.screens.mylife.audiogallery

import dev.livachat.app.data.remote.mylife.dto.AudioSegment
import java.time.OffsetDateTime
import java.time.ZoneId
import java.util.Locale
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 每段的时间标签（docs/android_timeline_audio_gallery.md §4.3，2026-09-24 由「桶变化才出标签」改）：
 * 每个 segment 都有自己的 `HH:mm`，与列表一一对应、不去重；数字经 localizeDigits（§20 缺口⑤）。
 */
class SegmentTimeLabelsTest {

    private val zone = ZoneId.of("Asia/Shanghai")
    private val en = Locale.US

    private fun seg(dateTime: String): AudioSegment {
        val iso = "2026-09-22T$dateTime+08:00"
        return AudioSegment(
            eventRef = "ref-$dateTime",
            tsMs = OffsetDateTime.parse(iso).toInstant().toEpochMilli(),
            mediaUrl = "https://h/audio.ogg",
            transcript = null,
            summary = null,
            durationMs = 60_000,
        )
    }

    @Test
    fun `每段都出标签（同分钟也不去重）`() {
        val labels = segmentTimeLabels(
            listOf(seg("09:12:00"), seg("09:12:30"), seg("09:13:00")),
            zone,
            en,
        )
        assertEquals(listOf("09:12", "09:12", "09:13"), labels)
    }

    @Test
    fun `相邻段的标签各归各段`() {
        assertEquals(
            listOf("19:29", "19:30", "19:31"),
            segmentTimeLabels(listOf(seg("19:29:41"), seg("19:30:41"), seg("19:31:20")), zone, en),
        )
    }

    @Test
    fun `标签数量与顺序与输入一一对应`() {
        val segments = listOf(
            seg("09:00:00"), seg("09:05:00"), seg("09:10:00"), seg("09:15:00"),
        )
        val labels = segmentTimeLabels(segments, zone, en)
        assertEquals(segments.size, labels.size)
        assertEquals(listOf("09:00", "09:05", "09:10", "09:15"), labels)
    }

    @Test
    fun `跨小时正确`() {
        assertEquals(
            listOf("09:59", "10:00"),
            segmentTimeLabels(listOf(seg("09:59:30"), seg("10:00:10")), zone, en),
        )
    }

    @Test
    fun `标签按传入时区渲染`() {
        // 输入按 +08:00 构造（09:12）→ 用 UTC 渲染应为 01:12
        assertEquals(
            listOf("01:12"),
            segmentTimeLabels(listOf(seg("09:12:00")), ZoneId.of("UTC"), en),
        )
    }

    @Test
    fun `空列表`() {
        assertEquals(emptyList<String>(), segmentTimeLabels(emptyList(), zone, en))
    }

    @Test
    fun `ar 标签数字本地化为阿数（§20 缺口⑤）`() {
        assertEquals(
            listOf("٠٩:١٢"),
            segmentTimeLabels(listOf(seg("09:12:00")), zone, Locale("ar")),
        )
    }

    @Test
    fun `时码格式 M SS`() {
        assertEquals("0:00", formatTimecode(0))
        assertEquals("0:23", formatTimecode(23_000))
        assertEquals("1:00", formatTimecode(60_000))
        assertEquals("12:34", formatTimecode(754_000))
        assertEquals("0:00", formatTimecode(-5))
    }
}
