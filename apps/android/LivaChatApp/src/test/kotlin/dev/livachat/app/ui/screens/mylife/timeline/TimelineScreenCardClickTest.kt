package dev.livachat.app.ui.screens.mylife.timeline

import io.mockk.mockk
import io.mockk.verify
import org.junit.Assert.assertNull
import org.junit.Test

/** 卡片本体点击分派（Q2：周卡本体点击移除；docs/android_timeline_hotslot.md §4.3）。 */
class TimelineScreenCardClickTest {

    private val viewModel = mockk<TimelineViewModel>(relaxed = true)

    private fun digest(granularity: Granularity) = PeriodDigest(
        key = when (granularity) {
            Granularity.DAY -> "2026-08-21"
            Granularity.WEEK -> "2026-W34"
            Granularity.MONTH -> "2026-08"
            Granularity.YEAR -> "2026"
        },
        granularity = granularity,
        rangeLabel = "x",
        hotSlots = emptyList(),
        summaryLines = emptyList(),
        summaryLimit = 3,
        topics = emptyList(),
        images = emptyList(),
        imageTotal = 0,
        hasPending = false,
    )

    @Test
    fun `日卡周卡本体不可点`() {
        assertNull(cardBodyClick(digest(Granularity.DAY), viewModel))
        assertNull(cardBodyClick(digest(Granularity.WEEK), viewModel))
    }

    @Test
    fun `月年本体点击下钻`() {
        cardBodyClick(digest(Granularity.MONTH), viewModel)!!.invoke()
        cardBodyClick(digest(Granularity.YEAR), viewModel)!!.invoke()
        verify(exactly = 2) { viewModel.onDrillDown(any()) }
    }
}
