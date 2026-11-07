package dev.livachat.app.ui.screens.mylife.timeline

import dev.livachat.app.ui.util.localizeDigits
import java.time.DayOfWeek
import java.time.Month
import java.time.format.TextStyle
import java.util.Locale

/**
 * MyLife 日期标签（docs/android_multilingual.md §2.3.1/§3.5/§9/§10/§11/§12/§13/§14/§15/§16/§17/§18/§20）：locale 纯函数，
 * zh 输出与迁移前硬编码逐字一致（"2026年8月" / "2026年" / "8月21日 · 周一"）。
 * 模板按语言明确展开（§10.2 D4 重构）：zh/ja 共用年月日、ko 년/월/일、ru 日先月后（§11 缺口①）
 * 且月年用 standalone 主格（§11 缺口②）、de 日先月后且日带尾点（§12 缺口①）月年全称（§12 缺口②）、
 * fr 日先月后（§13 缺口①）月年全称小写（§13 缺口②）、it 同日先月后全称小写（§14 缺口①②）、
 * es 同日先月后、月年全称小写含 de（§15 缺口①②）、pt 同 es 含 de 且 SHORT 带点（§16 缺口①②）、
 * tr 同日先月后、月年全称首字母大写（§17 缺口①②）、hi 同日先月后、月年全称天城文（§18 缺口①②）、
 * 其余走 CLDR 月名。
 * ar（§20）：日先月后（缺口①）；月年 SHORT==FULL 无结构分支（缺口②实测排除），插值数字统一经
 * [localizeDigits] 转阿拉伯-印度数字（缺口⑤，函数级包裹——其余语言恒等零回归）。
 */

internal fun yearText(year: Int, locale: Locale): String = localizeDigits(
    when (locale.language) {
        "zh", "ja" -> "${year}年"
        "ko" -> "${year}년"
        else -> "$year"
    },
    locale,
)

internal fun monthYearText(year: Int, month: Int, locale: Locale): String = localizeDigits(
    when (locale.language) {
        "zh", "ja" -> "${year}年${month}月"
        "ko" -> "${year}년 ${month}월"
        // ru：standalone 主格全称（getDisplayName 默认 format 语境为属格「августа」；§11.1 实测）
        "ru" -> "${Month.of(month).getDisplayName(TextStyle.FULL_STANDALONE, locale)} $year"
        // de：全称「August 2026」（德语月名无变格，FULL 格式/独立同形；§12.1 实测）
        "de" -> "${Month.of(month).getDisplayName(TextStyle.FULL, locale)} $year"
        // fr：全称小写「janvier 2026」（法语月名无变格、小写；§13.1 实测）
        "fr" -> "${Month.of(month).getDisplayName(TextStyle.FULL, locale)} $year"
        // it：全称小写「agosto 2026」（意语月名无变格、小写；§14.1 实测）
        "it" -> "${Month.of(month).getDisplayName(TextStyle.FULL, locale)} $year"
        // es：全称小写含 de「agosto de 2026」（西语标准形；§15.1 实测）
        "es" -> "${Month.of(month).getDisplayName(TextStyle.FULL, locale)} de $year"
        // pt：全称小写含 de「agosto de 2026」（葡语标准形；§16.1 实测）
        "pt" -> "${Month.of(month).getDisplayName(TextStyle.FULL, locale)} de $year"
        // tr：全称「Ağustos 2026」（土语月名首字母大写、无介词；§17.1 实测）
        "tr" -> "${Month.of(month).getDisplayName(TextStyle.FULL, locale)} $year"
        // hi：全称「अगस्त 2026」（印语月名天城文、无格变位；§18.1 实测）
        "hi" -> "${Month.of(month).getDisplayName(TextStyle.FULL, locale)} $year"
        // id/in：全称「Agustus 2026」（印尼语月名首字母大写；§19 遗留码双键实测：id/in 均归一「id」，双键防御旧运行时）
        "id", "in" -> "${Month.of(month).getDisplayName(TextStyle.FULL, locale)} $year"
        // th：全称「สิงหาคม 2026」；公历年份（§21，不使用佛历）
        "th" -> "${Month.of(month).getDisplayName(TextStyle.FULL, locale)} $year"
        // vi：standalone 首字母大写「Tháng 8 năm 2026」（§21）
        "vi" -> "${Month.of(month).getDisplayName(TextStyle.FULL_STANDALONE, locale)} năm $year"
        // ar：走 else——SHORT==FULL（「أغسطس」全称无缩写，§20.1 实测），仅数字经助手本地化
        else -> "${Month.of(month).getDisplayName(TextStyle.SHORT, locale)} $year"
    },
    locale,
)

internal fun monthDayText(month: Int, day: Int, locale: Locale): String = localizeDigits(
    when (locale.language) {
        "zh", "ja" -> "${month}月${day}日"
        "ko" -> "${month}월 ${day}일"
        // ru：日先月后；SHORT(format 语境) 带点缩写不变格，五月「мая」本身即属格（§11.1 实测）
        "ru" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // de：日先月后且日带尾点「21. Aug.」（März/Mai/Juni/Juli 不缩写无点，§12.1 实测）
        "de" -> "$day. ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // fr：日先月后、日不带尾点「21 août/janv.」（SHORT 缩写表；§13.1 实测）
        "fr" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // it：日先月后、日不带尾点「21 ago」（SHORT 三字母无点；§14.1 实测）
        "it" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // es：日先月后、日不带尾点「21 ago」（SHORT 无点，同 d MMM 平台形；§15.1 实测）
        "es" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // pt：日先月后、日不带尾点「21 ago.」（SHORT 缩写自带点；§16.1 实测）
        "pt" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // tr：日先月后、日不带尾点「21 Ağu」（SHORT 大写无点；§17.1 实测）
        "tr" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // hi：日先月后、日不带尾点「21 अग॰」（SHORT 含天城文缩写符 ॰；§18.1 实测）
        "hi" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // id/in：日先月后、日不带尾点「21 Agu」（SHORT 大写无点；§19 双键）
        "id", "in" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // th：日先月后「21 ส.ค.」（§21）
        "th" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // vi：日先月后「21 thg 8」（§21）
        "vi" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        // ar：日先月后「٢١ أغسطس」（缺口①；数字经助手本地化）
        "ar" -> "$day ${Month.of(month).getDisplayName(TextStyle.SHORT, locale)}"
        else -> "${Month.of(month).getDisplayName(TextStyle.SHORT, locale)} $day"
    },
    locale,
)

/**
 * 星期名：zh SHORT（"周一"/"週一"）/ en SHORT（"Mon"）/
 * ja·ko FULL（「月曜日」「월요일」——SHORT 皆为单字歧义，§9.1/§10.1 实测）/
 * it·es FULL（「martedì」「martes」——SHORT「mar」与月份 SHORT「mar」（marzo）同形碰撞，§14.1/§15.1 实测）/
 * vi FULL（「Thứ Sáu」；§21）。
 */
internal fun weekdayText(day: DayOfWeek, locale: Locale): String =
    day.getDisplayName(
        if (
            locale.language == "ja" || locale.language == "ko" ||
            locale.language == "it" || locale.language == "es" || locale.language == "vi"
        ) {
            TextStyle.FULL
        } else {
            TextStyle.SHORT
        },
        locale,
    )
