package dev.livachat.app.domain.usecase

import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.HistoryRepository
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.SessionSummary
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class HistoryUseCase @Inject constructor(
    private val historyRepository: HistoryRepository,
    private val chatRepository: ChatRepository,
) {
    fun getAllSessions(): Flow<List<SessionSummary>> =
        historyRepository.getAllSessions()

    fun searchSessions(query: String): Flow<List<SessionSummary>> =
        historyRepository.searchSessions(query)

    suspend fun getSessionDetail(sessionId: String): Pair<SessionSummary?, List<ChatMessage>> {
        val session = historyRepository.getSessionById(sessionId)
        val messages = chatRepository.getMessages(sessionId)
        return session to messages
    }

    suspend fun deleteSession(sessionId: String) {
        chatRepository.deleteSession(sessionId)
    }

    suspend fun clearAll() {
        // 先删文件再删行：进程在两步之间被杀时最多留下图片缺失的历史行（可再次清空），
        // 不会留下已无入口可触发的孤儿图片
        chatRepository.deleteAllImages()
        historyRepository.deleteAll()
    }
}
