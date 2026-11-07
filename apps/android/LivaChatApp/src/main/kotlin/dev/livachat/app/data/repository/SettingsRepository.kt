package dev.livachat.app.data.repository

import dev.livachat.app.data.local.PreferencesDataSource
import dev.livachat.app.domain.model.ServerConfig
import dev.livachat.app.domain.model.UserPreferences
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SettingsRepository @Inject constructor(
    private val dataSource: PreferencesDataSource,
) {
    fun observePreferences(): Flow<UserPreferences> = dataSource.preferences

    suspend fun setVadInterruptEnabled(enabled: Boolean) {
        dataSource.updateVadInterrupt(enabled)
    }

    suspend fun setDefaultFrontCamera(front: Boolean) {
        dataSource.updateDefaultFrontCamera(front)
    }

    suspend fun setAutoCameraOnJoin(enabled: Boolean) {
        dataSource.updateAutoCameraOnJoin(enabled)
    }

    suspend fun setHapticEnabled(enabled: Boolean) {
        dataSource.updateHaptic(enabled)
    }

    suspend fun setAvdetectorEnabled(enabled: Boolean) {
        dataSource.updateAvdetector(enabled)
    }

    suspend fun setAvatarVisible(visible: Boolean) {
        dataSource.updateAvatarVisible(visible)
    }

    suspend fun setScreenRecordRationaleShown() {
        dataSource.setScreenRecordRationaleShown()
    }

    suspend fun setAiGeneratedNoticeShown() {
        dataSource.setAiGeneratedNoticeShown()
    }

    val isFirstLaunch: Flow<Boolean> = dataSource.isFirstLaunch

    suspend fun setFirstLaunchComplete() {
        dataSource.setFirstLaunchComplete()
    }

    fun observeServerConfig(): Flow<ServerConfig> = dataSource.serverConfig

    suspend fun setServerConfig(
        address: String,
        token: String,
        tlsAllowSelfSigned: Boolean,
    ) {
        dataSource.updateServerConfig(address, token, tlsAllowSelfSigned)
    }
}
