package dev.livachat.sdk.internal.network

import dev.livachat.sdk.model.ConnectionState
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.assertj.core.api.Assertions.assertThat
import org.junit.After
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

class ConnectionManagerTest {

    private lateinit var mockServer: MockWebServer

    @Before
    fun setUp() {
        mockServer = MockWebServer()
        mockServer.start()
    }

    @After
    fun tearDown() {
        try {
            mockServer.shutdown()
        } catch (_: Exception) {
            // Server may already be shut down
        }
    }

    // ── Helpers ───────────────────────────────────────────────────

    private fun enqueueWebSocketUpgrade(listener: WebSocketListener = object : WebSocketListener() {}) {
        mockServer.enqueue(MockResponse().withWebSocketUpgrade(listener))
    }

    private fun createManager(
        reconnectConfig: ReconnectConfig = ReconnectConfig(),
        allowSelfSigned: Boolean = false,
        clock: () -> Long = { System.currentTimeMillis() },
    ): ConnectionManager {
        val url = mockServer.url("/v1/ws/stream").toString()
        return ConnectionManager(
            serverUrl = url,
            token = "test-token",
            deviceId = "test-device",
            reconnectConfig = reconnectConfig,
            allowSelfSigned = allowSelfSigned,
            clock = clock,
        )
    }

    private fun awaitState(manager: ConnectionManager, target: ConnectionState, timeoutMs: Long = 5_000) {
        runBlocking {
            withTimeout(timeoutMs) {
                manager.connectionState.first { it == target }
            }
        }
    }

    // ── Connect success ───────────────────────────────────────────

    @Test
    fun `connect transitions to CONNECTED on successful WebSocket upgrade`() {
        enqueueWebSocketUpgrade()
        val manager = createManager()
        manager.connect()
        awaitState(manager, ConnectionState.CONNECTED)
        // If we reach here without timeout, CONNECTED was emitted
    }

    // ── Send behavior ─────────────────────────────────────────────

    @Test
    fun `send returns false when disconnected`() {
        val manager = createManager()
        assertThat(manager.send("hello")).isFalse()
    }

    @Test
    fun `send returns true when connected`() {
        enqueueWebSocketUpgrade()
        val manager = createManager()
        manager.connect()
        awaitState(manager, ConnectionState.CONNECTED)
        assertThat(manager.send("hello")).isTrue()
    }

    // ── Normal close (code 1000) ──────────────────────────────────

    @Test
    fun `server close with code 1000 transitions to DISCONNECTED without reconnect`() {
        val serverOpenLatch = CountDownLatch(1)
        val serverListener = object : WebSocketListener() {
            override fun onOpen(webSocket: okhttp3.WebSocket, response: okhttp3.Response) {
                serverOpenLatch.countDown()
                webSocket.close(1000, "Normal closure")
            }
        }
        enqueueWebSocketUpgrade(serverListener)

        val manager = createManager()
        manager.connect()
        // Wait for server to receive the connection, then close it
        serverOpenLatch.await(5, TimeUnit.SECONDS)
        // Eventually state should become DISCONNECTED
        runBlocking {
            withTimeout(5_000L) {
                manager.connectionState.first { it == ConnectionState.DISCONNECTED }
            }
        }
    }

    // ── Abnormal close triggers reconnect ─────────────────────────

    @Test
    fun `server close with non-1000 code triggers RECONNECTING`() {
        val serverListener = object : WebSocketListener() {
            override fun onOpen(webSocket: okhttp3.WebSocket, response: okhttp3.Response) {
                webSocket.close(4000, "Custom close")
            }
        }
        enqueueWebSocketUpgrade(serverListener)

        val manager = createManager(
            reconnectConfig = ReconnectConfig(baseDelayMs = 10L, maxDelayMs = 100L, maxAttempts = 1, jitterMs = 0L)
        )
        manager.connect()
        runBlocking {
            withTimeout(5_000L) {
                manager.connectionState.first { it == ConnectionState.RECONNECTING }
            }
        }
    }

    // ── disconnect → connect 循环后仍可重连（重连线程池不得被永久 shutdown）──

    @Test
    fun `reconnect still works after disconnect-connect cycle`() {
        // 真实流程：用户离开会话页 → disconnect()（内部 shutdownNow 重连池）→
        // 再次进入 → connect() → 此后网络掉线必须**真的发起重连**（不只是置位
        // RECONNECTING 状态）。仅断言状态会漏掉"池已终止、调度被吞"的退化。
        enqueueWebSocketUpgrade()
        val manager = createManager(
            reconnectConfig = ReconnectConfig(baseDelayMs = 10L, maxDelayMs = 50L, maxAttempts = 1, jitterMs = 0L),
        )
        manager.connect()
        awaitState(manager, ConnectionState.CONNECTED)

        manager.disconnect()
        awaitState(manager, ConnectionState.DISCONNECTED)

        // 重进会话页；本次连接由服务端以非 1000 码掉线
        enqueueWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: okhttp3.WebSocket, response: okhttp3.Response) {
                webSocket.close(4000, "drop after reconnect cycle")
            }
        })
        manager.connect()
        awaitState(manager, ConnectionState.CONNECTED)

        // 重连尝试必须真的到达服务端：第三次 upgrade 的 onOpen 放行
        val retryOpenLatch = CountDownLatch(1)
        enqueueWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: okhttp3.WebSocket, response: okhttp3.Response) {
                retryOpenLatch.countDown()
            }
        })
        assertThat(retryOpenLatch.await(5, TimeUnit.SECONDS)).isTrue()
    }

    @Test
    fun `manual connect resets the backoff attempt counter`() {
        // 上一轮耗尽重试 → DISCONNECTED；用户手动 connect（重进会话页）后，
        // 新连接失败仍必须拥有完整重试预算（attempt 不得跨会话残留）。
        mockServer.enqueue(MockResponse().setResponseCode(503))
        mockServer.enqueue(MockResponse().setResponseCode(503))
        val manager = createManager(
            reconnectConfig = ReconnectConfig(baseDelayMs = 1L, maxDelayMs = 5L, maxAttempts = 1, jitterMs = 0L),
        )
        manager.connect()
        awaitState(manager, ConnectionState.DISCONNECTED, timeoutMs = 10_000)

        // 手动 connect：首个请求失败 → 必须还能进入重试并成功升级
        mockServer.enqueue(MockResponse().setResponseCode(503))
        val retryOpenLatch = CountDownLatch(1)
        enqueueWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: okhttp3.WebSocket, response: okhttp3.Response) {
                retryOpenLatch.countDown()
            }
        })
        manager.connect()
        assertThat(retryOpenLatch.await(5, TimeUnit.SECONDS)).isTrue()
    }

    @Test
    fun `back-to-back connect keeps the new socket authoritative`() {
        // 旧 socket 的迟到回调（close/failure 在网络迟滞下晚于新连接落地）不得改新连接
        // 状态、也不得触发多余重连（generation 守卫）。守卫只在时序交错时可观测——
        // 这里锁定「背靠背 connect 后状态仍为 CONNECTED 且发送可用」这一不变量。
        enqueueWebSocketUpgrade()
        val manager = createManager()
        manager.connect()
        awaitState(manager, ConnectionState.CONNECTED)

        enqueueWebSocketUpgrade()
        manager.connect()
        awaitState(manager, ConnectionState.CONNECTED)

        Thread.sleep(200) // 让旧 socket 的关闭回调落地
        assertThat(manager.connectionState.value).isEqualTo(ConnectionState.CONNECTED)
        assertThat(manager.send("probe")).isTrue()
    }

    // ── Heartbeat ping ────────────────────────────────────────────

    @Test
    fun `heartbeat triggers ping sender after keepalive interval`() {
        enqueueWebSocketUpgrade()
        var now = 0L
        val manager = createManager(clock = { now })
        val pingCount = AtomicInteger(0)

        manager.connect()
        awaitState(manager, ConnectionState.CONNECTED)
        manager.onPingSender { pingCount.incrementAndGet() }

        // Before 15s, no ping
        now = 14_000L
        manager.checkHeartbeat()
        assertThat(pingCount.get()).isEqualTo(0)

        // After 15s, ping is triggered
        now = 15_000L
        manager.checkHeartbeat()
        assertThat(pingCount.get()).isEqualTo(1)

        // Second call should NOT trigger another ping (onPingSent: 同一入站静默窗口只探测一次)
        manager.checkHeartbeat()
        assertThat(pingCount.get()).isEqualTo(1)
    }

    @Test
    fun `continuous outbound traffic does not suppress keepalive ping`() {
        // 真机复盘（2026-09-11）：推流（音频 50Hz 出站、服务端无回帧）时客户端曾因
        // "出站静默才 ping"永不探测 → 35s 无入站判死 → 每 ~36s 自杀重连。
        enqueueWebSocketUpgrade()
        var now = 0L
        val manager = createManager(clock = { now })
        val pingCount = AtomicInteger(0)

        manager.connect()
        awaitState(manager, ConnectionState.CONNECTED)
        manager.onPingSender { pingCount.incrementAndGet() }

        repeat(15) {
            now += 1_000L
            manager.send("audio-frame")   // 持续出站
            manager.checkHeartbeat()
        }
        assertThat(pingCount.get()).isEqualTo(1)   // 入站静默 15s → 必须探测

        now += 1_000L
        manager.checkHeartbeat()
        assertThat(pingCount.get()).isEqualTo(1)   // 同一静默窗口不重复探测
    }

    @Test
    fun `throwing ping sender does not kill the heartbeat loop`() {
        // 心跳线程内异常若穿出循环 → 线程静默死亡 → ping 与 keepalive 超时判定（同在
        // 该循环）双双停摆，连接假死且永不重连。锁定：单次 tick 抛异常后循环继续。
        enqueueWebSocketUpgrade()
        val now = AtomicLong(0)
        val manager = createManager(clock = { now.get() })
        val shouldThrow = AtomicBoolean(true)
        val pingCount = AtomicInteger(0)
        manager.onPingSender {
            if (shouldThrow.get()) throw IllegalStateException("boom")
            pingCount.incrementAndGet()
        }

        manager.connect()
        awaitState(manager, ConnectionState.CONNECTED)

        now.set(15_000L)      // ping 到期
        Thread.sleep(1_600)   // 保证至少一次 tick 在抛异常的 sender 下执行

        shouldThrow.set(false)
        val deadline = System.currentTimeMillis() + 5_000
        while (pingCount.get() == 0 && System.currentTimeMillis() < deadline) {
            Thread.sleep(100)
        }
        // 循环存活 → 下一次 tick（≤1s）重试 ping；线程已死则等不到
        assertThat(pingCount.get()).isEqualTo(1)
        manager.disconnect()
    }

    // ── Heartbeat timeout ─────────────────────────────────────────

    @Test
    fun `heartbeat timeout triggers RECONNECTING state`() {
        enqueueWebSocketUpgrade()
        var now = 0L
        val manager = createManager(
            clock = { now },
            reconnectConfig = ReconnectConfig(baseDelayMs = 10L, maxDelayMs = 100L, maxAttempts = 1, jitterMs = 0L),
        )

        manager.connect()
        awaitState(manager, ConnectionState.CONNECTED)

        // Simulate 35s of silence
        now = 35_000L
        manager.checkHeartbeat()

        runBlocking {
            withTimeout(5_000L) {
                manager.connectionState.first { it == ConnectionState.RECONNECTING }
            }
        }
    }

    // ── Message handler ───────────────────────────────────────────

    @Test
    fun `inbound messages are delivered to message handler`() {
        val serverListener = object : WebSocketListener() {
            override fun onOpen(webSocket: okhttp3.WebSocket, response: okhttp3.Response) {
                webSocket.send("hello from server")
            }
        }
        enqueueWebSocketUpgrade(serverListener)

        val messageLatch = CountDownLatch(1)
        val received = AtomicReference<String>()
        val manager = createManager()
        manager.onMessage { msg ->
            received.set(msg)
            messageLatch.countDown()
        }

        manager.connect()
        assertThat(messageLatch.await(5, TimeUnit.SECONDS)).isTrue()
        assertThat(received.get()).isEqualTo("hello from server")
    }

    // ── Reconnect max attempts exhausted ──────────────────────────

    @Test
    fun `reconnect gives up after max attempts and becomes DISCONNECTED`() {
        // Enqueue enough non-upgrade responses: initial attempt + 1 retry
        mockServer.enqueue(MockResponse().setResponseCode(503))
        mockServer.enqueue(MockResponse().setResponseCode(503))
        val manager = createManager(
            reconnectConfig = ReconnectConfig(baseDelayMs = 1L, maxDelayMs = 10L, maxAttempts = 1, jitterMs = 0L)
        )
        manager.connect()
        runBlocking {
            withTimeout(5_000L) {
                manager.connectionState.first { it == ConnectionState.DISCONNECTED }
            }
        }
    }

    // ── 认证拒绝（401 / close 4001）：终态失败，不重试 ──────────────

    @Test
    fun `http 401 fails terminal without retry`() {
        // 401 = 认证拒绝（token/device_id 非法）——重试必仍 401：直接落 DISCONNECTED，
        // 不得发起任何退避重连请求（docs/setting_server_address.md §8）
        mockServer.enqueue(MockResponse().setResponseCode(401))
        val manager = createManager(
            reconnectConfig = ReconnectConfig(baseDelayMs = 1L, maxDelayMs = 5L, maxAttempts = 5, jitterMs = 0L),
        )

        manager.connect()
        awaitState(manager, ConnectionState.DISCONNECTED, timeoutMs = 5_000)

        // 留足 ≥ 多轮退避窗口：若无该修复，这里会看到 2..5 个重试请求
        Thread.sleep(500)
        assertThat(mockServer.requestCount).isEqualTo(1)
        assertThat(manager.connectionState.value).isEqualTo(ConnectionState.DISCONNECTED)
    }

    @Test
    fun `server close 4001 fails terminal without retry`() {
        enqueueWebSocketUpgrade(object : WebSocketListener() {
            override fun onOpen(webSocket: okhttp3.WebSocket, response: okhttp3.Response) {
                webSocket.close(4001, "auth failed")
            }
        })
        val manager = createManager(
            reconnectConfig = ReconnectConfig(baseDelayMs = 1L, maxDelayMs = 5L, maxAttempts = 5, jitterMs = 0L),
        )

        manager.connect()
        awaitState(manager, ConnectionState.DISCONNECTED, timeoutMs = 5_000)

        Thread.sleep(500)
        assertThat(mockServer.requestCount).isEqualTo(1)
    }

    // ── TLS 自签名开关 ───────────────────────────────────────────

    @Test
    fun `self-signed websocket connects when allowed`() {
        val held = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
        val handshake = HandshakeCertificates.Builder().heldCertificate(held).build()
        val tlsServer = MockWebServer()
        tlsServer.useHttps(handshake.sslSocketFactory(), false)
        tlsServer.start()
        try {
            tlsServer.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {}))
            val manager = ConnectionManager(
                serverUrl = "wss://localhost:${tlsServer.port}/v1/ws/stream",
                token = "test-token",
                deviceId = "test-device",
                allowSelfSigned = true,
            )
            manager.connect()
            awaitState(manager, ConnectionState.CONNECTED)
            manager.disconnect()
        } finally {
            runCatching { tlsServer.shutdown() }
        }
    }

    @Test
    fun `self-signed websocket does not connect when not allowed`() {
        val held = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
        val handshake = HandshakeCertificates.Builder().heldCertificate(held).build()
        val tlsServer = MockWebServer()
        tlsServer.useHttps(handshake.sslSocketFactory(), false)
        tlsServer.start()
        try {
            tlsServer.enqueue(MockResponse().withWebSocketUpgrade(object : WebSocketListener() {}))
            val manager = ConnectionManager(
                serverUrl = "wss://localhost:${tlsServer.port}/v1/ws/stream",
                token = "test-token",
                deviceId = "test-device",
                allowSelfSigned = false,
                reconnectConfig = ReconnectConfig(
                    baseDelayMs = 1L,
                    maxDelayMs = 5L,
                    maxAttempts = 1,
                    jitterMs = 0L,
                ),
            )
            manager.connect()
            val connected = runBlocking {
                withTimeoutOrNull(2_000L) {
                    manager.connectionState.first { it == ConnectionState.CONNECTED }
                }
            }
            assertThat(connected).isNull()
            manager.disconnect()
        } finally {
            runCatching { tlsServer.shutdown() }
        }
    }
}
