package dev.livachat.app.ui.screens.settings

import android.content.Context
import dev.livachat.app.data.remote.ProbeResult
import dev.livachat.app.data.remote.ServiceProbe
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.ServerConfig
import dev.livachat.app.domain.model.UserPreferences
import dev.livachat.app.domain.usecase.SettingsUseCase
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SettingsViewModelTest {

    private val dispatcher = StandardTestDispatcher()

    private val settingsUseCase = mockk<SettingsUseCase>(relaxed = true)
    private val serviceProbe = mockk<ServiceProbe>()
    private val conversationCoordinator = mockk<ConversationCoordinator>(relaxed = true)
    private val context = mockk<Context>(relaxed = true)

    private val savedConfig = ServerConfig("example.com:9000", "dev-livachat-token")

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun buildViewModel(config: ServerConfig = savedConfig): SettingsViewModel {
        every { settingsUseCase.observePreferences() } returns flowOf(UserPreferences())
        every { settingsUseCase.observeServerConfig() } returns flowOf(config)
        return SettingsViewModel(
            settingsUseCase = settingsUseCase,
            serviceProbe = serviceProbe,
            conversationCoordinator = conversationCoordinator,
            context = context,
            deviceId = "device-1",
        )
    }

    @Test
    fun settingsUiState_defaults_are_correct() {
        val state = SettingsUiState()
        assertEquals(false, state.defaultFrontCamera)
        assertEquals(false, state.autoCameraOnJoin)
        assertTrue(state.hapticEnabled)
        assertTrue(state.avdetectorEnabled)
        assertTrue(state.tlsAllowSelfSigned)
        assertTrue(state.savedTlsAllowSelfSigned)
        assertEquals("", state.serverAddressInput)   // 未配置（升级/首装）为空（二改）
        assertEquals("", state.savedAddress)
        assertEquals("", state.authTokenInput)
        assertFalse(state.dirty)
        assertFalse(state.canProbe)   // 地址与 token 均空 → 不齐备
        assertEquals(false, state.showRestartDialog)
        assertEquals(ServiceStatus.Idle, state.serviceStatus)
    }

    @Test
    fun persistedServerConfig_fills_draft_and_baseline() = runTest(dispatcher) {
        val vm = buildViewModel(ServerConfig("example.org:8080", "lan-token"))
        advanceUntilIdle()

        val state = vm.uiState.value
        assertEquals("example.org:8080", state.serverAddressInput)
        assertEquals("lan-token", state.authTokenInput)
        assertEquals("example.org:8080", state.savedAddress)
        assertEquals("lan-token", state.savedToken)
        assertFalse(state.dirty)
        assertTrue(state.canProbe)
    }

    @Test
    fun editing_marks_dirty_and_resets_service_status() = runTest(dispatcher) {
        coEvery { serviceProbe.probe(any(), any(), any(), any()) } returns ProbeResult.AVAILABLE
        val vm = buildViewModel()
        advanceUntilIdle()
        vm.checkService()
        advanceUntilIdle()
        assertEquals(ServiceStatus.Available, vm.uiState.value.serviceStatus)

        vm.onServerAddressChange("example.org:8080")

        val state = vm.uiState.value
        assertTrue(state.dirty)
        assertEquals(ServiceStatus.Idle, state.serviceStatus)
    }

    @Test
    fun save_persists_and_shows_restart_prompt() = runTest(dispatcher) {
        val vm = buildViewModel()
        advanceUntilIdle()

        vm.onServerAddressChange("example.org:8080")
        vm.onAuthTokenChange("new-token")
        vm.saveServerConfig()
        advanceUntilIdle()

        coVerify { settingsUseCase.setServerConfig("example.org:8080", "new-token", true) }
        val state = vm.uiState.value
        assertTrue(state.showRestartDialog)
        assertEquals("example.org:8080", state.savedAddress)
        assertEquals("new-token", state.savedToken)
        assertFalse(state.dirty)
    }

    @Test
    fun save_ignores_clean_state() = runTest(dispatcher) {
        val vm = buildViewModel()
        advanceUntilIdle()

        vm.saveServerConfig()
        advanceUntilIdle()

        coVerify(exactly = 0) { settingsUseCase.setServerConfig(any(), any(), any()) }
        assertFalse(vm.uiState.value.showRestartDialog)
    }

    @Test
    fun save_invalid_input_sets_validation_and_does_not_persist() = runTest(dispatcher) {
        val vm = buildViewModel()
        advanceUntilIdle()

        vm.onServerAddressChange("bad address")
        vm.saveServerConfig()
        advanceUntilIdle()

        coVerify(exactly = 0) { settingsUseCase.setServerConfig(any(), any(), any()) }
        val state = vm.uiState.value
        assertNotNull(state.validationMessage)  // UI inline 文案
        assertFalse(state.showRestartDialog)
    }

    @Test
    fun checkService_maps_available_and_probes_draft() = runTest(dispatcher) {
        coEvery { serviceProbe.probe(any(), any(), any(), any()) } returns ProbeResult.AVAILABLE
        val vm = buildViewModel()
        advanceUntilIdle()

        vm.checkService()
        advanceUntilIdle()

        assertEquals(ServiceStatus.Available, vm.uiState.value.serviceStatus)
        coVerify { serviceProbe.probe("example.com:9000", "dev-livachat-token", "device-1", true) }
    }

    @Test
    fun checkService_maps_auth_failed() = runTest(dispatcher) {
        coEvery { serviceProbe.probe(any(), any(), any(), any()) } returns ProbeResult.AUTH_FAILED
        val vm = buildViewModel()
        advanceUntilIdle()

        vm.checkService()
        advanceUntilIdle()

        assertEquals(ServiceStatus.AuthFailed, vm.uiState.value.serviceStatus)
    }

    @Test
    fun checkService_maps_unreachable() = runTest(dispatcher) {
        coEvery { serviceProbe.probe(any(), any(), any(), any()) } returns ProbeResult.UNREACHABLE
        val vm = buildViewModel()
        advanceUntilIdle()

        vm.checkService()
        advanceUntilIdle()

        assertEquals(ServiceStatus.Unavailable, vm.uiState.value.serviceStatus)
    }

    @Test
    fun checkService_skips_invalid_input() = runTest(dispatcher) {
        val vm = buildViewModel(ServerConfig("example.com:9000", ""))
        advanceUntilIdle()

        vm.checkService()
        advanceUntilIdle()

        coVerify(exactly = 0) { serviceProbe.probe(any(), any(), any(), any()) }
        assertEquals(ServiceStatus.Idle, vm.uiState.value.serviceStatus)
    }

    @Test
    fun checkService_ignores_reentry_while_checking() = runTest(dispatcher) {
        val gate = CompletableDeferred<Unit>()
        coEvery { serviceProbe.probe(any(), any(), any(), any()) } coAnswers {
            gate.await()
            ProbeResult.AVAILABLE
        }
        val vm = buildViewModel()
        advanceUntilIdle()

        vm.checkService()
        advanceUntilIdle()
        assertEquals(ServiceStatus.Checking, vm.uiState.value.serviceStatus)

        vm.checkService()   // Checking 早退
        gate.complete(Unit)
        advanceUntilIdle()

        coVerify(exactly = 1) { serviceProbe.probe(any(), any(), any(), any()) }
        assertEquals(ServiceStatus.Available, vm.uiState.value.serviceStatus)
    }

    @Test
    fun checkService_result_dropped_when_draft_changed_mid_flight() = runTest(dispatcher) {
        val gate = CompletableDeferred<Unit>()
        coEvery { serviceProbe.probe(any(), any(), any(), any()) } coAnswers {
            gate.await()
            ProbeResult.AVAILABLE
        }
        val vm = buildViewModel()
        advanceUntilIdle()

        vm.checkService()
        advanceUntilIdle()
        vm.onServerAddressChange("example.org:8080")   // 探测期间改动草稿
        gate.complete(Unit)
        advanceUntilIdle()

        assertEquals(ServiceStatus.Idle, vm.uiState.value.serviceStatus)   // 旧结果不落地
    }

    @Test
    fun tls_change_marks_dirty_and_saves() = runTest(dispatcher) {
        val vm = buildViewModel()
        advanceUntilIdle()

        vm.setTlsAllowSelfSigned(false)
        assertTrue(vm.uiState.value.dirty)
        assertFalse(vm.uiState.value.tlsAllowSelfSigned)
        assertTrue(vm.uiState.value.savedTlsAllowSelfSigned)

        vm.saveServerConfig()
        advanceUntilIdle()

        coVerify { settingsUseCase.setServerConfig("example.com:9000", "dev-livachat-token", false) }
        val state = vm.uiState.value
        assertFalse(state.dirty)
        assertFalse(state.tlsAllowSelfSigned)
        assertFalse(state.savedTlsAllowSelfSigned)
    }

    @Test
    fun checkService_passes_draft_tls() = runTest(dispatcher) {
        coEvery { serviceProbe.probe(any(), any(), any(), any()) } returns ProbeResult.AVAILABLE
        val vm = buildViewModel()
        advanceUntilIdle()

        vm.setTlsAllowSelfSigned(false)
        vm.checkService()
        advanceUntilIdle()

        coVerify { serviceProbe.probe("example.com:9000", "dev-livachat-token", "device-1", false) }
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class SettingsViewModelAvdetectorTest {
    private val dispatcher = StandardTestDispatcher()
    private val settingsUseCase = mockk<SettingsUseCase>(relaxed = true)
    private val serviceProbe = mockk<ServiceProbe>(relaxed = true)
    private val conversationCoordinator = mockk<ConversationCoordinator>(relaxed = true)
    private val context = mockk<Context>(relaxed = true)

    @Before fun setUp() { Dispatchers.setMain(dispatcher) }
    @After fun tearDown() { Dispatchers.resetMain() }

    @Test
    fun setAvdetectorEnabled_persists_and_notifies_coordinator() = runTest(dispatcher) {
        every { settingsUseCase.observePreferences() } returns flowOf(UserPreferences())
        every { settingsUseCase.observeServerConfig() } returns flowOf(ServerConfig("example.com:9000", "token"))
        val vm = SettingsViewModel(
            settingsUseCase = settingsUseCase,
            serviceProbe = serviceProbe,
            conversationCoordinator = conversationCoordinator,
            context = context,
            deviceId = "device-1",
        )
        advanceUntilIdle()

        vm.setAvdetectorEnabled(false)
        advanceUntilIdle()

        coVerify { settingsUseCase.setAvdetectorEnabled(false) }
        io.mockk.verify { conversationCoordinator.setAvdetectorEnabled(false) }
    }
}
