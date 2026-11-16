package dev.livachat.app.ui.screens.webview

import android.net.Uri

private const val LANGUAGE_QUERY = "lang"

/**
 * Pass the native app language to the web page explicitly. Android WebView may
 * expose an English navigator.language even when the system/app locale is
 * Chinese; the web app gives this query parameter priority over browser
 * language for in-app pages.
 */
internal fun localizedWebUrl(
    url: String,
    language: String,
    serverBaseUrl: String,
    allowDebugLoopback: Boolean,
): String {
    // Only inject the internal language hint into trusted meeting pages. Adding
    // query parameters to arbitrary external URLs can break signed links.
    return if (isTrustedMeetingPage(url, serverBaseUrl, allowDebugLoopback)) {
        withLanguageQuery(url, language)
    } else {
        url
    }
}

internal fun withLanguageQuery(url: String, language: String): String {
    val uri = Uri.parse(url)
    // Uri.Builder has no removeQueryParameter API on all supported levels.
    // Keep the original encoded query, minus any previous lang entry.
    val queryParts = uri.encodedQuery
        ?.split('&')
        ?.filterNot { part ->
            val rawKey = part.substringBefore('=')
            runCatching { Uri.decode(rawKey) }.getOrDefault(rawKey) == LANGUAGE_QUERY
        }
        .orEmpty()
    val builder = uri.buildUpon().clearQuery()
    if (queryParts.isNotEmpty()) {
        builder.encodedQuery(queryParts.joinToString("&"))
    }
    if (language.isNotBlank()) {
        builder.appendQueryParameter(LANGUAGE_QUERY, language)
    }
    return builder.build().toString()
}
