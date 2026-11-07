package dev.livachat.app.data.repository

import dev.livachat.app.data.remote.mylife.MylifeApi
import dev.livachat.app.data.remote.mylife.dto.OverviewDayDto
import dev.livachat.app.data.remote.mylife.dto.OverviewDto
import dev.livachat.sdk.LivaChatConfig
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import java.io.IOException
import java.time.LocalDate
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * 回归：init 轮询协程的网络异常必须就地兜底，不得逃逸崩进程
 * （真机 13:25:09 FATAL：Dispatchers.Default 裸 launch 内 SSLHandshakeException）。
 * 与 MylifeRepositoryTest 分文件：catch 内走 android.util.Log，需 Robolectric 宿主。
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
class MylifeRepositoryPollTest {

    private fun config() = LivaChatConfig(
        serverUrl = "wss://example.com/v1/ws/stream",
        token = "dev-token",
        deviceId = "dev-1",
        clientVersion = "android-1.0.0",
        logFilePath = "/tmp/livachat.log",
    )

    private fun overviewDto(hasPending: Boolean) = OverviewDto(
        timezone = "Asia/Shanghai",
        days = listOf(OverviewDayDto(date = "2026-08-17", hasPending = hasPending)),
    )

    /** 自 2026-09-30 起倒推的第 [offset, offset+count) 天（分页窗口用例）。 */
    private fun window(count: Int, offset: Int, hasPending: Boolean, next: String? = null) = OverviewDto(
        timezone = "Asia/Shanghai",
        days = (0 until count).map {
            OverviewDayDto(
                date = LocalDate.of(2026, 9, 30).minusDays((offset + it).toLong()).toString(),
                hasPending = hasPending,
            )
        },
        nextCursor = next,
    )

    @Test
    fun `poll tick refresh 失败被兜底且按 60s 节奏重试`() = runTest {
        val api = mockk<MylifeApi>()
        var calls = 0
        coEvery { api.overview(timezone = any(), deviceId = null, limit = any(), cursor = any()) } answers {
            calls++
            when (calls) {
                1 -> overviewDto(true) // 手动首载 → pending
                2 -> throw IOException("TLS down") // 轮询 tick 刷新失败（崩溃场景）
                else -> overviewDto(false) // 下一轮恢复
            }
        }
        val repoScope = CoroutineScope(StandardTestDispatcher(testScheduler) + SupervisorJob())
        val repo = MylifeRepository(api, "https://example.com/api/mylife".toHttpUrl(), config(), repoScope)
        try {
            runCurrent() // 启动轮询协程（first{} 挂起：overview 尚为 null）
            repo.refreshOverview() // call 1：载入 pending
            runCurrent() // 轮询醒来：命中 pending → delay(60s)
            advanceTimeBy(60_000)
            runCurrent() // tick → call 2 抛异常（修复前：异常逃逸崩进程）
            advanceTimeBy(60_000)
            runCurrent() // tick → call 3 成功（证明轮询未死）
            assertEquals(3, calls)
            coVerify(exactly = 3) { api.overview(timezone = any(), deviceId = null, limit = any(), cursor = any()) }
            assertEquals(false, repo.overview.value!!.days[0].hasPending) // 恢复后状态已落库
        } finally {
            repoScope.cancel()
        }
    }

    /**
     * §8 E10/R4：轮询必须重取**已加载窗口**（limit = 窗口天数、不带 cursor）——只刷第一页
     * 会让窗口内更早的 pending 日永不更新（徽章常亮 + 轮询死循环）。
     */
    @Test
    fun `poll tick 按已加载窗口重取而非只刷第一页`() = runTest {
        val api = mockk<MylifeApi>()
        val limits = mutableListOf<Int?>()
        val cursors = mutableListOf<String?>()
        var call = 0
        coEvery { api.overview(timezone = any(), deviceId = null, limit = any(), cursor = any()) } answers {
            call++
            limits += arg<Int?>(2)
            cursors += arg<String?>(3)
            when (call) {
                1 -> window(30, 0, hasPending = true, next = "2026-09-01")  // 首载：最新 30 天 pending
                2 -> window(30, 30, hasPending = true)                      // loadMore：更早 30 天也 pending
                else -> window(60, 0, hasPending = false)                   // 轮询重取整窗（60 天）→ 全部 ok
            }
        }
        val repoScope = CoroutineScope(StandardTestDispatcher(testScheduler) + SupervisorJob())
        val repo = MylifeRepository(api, "https://example.com/api/mylife".toHttpUrl(), config(), repoScope)
        try {
            runCurrent()
            repo.refreshOverview(limit = 30)                    // call 1
            repo.loadMoreOverview(30)                           // call 2 → 窗口 60 天
            runCurrent()
            assertTrue(repo.hasPendingAnalysis())
            advanceTimeBy(60_000)
            runCurrent()                                        // call 3：轮询 tick
            assertEquals(listOf(30, 30, 60), limits)             // 轮询按窗口 60 天重取
            assertEquals(listOf(null, "2026-09-01", null), cursors)
            assertEquals(60, repo.overview.value!!.days.size)    // 合并保深度（未截回 30）
            assertFalse(repo.hasPendingAnalysis())               // 窗口内 pending 已熄 → 轮询可停
        } finally {
            repoScope.cancel()
        }
    }
}
