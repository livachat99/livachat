package dev.livachat.sdk.internal.session

import java.util.concurrent.atomic.AtomicLong

/**
 * Monotonically-increasing sequence number generator for C2S messages.
 *
 * 取号本身线程安全（[AtomicLong]），但**取号与发送必须同处一个临界区**：[next] 只允许在
 * `SessionManager.encodeAndSend` 的 sendLock 内调用——若在其他位置先取号再发送，多线程下
 * 到达服务端的顺序可能与取号顺序相反，被 `InboundSeqGuard`（services/gateway/seq.py）
 * 判为 seq 回退并静默丢弃该帧。
 *
 * 唯一例外：video 走 `SessionManager.encodeAndSendOutsideGate`（闸口外取号+编码，锁内只
 * 送包）——服务端对该类型的乱序记 `seq regression tolerated` 后照常处理，不丢帧。
 *
 * [reset] 无需在每次重连时调用：服务端 `InboundSeqGuard` 每连接新建，且收到 session.start
 * 时会重置水位；本方法只保留显式归零能力。
 */
internal class SeqGenerator {

    private val counter = AtomicLong(1)

    /** Return the next sequence number (1-based). 仅供 encodeAndSend / video 宽容路径调用。 */
    fun next(): Long = counter.getAndIncrement()

    /** Reset the counter back to 1 (explicit zeroing only; see class KDoc). */
    fun reset() {
        counter.set(1)
    }
}
