package dev.livachat.app.data.local.entity

import androidx.room.Entity
import androidx.room.PrimaryKey
import java.time.Instant

@Entity(tableName = "sessions")
data class SessionEntity(
    @PrimaryKey val id: String,
    val startTime: Instant,
    val endTime: Instant?,
    val messageCount: Int,
    val summary: String?,
    val firstUserMessage: String? = null,
)
