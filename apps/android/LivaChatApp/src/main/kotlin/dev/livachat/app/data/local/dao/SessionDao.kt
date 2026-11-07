package dev.livachat.app.data.local.dao

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Upsert
import dev.livachat.app.data.local.entity.SessionEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface SessionDao {

    // 禁止改回 @Insert(REPLACE)：整行替换会触发 messages 级联删除（SessionDaoTest 回归守护）
    @Upsert
    suspend fun upsert(session: SessionEntity)

    @Query("SELECT * FROM sessions ORDER BY startTime DESC")
    fun getAll(): Flow<List<SessionEntity>>

    @Query("SELECT * FROM sessions WHERE id = :id")
    suspend fun getById(id: String): SessionEntity?

    @Query("""
        SELECT * FROM sessions
        WHERE :query = ''
        OR id IN (
            SELECT sessionId FROM messages WHERE text LIKE '%' || :query || '%'
        )
        OR summary LIKE '%' || :query || '%'
        OR firstUserMessage LIKE '%' || :query || '%'
        ORDER BY startTime DESC
    """)
    fun search(query: String): Flow<List<SessionEntity>>

    @Query("DELETE FROM sessions WHERE id = :id")
    suspend fun deleteById(id: String)

    @Query("UPDATE sessions SET messageCount = messageCount + 1 WHERE id = :sessionId")
    suspend fun incrementMessageCount(sessionId: String)

    @Query("UPDATE sessions SET firstUserMessage = :text WHERE id = :sessionId AND firstUserMessage IS NULL")
    suspend fun setFirstUserMessageIfNull(sessionId: String, text: String)

    @Query("DELETE FROM sessions")
    suspend fun deleteAll()
}
