package dev.livachat.app.e2e

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Ignore
import org.junit.runner.RunWith

/**
 * Settings persistence E2E tests using DataStore on device.
 *
 * Currently @Ignore because PreferenceDataStoreFactory.create(scope=runTest) has
 * scope compatibility issues with coroutines on the Android emulator.
 * The settings persistence behavior is covered by unit tests:
 *   - PreferencesDataSourceTest (Robolectric)
 *   - SettingsRepositoryTest (mockk)
 *   - SettingsUseCaseTest (mockk)
 */
@Ignore("DataStore scope compatibility with runTest on emulator")
@RunWith(AndroidJUnit4::class)
class SettingsE2ETest
