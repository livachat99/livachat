package dev.livachat.app.data.repository

import dev.livachat.app.data.local.dao.DailySessionDao
import dev.livachat.app.data.local.dao.SessionDao
import dev.livachat.app.data.local.entity.SessionEntity
import dev.livachat.app.domain.model.SessionSummary
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class HistoryRepository @Inject constructor(
    private val sessionDao: SessionDao,
    private val dailySessionDao: DailySessionDao,
) {
    fun getAllSessions(): Flow<List<SessionSummary>> =
        sessionDao.getAll().map { entities ->
            entities.map { it.toDomain() }
        }

    fun searchSessions(query: String): Flow<List<SessionSummary>> =
        sessionDao.search(query).map { entities ->
            entities.map { it.toDomain() }
        }

    suspend fun getSessionById(id: String): SessionSummary? =
        sessionDao.getById(id)?.toDomain()

    suspend fun deleteAll() {
        sessionDao.deleteAll()
        dailySessionDao.deleteAll()
    }

    private fun SessionEntity.toDomain() = SessionSummary(
        id = id,
        startTime = startTime,
        endTime = endTime,
        messageCount = messageCount,
        summary = summary,
        firstUserMessage = firstUserMessage,
    )
}
