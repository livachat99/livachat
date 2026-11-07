package dev.livachat.app.data

import android.content.Context
import app.cash.turbine.test
import dev.livachat.app.data.local.dao.DailySessionDao
import dev.livachat.app.data.local.dao.MessageDao
import dev.livachat.app.data.local.dao.SessionDao
import dev.livachat.app.data.local.entity.MessageEntity
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.time.Instant

class ChatRepositoryTest {

    @get:Rule
    val tmpDir = TemporaryFolder()

    private lateinit var context: Context
    private val sessionDao = mockk<SessionDao>(relaxed = true)
    private val messageDao = mockk<MessageDao>(relaxed = true)
    private val dailySessionDao = mockk<DailySessionDao>(relaxed = true)
    private lateinit var repository: ChatRepository

    private val sampleMessage = ChatMessage(
        id = "msg-1",
        sessionId = "sess-1",
        role = MessageRole.USER,
        text = "Hello",
        isFinal = true,
        seq = 1,
        timestamp = Instant.ofEpochMilli(1000),
    )

    @Before
    fun setUp() {
        tmpDir.create()
        context = mockk {
            every { filesDir } returns tmpDir.root
        }
        repository = ChatRepository(sessionDao, messageDao, dailySessionDao, context)
    }

    @Test
    fun `appendMessage calls dao upsert once`() = runTest {
        val slot = slot<MessageEntity>()
        coEvery { messageDao.upsert(capture(slot)) } returns Unit

        repository.appendMessage(sampleMessage)

        coVerify { messageDao.upsert(any()) }
        assertEquals("msg-1", slot.captured.id)
        assertEquals(MessageRole.USER.name, slot.captured.role)
    }

    @Test
    fun `observeMessages maps entities to domain`() = runTest {
        val entity = MessageEntity(
            id = "msg-1", sessionId = "sess-1", role = "USER",
            text = "Hi", isFinal = false, seq = 0,
            timestamp = Instant.ofEpochMilli(2000),
            source = "agent",
        )
        every { messageDao.getBySessionId("sess-1") } returns flowOf(listOf(entity))

        repository.observeMessages("sess-1").test {
            val messages = awaitItem()
            assertEquals(1, messages.size)
            assertEquals("Hi", messages[0].text)
            assertEquals(MessageRole.USER, messages[0].role)
            assertEquals("agent", messages[0].source)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `deleteSession calls dao deleteById`() = runTest {
        repository.deleteSession("sess-1")
        coVerify { sessionDao.deleteById("sess-1") }
    }

    @Test
    fun `deleteSession deletes only that session's image files`() = runTest {
        val images = repository.imagesDirectory()
        val own = File(images, "own.jpg").apply { writeBytes(byteArrayOf(1)) }
        val other = File(images, "other.jpg").apply { writeBytes(byteArrayOf(2)) }
        coEvery { messageDao.getBySessionIdOnce("sess-1") } returns listOf(
            MessageEntity(
                id = "msg-1", sessionId = "sess-1", role = "USER",
                text = "Hi", isFinal = true, seq = 1,
                timestamp = Instant.ofEpochMilli(1000),
                imagePath = own.absolutePath,
            ),
        )

        repository.deleteSession("sess-1")

        assertFalse(own.exists())
        assertTrue(other.exists())
        coVerify { sessionDao.deleteById("sess-1") }
        coVerify { dailySessionDao.deleteBySessionId("sess-1") }
    }

    @Test
    fun `deleteAllImages removes all local image files`() {
        val images = repository.imagesDirectory()
        val first = File(images, "first.jpg").apply { writeBytes(byteArrayOf(1)) }
        val second = File(images, "second.jpg").apply { writeBytes(byteArrayOf(2)) }

        repository.deleteAllImages()

        assertFalse(first.exists())
        assertFalse(second.exists())
        assertTrue(images.isDirectory)
    }

    @Test
    fun `deleteAllImages recreates a missing images directory`() {
        val images = repository.imagesDirectory()
        images.deleteRecursively()
        assertFalse(images.exists())

        repository.deleteAllImages()

        assertTrue(images.isDirectory)
    }
}
