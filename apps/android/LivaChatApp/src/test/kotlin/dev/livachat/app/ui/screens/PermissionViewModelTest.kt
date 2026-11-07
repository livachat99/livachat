package dev.livachat.app.ui.screens

import dev.livachat.app.data.local.PreferencesDataSource
import dev.livachat.app.ui.screens.permission.PermissionViewModel
import io.mockk.mockk
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PermissionViewModelTest {

    private val dataSource = mockk<PreferencesDataSource>(relaxed = true)

    @Test
    fun onMicPermissionResult_updates_state() {
        val vm = PermissionViewModel(dataSource)
        vm.onMicPermissionResult(true)
        assertTrue(vm.uiState.value.micGranted)
    }

    @Test
    fun permanently_denied_mic_sets_state() {
        val vm = PermissionViewModel(dataSource)
        vm.onMicPermissionResult(false, permanentlyDenied = true)
        assertFalse(vm.uiState.value.micGranted)
        assertTrue(vm.uiState.value.micPermanentlyDenied)
        assertTrue(vm.uiState.value.showGoToSettings)
    }

    @Test
    fun onCameraPermissionResult_updates_state() {
        val vm = PermissionViewModel(dataSource)
        vm.onCameraPermissionResult(true)
        assertTrue(vm.uiState.value.cameraGranted)
    }

    @Test
    fun clearEvent_resets_event_to_null() {
        val vm = PermissionViewModel(dataSource)
        vm.clearEvent()
        assertEquals(null, vm.event.value)
    }

    @Test
    fun initial_state_is_correct() {
        val vm = PermissionViewModel(dataSource)
        assertEquals(false, vm.uiState.value.micGranted)
        assertEquals(false, vm.uiState.value.cameraGranted)
        assertEquals(false, vm.uiState.value.micPermanentlyDenied)
    }
}
