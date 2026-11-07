package dev.livachat.app.data

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import app.cash.turbine.test
import dev.livachat.app.data.local.PreferencesDataSource
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
class PreferencesDataSourceTest {

    @get:Rule
    val tempDir = TemporaryFolder()

    @org.junit.Ignore("DataStore requires Android context for file creation; covered by instrumentation tests")
    @Test
    fun `default preferences are returned on first read`() = runTest {
        val file = tempDir.newFile("prefs1")
        val dataStore = PreferenceDataStoreFactory.create(
            scope = this,
            produceFile = { file },
        )
        val dataSource = PreferencesDataSource(dataStore)

        dataSource.preferences.test {
            val prefs = awaitItem()
            assertTrue(prefs.vadInterruptEnabled)
            assertTrue(prefs.defaultFrontCamera)
            assertTrue(prefs.tlsAllowSelfSigned)
            assertEquals(false, prefs.aiGeneratedNoticeShown)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @org.junit.Ignore("DataStore requires Android context for file creation; covered by instrumentation tests")
    @Test
    fun `updateVadInterrupt persists and emits new value`() = runTest {
        val file = tempDir.newFile("prefs2")
        val dataStore = PreferenceDataStoreFactory.create(
            scope = this,
            produceFile = { file },
        )
        val dataSource = PreferencesDataSource(dataStore)

        dataSource.updateVadInterrupt(false)

        dataSource.preferences.test {
            assertEquals(false, awaitItem().vadInterruptEnabled)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @org.junit.Ignore("Robolectric 环境 DataStore 文件创建抛 IllegalStateException（本文件其余用例同因）")
    @Test
    fun `avatarVisible defaults to true and roundtrips`() = runTest {
        val file = tempDir.newFile("prefs_avatar")
        val dataStore = PreferenceDataStoreFactory.create(
            scope = this,
            produceFile = { file },
        )
        val dataSource = PreferencesDataSource(dataStore)

        dataSource.preferences.test {
            assertEquals(true, awaitItem().avatarVisible)
            cancelAndIgnoreRemainingEvents()
        }

        dataSource.updateAvatarVisible(false)

        dataSource.preferences.test {
            assertEquals(false, awaitItem().avatarVisible)
            cancelAndIgnoreRemainingEvents()
        }
    }
}
