package dev.livachat.app.domain.usecase

import android.content.Context
import androidx.room.Room
import dev.livachat.app.data.local.LivaChatDatabase
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import java.time.Instant

@RunWith(RobolectricTestRunner::class)
class SessionUseCaseTest {

    private lateinit var db: LivaChatDatabase
    private lateinit var chatRepository: ChatRepository
    private lateinit var useCase: SessionUseCase

    @Before
    fun setUp() {
        val context: Context = RuntimeEnvironment.getApplication()
        db = Room.inMemoryDatabaseBuilder(context, LivaChatDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        chatRepository = ChatRepository(db.sessionDao(), db.messageDao(), db.dailySessionDao(), context)
        useCase = SessionUseCase(chatRepository, db.dailySessionDao())
    }

    @After
    fun tearDown() {
        db.close()
    }

    @Test
    fun `endSession keeps messages and writes metadata from actual rows`() = runTest {
        val session = useCase.createOrGetTodaySession()
        chatRepository.appendMessage(chatMessage("user-u1", session.id, MessageRole.USER, "u1", seq = 0))
        chatRepository.appendMessage(chatMessage("ai-a1", session.id, MessageRole.AI, "a1", seq = 1))
        chatRepository.appendMessage(chatMessage("user-u2", session.id, MessageRole.USER, "u2", seq = 2))

        useCase.endSession(session.id)

        assertEquals("endSession 不得清空消息历史", 3, chatRepository.getMessages(session.id).size)

        val row = chatRepository.getSession(session.id)!!
        assertEquals(3, row.messageCount)
        assertEquals("u1 u2", row.summary)
        assertEquals("u1", row.firstUserMessage)
        assertNotNull(row.endTime)
    }

    private fun chatMessage(
        id: String,
        sessionId: String,
        role: MessageRole,
        text: String,
        seq: Int,
    ) = ChatMessage(
        id = id,
        sessionId = sessionId,
        role = role,
        text = text,
        isFinal = true,
        seq = seq,
        timestamp = Instant.now(),
    )
}
