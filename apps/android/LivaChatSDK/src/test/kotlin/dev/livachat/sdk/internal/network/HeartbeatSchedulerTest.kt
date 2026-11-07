package dev.livachat.sdk.internal.network

import org.assertj.core.api.Assertions.assertThat
import org.junit.Test

/**
 * 心跳判据：探测与判死**同源**（均基于入站静默）；出站流量不影响任何判据。
 * 背景见 HeartbeatScheduler KDoc（2026-09-11 单向流每 ~36s 重连复盘）。
 */
class HeartbeatSchedulerTest {

    private val keepaliveIntervalMs = 15_000L
    private val keepaliveTimeoutMs = 35_000L

    private fun scheduler(now: () -> Long) =
        HeartbeatScheduler(keepaliveIntervalMs, keepaliveTimeoutMs, now)

    @Test
    fun `shouldSendPing returns false before interval elapses`() {
        var now = 0L
        val s = scheduler { now }
        s.start()

        now = 14_000L
        assertThat(s.shouldSendPing()).isFalse()
    }

    @Test
    fun `shouldSendPing returns true after inbound silence reaches interval`() {
        var now = 0L
        val s = scheduler { now }
        s.start()

        now = 15_000L
        assertThat(s.shouldSendPing()).isTrue()
    }

    @Test
    fun `ping is sent at most once per inbound-silence window`() {
        var now = 0L
        val s = scheduler { now }
        s.start()

        now = 15_000L
        assertThat(s.shouldSendPing()).isTrue()
        s.onPingSent()               // checkHeartbeat 1Hz 轮询：发过就不再连发

        now = 16_000L
        assertThat(s.shouldSendPing()).isFalse()
        now = 34_000L
        assertThat(s.shouldSendPing()).isFalse()
    }

    @Test
    fun `probe does not postpone the timeout`() {
        var now = 0L
        val s = scheduler { now }
        s.start()

        now = 15_000L
        s.onPingSent()               // 探测已发出但对方未回
        assertThat(s.isTimeout()).isFalse()

        now = 35_000L
        assertThat(s.isTimeout()).isTrue()   // 判死只认入站，探测不算存活证据
    }

    @Test
    fun `continuous inbound traffic suppresses both probe and timeout`() {
        var now = 0L
        val s = scheduler { now }
        s.start()

        while (now < 60_000L) {
            now += 5_000L
            s.onInbound()            // 对端持续发帧（如服务端 pong / AI 输出）
            assertThat(s.shouldSendPing()).isFalse()
            assertThat(s.isTimeout()).isFalse()
        }
    }

    @Test
    fun `inbound reopens the probe window`() {
        var now = 0L
        val s = scheduler { now }
        s.start()

        now = 15_000L
        s.onPingSent()
        now = 20_000L
        s.onInbound()                // pong 到达 → 窗口重开
        assertThat(s.shouldSendPing()).isFalse()

        now = 20_000L + 14_000L
        assertThat(s.shouldSendPing()).isFalse()
        now = 20_000L + 15_000L
        assertThat(s.shouldSendPing()).isTrue()
    }

    @Test
    fun `isTimeout returns false before timeout elapses`() {
        var now = 0L
        val s = scheduler { now }
        s.start()

        now = 34_000L
        assertThat(s.isTimeout()).isFalse()
    }

    @Test
    fun `isTimeout returns true after timeout elapses`() {
        var now = 0L
        val s = scheduler { now }
        s.start()

        now = 35_000L
        assertThat(s.isTimeout()).isTrue()
    }

    @Test
    fun `onInbound resets timeout clock`() {
        var now = 0L
        val s = scheduler { now }
        s.start()

        now = 34_000L
        s.onInbound() // message received, resets timeout clock
        now = 34_000L + 34_000L // 34s since inbound
        assertThat(s.isTimeout()).isFalse()

        now = 34_000L + 35_000L // 35s since inbound
        assertThat(s.isTimeout()).isTrue()
    }

    @Test
    fun `start resets all timestamps`() {
        var now = 0L
        val s = scheduler { now }
        s.start()

        now = 15_000L
        assertThat(s.shouldSendPing()).isTrue()

        // Re-start (simulates a fresh connection)
        s.start()
        now = 15_000L + 14_000L
        assertThat(s.shouldSendPing()).isFalse()
        now = 15_000L + 15_000L
        assertThat(s.shouldSendPing()).isTrue()
    }

    @Test
    fun `reset delegates to start`() {
        var now = 0L
        val s = scheduler { now }
        s.start()
        now = 35_000L
        assertThat(s.isTimeout()).isTrue()

        s.reset()
        now = 35_000L + 14_000L
        assertThat(s.isTimeout()).isFalse()
    }

    @Test
    fun `shouldSendPing always false when not started`() {
        var now = 100_000L
        val s = scheduler { now }
        // never called start()
        assertThat(s.shouldSendPing()).isFalse()
        assertThat(s.isTimeout()).isFalse()
    }
}
