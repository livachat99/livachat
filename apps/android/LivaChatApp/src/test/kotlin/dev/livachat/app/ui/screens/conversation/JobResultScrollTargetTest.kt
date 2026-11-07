package dev.livachat.app.ui.screens.conversation

import org.junit.Assert.assertEquals
import org.junit.Test

/** 全屏落位目标计算（docs/android_agent_job_view.md §10.2）边界。 */
class JobResultScrollTargetTest {

    @Test
    fun normalLine_landsAtOneThirdFromTop() {
        // 视口 900：命中行在 y=1200 → 滚到 1200 - 300 = 900（该行落在视口 1/3 处）
        assertEquals(900, jobResultScrollTarget(lineTop = 1_200, viewportHeight = 900, maxScroll = 5_000))
    }

    @Test
    fun earlyLine_clampsToTop() {
        // 文首附近：lineTop - viewport/3 为负 → 0
        assertEquals(0, jobResultScrollTarget(lineTop = 100, viewportHeight = 900, maxScroll = 5_000))
        assertEquals(0, jobResultScrollTarget(lineTop = 0, viewportHeight = 0, maxScroll = 0))
    }

    @Test
    fun beyondContent_clampsToMaxScroll() {
        // 命中行接近文末：目标超过可滚范围 → 夹到 maxScroll
        assertEquals(5_000, jobResultScrollTarget(lineTop = 99_000, viewportHeight = 900, maxScroll = 5_000))
    }
}
