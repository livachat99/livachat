package dev.livachat.app.e2e

import android.content.Context
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.data.local.LivaChatDatabase
import dev.livachat.app.data.local.entity.MessageEntity
import dev.livachat.app.data.local.entity.SessionEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Instant

@RunWith(AndroidJUnit4::class)
class HistoryPersistenceE2ETest {

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
    fun fullConversation_persists_session_and_messages() = runBlocking {
        // Create session
        val session = SessionEntity("e2e-sess", Instant.now(), null, 0, "Test E2E")
        db.sessionDao().upsert(session)

        // Add messages
        val msg1 = MessageEntity("m1", "e2e-sess", "USER", "Hello", true, 0, Instant.now())
        val msg2 = MessageEntity("m2", "e2e-sess", "AI", "Hi there!", true, 1, Instant.now())
        db.messageDao().upsert(msg1)
        db.messageDao().upsert(msg2)

        // Verify persistence
        val retrieved = db.messageDao().getBySessionIdOnce("e2e-sess")
        assertEquals(2, retrieved.size)
        assertEquals("Hello", retrieved[0].text)
        assertEquals("Hi there!", retrieved[1].text)

        // End session
        val ended = session.copy(endTime = Instant.now(), messageCount = 2)
        db.sessionDao().upsert(ended)

        val stored = db.sessionDao().getById("e2e-sess")
        assertEquals(2, stored!!.messageCount)
    }
}
