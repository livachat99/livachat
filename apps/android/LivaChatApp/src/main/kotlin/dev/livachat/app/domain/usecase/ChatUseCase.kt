package dev.livachat.app.domain.usecase

import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.domain.model.ChatMessage
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ChatUseCase @Inject constructor(
    private val chatRepository: ChatRepository,
) {
    fun observeCurrentSessionMessages(sessionId: String): Flow<List<ChatMessage>> =
        chatRepository.observeMessages(sessionId)

    suspend fun appendMessage(message: ChatMessage) {
        chatRepository.appendMessage(message)
    }

    suspend fun getMessages(sessionId: String): List<ChatMessage> =
        chatRepository.getMessages(sessionId)
}
