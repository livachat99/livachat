package dev.livachat.app.ui.navigation

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TlsSettingsTest {

    @Test
    fun sameHostAndPort_matchesRegardlessOfPath() {
        assertTrue(
            isSameOrigin(
                "https://demo.example.com:8080/webroot/abc",
                "https://demo.example.com:8080",
            ),
        )
    }

    @Test
    fun hostComparison_isCaseInsensitive() {
        assertTrue(isSameOrigin("https://DEMO.example.com:8080/a", "https://demo.example.com:8080"))
    }

    @Test
    fun defaultPort_isNormalized() {
        assertTrue(isSameOrigin("https://demo.example.com/path", "https://demo.example.com:443"))
        assertTrue(isSameOrigin("http://demo.example.com/path", "http://demo.example.com:80"))
    }

    @Test
    fun differentPortOrHost_doesNotMatch() {
        assertFalse(isSameOrigin("https://demo.example.com:8443/a", "https://demo.example.com:8080"))
        assertFalse(isSameOrigin("https://evil.example/a", "https://demo.example.com:8080"))
        assertFalse(isSameOrigin("https://demo.example.com/a", "http://demo.example.com"))
    }

    @Test
    fun malformedOrEmpty_doesNotMatch() {
        assertFalse(isSameOrigin("not a url", "https://demo.example.com:8080"))
        assertFalse(isSameOrigin("https://demo.example.com:8080/a", ""))
    }
}
