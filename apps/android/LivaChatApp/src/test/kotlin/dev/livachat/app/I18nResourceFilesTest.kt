package dev.livachat.app

import dev.livachat.app.ui.util.appLanguageCode
import dev.livachat.app.ui.util.appLanguageCodes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.Locale

/**
 * 资源守卫（docs/android_multilingual.md §3.1/§3.9/§8/§9/§10/§11/§12/§13/§20）：
 * 1) 默认（en）、简体（b+zh+Hans）、繁体（b+zh+Hant）、日语（ja）、韩语（ko）、俄语（ru）、德语（de）、
 *    法语（fr）、意大利语（it）、西班牙语（es）、葡萄牙语（pt）、土耳其语（tr）、印地语（hi）、印尼语（id，目录 values-in）、
 *    泰语（th）、越南语（vi）、阿拉伯语（ar）十七目录 key 集合逐一对应（Missing/ExtraTranslation 的替代校验）；
 * 2) 默认目录不得出现汉字（XML 注释除外）——防中文漏搬进 en；
 * 3) plurals 按语言必含 CLDR 数量形（§11.4 Q4：把 MissingQuantity/ImpliedQuantity 语义固化进 JVM；ar 六形 §20）；
 * 4) 法语排版：? ! 前必含 U+00A0；「:」仅文案句（格式串含 port 豁免）（§13 Q4）；
 * 5) 阿语字面数字须为阿拉伯-印度数字（§20 缺口⑤——资源字面不随 %d 本地化，占位符剥离后不得剩 ASCII 数字）。
 */
class I18nResourceFilesTest {

    private val resDir: File = findResDir()
    private val defaultStrings = File(resDir, "values/strings.xml")
    private val zhHansStrings = File(resDir, "values-b+zh+Hans/strings.xml")
    private val zhHantStrings = File(resDir, "values-b+zh+Hant/strings.xml")
    private val jaStrings = File(resDir, "values-ja/strings.xml")
    private val koStrings = File(resDir, "values-ko/strings.xml")
    private val ruStrings = File(resDir, "values-ru/strings.xml")
    private val deStrings = File(resDir, "values-de/strings.xml")
    private val frStrings = File(resDir, "values-fr/strings.xml")
    private val itStrings = File(resDir, "values-it/strings.xml")
    private val esStrings = File(resDir, "values-es/strings.xml")
    private val ptStrings = File(resDir, "values-pt/strings.xml")
    private val hiStrings = File(resDir, "values-hi/strings.xml")
    private val inStrings = File(resDir, "values-in/strings.xml")
    private val thStrings = File(resDir, "values-th/strings.xml")
    private val viStrings = File(resDir, "values-vi/strings.xml")
    private val arStrings = File(resDir, "values-ar/strings.xml")

    private fun findResDir(): File {
        var dir: File? = File(System.getProperty("user.dir")!!)
        while (dir != null) {
            val candidate = File(dir, "src/main/res")
            if (candidate.isDirectory) return candidate
            dir = dir.parentFile
        }
        error("src/main/res not found from ${System.getProperty("user.dir")}")
    }

    private fun keys(file: File): Set<String> =
        Regex("<(?:string|plurals|string-array)\\s[^>]*name=\"([^\"]+)\"")
            .findAll(file.readText())
            .map { it.groupValues[1] }
            .toSet()

    private fun pluralQuantities(file: File): Map<String, Set<String>> =
        Regex("<plurals\\s[^>]*name=\"([^\"]+)\"[^>]*>([\\s\\S]*?)</plurals>")
            .findAll(file.readText())
            .associate { m ->
                m.groupValues[1] to
                    Regex("quantity=\"(\\w+)\"").findAll(m.groupValues[2]).map { it.groupValues[1] }.toSet()
            }

    @Test
    fun allLocaleKeySetsMatch() {
        val defaultKeys = keys(defaultStrings)
        val variants = mapOf(
            "Hans" to keys(zhHansStrings),
            "Hant" to keys(zhHantStrings),
            "ja" to keys(jaStrings),
            "ko" to keys(koStrings),
            "ru" to keys(ruStrings),
            "de" to keys(deStrings),
            "fr" to keys(frStrings),
            "it" to keys(itStrings),
            "es" to keys(esStrings),
            "pt" to keys(ptStrings),
            "hi" to keys(hiStrings),
            "id" to keys(inStrings),
            "th" to keys(thStrings),
            "vi" to keys(viStrings),
            "ar" to keys(arStrings),
        )
        for ((name, variantKeys) in variants) {
            assertTrue("values/ 有而 $name 缺: ${defaultKeys - variantKeys}", (defaultKeys - variantKeys).isEmpty())
            assertTrue("$name 有而 values/ 缺（ExtraTranslation 会报）: ${variantKeys - defaultKeys}", (variantKeys - defaultKeys).isEmpty())
        }
    }

    @Test
    fun pluralQuantitiesMatchCldrPerLocale() {
        val expected = mapOf(
            "en" to setOf("one", "other"),
            "Hans" to setOf("other"),
            "Hant" to setOf("other"),
            "ja" to setOf("other"),
            "ko" to setOf("other"),
            "ru" to setOf("one", "few", "many", "other"),
            "de" to setOf("one", "other"),
            "fr" to setOf("one", "other"),
            "it" to setOf("one", "other"),
            "es" to setOf("one", "other"),
            "pt" to setOf("one", "other"),
            "hi" to setOf("one", "other"),
            "id" to setOf("other"),
            "th" to setOf("other"),
            "vi" to setOf("other"),
            "ar" to setOf("zero", "one", "two", "few", "many", "other"),
        )
        val files = mapOf(
            "en" to defaultStrings,
            "Hans" to zhHansStrings,
            "Hant" to zhHantStrings,
            "ja" to jaStrings,
            "ko" to koStrings,
            "ru" to ruStrings,
            "de" to deStrings,
            "fr" to frStrings,
            "it" to itStrings,
            "es" to esStrings,
            "pt" to ptStrings,
            "hi" to hiStrings,
            "id" to inStrings,
            "th" to thStrings,
            "vi" to viStrings,
            "ar" to arStrings,
        )
        for ((name, file) in files) {
            val quantities = pluralQuantities(file)
            assertEquals("$name plurals 组数", 11, quantities.size)
            for ((key, qs) in quantities) {
                assertEquals("$name/$key quantity 集", expected.getValue(name), qs)
            }
        }
    }

    @Test
    fun defaultStringsContainNoHan() {
        val text = defaultStrings.readText().replace(Regex("<!--[\\s\\S]*?-->"), "")
        val han = Regex("[\\u4e00-\\u9fff]").findAll(text).map { it.value }.toSet()
        assertTrue("values/strings.xml 出现汉字（应只在 values-b+zh+Hans/）: $han", han.isEmpty())
    }

    private fun bodies(file: File): List<String> =
        Regex("<(?:string|item)\\s[^>]*>([\\s\\S]*?)</(?:string|item)>")
            .findAll(file.readText())
            .map { it.groupValues[1] }
            .toList()

    @Test
    fun frenchTypographyUsesNbsp() {
        val violations = mutableListOf<String>()
        for (raw in bodies(frStrings)) {
            // 兼容 \u00A0 转义（aapt 原生支持）与字面 NBSP 两种写法
            val body = raw.replace("\\u00A0", "\u00A0")
            for (mark in listOf('?', '!')) {
                var i = body.indexOf(mark)
                while (i >= 0) {
                    if (i == 0 || body[i - 1] != '\u00A0') violations += "'$mark' 前缺 U+00A0: $body"
                    i = body.indexOf(mark, i + 1)
                }
            }
            // 「:」仅文案句校验；技术格式串（ip:port/domain:port）豁免（§13 Q4）
            if (!body.contains("port")) {
                var i = body.indexOf(':')
                while (i >= 0) {
                    if (i == 0 || body[i - 1] != '\u00A0') violations += "':' 前缺 U+00A0: $body"
                    i = body.indexOf(':', i + 1)
                }
            }
        }
        assertTrue("法语排版违例（§13 Q4 双标点前 U+00A0）: $violations", violations.isEmpty())
    }

    @Test
    fun localeConfigMapsToCanonicalAppLanguageCodes() {
        val localeConfig = File(resDir, "xml/locales_config.xml")
        val tags = Regex("android:name=\"([^\"]+)\"")
            .findAll(localeConfig.readText())
            .map { it.groupValues[1] }
            .toSet()

        val canonical = tags.map { appLanguageCode(Locale.forLanguageTag(it)) }.toSet()

        assertEquals("locales_config 条目应全部映射到 appLanguageCodes", appLanguageCodes, canonical)
    }

    @Test
    fun arabicLiteralDigitsUseArabicIndic() {
        val violations = mutableListOf<String>()
        for (raw in bodies(arStrings)) {
            // 占位符（%1$d / %1$s 等）本身的数字合法；剥离后余下的字面数字须为阿数（资源字面不随 %d 本地化）
            val stripped = Regex("%\\d+\\$[sd]|%[sd]").replace(raw, "")
            if (Regex("[0-9]").containsMatchIn(stripped)) violations += stripped
        }
        assertTrue("ar 字面数字须写阿拉伯-印度数字（§20 缺口⑤）: $violations", violations.isEmpty())
    }
}
