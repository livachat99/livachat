package dev.livachat.app.ui.components

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class MarkdownUrlTest {

    @Test
    fun `http scheme passes`() {
        assertTrue("http://example.com/a?b=1#f".isHttpUrl())
    }

    @Test
    fun `https scheme passes`() {
        assertTrue("https://example.com".isHttpUrl())
    }

    @Test
    fun `javascript scheme rejected`() {
        assertFalse("javascript:alert(1)".isHttpUrl())
    }

    @Test
    fun `file scheme rejected`() {
        assertFalse("file:///etc/passwd".isHttpUrl())
    }

    @Test
    fun `data scheme rejected`() {
        assertFalse("data:text/html,<script>alert(1)</script>".isHttpUrl())
    }

    @Test
    fun `intent scheme rejected`() {
        assertFalse("intent://example.com/#Intent;end".isHttpUrl())
    }

    @Test
    fun `uppercase scheme passes`() {
        assertTrue("HTTPS://example.com".isHttpUrl())
    }

    @Test
    fun `url with whitespace rejected`() {
        assertFalse("http://exa mple.com".isHttpUrl())
    }

    @Test
    fun `url without scheme rejected`() {
        assertFalse("example.com".isHttpUrl())
    }

    @Test
    fun `empty scheme rejected`() {
        assertFalse("://x".isHttpUrl())
    }
}
