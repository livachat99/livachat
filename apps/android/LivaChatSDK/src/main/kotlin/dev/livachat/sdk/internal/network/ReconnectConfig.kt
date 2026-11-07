package dev.livachat.sdk.internal.network

/**
 * Configuration for exponential-backoff reconnect behavior.
 *
 * @property baseDelayMs initial delay before first retry (milliseconds).
 * @property maxDelayMs absolute ceiling on computed delay.
 * @property maxAttempts maximum number of consecutive retries before giving up.
 * @property jitterMs ± range of random jitter added to each delay.
 */
internal data class ReconnectConfig(
    val baseDelayMs: Long = 1000L,
    val maxDelayMs: Long = 30000L,
    val maxAttempts: Int = 5,
    val jitterMs: Long = 500L,
)
