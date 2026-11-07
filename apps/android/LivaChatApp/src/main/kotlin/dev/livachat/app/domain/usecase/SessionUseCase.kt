package dev.livachat.app.domain.usecase

import dev.livachat.app.data.local.dao.DailySessionDao
import dev.livachat.app.data.local.entity.DailySessionEntity
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.domain.model.SessionSummary
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SessionUseCase @Inject constructor(
    private val chatRepository: ChatRepository,
    private val dailySessionDao: DailySessionDao,
) {
    private val today: String
        get() = LocalDate.now(ZoneId.systemDefault()).toString()

    suspend fun createOrGetTodaySession(): SessionSummary {
        val existing = dailySessionDao.getByDate(today)
        if (existing != null) {
            chatRepository.getSession(existing.sessionId)?.let { return it }
        }
        val sessionId = UUID.randomUUID().toString()
        dailySessionDao.upsert(DailySessionEntity(date = today, sessionId = sessionId))
        val session = SessionSummary(
            id = sessionId,
            startTime = Instant.now(),
            endTime = null,
            messageCount = 0,
            summary = null,
        )
        chatRepository.upsertSession(session)
        return session
    }

    suspend fun forceNewTodaySession(): SessionSummary {
        dailySessionDao.deleteByDate(today)
        return createOrGetTodaySession()
    }

    suspend fun getNextSeq(sessionId: String): Int =
        chatRepository.getMaxSeq(sessionId) + 1

    suspend fun endSession(sessionId: String) {
        val messages = chatRepository.getMessages(sessionId)
        val existing = chatRepository.getSession(sessionId)
        val firstUserMessage = messages
            .firstOrNull { it.role == MessageRole.USER }
            ?.text
        val summary = SessionSummary(
            id = sessionId,
            startTime = existing?.startTime ?: Instant.now(),
            endTime = Instant.now(),
            messageCount = messages.size,
            summary = generateSummary(messages),
            firstUserMessage = firstUserMessage,
        )
        chatRepository.upsertSession(summary)
    }

    private fun generateSummary(messages: List<dev.livachat.app.domain.model.ChatMessage>): String? {
        if (messages.isEmpty()) return null
        val userMessages = messages.filter { it.role == dev.livachat.app.domain.model.MessageRole.USER }
        if (userMessages.isEmpty()) return null
        val preview = userMessages.take(3).joinToString(" ") { it.text }
        return if (preview.length > 100) preview.take(100) + "..." else preview
    }
}
