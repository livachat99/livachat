package dev.livachat.sdk.internal.protocol

import dev.livachat.protocol.generated.ClientMessage
import dev.livachat.protocol.generated.ServerMessage
import kotlinx.serialization.json.Json

/**
 * Stateless codec for encoding [ClientMessage] to JSON and decoding JSON to [ServerMessage].
 *
 * Json config matches the contract declared by the generated DTOs:
 * - [classDiscriminator] = "type"
 * - [ignoreUnknownKeys] = true
 * - [explicitNulls] = true
 *
 * Note: [Json.encodeDefaults] 未开启（kotlinx 默认 false）——生成物中**带默认值**的属性
 * 在取值等于默认值时不落线（如 `ClientVideoPayload.text = null` → 报文整体省略该字段），
 * 与"字段不存在"语义一致（服务端按空值处理）。
 */
internal object MessageCodec {

    internal val json = Json {
        classDiscriminator = "type"
        ignoreUnknownKeys = true
        explicitNulls = true
    }

    /**
     * Encodes a [ClientMessage] to its JSON wire representation.
     *
     * @throws CodecException if serialization fails.
     */
    fun encode(message: ClientMessage): String {
        return try {
            json.encodeToString(ClientMessage.serializer(), message)
        } catch (e: Exception) {
            throw CodecException("Failed to encode ClientMessage: ${message::class.simpleName}", e)
        }
    }

    /**
     * Decodes a JSON wire string into a [ServerMessage].
     *
     * @return the decoded message, or `null` if the JSON is malformed or the
     *         discriminator does not match any known server message type.
     */
    fun decode(jsonString: String): ServerMessage? {
        return try {
            json.decodeFromString(ServerMessage.serializer(), jsonString)
        } catch (e: Exception) {
            null
        }
    }
}
