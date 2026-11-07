package dev.livachat.app.domain

import dev.livachat.app.domain.model.SessionSummary
import dev.livachat.app.domain.usecase.SessionUseCase
import dev.livachat.app.media.AppForegroundState
import dev.livachat.app.media.AudioCaptureController
import dev.livachat.app.media.AudioPlaybackController
import dev.livachat.app.media.CameraController
import dev.livachat.app.media.CompositeLivaChatListener
import dev.livachat.app.media.InterruptCoordinator
import dev.livachat.app.ui.screens.conversation.ConversationListenerAdapter
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.ConnectionState
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Before
import org.junit.Test
import java.time.Instant

/**
 * 重连自动恢复：以 SDK `connectionEpoch`（每次成功建连单调 +1，免疫 StateFlow 值回归
 * 去重——回归 flap 也必达）为触发，gate `sessionActive && connectionState == CONNECTED`；
 * `session.start` 携带**服务端下发的** session_id 作为 resume_id（协议要求 `sess_<18>`，
 * 本地 Room id 是 UUID 不能直接用）——服务端 session_id 延续，不再每轮重连开新会话；
 * 首次连接为 null（新建）。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ConversationCoordinatorTest {

    private val sdk = mockk<LivaChatSDK>(relaxed = true)
    private val audioCapture = mockk<AudioCaptureController>(relaxed = true)
    private val audioPlayback = mockk<AudioPlaybackController>(relaxed = true)
    private val cameraController = mockk<CameraController>(relaxed = true)
    private val interruptCoordinator = mockk<InterruptCoordinator>(relaxed = true)
    private val sessionUseCase = mockk<SessionUseCase>(relaxed = true)
    private val listenerAdapter = mockk<ConversationListenerAdapter>(relaxed = true)
    private val compositeListener = mockk<CompositeLivaChatListener>(relaxed = true)
    private val appForegroundState = mockk<AppForegroundState>(relaxed = true)

    private val connectionFlow = MutableStateFlow(ConnectionState.DISCONNECTED)
    private val epochFlow = MutableStateFlow(0L)

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun newCoordinator() = ConversationCoordinator(
        sdk, audioCapture, audioPlayback, cameraController, interruptCoordinator,
        sessionUseCase, listenerAdapter, compositeListener, appForegroundState,
    )

    /** observer 订阅 connectionEpoch 为触发、读 connectionState.value 做门控，两者都要 stub。 */
    private fun stubConnectionFlows() {
        every { sdk.connectionState } returns connectionFlow
        every { sdk.connectionEpoch } returns epochFlow
    }

    private fun stubSessionUseCase() {
        coEvery { sessionUseCase.createOrGetTodaySession() } returns SessionSummary(
            id = "local-uuid-1", startTime = Instant.now(), endTime = null,
            messageCount = 0, summary = null,
        )
        coEvery { sessionUseCase.getNextSeq(any()) } returns 1
        coEvery { sessionUseCase.endSession(any()) } returns Unit
    }

    @Test
    fun `first connect starts a fresh session without resume id`() = runTest {
        stubConnectionFlows()
        every { listenerAdapter.serverSessionId } returns null
        stubSessionUseCase()
        every { appForegroundState.isForeground } returns MutableStateFlow(true)

        val coordinator = newCoordinator()
        coordinator.start()
        connectionFlow.value = ConnectionState.CONNECTED
        epochFlow.value = 1L

        verify(exactly = 1) { sdk.startSession(null) }
        coordinator.stop()
    }

    @Test
    fun `reconnect resumes with the server-issued session id`() = runTest {
        stubConnectionFlows()
        every { listenerAdapter.serverSessionId } returns null // 首次连接尚无会话
        stubSessionUseCase()
        every { appForegroundState.isForeground } returns MutableStateFlow(true)

        val coordinator = newCoordinator()
        coordinator.start()
        connectionFlow.value = ConnectionState.CONNECTED
        epochFlow.value = 1L
        verify(exactly = 1) { sdk.startSession(null) }

        // session.ready 到达 → 适配器记录服务端 id
        every { listenerAdapter.serverSessionId } returns "sess_abcdefghij01234567"

        connectionFlow.value = ConnectionState.RECONNECTING
        connectionFlow.value = ConnectionState.CONNECTED
        epochFlow.value = 2L

        verify(exactly = 1) { sdk.startSession("sess_abcdefghij01234567") }
        coordinator.stop()
    }

    @Test
    fun `flap that reverts before the collector runs still resumes`() = runTest {
        // 事故时序：RECONNECTING 与
        // CONNECTED 及代际自增全部落在一次 collector 运行窗口内——连接态流被"值回归去重"
        // 整段吞掉，但代际单调必达 ⇒ 仍恢复。v1 电平（订阅连接态）实现在此用例上失败。
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        stubConnectionFlows()
        every { listenerAdapter.serverSessionId } returns null
        stubSessionUseCase()
        every { appForegroundState.isForeground } returns MutableStateFlow(true)

        val coordinator = newCoordinator()
        coordinator.start()
        connectionFlow.value = ConnectionState.CONNECTED
        epochFlow.value = 1L
        advanceUntilIdle()
        verify(exactly = 1) { sdk.startSession(null) }

        // session.ready 到达 → 适配器记录服务端 id
        every { listenerAdapter.serverSessionId } returns "sess_abcdefghij01234567"

        // 两次赋值之间不 advance：回归 flap 全部落在一次 collector 运行窗口内
        connectionFlow.value = ConnectionState.RECONNECTING
        connectionFlow.value = ConnectionState.CONNECTED
        epochFlow.value = 2L
        advanceUntilIdle()

        verify(exactly = 1) { sdk.startSession("sess_abcdefghij01234567") }
        coordinator.stop()
    }

    @Test
    fun `epoch bump after the connection dropped again is skipped until next connect`() = runTest {
        // §3.2「观察到代际时已再次掉线」：open（代际自增）后立即又掉线，observer 才醒来
        // ⇒ 门控跳过，不向已死连接发 session.start；下一次建连代际再增 → 再发。
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        stubConnectionFlows()
        every { listenerAdapter.serverSessionId } returns null
        stubSessionUseCase()
        every { appForegroundState.isForeground } returns MutableStateFlow(true)

        val coordinator = newCoordinator()
        coordinator.start()
        connectionFlow.value = ConnectionState.CONNECTED
        epochFlow.value = 1L
        advanceUntilIdle()
        verify(exactly = 1) { sdk.startSession(null) }

        // session.ready 到达 → 适配器记录服务端 id
        every { listenerAdapter.serverSessionId } returns "sess_abcdefghij01234567"

        // 第二次建连 burst：open 后立即掉线——observer 全程未运行，醒来时 state 已 RECONNECTING
        connectionFlow.value = ConnectionState.CONNECTED
        epochFlow.value = 2L
        connectionFlow.value = ConnectionState.RECONNECTING
        advanceUntilIdle()
        // 跳过：不向死连接发（此刻唯一可能的误发参数是 re-stub 后的服务端 id）
        verify(exactly = 0) { sdk.startSession("sess_abcdefghij01234567") }

        // 第三次建连：代际再增 → 补发 resume
        connectionFlow.value = ConnectionState.CONNECTED
        epochFlow.value = 3L
        advanceUntilIdle()
        verify(exactly = 1) { sdk.startSession("sess_abcdefghij01234567") }
        coordinator.stop()
    }

    // ── 前后台门控转发（docs/android_audio_capture.md §3.6）────

    @Test
    fun `foreground transitions are forwarded to capture controller`() = runTest {
        stubConnectionFlows()
        every { listenerAdapter.serverSessionId } returns null
        stubSessionUseCase()
        val fg = MutableStateFlow(true)
        every { appForegroundState.isForeground } returns fg
        // 自愈分支读 isCapturing：采集活着 → no-op（不 stub 会命中 relaxed mock 的坏值）
        every { audioCapture.isCapturing } returns MutableStateFlow(true)

        val coordinator = newCoordinator()
        coordinator.start()

        // 订阅起点以当前值 true 发射（resume 门控的 no-op 路径）
        verify(exactly = 1) { audioCapture.onAppForegrounded() }

        fg.value = false
        verify(exactly = 1) { audioCapture.onAppBackgrounded() }
        fg.value = true
        verify(exactly = 2) { audioCapture.onAppForegrounded() }

        coordinator.stop()
    }

    @Test
    fun `duplicate start does not duplicate the foreground collector`() = runTest {
        // 本用例用 Standard 调度器：首次 launch 未派发时 _isActive 仍 false，
        // 第二次 start() 走"先取消再注册"防重路径（Unconfined 下会被 _isActive 守卫短路，覆盖不到）
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        stubConnectionFlows()
        every { listenerAdapter.serverSessionId } returns null
        stubSessionUseCase()
        val fg = MutableStateFlow(true)
        every { appForegroundState.isForeground } returns fg
        every { audioCapture.isCapturing } returns MutableStateFlow(true)

        val coordinator = newCoordinator()
        coordinator.start()
        coordinator.start()
        advanceUntilIdle()  // 首个 collector 已被取消，仅第二个存活

        fg.value = false
        advanceUntilIdle()
        verify(exactly = 1) { audioCapture.onAppBackgrounded() }

        coordinator.stop()
    }

    // ── 回前台自愈（docs/android_background_support.md §4）────

    @Test
    fun `foreground self-heal restarts dead capture and reconnects`() = runTest {
        stubConnectionFlows()
        every { listenerAdapter.serverSessionId } returns null
        stubSessionUseCase()
        val fg = MutableStateFlow(true)
        every { appForegroundState.isForeground } returns fg
        every { audioCapture.isCapturing } returns MutableStateFlow(false)  // 采集死态

        val coordinator = newCoordinator()
        coordinator.start()
        // 订阅起点 sessionActive 尚未置位：自愈不触发（start() 自身路径各 1 次）
        verify(exactly = 1) { audioCapture.start() }
        verify(exactly = 1) { sdk.connect() }

        fg.value = false
        fg.value = true

        verify(exactly = 2) { audioCapture.start() }
        verify(exactly = 2) { sdk.connect() }
        coordinator.stop()
    }

    @Test
    fun `foreground self-heal is a no-op when capture and connection are alive`() = runTest {
        stubConnectionFlows()
        every { listenerAdapter.serverSessionId } returns null
        stubSessionUseCase()
        val fg = MutableStateFlow(true)
        every { appForegroundState.isForeground } returns fg
        every { audioCapture.isCapturing } returns MutableStateFlow(true)
        connectionFlow.value = ConnectionState.CONNECTED

        val coordinator = newCoordinator()
        coordinator.start()
        fg.value = false
        fg.value = true

        verify(exactly = 1) { audioCapture.start() }   // 仅 start() 自身路径
        verify(exactly = 1) { sdk.connect() }
        coordinator.stop()
    }

    @Test
    fun `self-heal does not revive after stop`() = runTest {
        stubConnectionFlows()
        every { listenerAdapter.serverSessionId } returns null
        stubSessionUseCase()
        val fg = MutableStateFlow(true)
        every { appForegroundState.isForeground } returns fg
        every { audioCapture.isCapturing } returns MutableStateFlow(false)

        val coordinator = newCoordinator()
        coordinator.start()
        verify(exactly = 1) { audioCapture.onAppForegrounded() }  // 订阅起点发射

        coordinator.stop()
        fg.value = false
        fg.value = true

        // collector 已取消且 sessionActive=false：无追加调用，采集不复活
        verify(exactly = 1) { audioCapture.onAppForegrounded() }
        verify(exactly = 0) { audioCapture.onAppBackgrounded() }
        verify(exactly = 1) { audioCapture.start() }
    }
}
