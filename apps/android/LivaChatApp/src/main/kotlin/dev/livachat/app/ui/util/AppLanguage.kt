package dev.livachat.app.ui.util

import java.util.Locale

/** App UI languages, using the same canonical codes as iOS AppLocale.supportedCodes. */
internal val appLanguageCodes = setOf(
    "en", "zh-Hans", "zh-Hant", "ja", "ko", "ru", "de", "fr",
    "it", "es", "pt", "hi", "id", "th", "vi", "ar",
)

/**
 * Resolve the current App resource locale to the canonical code reported in
 * session.start.config.language. Unsupported locales fall back to English,
 * matching the values/ resource fallback used by Android.
 */
internal fun appLanguageCode(locale: Locale): String {
    val language = locale.language.lowercase(Locale.ROOT)

    if (language == "zh") {
        val script = locale.script.lowercase(Locale.ROOT)
        val country = locale.country.uppercase(Locale.ROOT)
        return when {
            script == "hant" -> "zh-Hant"
            script == "hans" -> "zh-Hans"
            country in setOf("TW", "HK", "MO") -> "zh-Hant"
            else -> "zh-Hans"
        }
    }

    val code = if (language == "in") "id" else language
    return if (code in appLanguageCodes) code else "en"
}
