package dev.livachat.sdk.internal.processors

/**
 * Loads the unified native library. Called once during SDK initialization
 * (Phase 6 Facade). App code must never call [System.loadLibrary] directly.
 */
object NativeBridge {
    private val loaded: Boolean = run {
        System.loadLibrary("livachat_native")
        true
    }

    fun isLoaded(): Boolean = loaded
}
