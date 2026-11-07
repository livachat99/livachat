package dev.livachat.app.di

import android.content.Context
import coil.ImageLoader
import dagger.Module
import dagger.Provides
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import dev.livachat.app.data.remote.TlsOkHttpClientFactory
import dev.livachat.app.media.AudioPlayer
import dev.livachat.app.media.ExoAudioPlayer
import dev.livachat.sdk.LivaChatConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import java.net.URI
import javax.inject.Named
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object MylifeModule {

    /** Compose 侧（非 VM）获取 D4 共享 Coil ImageLoader 的入口。 */
    @EntryPoint
    @InstallIn(SingletonComponent::class)
    interface MylifeEntryPoint {
        /** D4 client 注入的 Coil loader：自签 TLS 下帧图必须与 API/音频同一 trust-all client（§5.4）。 */
        fun coilImageLoader(): ImageLoader
    }

    fun mylifeImageLoader(context: android.content.Context): ImageLoader =
        EntryPointAccessors.fromApplication(context.applicationContext, MylifeEntryPoint::class.java)
            .coilImageLoader()

    @Provides
    @Singleton
    @Named("mylifeApiBase")
    fun provideMylifeApiBase(config: LivaChatConfig): HttpUrl {
        // 与 AppModule.provideMylifeUrl 同一推导规则：wss → https / ws → http，端口保留
        val uri = URI(config.serverUrl)
        val scheme = if (uri.scheme == "wss") "https" else "http"
        val defaultPort = if (uri.scheme == "wss") 443 else 80
        val port = if (uri.port == -1) defaultPort else uri.port
        val host = uri.host ?: throw IllegalStateException("serverUrl 无 host: ${config.serverUrl}")
        return HttpUrl.Builder()
            .scheme(scheme)
            .host(host)
            .port(port)
            .addPathSegments("api/mylife")
            .build()
    }

    /** Repository 级 pending 轮询作用域（MylifeRepository，各消费视图共享，§6.4）。 */
    @Provides
    @Singleton
    fun provideMylifeScope(): CoroutineScope =
        CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @Provides
    @Singleton
    @Named("mylife")
    fun provideMylifeOkHttpClient(
        config: LivaChatConfig,
        clients: TlsOkHttpClientFactory,
    ): OkHttpClient {
        // 复用 SDK 的同一份进程启动快照（AppModule 已读取持久化 TLS 设置），
        // 避免第二处 DataStore 读取导致 MyLife 与 WS 使用不同策略。
        // 该 client 用于 MyLife API/media、Coil、ExoPlayer；Settings 预检使用草稿策略。
        return clients.create(config.allowSelfSigned)
    }

    @Provides
    @Singleton
    fun provideCoilImageLoader(
        @ApplicationContext context: Context,
        @Named("mylife") client: OkHttpClient,
    ): ImageLoader = ImageLoader.Builder(context)
        .callFactory { client }
        .build()

    /** 播放器接口 → 实现（object 模块不能用 @Binds；audio gallery 单测注入 fake 走接口）。 */
    @Provides
    @Singleton
    fun provideAudioPlayer(player: ExoAudioPlayer): AudioPlayer = player
}
