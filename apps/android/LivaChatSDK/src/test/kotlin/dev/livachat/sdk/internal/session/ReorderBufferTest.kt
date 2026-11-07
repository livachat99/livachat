package dev.livachat.sdk.internal.session

import org.assertj.core.api.Assertions.assertThat
import org.junit.Test
import java.util.concurrent.atomic.AtomicReference

class ReorderBufferTest {

    @Test
    fun `in-order arrival dispatches immediately`() {
        val buffer = ReorderBuffer<String>()

        val r1 = buffer.push(1, "a")
        assertThat(r1).containsExactly("a")

        val r2 = buffer.push(2, "b")
        assertThat(r2).containsExactly("b")

        val r3 = buffer.push(3, "c")
        assertThat(r3).containsExactly("c")
    }

    @Test
    fun `out-of-order 1-3-2 dispatches 1 immediately then 2-3 after 2 arrives`() {
        val buffer = ReorderBuffer<String>()

        val r1 = buffer.push(1, "a")
        assertThat(r1).containsExactly("a")

        val r2 = buffer.push(3, "c")
        assertThat(r2).isEmpty() // buffered

        val r3 = buffer.push(2, "b")
        assertThat(r3).containsExactly("b", "c") // 2 fills gap, 3 follows
    }

    @Test
    fun `gap timeout skips missing seq and flushes`() {
        var now = 0L
        val buffer = ReorderBuffer<String>(clock = { now })

        buffer.push(1, "a")
        now = 50L
        val r2 = buffer.push(3, "c")
        assertThat(r2).isEmpty() // 2 missing, 3 buffered

        now = 250L // 200ms after gap started
        val r3 = buffer.push(4, "d") // triggers flush
        assertThat(r3).containsExactly("c", "d") // skipped 2
    }

    @Test
    fun `gap timeout logs warning`() {
        var now = 0L
        val warning = AtomicReference<String>()
        val buffer = ReorderBuffer<String>(clock = { now }, onWarning = { warning.set(it) })

        buffer.push(1, "a")
        now = 50L
        buffer.push(3, "c")
        now = 250L
        buffer.push(4, "d")

        assertThat(warning.get()).isNotNull()
        assertThat(warning.get()).contains("gap skip")
        assertThat(warning.get()).contains("expected=2")
    }

    @Test
    fun `buffer exceeds maxSize forces flush`() {
        val buffer = ReorderBuffer<String>(maxSize = 2)

        buffer.push(2, "b") // miss seq 1, buffer b
        assertThat(buffer.push(3, "c")).isEmpty()
        // Buffer now has 2 items (max size), next push triggers flush
        val result = buffer.push(5, "e")
        assertThat(result).containsExactly("b", "c", "e")
    }

    @Test
    fun `duplicate seq is ignored`() {
        val buffer = ReorderBuffer<String>()

        val r1 = buffer.push(1, "a")
        assertThat(r1).containsExactly("a")

        val r2 = buffer.push(1, "duplicate")
        assertThat(r2).isEmpty() // duplicate, ignored
    }

    @Test
    fun `late message below nextExpected is ignored`() {
        val buffer = ReorderBuffer<String>()

        buffer.push(1, "a")
        buffer.push(2, "b")
        // nextExpected is now 3
        val r = buffer.push(1, "too late")
        assertThat(r).isEmpty()
    }

    @Test
    fun `reset clears state`() {
        val buffer = ReorderBuffer<String>()
        buffer.push(2, "b") // gap, buffered
        buffer.reset()
        // After reset, start fresh
        val r = buffer.push(1, "a")
        assertThat(r).containsExactly("a")
    }

    @Test
    fun `complex out-of-order with gaps`() {
        val buffer = ReorderBuffer<String>()

        assertThat(buffer.push(1, "a")).containsExactly("a")
        assertThat(buffer.push(2, "b")).containsExactly("b")
        assertThat(buffer.push(5, "e")).isEmpty() // gap at 3
        assertThat(buffer.push(4, "d")).isEmpty() // still gap at 3
        assertThat(buffer.push(3, "c")).containsExactly("c", "d", "e") // gap filled
    }
}
