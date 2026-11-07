package dev.livachat.app.data

import app.cash.turbine.test
import dev.livachat.app.data.local.dao.DailySessionDao
import dev.livachat.app.data.local.dao.SessionDao
import dev.livachat.app.data.local.entity.SessionEntity
import dev.livachat.app.data.repository.HistoryRepository
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.Instant

class HistoryRepositoryTest {

    private val sessionDao = mockk<SessionDao>(relaxed = true)
    private val dailySessionDao = mockk<DailySessionDao>(relaxed = true)
    private val repository = HistoryRepository(sessionDao, dailySessionDao)

    private val sampleEntity = SessionEntity(
        id = "sess-1",
        startTime = Instant.ofEpochMilli(1000),
        endTime = Instant.ofEpochMilli(5000),
        messageCount = 3,
        summary = "test summary",
    )

    @Test
    fun `searchSessions maps entities to domain`() = runTest {
        every { sessionDao.search("天气") } returns flowOf(listOf(sampleEntity))

        repository.searchSessions("天气").test {
            val results = awaitItem()
            assertEquals(1, results.size)
            assertEquals("sess-1", results[0].id)
            assertEquals(3, results[0].messageCount)
            assertEquals("test summary", results[0].summary)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `deleteAll calls dao deleteAll`() = runTest {
        repository.deleteAll()
        coVerify { sessionDao.deleteAll() }
    }

    @Test
    fun `getSessionById returns mapped domain model`() = runTest {
        coEvery { sessionDao.getById("sess-1") } returns sampleEntity

        val result = repository.getSessionById("sess-1")
        assertEquals("sess-1", result?.id)
        assertEquals(3, result?.messageCount)
    }
}
