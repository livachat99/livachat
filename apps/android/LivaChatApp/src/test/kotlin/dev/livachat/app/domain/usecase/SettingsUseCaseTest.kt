package dev.livachat.app.domain.usecase

import app.cash.turbine.test
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.model.ServerConfig
import dev.livachat.app.domain.model.UserPreferences
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class SettingsUseCaseTest {

    private val settingsRepo = mockk<SettingsRepository>(relaxed = true)
    private val useCase = SettingsUseCase(settingsRepo)

    @Test
    fun `observePreferences delegates to repo`() = runTest {
        val prefs = UserPreferences(hapticEnabled = false)
        every { settingsRepo.observePreferences() } returns flowOf(prefs)

        useCase.observePreferences().test {
            assertEquals(false, awaitItem().hapticEnabled)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `setVadInterruptEnabled calls repo`() = runTest {
        useCase.setVadInterruptEnabled(false)
        coVerify { settingsRepo.setVadInterruptEnabled(false) }
    }

    @Test
    fun `observeServerConfig delegates to repo`() = runTest {
        val config = ServerConfig("example.com:9000", "tok-1")
        every { settingsRepo.observeServerConfig() } returns flowOf(config)

        useCase.observeServerConfig().test {
            assertEquals(config, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `setServerConfig calls repo`() = runTest {
        useCase.setServerConfig("example.com:9000", "tok-1", true)
        coVerify { settingsRepo.setServerConfig("example.com:9000", "tok-1", true) }
    }
}
