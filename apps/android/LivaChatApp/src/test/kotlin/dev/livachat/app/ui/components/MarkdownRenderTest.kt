package dev.livachat.app.ui.components

import android.content.Context
import android.text.Spannable
import android.text.Spanned
import android.widget.TextView
import io.noties.markwon.core.spans.LinkSpan
import io.noties.markwon.ext.tables.TableRowSpan
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment

@RunWith(RobolectricTestRunner::class)
class MarkdownRenderTest {

    private fun render(
        markdown: String,
        onLinkClick: (String) -> Unit = {},
    ): Pair<TextView, Spannable> {
        val context = RuntimeEnvironment.getApplication() as Context
        val markwon = buildMarkwon(
            context = context,
            colors = MarkdownColors(text = 0xFF000000.toInt(), link = 0xFF0000FF.toInt(), codeBg = 0xFFCCCCCC.toInt()),
            onLinkClick = onLinkClick,
        )
        val textView = TextView(context)
        markwon.setMarkdown(textView, markdown)
        return textView to (textView.text as Spannable)
    }

    private fun Spannable.linkSpans(): List<LinkSpan> =
        getSpans(0, length, LinkSpan::class.java).map { it as LinkSpan }

    @Test
    fun `bold heading and list render without literal markers`() {
        val (textView, _) = render("**粗体**\n\n# 标题\n\n- 列表项")
        val text = textView.text.toString()
        assertTrue(text.contains("粗体"))
        assertTrue(text.contains("标题"))
        assertTrue(text.contains("列表项"))
        assertFalse(text.contains("**"))
        assertFalse(text.contains("# 标题"))
    }

    @Test
    fun `markdown link click triggers callback with url`() {
        val links = mutableListOf<String>()
        val (textView, spannable) = render("[文档](https://example.com)") { links.add(it) }

        spannable.linkSpans().first().onClick(textView)
        assertEquals(listOf("https://example.com"), links)
    }

    @Test
    fun `bare url is auto-linked and clickable`() {
        val links = mutableListOf<String>()
        val (textView, spannable) = render("看这里 https://example.com") { links.add(it) }

        spannable.linkSpans().first().onClick(textView)
        assertEquals(listOf("https://example.com"), links)
    }

    @Test
    fun `table renders and link inside table cell is clickable`() {
        val links = mutableListOf<String>()
        val markdown = "| A | B |\n|---|---|\n| x | [链接](https://example.com/t) |"
        val (textView, spannable) = render(markdown) { links.add(it) }

        // Markwon 表格以 ReplacementSpan 绘制（TableRowSpan），文本不在 TextView 的 CharSequence 中
        val rowSpans = spannable.getSpans(0, spannable.length, TableRowSpan::class.java)
        assertTrue("table must render as TableRowSpan", rowSpans.isNotEmpty())

        val cellLinks = rowSpans.flatMap { row ->
            val cellsField = row.javaClass.getDeclaredField("cells").apply { isAccessible = true }
            @Suppress("UNCHECKED_CAST")
            val cells = cellsField.get(row) as List<*>
            cells.mapNotNull { cell ->
                val text = cell!!.javaClass.getMethod("text").invoke(cell) as CharSequence
                (text as? Spanned)?.getSpans(0, text.length, LinkSpan::class.java)
                    ?.map { it as LinkSpan }
            }.flatten()
        }
        assertTrue("cell link must be a LinkSpan wired to the resolver", cellLinks.isNotEmpty())
        cellLinks.first().onClick(textView)
        assertEquals(listOf("https://example.com/t"), links)
    }

    @Test
    fun `table after paragraph renders as table`() {
        // adapter 空行折叠已停用，段落与表格之间保留空行（Markwon 解析表格的硬性要求）
        val (textView, spannable) = render("说明如下\n\n| A | B |\n|---|---|\n| 1 | 2 |")
        val rowSpans = spannable.getSpans(0, spannable.length, TableRowSpan::class.java)
        assertTrue("table must render as TableRowSpan: [${textView.text}]", rowSpans.isNotEmpty())
    }

    @Test
    fun `non http scheme link click is blocked by whitelist`() {
        val links = mutableListOf<String>()
        val (textView, spannable) = render("[x](javascript:alert(1))") { links.add(it) }

        spannable.linkSpans().first().onClick(textView)
        assertTrue(links.isEmpty())
    }

    @Test
    fun `soft break renders as newline after paragraph folding`() {
        // 段内单个 \n（soft break）经 SoftBreakAddsNewLinePlugin 渲染为换行而不是空格
        val (textView, _) = render("第一段\n第二段")
        val text = textView.text.toString()
        assertTrue("段落应换行而不是粘连成空格: [$text]", text.contains("第一段\n第二段"))
    }

    @Test
    fun `code block and blockquote render`() {
        val (textView, _) = render("```kotlin\nval a = 1\n```\n\n> 引用内容")
        val text = textView.text.toString()
        assertTrue(text.contains("val a = 1"))
        assertTrue(text.contains("引用内容"))
    }

    @Test
    fun `malformed markdown degrades gracefully`() {
        val (textView, _) = render("未闭合 **加粗\n\n* 孤立列表符号")
        assertTrue(textView.text.toString().contains("未闭合"))
    }

    @Test
    fun `empty markdown renders without crash`() {
        val (textView, _) = render("")
        assertEquals("", textView.text.toString())
    }
}
