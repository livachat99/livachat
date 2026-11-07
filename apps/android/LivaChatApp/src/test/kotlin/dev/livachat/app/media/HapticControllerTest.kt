package dev.livachat.app.media

import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class HapticControllerTest {

    @Test
    fun `constructor does not throw`() {
        // Verifies the HapticController resolves Vibrator/VibratorManager
        // from Robolectric's shadow context without crashing.
        val context = org.robolectric.RuntimeEnvironment.getApplication().applicationContext
        HapticController(context)
    }
}
