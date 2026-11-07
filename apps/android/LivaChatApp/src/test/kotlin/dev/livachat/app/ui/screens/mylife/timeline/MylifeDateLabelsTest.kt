package dev.livachat.app.ui.screens.mylife.timeline

import java.time.DayOfWeek
import java.util.Locale
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * docs/android_multilingual.md §9（日语轮）：日期标签 locale 纯函数——
 * ja 走 CJK 年月日模板（同形 2026年8月21日）+ 星期 FULL（「月曜日」避 SHORT 单字「月」歧义）；
 * zh / en 行为不变（缺口①②的回归锁）。
 */
class MylifeDateLabelsTest {

    private val ja = Locale.JAPAN
    private val zh = Locale.SIMPLIFIED_CHINESE
    private val en = Locale.US

    @Test
    fun `ja uses CJK year-month-day template`() {
        assertEquals("2026年", yearText(2026, ja))
        assertEquals("2026年8月", monthYearText(2026, 8, ja))
        assertEquals("8月21日", monthDayText(8, 21, ja))
    }

    @Test
    fun `ja weekday is FULL form to avoid single-char month ambiguity`() {
        assertEquals("月曜日", weekdayText(DayOfWeek.MONDAY, ja))
        assertEquals("日曜日", weekdayText(DayOfWeek.SUNDAY, ja))
    }

    @Test
    fun `ko uses Korean year-month-day template`() {
        val ko = Locale.KOREA
        assertEquals("2026년", yearText(2026, ko))
        assertEquals("2026년 8월", monthYearText(2026, 8, ko))
        assertEquals("8월 21일", monthDayText(8, 21, ko))
    }

    @Test
    fun `ko weekday is FULL form to avoid single-char month ambiguity`() {
        val ko = Locale.KOREA
        assertEquals("월요일", weekdayText(DayOfWeek.MONDAY, ko))
        assertEquals("금요일", weekdayText(DayOfWeek.FRIDAY, ko))
    }

    @Test
    fun `ru uses day-first date with SHORT month (§11 缺口①)`() {
        val ru = Locale("ru")
        assertEquals("21 авг.", monthDayText(8, 21, ru))
        // 五月 SHORT 即属格「мая」（CLDR format 语境），日先月后位置正确
        assertEquals("21 мая", monthDayText(5, 21, ru))
    }

    @Test
    fun `ru month-year uses standalone nominative month (§11 缺口②)`() {
        val ru = Locale("ru")
        assertEquals("2026", yearText(2026, ru))
        assertEquals("август 2026", monthYearText(2026, 8, ru))
        assertEquals("май 2026", monthYearText(2026, 5, ru))
    }

    @Test
    fun `ru weekday stays SHORT (no single-char ambiguity)`() {
        val ru = Locale("ru")
        assertEquals("пн", weekdayText(DayOfWeek.MONDAY, ru))
        assertEquals("пт", weekdayText(DayOfWeek.FRIDAY, ru))
    }

    @Test
    fun `de uses day-first date with trailing period (§12 缺口①)`() {
        val de = Locale.GERMAN
        assertEquals("21. Aug.", monthDayText(8, 21, de))
        // 不缩写月无点：Mai/Juni（CLDR SHORT 即全称）
        assertEquals("21. Mai", monthDayText(5, 21, de))
    }

    @Test
    fun `de month-year uses full month name (§12 缺口②)`() {
        val de = Locale.GERMAN
        assertEquals("2026", yearText(2026, de))
        assertEquals("August 2026", monthYearText(2026, 8, de))
        assertEquals("Mai 2026", monthYearText(2026, 5, de))
    }

    @Test
    fun `de weekday stays SHORT with period (no ambiguity)`() {
        val de = Locale.GERMAN
        assertEquals("Mo.", weekdayText(DayOfWeek.MONDAY, de))
        assertEquals("Fr.", weekdayText(DayOfWeek.FRIDAY, de))
    }

    @Test
    fun `fr uses day-first date (§13 缺口①)`() {
        val fr = Locale.FRENCH
        assertEquals("21 août", monthDayText(8, 21, fr))
        assertEquals("21 janv.", monthDayText(1, 21, fr))
    }

    @Test
    fun `fr month-year uses full lowercase month (§13 缺口②)`() {
        val fr = Locale.FRENCH
        assertEquals("2026", yearText(2026, fr))
        assertEquals("janvier 2026", monthYearText(2026, 1, fr))
        assertEquals("août 2026", monthYearText(2026, 8, fr))
    }

    @Test
    fun `fr weekday stays SHORT (§13)`() {
        val fr = Locale.FRENCH
        assertEquals("lun.", weekdayText(DayOfWeek.MONDAY, fr))
        assertEquals("ven.", weekdayText(DayOfWeek.FRIDAY, fr))
    }

    @Test
    fun `it uses day-first date (§14 缺口①)`() {
        val it = Locale.ITALIAN
        assertEquals("21 ago", monthDayText(8, 21, it))
        assertEquals("17 mar", monthDayText(3, 17, it))
    }

    @Test
    fun `it month-year uses full lowercase month (§14 缺口②)`() {
        val it = Locale.ITALIAN
        assertEquals("2026", yearText(2026, it))
        assertEquals("agosto 2026", monthYearText(2026, 8, it))
        assertEquals("gennaio 2026", monthYearText(2026, 1, it))
    }

    @Test
    fun `it weekday is FULL to avoid mar-mar collision (§14 缺口⑤)`() {
        val it = Locale.ITALIAN
        assertEquals("martedì", weekdayText(DayOfWeek.TUESDAY, it))
        assertEquals("lunedì", weekdayText(DayOfWeek.MONDAY, it))
        assertEquals("venerdì", weekdayText(DayOfWeek.FRIDAY, it))
    }

    @Test
    fun `es uses day-first date (§15 缺口①)`() {
        val es = Locale("es")
        assertEquals("21 ago", monthDayText(8, 21, es))
        assertEquals("17 mar", monthDayText(3, 17, es))
    }

    @Test
    fun `es month-year includes de (§15 缺口②)`() {
        val es = Locale("es")
        assertEquals("2026", yearText(2026, es))
        assertEquals("agosto de 2026", monthYearText(2026, 8, es))
        assertEquals("enero de 2026", monthYearText(2026, 1, es))
    }

    @Test
    fun `es weekday is FULL to avoid mar-mar collision (§15 缺口⑤)`() {
        val es = Locale("es")
        assertEquals("martes", weekdayText(DayOfWeek.TUESDAY, es))
        assertEquals("lunes", weekdayText(DayOfWeek.MONDAY, es))
        assertEquals("viernes", weekdayText(DayOfWeek.FRIDAY, es))
    }

    @Test
    fun `pt uses day-first date (§16 缺口①)`() {
        val pt = Locale("pt", "BR")
        assertEquals("21 ago.", monthDayText(8, 21, pt))
        assertEquals("17 mar.", monthDayText(3, 17, pt))
    }

    @Test
    fun `pt month-year includes de (§16 缺口②)`() {
        val pt = Locale("pt", "BR")
        assertEquals("2026", yearText(2026, pt))
        assertEquals("agosto de 2026", monthYearText(2026, 8, pt))
        assertEquals("janeiro de 2026", monthYearText(2026, 1, pt))
    }

    @Test
    fun `pt weekday stays SHORT (§16 碰撞空集回归锁)`() {
        val pt = Locale("pt", "BR")
        assertEquals("ter.", weekdayText(DayOfWeek.TUESDAY, pt))
        assertEquals("seg.", weekdayText(DayOfWeek.MONDAY, pt))
        assertEquals("sex.", weekdayText(DayOfWeek.FRIDAY, pt))
    }

    @Test
    fun `tr uses day-first date (§17 缺口①)`() {
        val tr = Locale("tr")
        assertEquals("21 Ağu", monthDayText(8, 21, tr))
        assertEquals("17 Mar", monthDayText(3, 17, tr))
    }

    @Test
    fun `tr month-year uses capitalized full month (§17 缺口②)`() {
        val tr = Locale("tr")
        assertEquals("2026", yearText(2026, tr))
        assertEquals("Ağustos 2026", monthYearText(2026, 8, tr))
        assertEquals("Ocak 2026", monthYearText(2026, 1, tr))
    }

    @Test
    fun `tr weekday stays SHORT (§17 碰撞空集回归锁)`() {
        val tr = Locale("tr")
        assertEquals("Sal", weekdayText(DayOfWeek.TUESDAY, tr))
        assertEquals("Pzt", weekdayText(DayOfWeek.MONDAY, tr))
        assertEquals("Cum", weekdayText(DayOfWeek.FRIDAY, tr))
    }

    @Test
    fun `hi uses day-first date (§18 缺口①)`() {
        val hi = Locale("hi")
        assertEquals("21 अग॰", monthDayText(8, 21, hi))
        assertEquals("17 मार्च", monthDayText(3, 17, hi))
    }

    @Test
    fun `hi month-year uses full month (§18 缺口②)`() {
        val hi = Locale("hi")
        assertEquals("2026", yearText(2026, hi))
        assertEquals("अगस्त 2026", monthYearText(2026, 8, hi))
        assertEquals("जनवरी 2026", monthYearText(2026, 1, hi))
    }

    @Test
    fun `hi weekday stays SHORT (§18 碰撞空集回归锁)`() {
        val hi = Locale("hi")
        assertEquals("मंगल", weekdayText(DayOfWeek.TUESDAY, hi))
        assertEquals("सोम", weekdayText(DayOfWeek.MONDAY, hi))
        assertEquals("शुक्र", weekdayText(DayOfWeek.FRIDAY, hi))
    }

    @Test
    fun `id uses day-first date (§19 缺口①)`() {
        val id = Locale("id")
        assertEquals("21 Agu", monthDayText(8, 21, id))
        assertEquals("17 Mar", monthDayText(3, 17, id))
    }

    @Test
    fun `id month-year uses capitalized full month (§19 缺口②)`() {
        val id = Locale("id")
        assertEquals("2026", yearText(2026, id))
        assertEquals("Agustus 2026", monthYearText(2026, 8, id))
        assertEquals("Januari 2026", monthYearText(2026, 1, id))
    }

    @Test
    fun `id weekday stays SHORT (§19 碰撞空集回归锁)`() {
        val id = Locale("id")
        assertEquals("Sel", weekdayText(DayOfWeek.TUESDAY, id))
        assertEquals("Sen", weekdayText(DayOfWeek.MONDAY, id))
        assertEquals("Jum", weekdayText(DayOfWeek.FRIDAY, id))
    }

    @Test
    fun `legacy code dual-key in equals id (§19 归一锁)`() {
        // JVM/Android 将旧码 in 归一为 id（实测），双键防御新旧运行时——两构造器断言同值，锁归一行为
        val modern = Locale("id")
        val legacy = Locale("in")
        assertEquals(monthDayText(8, 21, modern), monthDayText(8, 21, legacy))
        assertEquals(monthYearText(2026, 8, modern), monthYearText(2026, 8, legacy))
        assertEquals(weekdayText(DayOfWeek.FRIDAY, modern), weekdayText(DayOfWeek.FRIDAY, legacy))
        assertEquals("21 Agu", monthDayText(8, 21, legacy))
    }

    @Test
    fun `ar uses day-first date with Arabic-Indic digits (§20 缺口①⑤)`() {
        val ar = Locale("ar")
        assertEquals("٢١ أغسطس", monthDayText(8, 21, ar))
        assertEquals("١٧ مارس", monthDayText(3, 17, ar))
    }

    @Test
    fun `ar month-year flows SHORT-equals-FULL with localized digits (§20 缺口②⑤)`() {
        val ar = Locale("ar")
        assertEquals("٢٠٢٦", yearText(2026, ar))
        assertEquals("أغسطس ٢٠٢٦", monthYearText(2026, 8, ar))
        assertEquals("يناير ٢٠٢٦", monthYearText(2026, 1, ar))
    }

    @Test
    fun `ar weekday stays SHORT (collision empty set, §20)`() {
        val ar = Locale("ar")
        assertEquals("الجمعة", weekdayText(DayOfWeek.FRIDAY, ar))
        assertEquals("الاثنين", weekdayText(DayOfWeek.MONDAY, ar))
    }

    @Test
    fun `th uses Gregorian full month-year and SHORT weekday (§21)`() {
        val th = Locale("th")
        assertEquals("2026", yearText(2026, th))
        assertEquals("สิงหาคม 2026", monthYearText(2026, 8, th))
        assertEquals("21 ส.ค.", monthDayText(8, 21, th))
        assertEquals("จ.", weekdayText(DayOfWeek.MONDAY, th))
        assertEquals("ศ.", weekdayText(DayOfWeek.FRIDAY, th))
    }

    @Test
    fun `vi uses standalone full month-year and FULL weekday (§21)`() {
        val vi = Locale("vi")
        assertEquals("2026", yearText(2026, vi))
        assertEquals("Tháng 8 năm 2026", monthYearText(2026, 8, vi))
        assertEquals("21 thg 8", monthDayText(8, 21, vi))
        assertEquals("Thứ Hai", weekdayText(DayOfWeek.MONDAY, vi))
        assertEquals("Thứ Sáu", weekdayText(DayOfWeek.FRIDAY, vi))
    }

    @Test
    fun `zh-TW (Hant) behavior unchanged (regression lock)`() {
        val zhTw = Locale.TAIWAN
        assertEquals("2026年", yearText(2026, zhTw))
        assertEquals("2026年8月", monthYearText(2026, 8, zhTw))
        assertEquals("8月21日", monthDayText(8, 21, zhTw))
        assertEquals("週一", weekdayText(DayOfWeek.MONDAY, zhTw))
    }

    @Test
    fun `zh and en behavior unchanged (regression lock)`() {
        assertEquals("2026年", yearText(2026, zh))
        assertEquals("2026年8月", monthYearText(2026, 8, zh))
        assertEquals("8月21日", monthDayText(8, 21, zh))
        assertEquals("周一", weekdayText(DayOfWeek.MONDAY, zh))
        assertEquals("2026", yearText(2026, en))
        assertEquals("Aug 2026", monthYearText(2026, 8, en))
        assertEquals("Aug 21", monthDayText(8, 21, en))
        assertEquals("Mon", weekdayText(DayOfWeek.MONDAY, en))
    }
}
