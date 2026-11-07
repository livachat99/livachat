package dev.livachat.app.data.local

import androidx.room.Database
import androidx.room.RoomDatabase
import androidx.room.TypeConverters
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import dev.livachat.app.data.local.dao.DailySessionDao
import dev.livachat.app.data.local.dao.MessageDao
import dev.livachat.app.data.local.dao.SessionDao
import dev.livachat.app.data.local.entity.DailySessionEntity
import dev.livachat.app.data.local.entity.MessageEntity
import dev.livachat.app.data.local.entity.SessionEntity

@Database(
    entities = [SessionEntity::class, MessageEntity::class, DailySessionEntity::class],
    version = 9,
    exportSchema = true,
)
@TypeConverters(Converters::class)
abstract class LivaChatDatabase : RoomDatabase() {
    abstract fun sessionDao(): SessionDao
    abstract fun messageDao(): MessageDao
    abstract fun dailySessionDao(): DailySessionDao

    companion object {
        val MIGRATION_5_6 = object : Migration(5, 6) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE sessions ADD COLUMN firstUserMessage TEXT")
            }
        }

        val MIGRATION_6_7 = object : Migration(6, 7) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("""
                    CREATE TABLE daily_sessions (
                        date TEXT PRIMARY KEY NOT NULL,
                        sessionId TEXT NOT NULL
                    )
                """)
                // Backfill: one entry per day from existing sessions (earliest session per day)
                db.execSQL("""
                    INSERT OR IGNORE INTO daily_sessions (date, sessionId)
                    SELECT date(startTime / 1000, 'unixepoch', 'localtime'), id
                    FROM sessions
                    GROUP BY date(startTime / 1000, 'unixepoch', 'localtime')
                    ORDER BY startTime ASC
                """)
            }
        }

        val MIGRATION_7_8 = object : Migration(7, 8) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // Existing rows get NULL (treated as LLM replies); only new
                // agent messages carry source going forward.
                db.execSQL("ALTER TABLE messages ADD COLUMN source TEXT")
            }
        }

        val MIGRATION_8_9 = object : Migration(8, 9) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // §2.3.3 D4=B：仅新写入携带 mode；历史行保持 NULL（其后缀已并入 text，不回拆）。
                db.execSQL("ALTER TABLE messages ADD COLUMN mode TEXT")
            }
        }
    }
}
