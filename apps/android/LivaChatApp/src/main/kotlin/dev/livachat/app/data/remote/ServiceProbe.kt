package dev.livachat.app.data.remote

import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import dev.livachat.app.domain.model.httpBase
import dev.livachat.app.domain.model.wireToken
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request

/** 服务预检结果（docs/setting_server_address.md §3.3）。 */
enum class ProbeResult { AVAILABLE, AUTH_FAILED, UNREACHABLE }

/** 「检测服务」预检：`GET /healthz_token`（wire token + device_id 校验语义与 WS 握手一致）。
 *  `token` 入参是输入框草稿**明文 secret**（可能 ≠ 已保存配置），实现内派生摘要后发送。 */
interface ServiceProbe {
    suspend fun probe(
        address: String,
        token: String,
        deviceId: String,
        allowSelfSigned: Boolean,
    ): ProbeResult
}

/**
 * OkHttp 实现：按草稿 TLS 设置选择系统校验/自签 client，5s 连接超时。
 * URL 用 [okhttp3.HttpUrl.Builder] 构造——token 自动百分号编码（探测侧不受 SDK 拼接限制）。
 */
@Singleton
class OkHttpServiceProbe @Inject constructor(
    private val clients: TlsOkHttpClientFactory,
) : ServiceProbe {

    override suspend fun probe(
        address: String,
        token: String,
        deviceId: String,
        allowSelfSigned: Boolean,
    ): ProbeResult =
        withContext(Dispatchers.IO) {
            try {
                val client = clients.create(allowSelfSigned)
                    .newBuilder()
                    .connectTimeout(5, TimeUnit.SECONDS)
                    .build()
                val url = httpBase(address).toHttpUrl().newBuilder()
                    .addPathSegment("healthz_token")
                    // 入参为明文 secret，发送前派生 wire token（docs/server_auth.md §4.1）
                    .addQueryParameter("token", wireToken(token))
                    .addQueryParameter("device_id", deviceId)
                    .build()
                val request = Request.Builder().url(url).get().build()
                client.newCall(request).execute().use { response ->
                    when (response.code) {
                        200 -> ProbeResult.AVAILABLE
                        401 -> ProbeResult.AUTH_FAILED
                        else -> ProbeResult.UNREACHABLE
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                ProbeResult.UNREACHABLE
            }
        }
}

@Module
@InstallIn(SingletonComponent::class)
internal abstract class ServiceProbeModule {
    @Binds
    internal abstract fun bindServiceProbe(impl: OkHttpServiceProbe): ServiceProbe
}
