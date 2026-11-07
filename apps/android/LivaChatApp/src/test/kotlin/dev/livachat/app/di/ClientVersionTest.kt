package dev.livachat.app.di

import dev.livachat.app.BuildConfig
import org.assertj.core.api.Assertions.assertThat
import org.junit.Test

class ClientVersionTest {

    @Test
    fun `formats versionName and versionCode`() {
        assertThat(androidClientVersion("1.0.0", 130)).isEqualTo("android-1.0.0.130")
    }

    @Test
    fun `actual build config matches wire pattern`() {
        val version = androidClientVersion(BuildConfig.VERSION_NAME, BuildConfig.VERSION_CODE)
        assertThat(version).matches("^android-\\d+\\.\\d+\\.\\d+\\.\\d+$")
    }
}
