package dev.livachat.app.media

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.AudioTrack
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.shadows.AudioDeviceInfoBuilder
import org.robolectric.shadows.ShadowLog

/**
 * docs/android_audio_output_devices.md §8.1：输出设备应用到播放链路（本控制器此前无 JVM 测试，
 * 随 AudioTrackFactory 接缝〔§5.4〕首次可测；结构镜像 AudioCaptureControllerSourceTest 的
 * Robolectric + fake 方案）。确定性手段：fake track 的 write() 可经 [writeGate] 冻结循环。
 *
 * 注意两点坑：relaxed mockk 布尔返回默认 false（setPreferredDevice 须显式 stub）；
 * getRoutedDevice 在非 playing 时为 null（本测试 fake 保持 relaxed 默认即可，不做日志断言时无碍）。
 */
@RunWith(RobolectricTestRunner::class)
class AudioPlaybackControllerTest {

    private lateinit var context: Context
    private lateinit var audioManager: AudioManager
    private lateinit var deviceManager: AudioDeviceManager
    private lateinit var controller: AudioPlaybackController

    private val outputDevicesFlow = MutableStateFlow<List<AudioOutputDevice>>(emptyList())
    private val tracks = mutableListOf<AudioTrack>()
    private var factoryCalls = 0
    private var preferredReturns = true      // setPreferredDevice 返回值档（平台拒绝 = false）
    private var preferredThrows = false
    private var createFailures = 0           // 剩余"构造抛异常"次数（与设备无关的构建失败）
    private var writeGate: CountDownLatch? = null   // 非空时 write 阻塞于 gate（冻结循环）
    private var writtenFrames = 0            // 跨 track 累计写入帧数（不丢帧用例的确定性锁）

    private fun fakeTrack(): AudioTrack {
        val t = mockk<AudioTrack>(relaxed = true)
        if (preferredThrows) {
            every { t.setPreferredDevice(any()) } throws RuntimeException("preferred boom")
        } else {
            every { t.setPreferredDevice(any()) } returns preferredReturns
        }
        every { t.write(any<ShortArray>(), any(), any(), any()) } answers {
            writeGate?.await(5, TimeUnit.SECONDS)
            writtenFrames++   // 闸后计数：阻塞中的 write 不算已写（场景锁用）
            arg<ShortArray>(0).size
        }
        every { t.playState } returns AudioTrack.PLAYSTATE_PLAYING
        tracks.add(t)
        return t
    }

    // shadow builder 无 setId（id 恒 0）→ 反射沿 mPort → mHandle → mId 改写（SDK14 结构；
    // 仅测试用，制造"多设备可分辨"场景）
    private fun outputDevice(id: Int, type: Int): AudioDeviceInfo {
        val dev = AudioDeviceInfoBuilder.newBuilder().setType(type).build()
        val portField = AudioDeviceInfo::class.java.getDeclaredField("mPort")
        portField.isAccessible = true
        val port = portField.get(dev)
        val handleField = port.javaClass.superclass.getDeclaredField("mHandle")
        handleField.isAccessible = true
        val handle = handleField.get(port)
        val idField = handle.javaClass.getDeclaredField("mId")
        idField.isAccessible = true
        idField.setInt(handle, id)
        return dev
    }

    @Before
    fun setUp() {
        ShadowLog.stream = System.out
        context = RuntimeEnvironment.getApplication()
        audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        deviceManager = mockk(relaxed = true)
        every { deviceManager.outputDevices } returns outputDevicesFlow
        controller = newController()
    }

    /** 构造注入了 fake 工厂的控制器；D10 初值用例需在构造前预置设备表 → 允许重复构造并替换字段。 */
    private fun newController(): AudioPlaybackController {
        val c = AudioPlaybackController(mockk(relaxed = true), deviceManager, context)
        c.trackFactory = AudioTrackFactory { _ ->
            factoryCalls++
            if (createFailures > 0) {
                createFailures--
                throw RuntimeException("create boom")
            }
            fakeTrack()
        }
        return c
    }

    @After
    fun tearDown() {
        writeGate?.countDown()
        if (controller.isPlaying.value) controller.stop()
    }

    private fun awaitPlaying(expected: Boolean, timeoutMs: Long = 3000) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline && controller.isPlaying.value != expected) {
            Thread.sleep(10)
        }
    }

    private fun awaitFactoryCalls(count: Int, timeoutMs: Long = 3000) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline && factoryCalls < count) {
            Thread.sleep(10)
        }
    }

    private fun awaitCondition(desc: String, timeoutMs: Long = 3000, cond: () -> Boolean) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline && !cond()) {
            Thread.sleep(10)
        }
        assertTrue("timeout waiting: $desc", cond())
    }

    private fun pushFrames(n: Int) {
        repeat(n) { i ->
            controller.onAiAudioPcm(ShortArray(320), 16_000, 16, 20, i.toLong(), "t1")
        }
    }

    private fun pushableDevice() =
        shadowOf(audioManager).setOutputDevices(listOf(outputDevice(0, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)))

    // ── D10 v3 冷启动默认与断连兜底（2026-10-11 用户定案；docs §6.1）─────

    private fun model(id: Int, type: Int) =
        AudioOutputDevice(id = id, type = type, productName = null, address = null)

    @Test
    fun `builtin-only cold start selects builtin speaker`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
        )
        shadowOf(audioManager).setOutputDevices(listOf(outputDevice(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)))
        controller = newController()   // 预置设备表后构造 → 初值规则生效

        assertEquals(3, controller.preferredOutputDeviceId.value)

        controller.start()
        awaitPlaying(true)
        verify { tracks.last().setPreferredDevice(any()) }   // 分类①：扬声器以 preferred 钉住
    }

    @Test
    fun `cold start with only classic bt selects sco over a2dp`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(4, AudioDeviceInfo.TYPE_BLUETOOTH_SCO),
            model(5, AudioDeviceInfo.TYPE_BLUETOOTH_A2DP),
        )
        controller = newController()

        assertEquals(4, controller.preferredOutputDeviceId.value)   // 分类②：SCO 优先
    }

    @Test
    fun `cold start with only a2dp selects a2dp`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(5, AudioDeviceInfo.TYPE_BLUETOOTH_A2DP),
        )
        controller = newController()

        assertEquals(5, controller.preferredOutputDeviceId.value)   // 分类②：无 SCO → A2DP
    }

    @Test
    fun `cold start picks lowest-id sco among multiple bt devices`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(9, AudioDeviceInfo.TYPE_BLUETOOTH_SCO),
            model(7, AudioDeviceInfo.TYPE_BLUETOOTH_SCO),
            model(4, AudioDeviceInfo.TYPE_BLUETOOTH_A2DP),   // id 更小，但 SCO 优先
        )
        controller = newController()

        assertEquals(7, controller.preferredOutputDeviceId.value)
    }

    @Test
    fun `cold start ble device counts as non-bt external`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(26, AudioDeviceInfo.TYPE_BLE_HEADSET),   // LE Audio 本版不考虑 → 分类③
        )
        controller = newController()

        assertNull(controller.preferredOutputDeviceId.value)
    }

    @Test
    fun `cold start with non-bt external keeps system default`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(4, AudioDeviceInfo.TYPE_USB_DEVICE),
        )
        controller = newController()

        assertNull(controller.preferredOutputDeviceId.value)   // 分类③

        controller.start()
        awaitPlaying(true)
        verify(exactly = 0) { tracks.last().setPreferredDevice(any()) }   // 不指定，交还平台
    }

    @Test
    fun `selected device disappearance while stopped only updates selection`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(4, AudioDeviceInfo.TYPE_USB_DEVICE),
        )
        controller = newController()
        controller.setPreferredOutputDevice(4)   // 未播放仅记录

        outputDevicesFlow.value = listOf(        // USB 拔掉：列表回到仅内置
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
        )
        awaitCondition("selection falls back to speaker 3") { controller.preferredOutputDeviceId.value == 3 }

        assertEquals(0, factoryCalls)            // 未播放不重建
    }

    @Test
    fun `selected device disappearance while playing falls back to speaker and rebuilds`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(4, AudioDeviceInfo.TYPE_USB_DEVICE),
        )
        shadowOf(audioManager).setOutputDevices(
            listOf(
                outputDevice(4, AudioDeviceInfo.TYPE_USB_DEVICE),
                outputDevice(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            ),
        )
        controller = newController()
        controller.start()
        awaitPlaying(true)

        controller.setPreferredOutputDevice(4)   // 播放中选中 USB（重建）
        awaitFactoryCalls(2)

        outputDevicesFlow.value = listOf(        // USB 拔掉（shadow 同步移除）
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
        )
        shadowOf(audioManager).setOutputDevices(listOf(outputDevice(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)))

        awaitCondition("selection falls back to speaker 3") { controller.preferredOutputDeviceId.value == 3 }
        awaitFactoryCalls(3)                     // 播放中立即重建生效
        verify { tracks.last().setPreferredDevice(any()) }
    }

    @Test
    fun `bt sco disconnected while a2dp remains falls back to speaker`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(4, AudioDeviceInfo.TYPE_BLUETOOTH_SCO),
            model(5, AudioDeviceInfo.TYPE_BLUETOOTH_A2DP),
        )
        controller = newController()
        assertEquals(4, controller.preferredOutputDeviceId.value)   // 冷启动选中 SCO

        outputDevicesFlow.value = listOf(          // SCO 单链瞬断：A2DP 尚存 → 按规则字面回扬声器
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(5, AudioDeviceInfo.TYPE_BLUETOOTH_A2DP),
        )
        awaitCondition("falls back to speaker despite a2dp present") {
            controller.preferredOutputDeviceId.value == 3
        }

        outputDevicesFlow.value = listOf(          // 第二拍：A2DP 也消失（非所选设备）→ 不动
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
        )
        Thread.sleep(200)

        assertEquals(3, controller.preferredOutputDeviceId.value)
        assertEquals(0, factoryCalls)
    }

    @Test
    fun `cold start bt removal while playing rebuilds to speaker`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(4, AudioDeviceInfo.TYPE_BLUETOOTH_SCO),
        )
        shadowOf(audioManager).setOutputDevices(
            listOf(
                outputDevice(4, AudioDeviceInfo.TYPE_BLUETOOTH_SCO),
                outputDevice(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            ),
        )
        controller = newController()
        assertEquals(4, controller.preferredOutputDeviceId.value)
        controller.start()                                     // 冷启动已选 SCO → 首会话即钉住
        awaitPlaying(true)
        verify { tracks.last().setPreferredDevice(any()) }

        outputDevicesFlow.value = listOf(                      // 蓝牙断开
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
        )
        awaitCondition("falls back to speaker") { controller.preferredOutputDeviceId.value == 3 }
        awaitFactoryCalls(2)                                   // start(1) + 回落重建(2)
        verify { tracks.last().setPreferredDevice(any()) }
    }

    @Test
    fun `device addition does not change selection`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
        )
        controller = newController()
        assertEquals(3, controller.preferredOutputDeviceId.value)   // 分类①：仅内置 → 扬声器

        outputDevicesFlow.value = listOf(                            // 插入外设（增加不触发）
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(4, AudioDeviceInfo.TYPE_USB_DEVICE),
        )
        Thread.sleep(200)

        assertEquals(3, controller.preferredOutputDeviceId.value)    // 保持当前选择
        assertEquals(0, factoryCalls)
    }

    @Test
    fun `system default selection unchanged by other device removal`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(4, AudioDeviceInfo.TYPE_USB_DEVICE),
        )
        controller = newController()
        assertNull(controller.preferredOutputDeviceId.value)   // 分类③

        outputDevicesFlow.value = listOf(                      // 非所选设备移除：一律不动
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
        )
        Thread.sleep(200)

        assertNull(controller.preferredOutputDeviceId.value)
        assertEquals(0, factoryCalls)
    }

    @Test
    fun `rebuild to vanished device falls back to speaker`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            // id=5 在可见列表内（观察协程静默 → 无双重建竞争），但 shadow 中不存在 → 解析失败路径
            model(5, AudioDeviceInfo.TYPE_USB_DEVICE),
        )
        shadowOf(audioManager).setOutputDevices(listOf(outputDevice(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER)))
        controller = newController()
        controller.start()                        // 分类③系统默认：不设 preferred
        awaitPlaying(true)

        controller.setPreferredOutputDevice(5)    // 解析不到（shadow 无 5）→ 兜底扬声器
        awaitCondition("fallback to speaker 3") { controller.preferredOutputDeviceId.value == 3 }
        awaitFactoryCalls(2)                      // start(1) + 重建(2)
        verify { tracks.last().setPreferredDevice(any()) }
    }

    // ── Gallery 当前输出设备（docs/android_audio_output_devices.md §4.7/§6.1）──────

    @Test
    fun `currentOutputDevice returns selected device when resolvable`() {
        outputDevicesFlow.value = listOf(
            model(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
        )
        shadowOf(audioManager).setOutputDevices(
            listOf(
                outputDevice(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE),
                outputDevice(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            )
        )
        controller.setPreferredOutputDevice(2)

        assertEquals(2, controller.currentOutputDevice()?.id)
    }

    @Test
    fun `currentOutputDevice falls back to speaker when selected device is missing`() {
        outputDevicesFlow.value = listOf(
            model(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER),
            model(5, AudioDeviceInfo.TYPE_USB_DEVICE),
        )
        shadowOf(audioManager).setOutputDevices(
            listOf(outputDevice(3, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER))
        )
        controller.setPreferredOutputDevice(5)   // 记录但 shadow 中不存在

        assertEquals(3, controller.currentOutputDevice()?.id)
    }

    @Test
    fun `currentOutputDevice returns null when selected missing and no speaker`() {
        outputDevicesFlow.value = listOf(model(5, AudioDeviceInfo.TYPE_USB_DEVICE))
        shadowOf(audioManager).setOutputDevices(emptyList())
        controller.setPreferredOutputDevice(5)

        assertNull(controller.currentOutputDevice())
    }

    @Test
    fun `currentOutputDevice falls back to routed device when selected missing and no speaker`() {
        outputDevicesFlow.value = listOf(model(5, AudioDeviceInfo.TYPE_USB_DEVICE))
        shadowOf(audioManager).setOutputDevices(
            listOf(outputDevice(5, AudioDeviceInfo.TYPE_USB_DEVICE))
        )
        controller = newController()
        controller.setPreferredOutputDevice(5)    // 未播放：仅记录，start 时应用
        controller.start()
        awaitPlaying(true)                        // 单一 Track，无 rebuild 时序依赖

        // 解析时设备已不可见；outputDevicesFlow 仍无扬声器 → 继续落到 Track routedDevice
        shadowOf(audioManager).setOutputDevices(emptyList())
        every { tracks.last().routedDevice } returns
            outputDevice(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE)

        assertEquals(2, controller.currentOutputDevice()?.id)
    }

    @Test
    fun `currentOutputDevice uses routed device when preference is null`() {
        outputDevicesFlow.value = listOf(model(5, AudioDeviceInfo.TYPE_USB_DEVICE))
        shadowOf(audioManager).setOutputDevices(
            listOf(outputDevice(5, AudioDeviceInfo.TYPE_USB_DEVICE))
        )
        controller = newController()
        assertNull(controller.preferredOutputDeviceId.value)   // D10 分类③：系统默认

        controller.start()
        awaitPlaying(true)
        every { tracks.last().routedDevice } returns
            outputDevice(2, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE)

        assertEquals(2, controller.currentOutputDevice()?.id)
    }

    @Test
    fun `currentOutputDevice tolerates released track routed device`() {
        outputDevicesFlow.value = listOf(model(5, AudioDeviceInfo.TYPE_USB_DEVICE))
        shadowOf(audioManager).setOutputDevices(
            listOf(outputDevice(5, AudioDeviceInfo.TYPE_USB_DEVICE))
        )
        controller = newController()
        controller.start()
        awaitPlaying(true)
        every { tracks.last().routedDevice } throws IllegalStateException("released")

        assertNull(controller.currentOutputDevice())
    }

    // ── 记录与生效 ─────────────────────────────────────────────────────

    @Test
    fun `record only when not playing and applies on next start`() {
        pushableDevice()

        controller.setPreferredOutputDevice(0)
        assertEquals(0, factoryCalls)   // 未播放不重建
        assertEquals(0, controller.preferredOutputDeviceId.value)

        controller.start()
        awaitPlaying(true)

        assertEquals(1, factoryCalls)
        verify { tracks.last().setPreferredDevice(any()) }
    }

    @Test
    fun `switching while playing rebuilds and applies to new track`() {
        pushableDevice()
        controller.start()
        awaitPlaying(true)
        assertEquals(1, factoryCalls)

        controller.setPreferredOutputDevice(0)
        awaitFactoryCalls(2)
        awaitPlaying(true)

        assertEquals(2, factoryCalls)
        verify { tracks.last().setPreferredDevice(any()) }
        assertEquals(0, controller.preferredOutputDeviceId.value)
    }

    @Test
    fun `selecting same device id again is a no-op`() {
        pushableDevice()
        controller.start()
        awaitPlaying(true)
        controller.setPreferredOutputDevice(0)
        awaitFactoryCalls(2)
        awaitPlaying(true)

        controller.setPreferredOutputDevice(0)
        Thread.sleep(100)

        assertEquals(2, factoryCalls)
        assertEquals(0, controller.preferredOutputDeviceId.value)
    }

    // ── 跨重建不丢帧（确定性方案：write 冻结 → 推帧 → 切换 → 放闸）──

    @Test
    fun `jitter buffer frames survive rebuild without drops`() {
        pushableDevice()
        controller.start()
        awaitPlaying(true)

        writeGate = CountDownLatch(1)
        pushFrames(5)
        awaitFactoryCalls(1)
        Thread.sleep(120)   // 首帧被 write 挂起；等待其弹出后再测）
        val sizeBefore = controller.jitterBuffer.size()
        assertTrue("scenario requires buffered frames, got $sizeBefore", sizeBefore > 0)

        controller.setPreferredOutputDevice(0)   // 重建协程排队（dispatcher 被 write 占住）
        assertEquals(1, factoryCalls)            // 尚未真正重建
        assertEquals(0, writtenFrames)           // 场景锁：首帧仍挂在闸上（write 未完成不计）

        writeGate!!.countDown()                  // 放闸：旧 loop 收敛 → 重建执行
        awaitFactoryCalls(2)
        awaitPlaying(true)

        // 不丢帧的确定性锁：推入 5 帧 → 新旧两 track 写入数之和 == 5。
        // 对 reset() 免疫失效：dropLate/dropOverflow 会被误调的 jitterBuffer.reset() 清零（正是本用例要防
        // 的回归，旧断言可被掩盖）；写入计数只看"帧是否到达任一 track"——被 reset 静默丢弃则和 < 5，守卫变红
        awaitCondition("all 5 frames written across tracks") { writtenFrames == 5 }
        assertEquals(5, writtenFrames)

        // 未丢帧：缓冲区无迟到/溢出丢弃；新 track 携带偏好
        assertEquals(0, controller.jitterBuffer.droppedLate)
        assertEquals(0, controller.jitterBuffer.droppedOverflow)
        verify { tracks.last().setPreferredDevice(any()) }
    }

    // ── 静音重放（setVolume 为 track 实例属性，§5.4 二轮发现）──────────

    @Test
    fun `muted state is replayed on the rebuilt track`() {
        pushableDevice()
        controller.start()
        awaitPlaying(true)
        controller.setMuted(true)

        controller.setPreferredOutputDevice(0)
        awaitFactoryCalls(2)
        awaitPlaying(true)

        verify { tracks.last().setVolume(0f) }
    }

    @Test
    fun `muted state is replayed on start`() {
        pushableDevice()
        controller.setMuted(true)   // 上个会话的静音（单例状态跨会话存活，无重置点）

        controller.start()
        awaitPlaying(true)

        verify { tracks.last().setVolume(0f) }   // 新 track 以重放恢复静音
    }

    // ── 三类"设备不可用"回落 ───────────────────────────────────────────

    @Test
    fun `unresolvable device id clears selection`() {
        // shadow 无任何输出设备
        controller.start()
        awaitPlaying(true)

        controller.setPreferredOutputDevice(123)
        awaitFactoryCalls(2)
        awaitPlaying(true)

        assertNull(controller.preferredOutputDeviceId.value)
        assertEquals(2, factoryCalls)
        verify(exactly = 0) { tracks.last().setPreferredDevice(any()) }
    }

    @Test
    fun `platform rejecting preferred device falls back silently`() {
        pushableDevice()
        controller.start()
        awaitPlaying(true)
        preferredReturns = false   // 之后新建的 track：平台拒绝

        controller.setPreferredOutputDevice(0)
        awaitFactoryCalls(2)
        awaitPlaying(true)

        assertNull(controller.preferredOutputDeviceId.value)
        verify { tracks.last().setPreferredDevice(any()) }
    }

    @Test
    fun `throwing setPreferredDevice falls back silently`() {
        pushableDevice()
        controller.start()
        awaitPlaying(true)
        preferredThrows = true

        controller.setPreferredOutputDevice(0)
        awaitFactoryCalls(2)
        awaitPlaying(true)

        assertNull(controller.preferredOutputDeviceId.value)
        verify { tracks.last().setPreferredDevice(any()) }
    }

    // ── 构建失败回退与终态收敛 ────────────────────────────────────────

    @Test
    fun `rebuild construction failure falls back to previous id`() {
        pushableDevice()
        controller.start()
        awaitPlaying(true)
        controller.setPreferredOutputDevice(0)
        awaitFactoryCalls(2)
        awaitPlaying(true)

        createFailures = 1   // 下一次 create 抛（新目标构建失败一次）
        controller.setPreferredOutputDevice(123)
        awaitFactoryCalls(4)
        awaitPlaying(true)

        // 回退上一 id（0）再建成功：选择回到 0，播放存活
        assertEquals(0, controller.preferredOutputDeviceId.value)
        assertTrue(controller.isPlaying.value)
    }

    @Test
    fun `both rebuild attempts failing converge to terminal state`() {
        pushableDevice()
        controller.start()
        awaitPlaying(true)

        createFailures = 2   // 目标与回退都构建失败
        controller.setPreferredOutputDevice(0)
        awaitPlaying(false)

        assertNull(controller.preferredOutputDeviceId.value)
        assertTrue(factoryCalls <= 3)   // start(1) + 目标(1) + 回退(1)；无第三次
    }

    // ── stop × rebuild 竞争收敛（无僵尸 loop）─────────────────────────

    @Test
    fun `stop during rebuild prevents zombie loop`() {
        pushableDevice()
        controller.start()
        awaitPlaying(true)

        writeGate = CountDownLatch(1)
        pushFrames(3)
        Thread.sleep(120)   // 旧 loop 冻结在 write

        controller.setPreferredOutputDevice(0)   // 重建排队（dispatcher 被占）
        controller.stop()                        // 立即 stop：rebuildJob 被取消

        writeGate!!.countDown()                  // 放闸让旧 loop 收敛、排队的重建协程被取消不执行
        Thread.sleep(200)

        assertEquals(1, factoryCalls)   // 重建从未真正创建 track（僵尸 loop 无源）
        assertTrue(!controller.isPlaying.value)
    }

    // ── routed device 日志取点（play() 之后；静音期不出现）────────────

    @Test
    fun `routed device log appears only after play`() {
        ShadowLog.clear()
        pushableDevice()
        controller.start()
        awaitPlaying(true)

        Thread.sleep(150)   // 无帧：loop 停在初缓冲，未 play
        assertTrue(
            "no routed-device log expected before play",
            ShadowLog.getLogs().none { it.tag == "LivaChat-Audio" && it.msg.startsWith("routed device") },
        )

        pushFrames(4)       // 初缓冲满足 → play → 打点
        val deadline = System.currentTimeMillis() + 3000
        while (System.currentTimeMillis() < deadline &&
            ShadowLog.getLogs().none { it.tag == "LivaChat-Audio" && it.msg.startsWith("routed device") }
        ) {
            Thread.sleep(20)
        }
        assertTrue(
            "routed-device log expected after play",
            ShadowLog.getLogs().any { it.tag == "LivaChat-Audio" && it.msg.startsWith("routed device") },
        )
    }
}
