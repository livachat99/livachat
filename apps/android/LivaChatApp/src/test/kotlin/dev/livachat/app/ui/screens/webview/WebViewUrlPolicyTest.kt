package dev.livachat.app.ui.screens.webview

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
}
