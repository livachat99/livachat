package dev.livachat.app.ui.navigation

import androidx.compose.runtime.staticCompositionLocalOf
import java.net.URI

/**
 * 内部链接协议 `livachat_url://webroot/<uuidv7>[/<path>][/]`（docs/webroot_feature.md §3.4）：
 * server 对该 scheme 零感知；存储/传输全链路保持原文（raw），App 在交 Markdown 渲染前一步转换。
 */

/**
 * 渲染期转换用服务器基址（`MainActivity` 以 `@Named("serverBaseUrl")` 注入值提供；
 * 默认 `""` = 不转换——预览/单测无副作用、配置异常时优雅降级）。
 */
internal val LocalServerBaseUrl = staticCompositionLocalOf { "" }

/** 前缀匹配用（大小写不敏感）。`_` 非 RFC 合法 scheme 字符，因此字符串匹配、不用 Uri.parse。 */
private const val LIVACHAT_WEBROOT_PREFIX = "livachat_url://webroot/"

private val UUIDV7 = Regex(
    "^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    RegexOption.IGNORE_CASE,
)

/** ws(s) 地址解析结果：wss→https / ws→http，端口缺省 443/80。 */
internal data class ServerAddress(val scheme: String, val host: String, val port: Int) {
    val base: String get() = "$scheme://$host:$port"
}

/** 与 AppModule.provideMylifeUrl / SettingsViewModel.checkService 同源推导。 */
internal fun serverAddress(wsUrl: String): ServerAddress? {
    val uri = runCatching { URI(wsUrl) }.getOrNull() ?: return null
    val host = uri.host ?: return null
    val wss = uri.scheme == "wss"
    val port = if (uri.port == -1) (if (wss) 443 else 80) else uri.port
    return ServerAddress(scheme = if (wss) "https" else "http", host = host, port = port)
}

// 对外域名映射（docs/setting_server_address.md）：WS 连接走 IP（不依赖 DNS），webroot 链接会被系统
// 浏览器打开——dev 证书仅签 DNS SAN（无 IP SAN），浏览器按 hostname 校验。目前不使用（渲染基址回退
// 原 host）；恢复时取消注释，把 "<server-host>" / "<public-domain>" 替换为实际 host / 域名，
// 并让 serverBaseUrl 改用 PUBLIC_DOMAIN_BY_HOST[addr.host] ?: addr.host。
// private val PUBLIC_DOMAIN_BY_HOST = mapOf("<server-host>" to "<public-domain>")

/** 服务器基址（形如 `https://192.168.1.10:8080`）；不可推导 → ""。 */
internal fun serverBaseUrl(wsUrl: String): String = serverAddress(wsUrl)?.base ?: ""

/**
 * 渲染期转换（§3.4 D4'）：把文本中全部 `livachat_url://webroot/<uuidv7>[/…]` 替换为
 * `"$serverBaseUrl/webroot/<rest>"`；任何不合法片段**原样保留**（渲染为不可点文本）。
 *
 * - `serverBaseUrl` 空 → 原样返回（幂等）；
 * - token 截断界：空白 / `)` / `]` / `>` / `"` / `'` / 任一非 ASCII 字符；再剥尾部 ASCII 标点
 *   （`.` `,` `;` `:` `!` `?`）——剥离字符不进校验、不参与替换（留原位）；
 * - token 校验：首段必须 uuidv7（大小写不敏感）；含空白 / `..` / `\` → 非法。
 */
internal fun convertLivachatUrls(text: String, serverBaseUrl: String): String {
    if (serverBaseUrl.isEmpty() || text.isEmpty()) return text
    var from = 0
    var replaced = false
    val sb = StringBuilder(text.length + 16)
    while (true) {
        val idx = text.indexOf(LIVACHAT_WEBROOT_PREFIX, from, ignoreCase = true)
        if (idx < 0) {
            sb.append(text, from, text.length)
            break
        }
        sb.append(text, from, idx)
        val tokenStart = idx + LIVACHAT_WEBROOT_PREFIX.length
        var end = tokenStart
        while (end < text.length && !isTokenBoundary(text[end])) end++
        var tokenEnd = end
        while (tokenEnd > tokenStart && text[tokenEnd - 1] in TRAILING_PUNCT) tokenEnd--
        val token = text.substring(tokenStart, tokenEnd)
        if (isValidWebrootToken(token)) {
            sb.append(serverBaseUrl).append("/webroot/").append(token)
            replaced = true
        } else {
            sb.append(text, idx, tokenEnd)
        }
        sb.append(text, tokenEnd, end)   // 被剥离的尾部标点（或非法 token 的其余部分）留原位
        from = end
    }
    return if (replaced) sb.toString() else text
}

private val TRAILING_PUNCT = setOf('.', ',', ';', ':', '!', '?')

private fun isTokenBoundary(c: Char): Boolean =
    c.isWhitespace() || c == ')' || c == ']' || c == '>' || c == '"' || c == '\'' || c.code > 0x7F

private fun isValidWebrootToken(token: String): Boolean {
    if (token.isEmpty()) return false
    if (token.any { it.isWhitespace() } || token.contains("..") || token.contains('\\')) return false
    return UUIDV7.matches(token.substringBefore('/'))
}
