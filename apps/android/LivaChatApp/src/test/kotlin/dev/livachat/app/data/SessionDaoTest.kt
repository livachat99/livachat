package dev.livachat.app.data

import androidx.room.Room
import dev.livachat.app.data.local.LivaChatDatabase
import dev.livachat.app.data.local.dao.MessageDao
import dev.livachat.app.data.local.dao.SessionDao
import dev.livachat.app.data.local.entity.MessageEntity
import dev.livachat.app.data.local.entity.SessionEntity
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
class SessionDaoTest {

    private lateinit var db: LivaChatDatabase
    private lateinit var sessionDao: SessionDao
    private lateinit var messageDao: MessageDao

    @Before
    fun setUp() {
        db = Room.inMemoryDatabaseBuilder(RuntimeEnvironment.getApplication(), LivaChatDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        sessionDao = db.sessionDao()
        messageDao = db.messageDao()
    }

    @After
    fun tearDown() {
        db.close()
    }

    private fun session(
        id: String,
        messageCount: Int = 0,
        summary: String? = null,
        endTime: Instant? = null,
    ) = SessionEntity(
        id = id,
        startTime = Instant.ofEpochMilli(1_000),
        endTime = endTime,
        messageCount = messageCount,
        summary = summary,
    )

    private fun message(id: String, sessionId: String, seq: Int) = MessageEntity(
        id = id,
        sessionId = sessionId,
        role = "USER",
        text = "msg-$id",
        isFinal = true,
        seq = seq,
        timestamp = Instant.ofEpochMilli(2_000L + seq),
    )

    @Test
    fun `upsert existing session keeps its messages`() = runTest {
        sessionDao.upsert(session("sess-1"))
        messageDao.upsert(message("m1", "sess-1", 0))
        messageDao.upsert(message("m2", "sess-1", 1))
        messageDao.upsert(message("m3", "sess-1", 2))

        // endSession 的写回路径：同 id 覆盖会话行 metadata
        sessionDao.upsert(
            session("sess-1", messageCount = 3, summary = "u1 u2", endTime = Instant.ofEpochMilli(9_000))
        )

        val remaining = messageDao.getBySessionIdOnce("sess-1")
        assertEquals("upsert 会话行不得级联删除消息", 3, remaining.size)
    }

    @Test
    fun `upsert existing session updates metadata`() = runTest {
        sessionDao.upsert(session("sess-1"))

        sessionDao.upsert(
            session("sess-1", messageCount = 3, summary = "u1 u2", endTime = Instant.ofEpochMilli(9_000))
        )

        val row = sessionDao.getById("sess-1")!!
        assertEquals(3, row.messageCount)
        assertEquals("u1 u2", row.summary)
        assertEquals(Instant.ofEpochMilli(9_000), row.endTime)
    }

    @Test
    fun `upsert new session inserts`() = runTest {
        sessionDao.upsert(session("sess-1"))

        val row = sessionDao.getById("sess-1")
        assertNotNull(row)
        assertEquals(0, row!!.messageCount)
    }
}
