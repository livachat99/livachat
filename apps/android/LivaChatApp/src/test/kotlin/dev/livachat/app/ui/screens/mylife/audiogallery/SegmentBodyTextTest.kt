package dev.livachat.app.ui.screens.mylife.audiogallery

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 段正文单区规则（docs/android_timeline_audio_gallery.md §4.4，2026-09-24 定）：
 * 转写字数 > 5 显示转写，否则显示摘要；两者皆无 → 不渲染。
 */
class SegmentBodyTextTest {

    @Test
    fun `转写超过 5 字用转写（摘要被忽略）`() {
        assertEquals(
            "今天天气不错我们下午三点在会议室碰面",
            segmentBodyText("今天天气不错我们下午三点在会议室碰面", "一句摘要"),
        )
        assertEquals("abcdef", segmentBodyText("abcdef", "一句摘要"))
    }

    @Test
    fun `转写恰好 5 字回退摘要（规则是严格大于 5）`() {
        assertEquals("一句摘要", segmentBodyText("第一段转写", "一句摘要"))
        assertEquals("一句摘要", segmentBodyText("abcde", "一句摘要"))
    }

    @Test
    fun `短转写回退摘要`() {
        assertEquals("一句摘要", segmentBodyText("短句", "一句摘要"))
    }

    @Test
    fun `无转写（纯 ASMR 段）用摘要`() {
        assertEquals("ASMR 摘要", segmentBodyText(null, "ASMR 摘要"))
    }

    @Test
    fun `两者皆无返回 null`() {
        assertNull(segmentBodyText(null, null))
    }

    @Test
    fun `短转写且无摘要返回 null`() {
        assertNull(segmentBodyText("短句", null))
    }
}
