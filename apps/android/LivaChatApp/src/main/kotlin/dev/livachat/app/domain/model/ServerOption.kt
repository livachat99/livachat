package dev.livachat.app.domain.model

import java.security.MessageDigest

/**
 * 服务器配置（地址 + 认证密钥），读取/保存的原子对；持久化于 DataStore，
 * 进程启动时装配进 SDK（docs/setting_server_address.md §3.1）。
 */
data class ServerConfig(
    val address: String,
    val token: String,
)

private val HOST_PORT = Regex("""^([A-Za-z0-9.-]+):(\d{1,5})$""")

/** 严格校验输入为 `ip:port` 或 `域名:port`；合法返回原文，非法返回 null。 */
fun parseServerAddress(input: String): String? {
    val match = HOST_PORT.matchEntire(input) ?: return null
    val port = match.groupValues[2].toIntOrNull() ?: return null
    return if (port in 1..65535) input else null
}

/** `ip:port` → SDK 连接用完整 ws URL。 */
fun wsUrlFromAddress(address: String): String = "wss://$address/v1/ws/stream"

/** `ip:port` → HTTP 基址（`/healthz_token` 预检等；wss → https 与既有推导一致）。 */
fun httpBase(address: String): String = "https://$address"

/** 认证密钥字符集（§3.2）：字母 / 数字 / `-` / `_` / `.`，非空；均 URL-safe。 */
private val AUTH_TOKEN = Regex("""^[A-Za-z0-9._-]+$""")

fun isValidAuthToken(token: String): Boolean = AUTH_TOKEN.matches(token)

/** wire token 的固定盐（协议常量，服务端同值；docs/server_auth.md §3.1）。 */
private const val AUTH_SALT = "LivaChat"

/**
 * 明文 secret → 线上传输的 wire token = 小写 hex(sha1(secret + "LivaChat"))。
 * 明文 secret 永不入网；公式与 Python 端逐字节一致（固定向量见 docs/server_auth.md §3.1）。
 */
fun wireToken(secret: String): String =
    MessageDigest.getInstance("SHA-1")
        .digest((secret + AUTH_SALT).toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

/** 地址与密钥是否齐备（互检通过）——对话页启动门控与设置页按钮可用性共用同一条规则。 */
fun isServerConfigComplete(address: String, token: String): Boolean =
    parseServerAddress(address) != null && isValidAuthToken(token)

/**
 * 未配置（地址空/非法）时的装配占位 ws URL：语法合法（SDK / MylifeModule 各推导不抛异常）
 * 但不可路由——真实连接由对话页「配置不齐备不启动」门控拦截（§3.4）。
 */
const val UNSET_SERVER_WS_URL = "wss://0.0.0.0:1/v1/ws/stream"
