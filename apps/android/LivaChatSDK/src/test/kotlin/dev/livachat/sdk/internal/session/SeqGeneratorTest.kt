package dev.livachat.sdk.internal.session

import org.assertj.core.api.Assertions.assertThat
import org.junit.Test

class SeqGeneratorTest {

    @Test
    fun `first call returns 1`() {
        val gen = SeqGenerator()
        assertThat(gen.next()).isEqualTo(1)
    }

    @Test
    fun `seq is monotonically increasing`() {
        val gen = SeqGenerator()
        assertThat(gen.next()).isEqualTo(1)
        assertThat(gen.next()).isEqualTo(2)
        assertThat(gen.next()).isEqualTo(3)
        assertThat(gen.next()).isEqualTo(4)
        assertThat(gen.next()).isEqualTo(5)
    }

    @Test
    fun `reset brings counter back to 1`() {
        val gen = SeqGenerator()
        repeat(5) { gen.next() }
        gen.reset()
        assertThat(gen.next()).isEqualTo(1)
        assertThat(gen.next()).isEqualTo(2)
    }

    @Test
    fun `multiple resets are idempotent`() {
        val gen = SeqGenerator()
        gen.next()
        gen.next()
        gen.reset()
        gen.reset()
        assertThat(gen.next()).isEqualTo(1)
    }
}
