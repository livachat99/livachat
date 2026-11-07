package dev.livachat.app.ui.util

import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.Locale

class AppLanguageTest {

    @Test
    fun `zh locales resolve to canonical script codes`() {
        assertEquals("zh-Hans", appLanguageCode(Locale.forLanguageTag("zh-CN")))
        assertEquals("zh-Hans", appLanguageCode(Locale.forLanguageTag("zh-SG")))
        assertEquals("zh-Hans", appLanguageCode(Locale.forLanguageTag("zh-Hans")))
        assertEquals("zh-Hant", appLanguageCode(Locale.forLanguageTag("zh-TW")))
        assertEquals("zh-Hant", appLanguageCode(Locale.forLanguageTag("zh-HK")))
        assertEquals("zh-Hant", appLanguageCode(Locale.forLanguageTag("zh-MO")))
        assertEquals("zh-Hant", appLanguageCode(Locale.forLanguageTag("zh-Hant")))
    }

    @Test
    fun `legacy Indonesian maps to id`() {
        assertEquals("id", appLanguageCode(Locale.forLanguageTag("in-ID")))
        assertEquals("id", appLanguageCode(Locale.forLanguageTag("id-ID")))
    }

    @Test
    fun `region variants use the base app language`() {
        assertEquals("pt", appLanguageCode(Locale.forLanguageTag("pt-BR")))
        assertEquals("pt", appLanguageCode(Locale.forLanguageTag("pt-PT")))
        assertEquals("en", appLanguageCode(Locale.forLanguageTag("en-US")))
        assertEquals("th", appLanguageCode(Locale.forLanguageTag("th-TH")))
        assertEquals("vi", appLanguageCode(Locale.forLanguageTag("vi-VN")))
        assertEquals("ar", appLanguageCode(Locale.forLanguageTag("ar-EG")))
    }

    @Test
    fun `unsupported or empty locales fall back to English`() {
        assertEquals("en", appLanguageCode(Locale.forLanguageTag("fil-PH")))
        assertEquals("en", appLanguageCode(Locale.forLanguageTag("tr-TR")))
        assertEquals("en", appLanguageCode(Locale.ROOT))
    }

    @Test
    fun `supported set contains all 16 app languages`() {
        assertEquals(
            setOf(
                "en", "zh-Hans", "zh-Hant", "ja", "ko", "ru", "de", "fr",
                "it", "es", "pt", "hi", "id", "th", "vi", "ar",
            ),
            appLanguageCodes,
        )
    }
}
