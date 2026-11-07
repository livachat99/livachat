package dev.livachat.app.ui.components

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** 折叠态剩余字数与展示格式（docs/android_agent_job_view.md §11）。 */
class JobResultCollapseTest {

    @Test
    fun truncated_returnsRemainingChars() {
        // 8 行上限、末行有省略号 → 剩余 = 全文长度 - 可见前缀偏移
        assertEquals(
            1_240,
            jobResultRemainingChars(
                textLength = 1_800,
                lineCount = 30,
                lastVisibleLineEnd = 560,
                maxLines = 8,
            ),
        )
    }

    @Test
    fun notReachingLineLimit_returnsNull() {
        assertNull(
            jobResultRemainingChars(
                textLength = 300,
                lineCount = 5,
                lastVisibleLineEnd = 300,
                maxLines = 8,
            ),
        )
    }

    @Test
    fun lineCountAtLimit_returnsNull() {
        // 正好 8 行 = 未超限（边界，不折叠）
        assertNull(
            jobResultRemainingChars(
                textLength = 400,
                lineCount = 8,
                lastVisibleLineEnd = 400,
                maxLines = 8,
            ),
        )
    }

    @Test
    fun offsetBeyondText_clampsToZero() {
        assertEquals(
            0,
            jobResultRemainingChars(
                textLength = 100,
                lineCount = 30,
                lastVisibleLineEnd = 140,
                maxLines = 8,
            ),
        )
    }

    @Test
    fun formatRemainingChars_usesThousandsSeparator() {
        assertEquals("0", formatRemainingChars(0))
        assertEquals("999", formatRemainingChars(999))
        assertEquals("1,240", formatRemainingChars(1_240))
        assertEquals("12,345", formatRemainingChars(12_345))
    }
}
