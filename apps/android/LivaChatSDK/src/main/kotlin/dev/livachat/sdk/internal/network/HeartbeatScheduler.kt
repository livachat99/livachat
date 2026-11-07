package dev.livachat.sdk.internal.network

/**
 * Pure-math keepalive timer. Drives ping scheduling and timeout detection.
 *
 * ZERO Android / coroutine dependencies — testable on plain JVM with an injected [clock].
 *
 * 判据一律以**入站**为准（探测与判死同源），出站流量不影响：
 * 入站静默 ≥ interval 探测一次（每个静默窗口最多一条，发后由 [onPingSent] 记账），
 * 入站静默 ≥ timeout 判死。反例（2026-09-11 真机复盘）：若按"出站静默"决定 ping，
 * 持续推流（音频 50Hz）时客户端永远不 ping、服务端（按入站判活）也不 ping，
 * 单向流场景下 35s 无入站 → 每 ~36s 自杀重连一次。
 *
 * @param keepaliveIntervalMs inbound silence that triggers one keepalive probe (default 15 000 ms).
 * @param keepaliveTimeoutMs  inbound silence that declares the connection dead (default 35 000 ms).
 */
internal class HeartbeatScheduler(
    private val keepaliveIntervalMs: Long = 15_000L,
    private val keepaliveTimeoutMs: Long = 35_000L,
    private val clock: () -> Long = { System.currentTimeMillis() },
) {

    private var lastInboundMs: Long = 0L
    private var started = false

    /** 当前入站静默窗口内是否已发过探测帧（checkHeartbeat 1Hz 轮询，防连发）。 */
    private var pingSent = false

    /** Call once when the connection is established to initialise timestamps. */
    fun start() {
        lastInboundMs = clock()
        started = true
        pingSent = false
    }

    /** Call on every inbound message (including pongs) — 入站即存活证据，重开探测窗口。 */
    fun onInbound() {
        lastInboundMs = clock()
        pingSent = false
    }

    /** Returns `true` when inbound silence warrants a keepalive probe (once per silence window). */
    fun shouldSendPing(): Boolean =
        started && !pingSent && (clock() - lastInboundMs) >= keepaliveIntervalMs

    /** Mark the probe as sent for the current inbound-silence window. */
    fun onPingSent() {
        pingSent = true
    }

    /** Returns `true` when no inbound message has been received within the timeout window. */
    fun isTimeout(): Boolean =
        started && (clock() - lastInboundMs) >= keepaliveTimeoutMs

    /** Reset timestamps as if a new connection was just established. */
    fun reset() = start()
}
