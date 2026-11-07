package dev.livachat.app.data

import app.cash.turbine.test
import dev.livachat.app.data.local.PreferencesDataSource
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.model.ServerConfig
import dev.livachat.app.domain.model.UserPreferences
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class SettingsRepositoryTest {

    private val dataSource = mockk<PreferencesDataSource>(relaxed = true)
    private val repository = SettingsRepository(dataSource)

    @Test
    fun `observePreferences delegates to dataSource`() = runTest {
        val prefs = UserPreferences(hapticEnabled = false)
        every { dataSource.preferences } returns flowOf(prefs)

        repository.observePreferences().test {
            assertEquals(false, awaitItem().hapticEnabled)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `setVadInterruptEnabled calls dataSource`() = runTest {
        repository.setVadInterruptEnabled(false)
        coVerify { dataSource.updateVadInterrupt(false) }
    }

    @Test
    fun `setDefaultFrontCamera calls dataSource`() = runTest {
        repository.setDefaultFrontCamera(false)
        coVerify { dataSource.updateDefaultFrontCamera(false) }
    }

    @Test
    fun `setAutoCameraOnJoin calls dataSource`() = runTest {
        repository.setAutoCameraOnJoin(true)
        coVerify { dataSource.updateAutoCameraOnJoin(true) }
    }

    @Test
    fun `setHapticEnabled calls dataSource`() = runTest {
        repository.setHapticEnabled(false)
        coVerify { dataSource.updateHaptic(false) }
    }

    @Test
    fun `setAvatarVisible calls dataSource`() = runTest {
        repository.setAvatarVisible(false)
        coVerify { dataSource.updateAvatarVisible(false) }
    }

    @Test
    fun `observeServerConfig delegates to dataSource`() = runTest {
        val config = ServerConfig("example.com:9000", "tok-1")
        every { dataSource.serverConfig } returns flowOf(config)

        repository.observeServerConfig().test {
            assertEquals(config, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `setServerConfig calls dataSource`() = runTest {
        repository.setServerConfig("example.com:9000", "tok-1", true)
        coVerify { dataSource.updateServerConfig("example.com:9000", "tok-1", true) }
    }
}
