package dev.livachat.app.data.local.entity

import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import java.time.Instant

@Entity(
    tableName = "messages",
    foreignKeys = [
        ForeignKey(
            entity = SessionEntity::class,
            parentColumns = ["id"],
            childColumns = ["sessionId"],
            onDelete = ForeignKey.CASCADE,
        ),
    ],
    indices = [Index("sessionId")],
)
data class MessageEntity(
    @PrimaryKey val id: String,
    val sessionId: String,
    val role: String,
    val text: String,
    val isFinal: Boolean,
    val seq: Int,
    val timestamp: Instant,
    // Image stored as a JPEG file on disk; Room holds the file path.
    val imagePath: String? = null,
    val imageMime: String? = null,
    val imageWidth: Int? = null,
    val imageHeight: Int? = null,
    val imageOrientation: Int? = null,
    val imageCameraFacing: String? = null,
    // Origin of the message: "agent" for agent-pushed replies, null/"llm" for LLM replies.
    val source: String? = null,
    // 会话模式标记（§2.3.3 D4=B）："transcribe"/"translate"；null = 无后缀或迁移前历史行（后缀已并入 text）。
    val mode: String? = null,
)
