package dev.livachat.app.ui.screens.webview

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class WebViewUrlPolicyTest {

    @Test
    fun `http navigation is allowed`() {
        assertFalse(shouldBlockNavigation("http://example.com/a"))
    }

    @Test
    fun `https navigation is allowed`() {
        assertFalse(shouldBlockNavigation("https://example.com/a?b=1"))
    }

    @Test
    fun `javascript navigation is blocked`() {
        assertTrue(shouldBlockNavigation("javascript:alert(1)"))
    }

    @Test
    fun `file navigation is blocked`() {
        assertTrue(shouldBlockNavigation("file:///etc/passwd"))
    }

    @Test
    fun `data navigation is blocked`() {
        assertTrue(shouldBlockNavigation("data:text/html,<script>alert(1)</script>"))
    }

    @Test
    fun `intent navigation is blocked`() {
        assertTrue(shouldBlockNavigation("intent://example.com/#Intent;end"))
    }

    @Test
    fun `language query is added to meeting url`() {
        assertEquals(
            "https://example.com/meeting/123?lang=zh-Hans",
            withLanguageQuery("https://example.com/meeting/123", "zh-Hans"),
        )
    }

    @Test
    fun `language query replaces an existing lang value`() {
        assertEquals(
            "https://example.com/meeting/123?foo=1&lang=zh-Hant",
            withLanguageQuery(
                "https://example.com/meeting/123?foo=1&lang=en-US",
                "zh-Hant",
            ),
        )
    }

    @Test
    fun `language query preserves path and fragment`() {
        assertEquals(
            "https://example.com/meeting/123?lang=ja#room",
            withLanguageQuery("https://example.com/meeting/123#room", "ja"),
        )
    }

    @Test
    fun `language query is only added to trusted meeting pages`() {
        assertEquals(
            "https://example.com/meeting/123?lang=zh-Hans",
            localizedWebUrl(
                "https://example.com/meeting/123",
                "zh-Hans",
                "https://example.com",
                allowDebugLoopback = false,
            ),
        )
        assertEquals(
            "https://example.com/docs/123",
            localizedWebUrl(
                "https://example.com/docs/123",
                "zh-Hans",
                "https://example.com",
                allowDebugLoopback = false,
            ),
        )
        assertEquals(
            "https://external.test/meeting/123",
            localizedWebUrl(
                "https://external.test/meeting/123",
                "zh-Hans",
                "https://example.com",
                allowDebugLoopback = false,
            ),
        )
    }
}
