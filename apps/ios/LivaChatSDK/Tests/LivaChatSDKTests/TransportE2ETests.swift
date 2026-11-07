import Foundation
import LivaChatProtocol
import XCTest
@testable import LivaChatSDK

#if os(macOS)
private final class E2EEventSink: EventSink, @unchecked Sendable {
    private let lock = NSLock()
    private var handler: ((LivaChatEvent) -> Void)?
    private var state: SessionState = .idle

    func setHandler(_ handler: @escaping (LivaChatEvent) -> Void) {
        lock.lock()
        self.handler = handler
        lock.unlock()
    }

    func emit(_ event: LivaChatEvent) {
        lock.lock()
        let handler = self.handler
        lock.unlock()
        handler?(event)
    }

    func updateSessionState(_ state: SessionState) {
        lock.lock()
        self.state = state
        lock.unlock()
    }
}

private final class LockedCode: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: UInt16?

    var value: UInt16? {
        lock.lock()
        defer { lock.unlock() }
        return stored
    }

    func set(_ value: UInt16) {
        lock.lock()
        stored = value
        lock.unlock()
    }
}

final class TransportE2ETests: XCTestCase {
    private var server: TestWebSocketServer!

    override func tearDown() {
        server?.stop()
        server = nil
        super.tearDown()
    }

    func testHandshakeSessionAudioAndFlush() throws {
        let server = try TestWebSocketServer()
        self.server = server
        try server.start()

        let logger = SdkLogger(logFileURL: nil)
        let sink = E2EEventSink()
        let stateStore = StateStore()
        let ready = expectation(description: "session ready")
        let listening = expectation(description: "listening")
        let transcription = expectation(description: "transcription")
        let flush = expectation(description: "flush")
        let audio = expectation(description: "ai audio")
        let noTermination = expectation(description: "no termination")
        noTermination.isInverted = true

        sink.setHandler { event in
            switch event {
            case .sessionReady:
                ready.fulfill()
            case .sessionStateChanged(.listening):
                listening.fulfill()
            case let .transcription(text, isFinal, _):
                if text == "hello", isFinal { transcription.fulfill() }
            case .playbackShouldFlush(.interruptAck, _):
                flush.fulfill()
            default:
                break
            }
        }

        let startConfig = ClientSessionStartConfig(
            enableVideo: true,
            audioSampleRate: .hz16000,
            vadSensitivity: .medium,
            clientVersion: "ios-test",
            timezone: nil
        )

        let connectionManager = ConnectionManager(
            serverURL: URL(string: "ws://127.0.0.1:\(server.port)/v1/ws/stream")!,
            token: "token",
            deviceID: "uuid",
            connectTimeout: 2,
            reconnectConfig: ReconnectConfig(maxAttempts: 1, baseDelay: 0.1, maxDelay: 0.1),
            keepaliveInterval: 15,
            keepaliveTimeout: 35,
            tlsPolicy: .system,
            logger: logger
        )

        let sessionManager = SessionManager(
            codec: MessageCodec(),
            sendRaw: { [weak connectionManager] text in
                connectionManager?.send(text) ?? false
            },
            eventSink: sink,
            stateStore: stateStore,
            onAIAudio: { _, _, _, _ in audio.fulfill() },
            now: { 1_000 },
            configProvider: { startConfig },
            logger: logger
        )

        let connectionCodec = MessageCodec()
        connectionManager.onMessage = { text in
            guard let message = connectionCodec.decode(text) else { return }
            SDKQueue.session.async {
                sessionManager.handleInbound(message)
            }
        }
        connectionManager.onPing = {
            SDKQueue.session.async {
                sessionManager.sendPing()
            }
        }
        connectionManager.onOpen = { epoch in
            SDKQueue.session.async {
                sessionManager.resetSessionGate(connectionEpoch: epoch)
                sessionManager.startSession(resumeSessionID: nil)
            }
        }
        connectionManager.onTerminated = { _ in
            noTermination.fulfill()
        }

        server.onText = { [weak server] text in
            guard let server else { return }
            let object = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any]
            switch object?["type"] as? String {
            case "session.start":
                // A stray binary frame must not stop the receive loop.
                server.sendBinary(Data([0x00, 0x01]))
                server.sendText(#"{"type":"session.ready","seq":1,"timestamp":0,"payload":{"session_id":"sess_test","session_restored":false,"server_time_ms":1000,"keepalive_interval_s":15}}"#)
                server.sendText(#"{"type":"status","seq":2,"timestamp":0,"payload":{"session_status":"idle","reason":"connected"}}"#)
                server.sendText(#"{"type":"status","seq":3,"timestamp":0,"payload":{"session_status":"listening","reason":"connected"}}"#)
            case "audio":
                server.sendText(#"{"type":"transcription","seq":4,"timestamp":0,"payload":{"text":"hello","is_final":true,"transcription_id":"t1"}}"#)
                server.sendText(#"{"type":"status","seq":5,"timestamp":0,"payload":{"session_status":"thinking","reason":"connected"}}"#)
                server.sendText(#"{"type":"ai_text","seq":6,"timestamp":0,"payload":{"text":"hi","message_id":"m1","index":0,"is_final":true}}"#)
                server.sendText(#"{"type":"ai_audio","seq":7,"timestamp":0,"payload":{"data":"AQID","codec":"opus","message_id":"m1","index":0,"duration_ms":20,"is_final":true}}"#)
            default:
                break
            }
        }

        connectionManager.connect()
        wait(for: [ready, listening], timeout: 5)
        XCTAssertEqual(server.receivedTypes.first, "session.start")

        SDKQueue.session.async {
            sessionManager.sendAudio(ClientAudioPayload(
                data: Data([1, 2, 3]),
                codec: "opus",
                durationMs: 20,
                vadState: .speech
            ))
        }
        wait(for: [transcription, flush, audio], timeout: 5)
        XCTAssertEqual(stateStore.snapshot().playbackEpoch, 2)

        connectionManager.disconnect()
        wait(for: [noTermination], timeout: 0.3)
    }

    func testReconnectExhaustionSkipsReconnectingState() throws {
        let server = try TestWebSocketServer()
        self.server = server
        server.closeAfterHandshake = 4002
        try server.start()

        let manager = makeConnectionManager(server: server, maxAttempts: 0)
        let terminated = expectation(description: "terminated")
        let lock = NSLock()
        var states: [ConnectionState] = []
        manager.onStateChange = { state, _ in
            lock.lock()
            states.append(state)
            lock.unlock()
        }
        manager.onTerminated = { reason in
            XCTAssertEqual(reason, .reconnectExhausted)
            terminated.fulfill()
        }
        manager.connect()
        wait(for: [terminated], timeout: 4)

        let deadline = Date().addingTimeInterval(1)
        while manager.currentState != .disconnected, Date() < deadline {
            Thread.sleep(forTimeInterval: 0.01)
        }
        XCTAssertEqual(manager.currentState, .disconnected)

        lock.lock()
        let recorded = states
        lock.unlock()
        XCTAssertFalse(recorded.contains { state in
            if case .reconnecting = state { return true }
            return false
        })
    }

    func testReleasedManagerDeallocatesWithoutCycle() throws {
        let server = try TestWebSocketServer()
        self.server = server
        server.blackhole = true
        try server.start()

        var manager: ConnectionManager? = makeConnectionManager(
            server: server,
            connectTimeout: 30
        )
        weak var weakManager = manager
        manager?.connect()

        let connectingDeadline = Date().addingTimeInterval(2)
        while manager?.currentState != .connecting, Date() < connectingDeadline {
            Thread.sleep(forTimeInterval: 0.01)
        }
        XCTAssertEqual(manager?.currentState, .connecting)

        // Without a disconnect(), a strong delegate cycle would keep the
        // manager (and its URLSession) alive forever.
        manager = nil
        let releaseDeadline = Date().addingTimeInterval(2)
        while weakManager != nil, Date() < releaseDeadline {
            Thread.sleep(forTimeInterval: 0.01)
        }
        XCTAssertNil(weakManager)
    }

    // MARK: - Terminal paths

    private func makeConnectionManager(
        server: TestWebSocketServer,
        connectTimeout: TimeInterval = 2,
        maxAttempts: Int = 0
    ) -> ConnectionManager {
        ConnectionManager(
            serverURL: URL(string: "ws://127.0.0.1:\(server.port)/v1/ws/stream")!,
            token: "token",
            deviceID: "uuid",
            connectTimeout: connectTimeout,
            reconnectConfig: ReconnectConfig(
                maxAttempts: maxAttempts,
                baseDelay: 0.05,
                maxDelay: 0.05
            ),
            keepaliveInterval: 15,
            keepaliveTimeout: 35,
            tlsPolicy: .system,
            logger: SdkLogger(logFileURL: nil)
        )
    }

    private func assertTermination(
        server: TestWebSocketServer,
        connectTimeout: TimeInterval = 2,
        maxAttempts: Int = 0,
        expected: ConnectionTerminatedReason,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        let manager = makeConnectionManager(
            server: server,
            connectTimeout: connectTimeout,
            maxAttempts: maxAttempts
        )
        let terminated = expectation(description: "terminated")
        manager.onTerminated = { reason in
            XCTAssertEqual(reason, expected, file: file, line: line)
            terminated.fulfill()
        }
        manager.connect()
        wait(for: [terminated], timeout: 4)

        // The termination callback is followed by the DISCONNECTED transition on
        // the same queue; allow it to settle before asserting.
        let deadline = Date().addingTimeInterval(1)
        while manager.currentState != .disconnected, Date() < deadline {
            Thread.sleep(forTimeInterval: 0.01)
        }
        XCTAssertEqual(manager.currentState, .disconnected, file: file, line: line)
    }

    func testClose4001IsAuthTerminal() throws {
        let server = try TestWebSocketServer()
        self.server = server
        server.closeAfterHandshake = 4001
        try server.start()
        assertTermination(server: server, expected: .authFailed)
    }

    func testClose1000IsServerClosed() throws {
        let server = try TestWebSocketServer()
        self.server = server
        server.closeAfterHandshake = 1000
        try server.start()
        assertTermination(server: server, expected: .serverClosed)
    }

    func testClose4002ReconnectsThenExhausts() throws {
        let server = try TestWebSocketServer()
        self.server = server
        server.closeAfterHandshake = 4002
        try server.start()
        // If 4002 were misclassified as terminal, the reason would be
        // .serverClosed; exhausted proves the reconnect path was taken.
        assertTermination(server: server, expected: .reconnectExhausted)
    }

    func testHandshake401IsAuthTerminal() throws {
        let server = try TestWebSocketServer()
        self.server = server
        server.rejectWithUnauthorized = true
        try server.start()
        assertTermination(server: server, expected: .authFailed)
    }

    func testBlackholeHandshakeWatchdog() throws {
        let server = try TestWebSocketServer()
        self.server = server
        server.blackhole = true
        try server.start()
        assertTermination(
            server: server,
            connectTimeout: 0.2,
            expected: .reconnectExhausted
        )
    }

    func testHeartbeatTimeoutClosesWith4000() throws {
        let server = try TestWebSocketServer()
        self.server = server
        let closed = expectation(description: "client closed")
        let code = LockedCode()
        server.onClose = { value in
            code.set(value)
            closed.fulfill()
        }
        try server.start()

        let manager = ConnectionManager(
            serverURL: URL(string: "ws://127.0.0.1:\(server.port)/v1/ws/stream")!,
            token: "token",
            deviceID: "uuid",
            connectTimeout: 2,
            reconnectConfig: ReconnectConfig(
                maxAttempts: 0,
                baseDelay: 0.05,
                maxDelay: 0.05
            ),
            keepaliveInterval: 0.2,
            keepaliveTimeout: 0.5,
            tlsPolicy: .system,
            logger: SdkLogger(logFileURL: nil)
        )
        manager.connect()
        wait(for: [closed], timeout: 5)
        XCTAssertEqual(code.value, 4000)
    }
}
#endif
