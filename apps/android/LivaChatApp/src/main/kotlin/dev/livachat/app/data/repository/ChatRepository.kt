package dev.livachat.app.data.repository

import android.content.Context
import android.util.Base64
import androidx.annotation.VisibleForTesting
import dev.livachat.app.data.local.dao.DailySessionDao
import dev.livachat.app.data.local.dao.MessageDao
import dev.livachat.app.data.local.dao.SessionDao
import dev.livachat.app.data.local.entity.MessageEntity
import dev.livachat.app.data.local.entity.SessionEntity
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.domain.model.SessionSummary
import dev.livachat.sdk.model.AiImage
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ChatRepository @Inject constructor(
    private val sessionDao: SessionDao,
    private val messageDao: MessageDao,
    private val dailySessionDao: DailySessionDao,
    @ApplicationContext private val context: Context,
) {
    private val imagesDir: File
        get() = File(context.filesDir, "chat_images").also { it.mkdirs() }

    fun observeMessages(sessionId: String): Flow<List<ChatMessage>> =
        messageDao.getBySessionId(sessionId).map { entities ->
            entities.map { it.toDomain() }
        }

    suspend fun appendMessage(message: ChatMessage) {
        messageDao.upsert(message.toEntity())
        sessionDao.incrementMessageCount(message.sessionId)
        if (message.role == MessageRole.USER) {
            sessionDao.setFirstUserMessageIfNull(message.sessionId, message.text)
        }
    }

    suspend fun getMessages(sessionId: String): List<ChatMessage> =
        messageDao.getBySessionIdOnce(sessionId).map { it.toDomain() }

    suspend fun getSession(sessionId: String): SessionSummary? =
        sessionDao.getById(sessionId)?.let { it.toDomain() }

    suspend fun getMaxSeq(sessionId: String): Int =
        messageDao.getMaxSeq(sessionId)

    suspend fun upsertSession(session: SessionSummary) {
        sessionDao.upsert(session.toEntity())
    }

    suspend fun deleteSession(sessionId: String) {
        // Clean up image files for this session
        val messages = messageDao.getBySessionIdOnce(sessionId)
        for (msg in messages) {
            msg.imagePath?.let { File(it).delete() }
        }
        sessionDao.deleteById(sessionId)
        dailySessionDao.deleteBySessionId(sessionId)
    }

    /** 清空全部聊天图片；顺带回收旧版本清空后遗留的孤儿文件。 */
    fun deleteAllImages() {
        val dir = imagesDir
        dir.deleteRecursively()
        dir.mkdirs()
    }

    // ------------------------------------------------------------------
    // Mappers
    // ------------------------------------------------------------------

    private fun MessageEntity.toDomain(): ChatMessage {
        val image = if (imagePath != null) {
            val file = File(imagePath)
            if (file.exists()) {
                val bytes = file.readBytes()
                AiImage(
                    data = Base64.encodeToString(bytes, Base64.DEFAULT),
                    mime = imageMime ?: "image/jpeg",
                    width = imageWidth ?: 0,
                    height = imageHeight ?: 0,
                    orientation = imageOrientation ?: 0,
                    cameraFacing = imageCameraFacing ?: "front",
                )
            } else {
                null
            }
        } else {
            null
        }
        return ChatMessage(
            id = id,
            sessionId = sessionId,
            role = MessageRole.valueOf(role),
            text = text,
            isFinal = isFinal,
            seq = seq,
            timestamp = timestamp,
            image = image,
            source = source,
            mode = mode,
        )
    }

    private suspend fun ChatMessage.toEntity(): MessageEntity {
        val img = image
        val path: String?
        val mime: String?
        val w: Int?
        val h: Int?
        val orient: Int?
        val facing: String?
        if (img != null) {
            val file = File(imagesDir, "${id}.jpg")
            val bytes = Base64.decode(img.data, Base64.DEFAULT)
            file.writeBytes(bytes)
            path = file.absolutePath
            mime = img.mime
            w = img.width
            h = img.height
            orient = img.orientation
            facing = img.cameraFacing
        } else {
            path = null
            mime = null
            w = null
            h = null
            orient = null
            facing = null
        }
        return MessageEntity(
            id = id,
            sessionId = sessionId,
            role = role.name,
            text = text,
            isFinal = isFinal,
            seq = seq,
            timestamp = timestamp,
            imagePath = path,
            imageMime = mime,
            imageWidth = w,
            imageHeight = h,
            imageOrientation = orient,
            imageCameraFacing = facing,
            source = source,
            mode = mode,
        )
    }

    @VisibleForTesting
    fun imagesDirectory(): File = imagesDir

    // ------------------------------------------------------------------
    // Session mappers (unchanged)
    // ------------------------------------------------------------------

    private fun SessionEntity.toDomain() = SessionSummary(
        id = id,
        startTime = startTime,
        endTime = endTime,
        messageCount = messageCount,
        summary = summary,
        firstUserMessage = firstUserMessage,
    )

    private fun SessionSummary.toEntity() = SessionEntity(
        id = id,
        startTime = startTime,
        endTime = endTime,
        messageCount = messageCount,
        summary = summary,
        firstUserMessage = firstUserMessage,
    )
}
