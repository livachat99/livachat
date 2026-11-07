package dev.livachat.app.data.remote

import dev.livachat.app.domain.model.wireToken
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/**
 * 「检测服务」预检契约（docs/setting_server_address.md §3.3）：https MockWebServer（自签证书）
 * 验证 URL 拼装、token 编码、200/401/其它 三态映射，以及 TLS 开关对自签证书的影响。
 */
class ServiceProbeTest {

    private lateinit var server: MockWebServer
    private lateinit var probe: OkHttpServiceProbe

    @Before
    fun setUp() {
        val held = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
        val handshake = HandshakeCertificates.Builder().heldCertificate(held).build()
        server = MockWebServer()
        server.useHttps(handshake.sslSocketFactory(), false)
        server.start()
        probe = OkHttpServiceProbe(TlsOkHttpClientFactory())
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun address(): String = "127.0.0.1:${server.port}"

    @Test
    fun `200 maps to AVAILABLE and sends derived wire token and device_id`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"ok":true}"""))

        val result = probe.probe(
            address(),
            "tok-1_a.b",
            "123e4567-e89b-42d3-a456-426614174000",
            allowSelfSigned = true,
        )

        assertEquals(ProbeResult.AVAILABLE, result)
        val request = server.takeRequest()
        assertEquals("/healthz_token", request.path?.substringBefore('?'))
        // 入参是明文 secret；线上 token 必须是派生摘要（docs/server_auth.md §4.1）
        assertEquals(wireToken("tok-1_a.b"), request.requestUrl!!.queryParameter("token"))
        assertEquals("123e4567-e89b-42d3-a456-426614174000", request.requestUrl!!.queryParameter("device_id"))
    }

    @Test
    fun `401 maps to AUTH_FAILED`() = runBlocking {
        server.enqueue(
            MockResponse().setResponseCode(401).setBody("""{"detail":"authentication failed"}"""),
        )

        assertEquals(
            ProbeResult.AUTH_FAILED,
            probe.probe(address(), "wrong-token", "d", allowSelfSigned = true),
        )
    }

    @Test
    fun `503 maps to UNREACHABLE`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(503))

        assertEquals(
            ProbeResult.UNREACHABLE,
            probe.probe(address(), "t", "d", allowSelfSigned = true),
        )
    }

    @Test
    fun `connection refused maps to UNREACHABLE`() = runBlocking {
        val addr = address()
        server.shutdown()

        assertEquals(
            ProbeResult.UNREACHABLE,
            probe.probe(addr, "t", "d", allowSelfSigned = true),
        )
    }

    @Test
    fun `self-signed certificate fails when not allowed`() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"ok":true}"""))

        assertEquals(
            ProbeResult.UNREACHABLE,
            probe.probe(address(), "t", "d", allowSelfSigned = false),
        )
    }
}
