package dev.livachat.app.domain.usecase

import app.cash.turbine.test
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.HistoryRepository
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.domain.model.SessionSummary
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.coVerifyOrder
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.Instant

class HistoryUseCaseTest {

    private val historyRepo = mockk<HistoryRepository>(relaxed = true)
    private val chatRepo = mockk<ChatRepository>(relaxed = true)
    private val useCase = HistoryUseCase(historyRepo, chatRepo)

    @Test
    fun `searchSessions calls repo search`() = runTest {
        val sessions = listOf(
            SessionSummary("s1", Instant.now(), null, 0, "weather chat"),
        )
        every { historyRepo.searchSessions("weather") } returns flowOf(sessions)

        useCase.searchSessions("weather").test {
            val result = awaitItem()
            assertEquals(1, result.size)
            assertEquals("weather chat", result[0].summary)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `getSessionDetail returns session and messages`() = runTest {
        val session = SessionSummary("s1", Instant.now(), null, 2, "summary")
        val messages = listOf(
            ChatMessage("m1", "s1", MessageRole.USER, "hi", true, 0, Instant.now()),
        )
        coEvery { historyRepo.getSessionById("s1") } returns session
        coEvery { chatRepo.getMessages("s1") } returns messages

        val (s, m) = useCase.getSessionDetail("s1")
        assertEquals("s1", s?.id)
        assertEquals(1, m.size)
    }

    @Test
    fun `deleteSession calls chat repo`() = runTest {
        useCase.deleteSession("s1")
        coVerify { chatRepo.deleteSession("s1") }
    }

    @Test
    fun `clearAll deletes images before db rows`() = runTest {
        useCase.clearAll()
        coVerifyOrder {
            chatRepo.deleteAllImages()
            historyRepo.deleteAll()
        }
    }
}
