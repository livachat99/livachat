package dev.livachat.app.ui.screens.mylife.hotslot

import dev.livachat.app.data.remote.mylife.dto.EventType
import dev.livachat.app.data.remote.mylife.dto.HotSlotEvent
import java.time.Instant
import java.util.TimeZone
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** 行模型纯逻辑（docs/android_timeline_hotslot.md §10.3）：五类 side/后缀、分钟桶去重、Q10 白名单、帧分组。 */
class HotSlotRowsTest {

    /** Wave6：固定 zh locale + zh 后缀（生产由 UI 层传入）。 */
    private fun rowsOf(events: List<HotSlotEvent>) =
        buildHotSlotRows(events, java.util.Locale.SIMPLIFIED_CHINESE, "(转录)", "(原文)", "(译文)")


    /** 固定 +08 下的基准时刻 = 2026-08-21 09:12:00 +08。 */
    private val base = Instant.parse("2026-08-21T01:12:00Z").toEpochMilli()

    private fun at(offsetMs: Long) = base + offsetMs

    private fun <T> withShanghai(block: () -> T): T {
        val original = TimeZone.getDefault()
        return try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            block()
        } finally {
            TimeZone.setDefault(original)
        }
    }

    private fun ev(
        ts: Long,
        type: String,
        role: String? = null,
        text: String? = null,
        frameId: String? = null,
        mediaUrl: String? = null,
        ref: String = "r-$ts",
    ) = HotSlotEvent(
        tsMs = ts, type = type, role = role, text = text,
        frameId = frameId, mediaUrl = mediaUrl, eventRef = ref,
    )

    @Test
    fun `五类映射 side 与后缀`() {
        val rows = rowsOf(
            listOf(
                ev(1000, EventType.CHAT, role = "user", text = "你好"),
                ev(2000, EventType.CHAT, role = "assistant", text = "Hello"),
                ev(3000, EventType.AGENT_JOB, text = "完成任务"),
                ev(4000, EventType.TRANSCRIBE, role = "user", text = "转录文本"),
                ev(5000, EventType.TRANSLATE, role = "user", text = "原文"),
                ev(6000, EventType.TRANSLATE, role = "assistant", text = "译文"),
            ),
        ).map { it as HotSlotRow.Text }
        assertEquals(
            listOf(
                BubbleSide.USER, BubbleSide.AI, BubbleSide.AGENT,
                BubbleSide.USER, BubbleSide.USER, BubbleSide.AI,
            ),
            rows.map { it.side },
        )
        assertEquals(
            listOf("你好", "Hello", "完成任务", "转录文本 (转录)", "原文 (原文)", "译文 (译文)"),
            rows.map { it.body },
        )
    }

    @Test
    fun `文本行携带原始 tsMs（锚点定位用）`() {
        val rows = rowsOf(listOf(ev(7000, EventType.CHAT, role = "user", text = "你好")))
            .map { it as HotSlotRow.Text }
        assertEquals(7000L, rows[0].tsMs)
    }

    @Test
    fun `锚点行解析：精确命中优先、帧行内命中、被剔除时退化首个覆盖行`() {
        val rows = rowsOf(
            listOf(
                ev(1000, EventType.CHAT, role = "user", text = "a"),
                ev(2000, EventType.VIDEO, frameId = "f1", mediaUrl = "u1"),
                ev(2100, EventType.VIDEO, frameId = "f2", mediaUrl = "u2"),
                ev(2200, EventType.VIDEO, frameId = "f3", mediaUrl = "u3"),
                ev(3000, EventType.CHAT, role = "user", text = "b"),
            ),
        )
        assertEquals(0, anchorRowIndex(rows, 1000L))   // 文本行精确命中
        assertEquals(1, anchorRowIndex(rows, 2100L))   // 帧行内任意帧命中 → 整行
        assertEquals(0, anchorRowIndex(rows, 500L))    // 锚点在首行之前 → 首行覆盖
        assertEquals(1, anchorRowIndex(rows, 1500L))   // 1500 被剔除 → 帧行尾 2200 ≥ 1500
        assertEquals(2, anchorRowIndex(rows, 2500L))   // 2500 被剔除 → 帧行尾 2200 < 2500 → 下一文本行
        assertEquals(-1, anchorRowIndex(rows, 4000L))  // 全部早于锚点 → 不滚动
    }

    // ---- 分钟桶（§10.3 需求 2 ㊲）----

    @Test
    fun `分钟桶：仅首行带标签、同分钟为 null、跨分钟再现`() = withShanghai {
        val rows = rowsOf(
            listOf(
                ev(at(0), EventType.CHAT, role = "user", text = "a"),        // 09:12:00
                ev(at(30_000), EventType.CHAT, role = "assistant", text = "b"), // 09:12:30 同桶
                ev(at(60_000), EventType.CHAT, role = "user", text = "c"),   // 09:13:00 新桶
                ev(at(119_000), EventType.CHAT, role = "user", text = "d"),  // 09:13:59 同桶
                ev(at(120_000), EventType.CHAT, role = "user", text = "e"),  // 09:14:00 新桶
            ),
        )
        assertEquals(listOf("09:12", null, "09:13", null, "09:14"), rows.map { it.timeLabel })
    }

    @Test
    fun `被白名单丢弃的行不消耗分钟标签`() = withShanghai {
        val rows = rowsOf(
            listOf(
                ev(at(0), EventType.CHAT, role = "system", text = "sys"),     // 丢弃
                ev(at(30_000), EventType.CHAT, role = "user", text = "ok"),   // 同桶仍带标签
            ),
        )
        assertEquals(listOf("09:12"), rows.map { it.timeLabel })
    }

    @Test
    fun `帧行多 chunk 仅首块带标签`() = withShanghai {
        val frames = (0 until 5).map {
            ev(at(it * 1000L), EventType.VIDEO, frameId = "f$it", mediaUrl = "u$it")
        }
        val rows = rowsOf(frames)
        assertEquals(2, rows.size)
        assertEquals(listOf("09:12", null), rows.map { it.timeLabel })
    }

    @Test
    fun `帧 chunk 跨分钟：按首帧定桶，次帧不另出标签（边界 #11）`() = withShanghai {
        val rows = rowsOf(
            listOf(
                ev(at(0), EventType.CHAT, role = "user", text = "prev"),      // 09:12:00 → 标签
                ev(at(59_000), EventType.VIDEO, frameId = "f1", mediaUrl = "u1"), // 09:12:59（首帧定桶）
                ev(at(61_000), EventType.VIDEO, frameId = "f2", mediaUrl = "u2"), // 09:13:01 同 chunk
                ev(at(90_000), EventType.CHAT, role = "user", text = "after"),  // 09:13:30 → 新桶标签
            ),
        )
        assertEquals(listOf("09:12", null, "09:13"), rows.map { it.timeLabel })
    }

    @Test
    fun `白名单外 role 行不渲染（Q10）`() {
        val rows = rowsOf(
            listOf(
                ev(1000, EventType.CHAT, role = "system", text = "sys"),
                ev(2000, EventType.TRANSCRIBE, role = "unknown", text = "转录"),
                ev(3000, EventType.CHAT, role = "user", text = "ok"),
            ),
        )
        assertEquals(1, rows.size)
        assertEquals("ok", (rows[0] as HotSlotRow.Text).body)
    }

    @Test
    fun `连续帧按每行 3 张分组且文本行打断`() {
        val frames = (0 until 5).map {
            ev(100L + it, EventType.VIDEO, frameId = "f$it", mediaUrl = "u$it")
        }
        val rows = rowsOf(
            frames.take(2) + ev(200, EventType.CHAT, role = "user", text = "打断") + frames.drop(2),
        )
        val groups = rows.filterIsInstance<HotSlotRow.Frames>()
        assertEquals(listOf(2, 3), groups.map { it.frames.size })
        assertTrue(rows[1] is HotSlotRow.Text)
    }

    @Test
    fun `标题与行时间用设备时区 HHmm`() {
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"))
            val ts = Instant.parse("2026-08-21T01:12:00Z").toEpochMilli() // 09:12 +08
            assertEquals("8月21日 09:12", formatHotSlotTitle(ts, java.util.Locale.SIMPLIFIED_CHINESE))
            assertEquals("09:12", formatRowTime(ts, java.util.Locale.SIMPLIFIED_CHINESE))
        } finally {
            TimeZone.setDefault(original)
        }
    }

    @Test
    fun `ar 标题与行时间数字本地化为阿数（§20 缺口⑤）`() = withShanghai {
        val ts = Instant.parse("2026-08-21T01:12:00Z").toEpochMilli() // 09:12 +08
        assertEquals("٢١ أغسطس ٠٩:١٢", formatHotSlotTitle(ts, java.util.Locale("ar")))
        assertEquals("٠٩:١٢", formatRowTime(ts, java.util.Locale("ar")))
    }
}
