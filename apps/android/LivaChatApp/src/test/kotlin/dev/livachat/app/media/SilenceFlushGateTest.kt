package dev.livachat.app.media

import org.junit.Assert.assertEquals
import org.junit.Test

class SilenceFlushGateTest {
    private val gate = SilenceFlushGate()

    @Test
    fun unmutedAlwaysSendsReal() {
        repeat(10) {
            assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        }
    }

    @Test
    fun freshGateWithoutBaselineArmsWindowOnFirstMutedFrame() {
        // 原始行为:未校准基线时,首个 muted 帧本身构成 mute 沿
        repeat(50) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        assertEquals(FrameAction.DROP, gate.decide(true))
    }

    @Test
    fun loopStartWhileMutedDoesNotTriggerWindow() {
        // 会话开始/重启时已处于静音(无新 mute 事件)→ 不补发
        gate.resetBaseline(mutedNow = true)
        repeat(10) {
            assertEquals(FrameAction.DROP, gate.decide(true))
        }
    }

    @Test
    fun muteEdgeArmsWindowThenDropsAfter50() {
        // 控制器真实时序:未静音跑了一段时间,然后 setMuted(true)
        repeat(5) {
            assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        }
        repeat(50) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        assertEquals(FrameAction.DROP, gate.decide(true))
    }

    @Test
    fun unmuteResumesRealAndNextMuteArmsFreshWindow() {
        repeat(5) {
            assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        }
        repeat(10) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        // setMuted(false):下帧恢复真实上行
        assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        repeat(5) {
            assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        }
        // 再次 setMuted(true):重新完整窗口
        repeat(50) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        assertEquals(FrameAction.DROP, gate.decide(true))
    }

    @Test
    fun rapidMuteUnmuteMuteWithinOneFrameTickKeepsTotalSilenceWindow() {
        // 快速 toggle:unmute 沿未被读帧周期观察到 → 不重新武装也不归零,
        // 总静音时长仍恰好 50 帧(连续静音不被分割)
        repeat(3) {
            assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        }
        repeat(30) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        repeat(20) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        assertEquals(FrameAction.DROP, gate.decide(true))
    }

    @Test
    fun loopRestartMidWindowContinuesRemainingFrames() {
        // handleReadError 重开读循环(仍处于静音):剩余帧续发,不重复整窗、不归零
        repeat(3) {
            assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        }
        repeat(10) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        gate.resetBaseline(mutedNow = true)
        repeat(40) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        assertEquals(FrameAction.DROP, gate.decide(true))
    }

    @Test
    fun loopRestartAfterWindowConsumedWhileMutedStaysDropping() {
        // 会话结束在窗口耗尽之后、重启时仍静音 → 不产生新窗口
        repeat(3) {
            assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        }
        repeat(50) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        assertEquals(FrameAction.DROP, gate.decide(true))
        gate.resetBaseline(mutedNow = true)
        assertEquals(FrameAction.DROP, gate.decide(true))
    }

    @Test
    fun sessionStopDropsStaleWindowAcrossRestart() {
        // stop()(会话 teardown)清空窗口;重启后仍静音 → 不续发旧会话残留
        repeat(3) {
            assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        }
        repeat(10) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        gate.reset()
        gate.resetBaseline(mutedNow = true)
        assertEquals(FrameAction.DROP, gate.decide(true))
        assertEquals(FrameAction.DROP, gate.decide(true))
    }

    @Test
    fun loopRestartAfterUnmuteResetsCleanly() {
        // 重启时已 unmute:清零残留,后续 mute 沿武装完整窗口
        repeat(3) {
            assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        }
        repeat(20) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        gate.resetBaseline(mutedNow = false)
        repeat(3) {
            assertEquals(FrameAction.SEND_REAL, gate.decide(false))
        }
        repeat(50) {
            assertEquals(FrameAction.SEND_SILENCE, gate.decide(true))
        }
        assertEquals(FrameAction.DROP, gate.decide(true))
    }
}
