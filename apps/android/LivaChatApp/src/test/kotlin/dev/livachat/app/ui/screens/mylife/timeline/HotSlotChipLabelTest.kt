package dev.livachat.app.ui.screens.mylife.timeline

import dev.livachat.app.ui.screens.mylife.timeline.components.formatStart
import java.time.Instant
import java.util.TimeZone
import org.junit.Assert.assertEquals
import org.junit.Test

/** chip 标签（docs/android_timeline_hotslot.md §4.2）：只显示开始时间 hh:mm，无结束时间/日期前缀。 */
class HotSlotChipLabelTest {

    @Test
    fun `chip 标签只含开始时间`() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            val slot = HotSlot(
                startMs = Instant.parse("2026-08-21T01:12:00Z").toEpochMilli(), // 09:12 +08
                endMs = Instant.parse("2026-08-21T03:45:00Z").toEpochMilli(),
            )
            assertEquals("09:12", formatStart(slot, java.util.Locale.SIMPLIFIED_CHINESE))
        } finally {
            TimeZone.setDefault(original)
        }
    }

    @Test
    fun `ar chip 标签数字本地化为阿数（§20 缺口⑤）`() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            val slot = HotSlot(
                startMs = Instant.parse("2026-08-21T01:12:00Z").toEpochMilli(), // 09:12 +08
                endMs = Instant.parse("2026-08-21T03:45:00Z").toEpochMilli(),
            )
            assertEquals("٠٩:١٢", formatStart(slot, java.util.Locale("ar")))
        } finally {
            TimeZone.setDefault(original)
        }
    }
}
