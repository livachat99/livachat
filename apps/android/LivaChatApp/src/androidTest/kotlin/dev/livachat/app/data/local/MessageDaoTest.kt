package dev.livachat.app.data.local

import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.data.local.entity.MessageEntity
import dev.livachat.app.data.local.entity.SessionEntity
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Instant

@RunWith(AndroidJUnit4::class)
class MessageDaoTest {

    private lateinit var db: LivaChatDatabase

    @Before
    fun setUp() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        db = Room.inMemoryDatabaseBuilder(context, LivaChatDatabase::class.java).build()
    }

    @After
    fun tearDown() {
        db.close()
    }

    @Test
    fun insertAndGetBySessionIdOrderedBySeq() = runBlocking {
        db.sessionDao().upsert(SessionEntity("s1", Instant.now(), null, 0, null))
        val msg1 = MessageEntity("m1", "s1", "USER", "Hi", true, 0, Instant.now())
        val msg2 = MessageEntity("m2", "s1", "AI", "Hello there", false, 1, Instant.now())
        db.messageDao().upsert(msg1)
        db.messageDao().upsert(msg2)

        val messages = db.messageDao().getBySessionIdOnce("s1")
        assertEquals(2, messages.size)
        assertEquals("Hi", messages[0].text)
        assertEquals("Hello there", messages[1].text)
    }

    @Test
    fun deleteBySessionIdRemovesAllMessages() = runBlocking {
        db.sessionDao().upsert(SessionEntity("s1", Instant.now(), null, 0, null))
        db.sessionDao().upsert(SessionEntity("s2", Instant.now(), null, 0, null))
        db.messageDao().upsert(MessageEntity("m1", "s1", "USER", "a", true, 0, Instant.now()))
        db.messageDao().upsert(MessageEntity("m2", "s1", "AI", "b", true, 1, Instant.now()))
        db.messageDao().upsert(MessageEntity("m3", "s2", "USER", "c", true, 0, Instant.now()))

        db.messageDao().deleteBySessionId("s1")

        assertEquals(0, db.messageDao().getBySessionIdOnce("s1").size)
        assertEquals(1, db.messageDao().getBySessionIdOnce("s2").size)
    }
}
