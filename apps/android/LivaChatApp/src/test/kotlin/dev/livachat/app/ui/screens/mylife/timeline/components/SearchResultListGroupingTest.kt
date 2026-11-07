package dev.livachat.app.ui.screens.mylife.timeline.components

import dev.livachat.app.data.remote.mylife.dto.SearchHitDto
import org.junit.Assert.assertEquals
import org.junit.Test

/** 搜索结果按日聚合（docs/bff_search_fts.md §3.4 A1：相关度序下同日命中不再相邻）。 */
class SearchResultListGroupingTest {

    private fun hit(date: String, snippet: String) = SearchHitDto(
        date = date, tsMs = 1000L, kind = "chat", snippet = snippet,
    )

    @Test
    fun `同日命中不相邻时聚合为单一日头（首现顺序）`() {
        val groups = groupHits(
            listOf(
                hit("2026-08-21", "a"),
                hit("2026-08-20", "b"),
                hit("2026-08-21", "c"),   // 相关度序下与 a 不相邻
            ),
        )
        assertEquals(listOf("2026-08-21", "2026-08-20"), groups.map { it.date })
        assertEquals(listOf("a", "c"), groups[0].hits.map { it.snippet })
        assertEquals(listOf("b"), groups[1].hits.map { it.snippet })
    }

    @Test
    fun `空列表与单日多命中`() {
        assertEquals(emptyList<HitGroup>(), groupHits(emptyList()))
        val one = groupHits(listOf(hit("2026-08-21", "a"), hit("2026-08-21", "b")))
        assertEquals(1, one.size)
        assertEquals(listOf("a", "b"), one[0].hits.map { it.snippet })
    }
}
