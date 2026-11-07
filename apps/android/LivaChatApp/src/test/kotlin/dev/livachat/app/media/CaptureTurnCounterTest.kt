package dev.livachat.app.media

import org.junit.Assert.assertEquals
import org.junit.Test

/** 采集轮计数（docs/fd_pipeline_video_process.md §8.2.2）：从 1 起单调递增；0 = 初始"未启用"哨兵。 */
class CaptureTurnCounterTest {

    @Test
    fun `starts at zero sentinel and increments from one`() {
        val counter = CaptureTurnCounter()

        assertEquals(0L, counter.current)
        assertEquals(1L, counter.next())
        assertEquals(1L, counter.current)
        assertEquals(2L, counter.next())
        assertEquals(2L, counter.current)
    }
}
