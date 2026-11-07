package dev.livachat.app.domain.model

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerOptionTest {

    @Test
    fun parseServerAddress_accepts_ip_and_domain_with_port() {
        assertEquals("192.0.2.1:8080", parseServerAddress("192.0.2.1:8080"))
        assertEquals("example.com:443", parseServerAddress("example.com:443"))
        assertEquals("a-b.example.com:1", parseServerAddress("a-b.example.com:1"))
        assertEquals("host:65535", parseServerAddress("host:65535"))
    }

    @Test
    fun parseServerAddress_rejects_invalid_forms() {
        assertNull(parseServerAddress(""))                     // 空
        assertNull(parseServerAddress("host"))                 // 无端口
        assertNull(parseServerAddress("host:"))                // 空端口
        assertNull(parseServerAddress(":8080"))                // 缺 host
        assertNull(parseServerAddress("wss://host:8080"))      // 带 scheme
        assertNull(parseServerAddress("host:8080/v1/ws"))      // 带路径
        assertNull(parseServerAddress(" host:8080"))           // 空白
        assertNull(parseServerAddress("host :8080"))
        assertNull(parseServerAddress("host:0"))               // 端口越界
        assertNull(parseServerAddress("host:65536"))
        assertNull(parseServerAddress("host:80a"))             // 端口非数字
        assertNull(parseServerAddress("host:8080:1"))          // 双冒号
        assertNull(parseServerAddress("host_name:80"))         // host 非字符集
        assertNull(parseServerAddress("中文.com:80"))
    }

    @Test
    fun ws_url_and_http_base_derive_from_address() {
        assertEquals("wss://example.com:8080/v1/ws/stream", wsUrlFromAddress("example.com:8080"))
        assertEquals("https://example.com:8080", httpBase("example.com:8080"))
    }

    @Test
    fun auth_token_charset_is_letters_digits_dash_underscore_dot() {
        assertTrue(isValidAuthToken("dev-livachat-token"))
        assertTrue(isValidAuthToken("Abc123._-"))
        assertFalse(isValidAuthToken(""))      // 空串非法（Q3 默认空）
        assertFalse(isValidAuthToken("a b"))
        assertFalse(isValidAuthToken("a&b"))   // SDK query 拼接危险字符
        assertFalse(isValidAuthToken("a?b"))
        assertFalse(isValidAuthToken("a#b"))
        assertFalse(isValidAuthToken("a%b"))
        assertFalse(isValidAuthToken("a=b"))
        assertFalse(isValidAuthToken("令牌"))
    }

    @Test
    fun wire_token_is_fixed_salt_sha1() {
        // 固定向量（docs/server_auth.md §3.1）：与 Python wire_digest("dev-livachat-token") 逐字节一致
        assertEquals("1161f736057f8e087b6351a66254f32510d0121c", wireToken("dev-livachat-token"))
        // 摘要恒为 URL-safe 小写 hex（SDK 裸拼接安全）
        val digest = wireToken("x")
        assertEquals(40, digest.length)
        assertTrue(isValidAuthToken(digest))
    }

    @Test
    fun server_config_completeness_requires_both_address_and_token() {
        assertTrue(isServerConfigComplete("example.com:8080", "dev-livachat-token"))
        assertFalse(isServerConfigComplete("", "dev-livachat-token"))
        assertFalse(isServerConfigComplete("bad address", "dev-livachat-token"))
        assertFalse(isServerConfigComplete("example.com:8080", ""))
        assertFalse(isServerConfigComplete("example.com:8080", "bad token"))
    }
}
