package dev.livachat.app.domain.usecase

import app.cash.turbine.test
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.Instant

class ChatUseCaseTest {

    private val chatRepository = mockk<ChatRepository>(relaxed = true)
    private val useCase = ChatUseCase(chatRepository)

    @Test
    fun `observeCurrentSessionMessages returns messages via repo`() = runTest {
        val messages = listOf(
            ChatMessage("m1", "s1", MessageRole.USER, "Hello", true, 1, Instant.now()),
        )
        every { chatRepository.observeMessages("s1") } returns MutableStateFlow(messages)

        useCase.observeCurrentSessionMessages("s1").test {
            val result = awaitItem()
            assertEquals(1, result.size)
            assertEquals("Hello", result[0].text)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `appendMessage calls repo`() = runTest {
        val msg = ChatMessage("m1", "s1", MessageRole.AI, "Hi", false, 2, Instant.now())
        useCase.appendMessage(msg)
        coVerify { chatRepository.appendMessage(msg) }
    }
}
