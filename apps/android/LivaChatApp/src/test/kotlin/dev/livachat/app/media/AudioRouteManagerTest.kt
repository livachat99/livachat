package dev.livachat.app.media

import dev.livachat.app.domain.model.AudioRoute
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowAudioManager

@RunWith(RobolectricTestRunner::class)
class AudioRouteManagerTest {

    @Test
    fun `switch to SPEAKER returns true`() {
        val context = org.robolectric.RuntimeEnvironment.getApplication().applicationContext
        val manager = AudioRouteManager(context)
        assertTrue(manager.switchRoute(AudioRoute.SPEAKER))
    }

    @Test
    fun `switch to EARPIECE returns true`() {
        val context = org.robolectric.RuntimeEnvironment.getApplication().applicationContext
        val manager = AudioRouteManager(context)
        assertTrue(manager.switchRoute(AudioRoute.EARPIECE))
    }

    @Test
    fun `switch to BLUETOOTH returns false when no bluetooth`() {
        val context = org.robolectric.RuntimeEnvironment.getApplication().applicationContext
        val manager = AudioRouteManager(context)
        // Robolectric shadow has no BT devices by default
        assertFalse(manager.switchRoute(AudioRoute.BLUETOOTH))
    }
}
