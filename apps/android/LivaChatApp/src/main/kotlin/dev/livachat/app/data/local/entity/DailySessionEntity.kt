package dev.livachat.app.data.local.entity

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "daily_sessions")
data class DailySessionEntity(
    @PrimaryKey val date: String,
    val sessionId: String,
)
