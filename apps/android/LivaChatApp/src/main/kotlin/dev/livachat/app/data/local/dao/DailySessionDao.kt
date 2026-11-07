package dev.livachat.app.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import dev.livachat.app.data.local.entity.DailySessionEntity

@Dao
interface DailySessionDao {

    @Query("SELECT * FROM daily_sessions WHERE date = :date")
    suspend fun getByDate(date: String): DailySessionEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(dailySession: DailySessionEntity)

    @Query("DELETE FROM daily_sessions WHERE date = :date")
    suspend fun deleteByDate(date: String)

    @Query("DELETE FROM daily_sessions WHERE sessionId = :sessionId")
    suspend fun deleteBySessionId(sessionId: String)

    @Query("DELETE FROM daily_sessions")
    suspend fun deleteAll()
}
