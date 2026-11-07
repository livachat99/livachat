package dev.livachat.app.data.remote

import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import okhttp3.OkHttpClient

/**
 * 按用户设置创建 OkHttpClient：
 * - `allowSelfSigned=false` → 系统默认证书校验；
 * - `allowSelfSigned=true` → trust-all（对齐 iOS `TLSPolicy.allowSelfSigned`）。
 *
 * 该工厂只用于 App 层 HTTP（MyLife/Coil/ExoPlayer/ServiceProbe）；SDK 的 WS
 * client 在 `ConnectionManager` 内部按 `LivaChatConfig.allowSelfSigned` 构造。
 */
@Singleton
class TlsOkHttpClientFactory @Inject constructor() {

    private val trustAllManager = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
        override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
    }

    private val selfSignedClient: OkHttpClient = run {
        val sslContext = SSLContext.getInstance("TLS").apply {
            init(null, arrayOf<TrustManager>(trustAllManager), SecureRandom())
        }
        OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustAllManager)
            .hostnameVerifier { _, _ -> true }
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(10, TimeUnit.SECONDS)
            .build()
    }

    private val systemClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    fun create(allowSelfSigned: Boolean): OkHttpClient =
        if (allowSelfSigned) selfSignedClient else systemClient
}
