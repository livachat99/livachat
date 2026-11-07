package dev.livachat.app.ui.legal

import org.junit.Assert.assertEquals
import org.junit.Test

class LegalLinksTest {

    @Test
    fun `legal urls match iOS LegalLinks`() {
        assertEquals("https://livachat99.github.io/privacy/", LegalLinks.PRIVACY)
        assertEquals("https://livachat99.github.io/terms/", LegalLinks.EULA)
        assertEquals("https://livachat99.github.io/support/", LegalLinks.SUPPORT)
    }
}
