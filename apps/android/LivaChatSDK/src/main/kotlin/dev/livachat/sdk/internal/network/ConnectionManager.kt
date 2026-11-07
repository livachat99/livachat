package dev.livachat.sdk.internal.network

import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.internal.util.SdkLogger
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

internal class ConnectionManager(
    private val serverUrl: String,
    private val token: String,
    private val deviceId: String,
    private val reconnectConfig: ReconnectConfig = ReconnectConfig(),
    private val keepaliveIntervalS: Int = 15,
    private val keepaliveTimeoutS: Int = 35,
    private val allowSelfSigned: Boolean = false,
    private val clock: () -> Long = { System.currentTimeMillis() },
) {

    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<ConnectionState> = _connectionState.asStateFlow()

    // 连接代际：每次成功建连（onOpen）自增。单调不回退 ⇒ StateFlow 值回归去重无法吞并
    //（app 自动恢复的可靠触发信号）。
    private val _connectionEpoch = MutableStateFlow(0L)
    val connectionEpoch: StateFlow<Long> = _connectionEpoch.asStateFlow()

    @Volatile private var webSocket: WebSocket? = null
    private var client: OkHttpClient? = null

    // 每建一次 socket 自增，回调按建号过滤：旧 socket 的迟到回调（close/failure 在网络
    // 迟滞下晚于重连落地）不得改新连接状态、也不得触发多余重连（disconnect→connect
    // 背靠背时同一 RTT 量级，真实竞态）
    private val socketGeneration = AtomicInteger(0)

    private var messageHandler: ((String) -> Unit)? = null
    private var pingSender: (() -> Unit)? = null

    private val reconnectScheduler = ReconnectScheduler(reconnectConfig, clock)
    private val heartbeatScheduler = HeartbeatScheduler(
        keepaliveIntervalMs = keepaliveIntervalS * 1_000L,
        keepaliveTimeoutMs = keepaliveTimeoutS * 1_000L,
        clock = clock,
    )

    @Volatile private var shutdown = false
    @Volatile private var reconnecting = false
    @Volatile private var reconnectScheduled = false
    @Volatile private var keepaliveTimeoutLogged = false
    // 发送失败已告警：同一失败段只记一次（成功发送后复位），
    // 否则 audio 50Hz 连续失败会把 logcat 淹没
    @Volatile private var sendDroppedLogged = false

    // disconnect() 会 shutdownNow 该池；同一实例可再次 connect()（用户进出会话页），
    // 故 connect() 必须在池已终止时换新实例，否则后续 scheduleReconnect 抛
    // RejectedExecutionException → 永远不再重连（且异常会打断 OkHttp/心跳线程）
    @Volatile private var reconnectExecutor = newReconnectExecutor()
    private var heartbeatThread: Thread? = null

    // ── Public API ────────────────────────────────────────────────

    fun connect() {
        shutdown = false
        reconnecting = false
        reconnectScheduled = false
        if (reconnectExecutor.isShutdown) {
            reconnectExecutor = newReconnectExecutor()
        }
        // 手动 connect = 新一轮：清零退避计数（attempt 只在 onOpen 清零，跨会话残留会导致
        // 上一轮耗尽重试后新连接「一次重试都不给」）
        reconnectScheduler.reset()
        _connectionState.value = ConnectionState.CONNECTING
        connectInternal()
    }

    fun disconnect() {
        shutdown = true
        reconnecting = false
        reconnectScheduled = false
        stopHeartbeatLoop()
        reconnectExecutor.shutdownNow()
        webSocket?.close(1000, "Client disconnect")
        _connectionState.value = ConnectionState.DISCONNECTED
    }

    fun send(message: String): Boolean {
        val ws = webSocket ?: return drop("no websocket")
        if (_connectionState.value != ConnectionState.CONNECTED) {
            return drop("state=${_connectionState.value}")
        }
        val result = ws.send(message)
        if (!result) return drop("ws send queue full")
        if (sendDroppedLogged) {
            sendDroppedLogged = false
            SdkLogger.i(TAG, "send resumed | drops ended")
        }
        // 注意：出站流量不参与心跳判据（入站静默才探测）——持续推流时也必须能发 ping，
        // 否则单向流场景下会被 35s 超时判死重连（HeartbeatScheduler KDoc）
        return true
    }

    /** 静默丢帧点打点：同段失败只记首条（含原因），音频 50Hz 重试不再重复刷屏。 */
    private fun drop(reason: String): Boolean {
        if (!sendDroppedLogged) {
            sendDroppedLogged = true
            SdkLogger.w(TAG, "send dropped | $reason")
        }
        return false
    }

    fun onMessage(handler: (String) -> Unit) {
        messageHandler = handler
    }

    fun onPingSender(sender: () -> Unit) {
        pingSender = sender
    }

    // ── Internals ─────────────────────────────────────────────────

    private fun connectInternal() {
        val url = "$serverUrl?token=$token&device_id=$deviceId"
        val request = Request.Builder().url(url).build()

        webSocket?.close(1000, "Reconnect")

        val builder = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .pingInterval(0, TimeUnit.MILLISECONDS)

        if (allowSelfSigned) {
            val trustAllCerts = arrayOf<TrustManager>(object : X509TrustManager {
                override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
                override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
                override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
            })
            val sslContext = SSLContext.getInstance("TLS").apply {
                init(null, trustAllCerts, SecureRandom())
            }
            builder
                .sslSocketFactory(sslContext.socketFactory, trustAllCerts[0] as X509TrustManager)
                .hostnameVerifier { _, _ -> true }
        }

        client = builder.build()

        val generation = socketGeneration.incrementAndGet()
        webSocket = client!!.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                if (generation != socketGeneration.get()) return
                SdkLogger.i(TAG, "ws_open | ${response.protocol}")
                keepaliveTimeoutLogged = false
                _connectionState.value = ConnectionState.CONNECTED
                // 顺序敏感：先置 CONNECTED
                // 再增代际——observer 以代际为触发、唤醒后读状态做门控，反序会读到 CONNECTING
                // 而漏发（代际已消费不会重发）
                _connectionEpoch.update { it + 1 }
                reconnectScheduler.reset()
                reconnecting = false
                reconnectScheduled = false
                heartbeatScheduler.start()
                startHeartbeatLoop()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                if (generation != socketGeneration.get()) return
                keepaliveTimeoutLogged = false
                heartbeatScheduler.onInbound()
                messageHandler?.invoke(text)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                if (generation != socketGeneration.get()) return
                SdkLogger.d(TAG, "ws_closing | code=$code | $reason")
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (generation != socketGeneration.get()) return
                SdkLogger.i(TAG, "ws_closed | code=$code | $reason")
                stopHeartbeatLoop()
                if (!shutdown && code != 1000) {
                    if (code == CLOSE_AUTH_FAILED) {
                        failTerminal("close 4001")
                    } else {
                        scheduleReconnect()
                    }
                } else if (!reconnecting) {
                    _connectionState.value = ConnectionState.DISCONNECTED
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (generation != socketGeneration.get()) return
                SdkLogger.w(TAG, "ws_failure | ${t.message}", t)
                stopHeartbeatLoop()
                if (shutdown) return
                if (response?.code == HTTP_UNAUTHORIZED) {
                    failTerminal("HTTP 401")
                } else {
                    scheduleReconnect()
                }
            }
        })
    }

    // ── Reconnect scheduling ──────────────────────────────────────

    private fun scheduleReconnect() {
        // 入口即捕获池引用：此后 disconnect() 只能 shutdown 这个（旧）实例——即便
        // disconnect()（shutdownNow 旧池）+ connect()（换入新池）恰好落在下面的
        // shutdown 检查与 schedule 之间，排的也是旧池，必抛 RejectedExecutionException
        // 走安全分支；绝不会把任务排进新池（否则会延迟触发 connectInternal()，把刚
        // 手动建立的连接以 1000 关掉重连一次）
        val pool = reconnectExecutor
        if (shutdown || reconnectScheduled) return
        reconnectScheduled = true
        reconnecting = true
        _connectionState.value = ConnectionState.RECONNECTING

        val delay = reconnectScheduler.nextDelay()
        if (delay == ReconnectScheduler.STOP) {
            _connectionState.value = ConnectionState.DISCONNECTED
            reconnecting = false
            reconnectScheduled = false
            return
        }

        try {
            pool.schedule(
                {
                    reconnectScheduled = false
                    if (!shutdown) connectInternal()
                },
                delay,
                TimeUnit.MILLISECONDS,
            )
        } catch (_: RejectedExecutionException) {
            // 与 disconnect()（用户主动断开 → shutdownNow）竞态：按用户意图停止重连，
            // 不让异常穿出 OkHttp 回调/心跳线程
            reconnectScheduled = false
            reconnecting = false
            // 用户已断开且此后未再 connect → 复位状态（否则 UI 停在"重连中"且永不重连）
            if (shutdown) {
                _connectionState.value = ConnectionState.DISCONNECTED
            }
        }
    }

    // ── Heartbeat loop ────────────────────────────────────────────

    private fun startHeartbeatLoop() {
        stopHeartbeatLoop()
        heartbeatThread = Thread {
            while (!shutdown && _connectionState.value == ConnectionState.CONNECTED) {
                try {
                    Thread.sleep(HEARTBEAT_CHECK_INTERVAL_MS)
                } catch (_: InterruptedException) {
                    return@Thread
                }
                // 单次 tick 抛异常不得杀死循环：线程一死，ping 与 keepalive 超时判定
                // （同在此循环）双双停摆，连接假死且永不重连
                try {
                    checkHeartbeat()
                } catch (e: Exception) {
                    SdkLogger.e(TAG, "heartbeat tick failed | ${e.message}", e)
                }
            }
        }.apply {
            name = "LivaChat-Heartbeat"
            isDaemon = true
            start()
        }
    }

    private fun stopHeartbeatLoop() {
        heartbeatThread?.interrupt()
        heartbeatThread = null
    }

    internal fun checkHeartbeat() {
        if (heartbeatScheduler.isTimeout()) {
            if (!keepaliveTimeoutLogged) {
                keepaliveTimeoutLogged = true
                SdkLogger.i(TAG, "keepalive_timeout | no inbound for ${keepaliveTimeoutS}s")
            }
            // 判定连接死亡：停心跳循环、关闭 socket（非 1000 码），走重连路径
            stopHeartbeatLoop()
            webSocket?.close(4000, "keepalive timeout")
            if (!shutdown) {
                scheduleReconnect()
            }
            return
        }
        keepaliveTimeoutLogged = false
        if (heartbeatScheduler.shouldSendPing()) {
            pingSender?.invoke()
            heartbeatScheduler.onPingSent()
        }
    }

    /**
     * 认证拒绝终态（HTTP 401 / 服务端 4001 关闭码）：token / device_id 非法时重试必仍被拒 →
     * **不退避重连**，直接落 DISCONNECTED（docs/setting_server_address.md §8）。手动 connect()
     * （状态指示器重连 / 重进会话页）会重置退避计数，修正配置后重启进程即可恢复。
     */
    private fun failTerminal(reason: String) {
        SdkLogger.w(TAG, "ws_auth_failed | $reason — terminal, no retry")
        reconnecting = false
        reconnectScheduled = false
        _connectionState.value = ConnectionState.DISCONNECTED
    }

    companion object {
        private const val TAG = "WS"
        private const val HEARTBEAT_CHECK_INTERVAL_MS = 1_000L
        private const val HTTP_UNAUTHORIZED = 401
        private const val CLOSE_AUTH_FAILED = 4001

        private fun newReconnectExecutor(): ScheduledExecutorService =
            Executors.newSingleThreadScheduledExecutor { r ->
                Thread(r, "LivaChat-Reconnect").apply { isDaemon = true }
            }
    }
}
