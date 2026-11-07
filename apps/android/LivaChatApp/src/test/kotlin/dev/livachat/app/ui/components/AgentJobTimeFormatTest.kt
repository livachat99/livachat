package dev.livachat.app.ui.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class AgentJobTimeFormatTest {

    // ── formatElapsed ─────────────────────────────────────────────

    @Test
    fun `formatElapsed zero`() {
        assertEquals("0s", formatElapsed(0, java.util.Locale.US))
    }

    @Test
    fun `formatElapsed under a minute`() {
        assertEquals("59s", formatElapsed(59_000, java.util.Locale.US))
    }

    @Test
    fun `formatElapsed exactly one minute`() {
        assertEquals("1m 0s", formatElapsed(60_000, java.util.Locale.US))
    }

    @Test
    fun `formatElapsed minutes and seconds`() {
        assertEquals("1m 1s", formatElapsed(61_000, java.util.Locale.US))
        assertEquals("59m 59s", formatElapsed(3_599_000, java.util.Locale.US))
    }

    @Test
    fun `formatElapsed over an hour`() {
        assertEquals("1h 0m", formatElapsed(3_600_000, java.util.Locale.US))
        assertEquals("2h 0m", formatElapsed(7_200_000, java.util.Locale.US))
        assertEquals("1h 1m", formatElapsed(3_661_000, java.util.Locale.US))
    }

    @Test
    fun `formatElapsed ar 数字为阿拉伯-印度数字单位保持拉丁（§20 缺口⑤ 同面板混排修复）`() {
        val ar = java.util.Locale("ar")
        assertEquals("١٥s", formatElapsed(15_000, ar))
        assertEquals("٥m ٣٠s", formatElapsed(330_000, ar))
        assertEquals("١h ١m", formatElapsed(3_661_000, ar))
    }

    // ── formatStartTime ───────────────────────────────────────────

    @Test
    fun `formatStartTime outputs HH_colon_mm_colon_ss in system timezone`() {
        val epochMs = 1_700_000_000_000L
        val formatted = formatStartTime(epochMs, java.util.Locale.US)
        assertTrue("expected HH:mm:ss but was $formatted", formatted.matches(Regex("\\d{2}:\\d{2}:\\d{2}")))
        assertEquals(
            DateTimeFormatter.ofPattern("HH:mm:ss")
                .withZone(ZoneId.systemDefault())
                .format(Instant.ofEpochMilli(epochMs)),
            formatted,
        )
    }

    @Test
    fun `formatStartTime ar 数字为阿拉伯-印度数字（§20 缺口⑤）`() {
        val epochMs = 1_700_000_000_000L
        val formatted = formatStartTime(epochMs, java.util.Locale("ar"))
        val ascii = DateTimeFormatter.ofPattern("HH:mm:ss")
            .withZone(ZoneId.systemDefault())
            .format(Instant.ofEpochMilli(epochMs))
        val expected = ascii.map { if (it in '0'..'9') '٠' + (it - '0') else it }.joinToString("")
        assertEquals(expected, formatted)
    }
}
