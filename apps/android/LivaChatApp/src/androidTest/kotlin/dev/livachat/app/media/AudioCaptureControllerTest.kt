package dev.livachat.app.media

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Ignore
import org.junit.runner.RunWith

/**
 * AudioCaptureController instrumentation tests.
 *
 * Currently @Ignore because mockk bytecode manipulation (JvmMockKGateway)
 * is not compatible with ART on Android devices. For proper instrumented
 * coverage, we need either:
 *   1. A test-specific LivaChatSDK interface (not possible — SDK is a final class)
 *   2. Mockito + dexmaker-mockito-inline for Android mocking
 *   3. Manual fake SDK implementation via subclass (not possible — SDK is not open)
 *
 * The AudioRecord lifecycle is verified manually on emulator in Phase 8 E2E.
 */
@Ignore("mockk incompatible with Android ART — requires alternative SDK stubbing")
@RunWith(AndroidJUnit4::class)
class AudioCaptureControllerTest
