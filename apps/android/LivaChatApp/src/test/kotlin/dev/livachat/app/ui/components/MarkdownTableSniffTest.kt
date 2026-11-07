package dev.livachat.app.ui.components

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 表格嗅探 `markdownHasTable`（docs/android_agent_job_view.md §12.9 缺陷 4）：判据 = GFM **分隔行**。
 * 命中 → 正文给整宽（规避 Markwon 表格 0 宽首测列宽坍缩，§12.2）；漏判 → 气泡内表格列宽坍缩。
 */
class MarkdownTableSniffTest {

    // ── 命中：四种合法表格写法 ──

    @Test
    fun leadingAndTrailingPipe_detected() {
        assertTrue(
            markdownHasTable(
                """
                | 航班 | 价格 |
                | --- | --- |
                | MU 523 | ¥2,150 |
                """.trimIndent(),
            ),
        )
    }

    @Test
    fun noLeadingPipe_detected() {
        // 缺陷 4 的主诉：旧嗅探 `^\s*\|` 漏掉这种合法 GFM 写法
        assertTrue(
            markdownHasTable(
                """
                航班 | 价格
                --- | ---
                MU 523 | ¥2,150
                """.trimIndent(),
            ),
        )
    }

    @Test
    fun alignmentColons_detected() {
        assertTrue(markdownHasTable("a | b\n:--- | ---:\n1 | 2"))
    }

    @Test
    fun singleColumn_withPipes_detected() {
        assertTrue(markdownHasTable("| 项 |\n| --- |\n| 甲 |"))
    }

    @Test
    fun minimalHyphens_detected() {
        // 宽容到 1 个短横：各实现的最小横线数要求不一，漏判代价 > 误判代价（误判只多占宽度）
        assertTrue(markdownHasTable("a | b\n- | -\n1 | 2"))
    }

    @Test
    fun indentedDelimiter_withinThreeSpaces_detected() {
        assertTrue(markdownHasTable("a | b\n  --- | ---\n1 | 2"))
    }

    // ── 不命中：正文/其它 markdown 构造 ──

    @Test
    fun proseWithPipe_notDetected() {
        assertFalse(markdownHasTable("预算 3,000 | 实际 2,150 元"))
    }

    @Test
    fun proseLineBeforeDelimiterLike_proseLineItselfNotMatched() {
        // 表格首行（表头）不该被当分隔行；单看表头 `航班 | 价格` 不应判为表格
        assertFalse(markdownHasTable("航班 | 价格\nMU 523 | ¥2,150"))
    }

    @Test
    fun horizontalRule_notDetected() {
        // `---` 单独一行是水平线（或被吸成 setext 标题下划线），不是表格
        assertFalse(markdownHasTable("标题\n---\n正文"))
    }

    @Test
    fun codeFencePipeLine_notDetected() {
        // 旧嗅探在此误报（把短气泡拉成整宽）
        assertFalse(
            markdownHasTable(
                """
                示例：
                ```
                | x = 1
                | y = 2
                ```
                """.trimIndent(),
            ),
        )
    }

    @Test
    fun emptyBody_notDetected() {
        assertFalse(markdownHasTable(""))
    }

    // ── 病态输入：不得抛错/爆栈（§12.9 缺陷 5：早先的正则在「长 `|-` 交替」上栈溢出）──

    @Test
    fun pathologicalInputs_doNotThrow() {
        val n = 16_000
        assertFalse("纯管道行：非表格", markdownHasTable("|".repeat(n)))
        assertFalse("纯短横行：非表格（无管道）", markdownHasTable("-".repeat(n)))
        assertFalse("末尾破坏形态：非表格", markdownHasTable("| ".repeat(n / 2) + "x"))
        assertFalse("冒号空白短横：非表格（无管道）", markdownHasTable(": - ".repeat(n / 6)))
        // `|-|-|…` 本身是合法 2 列分隔行（各格 1 个短横）→ 命中
        assertTrue(markdownHasTable("|-".repeat(n / 2)))
    }
}
