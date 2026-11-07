package dev.livachat.app.data.local

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import dev.livachat.app.domain.model.ServerConfig
import dev.livachat.app.domain.model.UserPreferences
import dev.livachat.app.domain.model.parseServerAddress
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class PreferencesDataSource @Inject constructor(
    private val dataStore: DataStore<Preferences>,
) {
    val preferences: Flow<UserPreferences> = dataStore.data.map { prefs ->
        UserPreferences(
            vadInterruptEnabled = prefs[KEY_VAD_INTERRUPT] ?: false,
            defaultFrontCamera = prefs[KEY_FRONT_CAMERA] ?: false,
            autoCameraOnJoin = prefs[KEY_AUTO_CAMERA] ?: false,
            hapticEnabled = prefs[KEY_HAPTIC] ?: true,
            avatarVisible = prefs[KEY_AVATAR_VISIBLE] ?: true,
            screenRecordRationaleShown = prefs[KEY_SCREEN_RECORD_RATIONALE] ?: false,
            aiGeneratedNoticeShown = prefs[KEY_AI_GENERATED_NOTICE_SHOWN] ?: false,
            tlsAllowSelfSigned = prefs[KEY_TLS_ALLOW_SELF_SIGNED] ?: true,
            avdetectorEnabled = prefs[KEY_AVDETECTOR_ENABLED] ?: true,
        )
    }

    /** 供 AppModule/MylifeModule 进程启动时同步读取 TLS 策略。 */
    val tlsAllowSelfSigned: Flow<Boolean> = dataStore.data.map {
        it[KEY_TLS_ALLOW_SELF_SIGNED] ?: true
    }

    /** 供 AppModule 进程启动时同步读取 AVDetector 初始开关。 */
    val avdetectorEnabled: Flow<Boolean> = dataStore.data.map {
        it[KEY_AVDETECTOR_ENABLED] ?: true
    }

    suspend fun setScreenRecordRationaleShown() {
        dataStore.edit { it[KEY_SCREEN_RECORD_RATIONALE] = true }
    }

    suspend fun setAiGeneratedNoticeShown() {
        dataStore.edit { it[KEY_AI_GENERATED_NOTICE_SHOWN] = true }
    }

    suspend fun updateVadInterrupt(enabled: Boolean) {
        dataStore.edit { it[KEY_VAD_INTERRUPT] = enabled }
    }

    suspend fun updateDefaultFrontCamera(front: Boolean) {
        dataStore.edit { it[KEY_FRONT_CAMERA] = front }
    }

    suspend fun updateAutoCameraOnJoin(enabled: Boolean) {
        dataStore.edit { it[KEY_AUTO_CAMERA] = enabled }
    }

    suspend fun updateHaptic(enabled: Boolean) {
        dataStore.edit { it[KEY_HAPTIC] = enabled }
    }

    suspend fun updateAvdetector(enabled: Boolean) {
        dataStore.edit { it[KEY_AVDETECTOR_ENABLED] = enabled }
    }

    suspend fun updateAvatarVisible(visible: Boolean) {
        dataStore.edit { it[KEY_AVATAR_VISIBLE] = visible }
    }

    val isFirstLaunch: Flow<Boolean> = dataStore.data.map { prefs ->
        prefs[KEY_FIRST_LAUNCH] ?: true
    }

    suspend fun setFirstLaunchComplete() {
        dataStore.edit { it[KEY_FIRST_LAUNCH] = false }
    }

    /**
     * 用户服务器配置（地址 + 认证密钥）；读取口径见 [resolveServerAddress]（docs/setting_server_address.md §3.1）：
     * 缺失/非法一律空串（未配置态，由对话页引导弹窗提示用户去设置；不做旧键映射与默认回退）。
     */
    val serverConfig: Flow<ServerConfig> = dataStore.data.map { prefs ->
        ServerConfig(
            address = resolveServerAddress(prefs[KEY_SERVER_ADDRESS]),
            token = prefs[KEY_AUTH_TOKEN] ?: "",
        )
    }

    suspend fun updateServerConfig(
        address: String,
        token: String,
        tlsAllowSelfSigned: Boolean,
    ) {
        dataStore.edit {
            it[KEY_SERVER_ADDRESS] = address
            it[KEY_AUTH_TOKEN] = token
            it[KEY_TLS_ALLOW_SELF_SIGNED] = tlsAllowSelfSigned
        }
    }

    companion object {
        private val KEY_VAD_INTERRUPT = booleanPreferencesKey("vad_interrupt_enabled")
        private val KEY_FRONT_CAMERA = booleanPreferencesKey("default_front_camera")
        private val KEY_AUTO_CAMERA = booleanPreferencesKey("auto_camera_on_join")
        private val KEY_HAPTIC = booleanPreferencesKey("haptic_enabled")
        private val KEY_AVATAR_VISIBLE = booleanPreferencesKey("avatar_visible")
        private val KEY_FIRST_LAUNCH = booleanPreferencesKey("is_first_launch")
        private val KEY_SCREEN_RECORD_RATIONALE = booleanPreferencesKey("screen_record_rationale_shown")
        private val KEY_AI_GENERATED_NOTICE_SHOWN = booleanPreferencesKey("ai_generated_notice_shown")
        private val KEY_TLS_ALLOW_SELF_SIGNED = booleanPreferencesKey("tls_allow_self_signed")
        private val KEY_AVDETECTOR_ENABLED = booleanPreferencesKey("avdetector_enabled")
        private val KEY_SERVER_ADDRESS = stringPreferencesKey("server_address")
        private val KEY_AUTH_TOKEN = stringPreferencesKey("auth_token")
    }
}

/** 地址读取解析（纯函数，单测直测）：合法则用原文；缺失/非法 → 空串（未配置态，不回退默认）。 */
internal fun resolveServerAddress(storedAddress: String?): String =
    storedAddress?.takeIf { parseServerAddress(it) != null }.orEmpty()
