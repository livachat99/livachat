package dev.livachat.app.di

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.preferencesDataStoreFile
import androidx.room.Room
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import dev.livachat.app.data.local.LivaChatDatabase
import dev.livachat.app.data.local.dao.DailySessionDao
import dev.livachat.app.data.local.dao.MessageDao
import dev.livachat.app.data.local.dao.SessionDao
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object DatabaseModule {

    @Provides
    @Singleton
    fun provideDatabase(@ApplicationContext context: Context): LivaChatDatabase =
        Room.databaseBuilder(context, LivaChatDatabase::class.java, "livachat.db")
            .addMigrations(
                LivaChatDatabase.MIGRATION_5_6,
                LivaChatDatabase.MIGRATION_6_7,
                LivaChatDatabase.MIGRATION_7_8,
                LivaChatDatabase.MIGRATION_8_9,
            )
            .fallbackToDestructiveMigration()
            .build()

    @Provides
    fun provideSessionDao(db: LivaChatDatabase): SessionDao = db.sessionDao()

    @Provides
    fun provideMessageDao(db: LivaChatDatabase): MessageDao = db.messageDao()

    @Provides
    fun provideDailySessionDao(db: LivaChatDatabase): DailySessionDao = db.dailySessionDao()

    @Provides
    @Singleton
    fun provideDataStore(@ApplicationContext context: Context): DataStore<Preferences> =
        PreferenceDataStoreFactory.create {
            context.preferencesDataStoreFile("livachat_preferences")
        }
}
