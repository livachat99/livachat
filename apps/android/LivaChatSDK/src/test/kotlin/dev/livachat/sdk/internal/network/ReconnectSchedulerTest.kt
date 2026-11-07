package dev.livachat.sdk.internal.network

import org.assertj.core.api.Assertions.assertThat
import org.junit.Test

class ReconnectSchedulerTest {

    private val config = ReconnectConfig(
        baseDelayMs = 1000L,
        maxDelayMs = 30000L,
        maxAttempts = 5,
        jitterMs = 500L,
    )

    @Test
    fun `nextDelay for attempt 1 is base delay`() {
        val scheduler = ReconnectScheduler(config)
        val delay = scheduler.nextDelay()
        // With jitter: 1000 ± 500 → [500, 1500]
        assertThat(delay).isBetween(500L, 1500L)
        assertThat(scheduler.currentAttempt()).isEqualTo(1)
    }

    @Test
    fun `nextDelay for attempt 2 is 2x base`() {
        val scheduler = ReconnectScheduler(config)
        scheduler.nextDelay() // attempt 1
        val delay = scheduler.nextDelay() // attempt 2: 2000 ± 500
        assertThat(delay).isBetween(1500L, 2500L)
    }

    @Test
    fun `nextDelay for attempt 3 is 4x base`() {
        val scheduler = ReconnectScheduler(config)
        repeat(2) { scheduler.nextDelay() }
        val delay = scheduler.nextDelay() // 4000 ± 500
        assertThat(delay).isBetween(3500L, 4500L)
    }

    @Test
    fun `nextDelay caps at maxDelay`() {
        val scheduler = ReconnectScheduler(
            config.copy(maxAttempts = 10, jitterMs = 500L)
        )
        repeat(5) { scheduler.nextDelay() } // attempts 1-5: up to 16000
        val delay = scheduler.nextDelay() // attempt 6: 32000 → capped at 30000 ± 500
        assertThat(delay).isBetween(29500L, 30500L)
    }

    @Test
    fun `nextDelay returns STOP after maxAttempts exceeded`() {
        val scheduler = ReconnectScheduler(config)
        // 5 attempts (maxAttempts=5), then one more
        repeat(5) { scheduler.nextDelay() }
        val delay = scheduler.nextDelay() // attempt 6 > maxAttempts
        assertThat(delay).isEqualTo(ReconnectScheduler.STOP)
    }

    @Test
    fun `exceeding maxAttempts returns STOP on every call`() {
        val scheduler = ReconnectScheduler(config)
        repeat(5) { scheduler.nextDelay() }
        assertThat(scheduler.nextDelay()).isEqualTo(ReconnectScheduler.STOP)
        assertThat(scheduler.nextDelay()).isEqualTo(ReconnectScheduler.STOP)
    }

    @Test
    fun `reset clears attempt counter`() {
        val scheduler = ReconnectScheduler(config)
        scheduler.nextDelay()
        scheduler.nextDelay()
        assertThat(scheduler.currentAttempt()).isEqualTo(2)
        scheduler.reset()
        assertThat(scheduler.currentAttempt()).isEqualTo(0)
        // After reset, nextDelay is back to attempt 1
        val delay = scheduler.nextDelay()
        assertThat(delay).isBetween(500L, 1500L)
    }

    @Test
    fun `jitter varies between calls`() {
        val scheduler = ReconnectScheduler(config.copy(jitterMs = 500L))
        val delays = List(20) {
            scheduler.reset()
            scheduler.nextDelay()
        }
        // With jitter, not all values should be identical
        val unique = delays.toSet().size
        assertThat(unique).isGreaterThan(1)
    }

    @Test
    fun `zero jitter produces exact values`() {
        val noJitterConfig = config.copy(jitterMs = 0L)
        val scheduler = ReconnectScheduler(noJitterConfig)
        assertThat(scheduler.nextDelay()).isEqualTo(1000L)
        assertThat(scheduler.nextDelay()).isEqualTo(2000L)
        assertThat(scheduler.nextDelay()).isEqualTo(4000L)
    }

    @Test
    fun `custom config is respected`() {
        val custom = ReconnectConfig(baseDelayMs = 500L, maxDelayMs = 10000L, maxAttempts = 3, jitterMs = 0L)
        val scheduler = ReconnectScheduler(custom)
        assertThat(scheduler.nextDelay()).isEqualTo(500L)
        assertThat(scheduler.nextDelay()).isEqualTo(1000L)
        assertThat(scheduler.nextDelay()).isEqualTo(2000L)
        assertThat(scheduler.nextDelay()).isEqualTo(ReconnectScheduler.STOP)
    }
}
