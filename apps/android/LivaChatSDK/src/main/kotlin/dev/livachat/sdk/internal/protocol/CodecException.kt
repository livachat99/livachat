package dev.livachat.sdk.internal.protocol

/** Thrown when encoding a [dev.livachat.protocol.generated.ClientMessage] fails. */
class CodecException(message: String, cause: Throwable? = null) : RuntimeException(message, cause)
