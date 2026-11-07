package dev.livachat.sdk.internal.session

import dev.livachat.protocol.generated.CameraFacing
import dev.livachat.protocol.generated.ClientAudioPayload
import dev.livachat.protocol.generated.ClientSessionStartConfig
import dev.livachat.protocol.generated.ClientVideoPayload
import dev.livachat.protocol.generated.VadSensitivity
import dev.livachat.protocol.generated.VadState
import dev.livachat.sdk.internal.protocol.MessageCodec
import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.AiImage
import dev.livachat.sdk.model.InterruptReason
import dev.livachat.sdk.model.PlaybackFlushReason
import dev.livachat.sdk.model.SessionEndReason
import org.assertj.core.api.Assertions.assertThat
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicLong

/**
 * C2S 发送序回归（对应服务端 InboundSeqGuard：services/gateway/seq.py）。
 *
 * 不变量：**除 video 外**，多线程并发发送时，帧交给传输层的顺序必须与 seq 取号顺序一致
 * ——否则服务端判定 seq 回退并丢弃该帧（interrupt / session.change 丢失则客户端意图落空）。
 * video 走闸口外取号+编码的宽容路径（大帧编码不阻塞 50Hz 音频），其 seq 允许小于已上线的
 * 音频帧——服务端记 `seq regression tolerated` 后照常处理，不丢帧；本侧只锁"全量 seq 不重复"。
 *
 * 用例在 sendRaw 内人为 sleep 放大"取号→发送"窗口：闸口不变量在修复前（取号在闸口外）会
 * 失败；video 宽容路径下非 video 部分仍必须严格递增。
 */
class SendOrderingTest {

    private val sent = CopyOnWriteArrayList<String>()
    private val clock = AtomicLong(1000)

    private fun newManager(sendRawDelayMs: Long = 0): SessionManager {
        val dispatcher = object : ListenerDispatcher {
            override fun onSessionReady(sessionId: String, sessionRestored: Boolean) {}
            override fun onSessionEnded(sessionId: String, reason: String) {}
            override fun onJobStatus(jobs: List<AgentJob>) {}
            override fun onTranscription(text: String, isFinal: Boolean, transcriptionId: String) {}
            override fun onAiText(
                text: String, messageId: String, index: Long, isFinal: Boolean,
                image: AiImage?, source: String?,
            ) {}
            override fun onAiAudio(data: String, codec: String, durationMs: Int) {}
            override fun onAiVideoAck(frameId: Long, ackFrameId: Long, processed: Boolean) {}
            override fun onError(code: Int, message: String, fatal: Boolean, recoverable: Boolean) {}
            override fun onSessionChanged(
                mode: String, myLanguage: String?, localLanguage: String?, usecase: String?,
            ) {}
            override fun onPlaybackShouldFlush(reason: PlaybackFlushReason) {}
        }
        return SessionManager(
            codec = MessageCodec,
            sendRaw = { json ->
                if (sendRawDelayMs > 0) Thread.sleep(sendRawDelayMs)
                sent.add(json)
            },
            listenerDispatcher = dispatcher,
            now = { clock.incrementAndGet() },
            configProvider = {
                ClientSessionStartConfig(
                    enableVideo = true,
                    audioSampleRate = 16000,
                    vadSensitivity = VadSensitivity.MEDIUM,
                    clientVersion = "1.0.0",
                    timezone = "Asia/Shanghai",
                )
            },
        )
    }

    private fun audioPayload() = ClientAudioPayload("ZGF0YQ==", "opus", 20, VadState.SPEECH)

    private fun videoPayload(frameId: Long) = ClientVideoPayload(
        data = "ZGF0YQ==", frameId = frameId, width = 640, height = 480,
        orientation = 0, cameraFacing = CameraFacing.FRONT, quality = 70,
    )

    /** S2C ping（handleInbound 直接分发，不经 ReorderBuffer）→ 触发客户端 pong。 */
    private fun s2cPing(seq: Long) = """{"type":"ping","seq":$seq,"timestamp":1000,"payload":{}}"""

    private fun seqOf(json: String): Long = SEQ_RE.find(json)!!.groupValues[1].toLong()

    private fun tsOf(json: String): Long = TS_RE.find(json)!!.groupValues[1].toLong()

    private fun typeOf(json: String): String = TYPE_RE.find(json)!!.groupValues[1]

    @Test
    fun `concurrent senders keep non-video wire order identical to seq order`() {
        val perThread = 40
        val manager = newManager(sendRawDelayMs = 1)

        // 五类真实发送线程：audioSend(50Hz) / videoSend / heartbeat ping /
        // 主线程(session.change/interrupt) / OkHttp reader(pong)
        val threads = listOf(
            Thread { repeat(perThread) { manager.sendAudio(audioPayload()) } },
            Thread { repeat(perThread) { manager.sendVideo(videoPayload(it.toLong())) } },
            Thread { repeat(perThread) { manager.sendPing() } },
            Thread {
                repeat(perThread) { i ->
                    if (i % 2 == 0) manager.sendSessionChange("chat", null, null)
                    else manager.sendInterrupt(InterruptReason.USER_TAP)
                }
            },
            Thread { repeat(perThread) { manager.handleInbound(s2cPing(it + 1L)) } },
        )
        threads.forEach { it.start() }
        threads.forEach { it.join() }

        val total = perThread * threads.size
        assertThat(sent).hasSize(total)
        // 覆盖五类发送路径（防止用例空转）
        assertThat(sent.map { typeOf(it) }.toSet())
            .contains("audio", "video", "ping", "pong", "session.change", "interrupt")

        // 全量：seq 唯一（video 允许乱序，但绝不允许重复取号）
        assertThat(sent.map { seqOf(it) }.distinct()).hasSize(total)

        // 闸口不变量：非 video 帧上线序 == 取号序，严格递增（无重复 + 非降）
        val gated = sent.filter { typeOf(it) != "video" }
        val gatedSeqs = gated.map { seqOf(it) }
        assertThat(gatedSeqs).isSorted()
        assertThat(gatedSeqs.distinct()).hasSize(gated.size)

        // timestamp 与 seq 同刻取自闸口，非 video 同样严格递增
        val gatedTimestamps = gated.map { tsOf(it) }
        assertThat(gatedTimestamps).isSorted()
        assertThat(gatedTimestamps.distinct()).hasSize(gated.size)
    }

    @Test
    fun `sequential burst allocates and sends in identical order`() {
        val manager = newManager()

        manager.startSession()
        repeat(50) { manager.sendAudio(audioPayload()) }
        manager.endSession(SessionEndReason.USER_HANGUP)

        assertThat(sent).hasSize(52)
        assertThat(sent.map { seqOf(it) }).isEqualTo((1L..52L).toList())
        assertThat(sent.first()).contains(""""type":"session.start"""")
        assertThat(sent.last()).contains(""""type":"session.end"""")
    }

    @Test
    fun `server ping produces pong echoing ping_seq`() {
        val manager = newManager()

        manager.handleInbound(s2cPing(42))

        assertThat(sent).hasSize(1)
        assertThat(sent[0]).contains(""""type":"pong"""")
        assertThat(sent[0]).contains(""""ping_seq":42""")
    }

    private companion object {
        private val SEQ_RE = Regex(""""seq":(\d+)""")
        private val TS_RE = Regex(""""timestamp":(\d+)""")
        private val TYPE_RE = Regex(""""type":"([a-z._]+)"""")
    }
}
