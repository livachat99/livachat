package dev.livachat.app.ui.util

import java.util.Locale
import org.junit.Assert.assertEquals
import org.junit.Test

/** 数字本地化助手（docs/android_multilingual.md §20.2 D4/§20.1 缺口⑤）：ar → 阿数、其余恒等、幂等。 */
class LocaleDigitsTest {

    private val ar = Locale("ar")

    @Test
    fun `ar maps ASCII digits to Arabic-Indic`() {
        assertEquals("١٧٧٦", localizeDigits("1776", ar))
        assertEquals("٠٩:١٢", localizeDigits("09:12", ar))
        assertEquals("١٧/٠٨ – ٢٣/٠٨", localizeDigits("17/08 – 23/08", ar))
        assertEquals("LivaChat ٢٠٢٦", localizeDigits("LivaChat 2026", ar))
    }

    @Test
    fun `ar region variants hit the same mapping`() {
        assertEquals("٢٠٢٦", localizeDigits("2026", Locale("ar", "SA")))
    }

    @Test
    fun `already Arabic-Indic digits unchanged (idempotent)`() {
        val s = "٢١ أغسطس ٢٠٢٦"
        assertEquals(s, localizeDigits(s, ar))
    }

    @Test
    fun `mixed text maps digits only`() {
        assertEquals("٢١ أغسطس · الجمعة", localizeDigits("21 أغسطس · الجمعة", ar))
    }

    @Test
    fun `non-ar locales are identity`() {
        val s = "1776 09:12 – 2026"
        for (loc in listOf(Locale.US, Locale.SIMPLIFIED_CHINESE, Locale.JAPAN, Locale("ru"), Locale("hi"), Locale("id"))) {
            assertEquals(s, localizeDigits(s, loc))
        }
    }
}
