package dev.livachat.app

import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.assertj.core.api.Assertions.assertThat

@RunWith(RobolectricTestRunner::class)
class LivaChatApplicationTest {

    @Test
    fun `application starts without crashing`() {
        val application = RuntimeEnvironment.getApplication()
        assertThat(application).isNotNull
        assertThat(application.packageName).isEqualTo(BuildConfig.APPLICATION_ID)
    }
}
