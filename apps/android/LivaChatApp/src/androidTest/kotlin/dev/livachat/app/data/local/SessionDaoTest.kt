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
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Instant

@RunWith(AndroidJUnit4::class)
class SessionDaoTest {

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
    fun insertAndGetById() = runBlocking {
        val session = SessionEntity("s1", Instant.now(), null, 0, null)
        db.sessionDao().upsert(session)

        val result = db.sessionDao().getById("s1")
        assertNotNull(result)
        assertEquals("s1", result!!.id)
    }

    @Test
    fun deleteSessionCascadesToMessages() = runBlocking {
        val session = SessionEntity("s1", Instant.now(), null, 0, null)
        db.sessionDao().upsert(session)

        val msg = MessageEntity("m1", "s1", "USER", "hello", true, 0, Instant.now())
        db.messageDao().upsert(msg)

        db.sessionDao().deleteById("s1")

        assertNull(db.sessionDao().getById("s1"))
        val messages = db.messageDao().getBySessionIdOnce("s1")
        assertEquals(0, messages.size)
    }

    @Test
    fun upsertReplacesExisting() = runBlocking {
        val session = SessionEntity("s1", Instant.ofEpochMilli(1000), null, 0, null)
        db.sessionDao().upsert(session)

        val updated = SessionEntity("s1", Instant.ofEpochMilli(2000), Instant.ofEpochMilli(3000), 5, "done")
        db.sessionDao().upsert(updated)

        val result = db.sessionDao().getById("s1")
        assertEquals(5, result!!.messageCount)
        assertEquals("done", result.summary)
    }
}
