package dev.livachat.app.di

import android.content.Context
import android.util.Log
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import dev.livachat.app.BuildConfig
import dev.livachat.app.data.local.PreferencesDataSource
import dev.livachat.app.domain.model.UNSET_SERVER_WS_URL
import dev.livachat.app.domain.model.parseServerAddress
import dev.livachat.app.domain.model.wireToken
import dev.livachat.app.domain.model.wsUrlFromAddress
import dev.livachat.app.media.CompositeLivaChatListener
import dev.livachat.app.ui.navigation.serverBaseUrl
import dev.livachat.app.ui.util.appLanguageCode
import dev.livachat.sdk.LivaChatConfig
import dev.livachat.sdk.LivaChatListener
import dev.livachat.sdk.LivaChatSDK
import java.util.TimeZone
import java.util.UUID
import javax.inject.Named
import javax.inject.Singleton
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking

@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    private const val TAG = "LivaChatApp"

    @Provides
    @Singleton
    fun provideLivaChatConfig(
        @ApplicationContext context: Context,
        @Named("deviceId") deviceId: String,
        preferences: PreferencesDataSource,
        @Named("allowSelfSigned") allowSelfSigned: Boolean,
    ): LivaChatConfig {
        val logFile = context.filesDir.resolve("logs/livachat.log").absolutePath
        // 服务器配置持久化于 DataStore（设置页保存后需重启进程生效），
        // 这里同步读取首值以决定本次进程的连接目标；文件极小，开销可忽略。
        val serverConfig = runBlocking { preferences.serverConfig.first() }
        // AVDetector 初始开关：运行期变更由 setAvdetectorEnabled 热更新，不必重启。
        val avdetectorEnabled = runBlocking { preferences.avdetectorEnabled.first() }
        // 未配置（地址空/非法）→ 占位 URL（不可路由）：SDK/MylifeModule 各推导不抛异常；
        // 真实连接由对话页「配置不齐备不启动」门控拦截（docs/setting_server_address.md §3.4）。
        val wsUrl = parseServerAddress(serverConfig.address)?.let { wsUrlFromAddress(it) }
            ?: UNSET_SERVER_WS_URL
        // 时区诊断（timezone-support.md）：app 不设 override，SDK 每次上报
        // 系统时区；记录启动时系统时区，便于与 session.json 落值对照。
        Log.d(TAG, "config | server=${serverConfig.address} | $wsUrl | " +
            "timezone_override=null | system_timezone=${TimeZone.getDefault().id}")
        return LivaChatConfig(
            serverUrl = wsUrl,
            // 单点派生（docs/server_auth.md §4.1）：装配处写入 wire token（摘要），
            // 明文 secret 只保留在设置层/DataStore；SDK/MylifeApi/MylifeRepository 零改动。
            token = wireToken(serverConfig.token),
            deviceId = deviceId,
            clientVersion = androidClientVersion(BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE),
            logFilePath = logFile,
            // 上行电平补偿：实测免提语音仅 -23~-42 dBFS（正常录音 -20~-14），
            // 无补偿则录制回放偏小。详见 docs/android_audio_capture.md §4.3。
            uplinkAgc = true,
            // 用户设置（默认 true，对齐 iOS）；修改后需重启进程生效。
            allowSelfSigned = allowSelfSigned,
            // 每次 session.start 现取 App 资源语言，避免 per-app locale 切换后
            // 进程级 Hilt singleton 继续上报旧值。
            languageProvider = {
                val locales = context.resources.configuration.locales
                if (locales.isEmpty()) "en" else appLanguageCode(locales[0])
            },
            avdetectorEnabled = avdetectorEnabled,
        )
    }

    /** 设备持久化 UUID v4：SDK 会话标识与「检测服务」预检共用。 */
    @Provides
    @Singleton
    @Named("deviceId")
    fun provideDeviceId(@ApplicationContext context: Context): String = getOrCreateDeviceId(context)

    /**
     * 进程启动时的自签名证书策略（与 SDK/MyLife/WebView 共用同一持久化值）。
     * 设置页保存后需重启进程生效；WebView 只对已配置服务器放行自签，外部站点仍走系统校验。
     */
    @Provides
    @Singleton
    @Named("allowSelfSigned")
    fun provideAllowSelfSigned(preferences: PreferencesDataSource): Boolean =
        runBlocking { preferences.tlsAllowSelfSigned.first() }

    @Provides
    @Singleton
    fun provideLivaChatSDK(
        config: LivaChatConfig,
        listener: CompositeLivaChatListener,
    ): LivaChatSDK = LivaChatSDK(config, listener)

    /** webroot 链接转换用服务器基址（docs/webroot_feature.md §3.4；
     *  形如 https://<server-host>:8080；IP→域名映射暂注释停用，见 docs/setting_server_address.md）。 */
    @Provides
    @Singleton
    @Named("serverBaseUrl")
    fun provideServerBaseUrl(config: LivaChatConfig): String = serverBaseUrl(config.serverUrl)

    private fun getOrCreateDeviceId(context: Context): String {
        val prefs = context.getSharedPreferences("livachat_device", Context.MODE_PRIVATE)
        var deviceId = prefs.getString("device_id", null)
        if (deviceId == null) {
            deviceId = UUID.randomUUID().toString()
            prefs.edit().putString("device_id", deviceId).apply()
        }
        return deviceId
    }
}
