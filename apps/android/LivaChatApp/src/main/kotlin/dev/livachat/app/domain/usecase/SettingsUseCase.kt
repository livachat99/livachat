package dev.livachat.app.domain.usecase

import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.model.ServerConfig
import dev.livachat.app.domain.model.UserPreferences
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SettingsUseCase @Inject constructor(
    private val settingsRepository: SettingsRepository,
) {
    fun observePreferences(): Flow<UserPreferences> =
        settingsRepository.observePreferences()

    suspend fun setVadInterruptEnabled(enabled: Boolean) {
        settingsRepository.setVadInterruptEnabled(enabled)
    }

    suspend fun setDefaultFrontCamera(front: Boolean) {
        settingsRepository.setDefaultFrontCamera(front)
    }

    suspend fun setAutoCameraOnJoin(enabled: Boolean) {
        settingsRepository.setAutoCameraOnJoin(enabled)
    }

    suspend fun setHapticEnabled(enabled: Boolean) {
        settingsRepository.setHapticEnabled(enabled)
    }

    suspend fun setAvdetectorEnabled(enabled: Boolean) {
        settingsRepository.setAvdetectorEnabled(enabled)
    }

    fun observeServerConfig(): Flow<ServerConfig> =
        settingsRepository.observeServerConfig()

    suspend fun setServerConfig(
        address: String,
        token: String,
        tlsAllowSelfSigned: Boolean,
    ) {
        settingsRepository.setServerConfig(address, token, tlsAllowSelfSigned)
    }
}
