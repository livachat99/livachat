package dev.livachat.app.ui.screens.conversation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LanguagePickerSheetTest {

    @Test
    fun `peer language list includes thai and vietnamese before arabic`() {
        assertEquals(
            listOf(
                "zh", "en", "ja", "ko", "fr", "it", "de", "es", "pt", "ru", "hi", "id", "th", "vi", "ar",
            ),
            languageCodes,
        )
        assertTrue(languageCodes.contains("it"))
        assertTrue(languageCodes.contains("hi"))
        assertTrue(languageCodes.contains("id"))
        assertTrue(languageCodes.contains("th"))
        assertTrue(languageCodes.contains("vi"))
        assertEquals("th", languageCodes[languageCodes.indexOf("id") + 1])
        assertEquals("vi", languageCodes[languageCodes.indexOf("th") + 1])
        assertEquals("ar", languageCodes.last())
    }
}
