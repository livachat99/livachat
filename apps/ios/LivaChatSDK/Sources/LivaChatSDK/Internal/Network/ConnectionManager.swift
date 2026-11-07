import CWebSocketClose
import Foundation

/// WebSocket transport with generation handling, heartbeat, reconnect,
/// connection watchdog and terminal-state classification.
internal final class ConnectionManager: @unchecked Sendable {
    var onMessage: ((String) -> Void)?
    var onPing: (() -> Void)?
    /// Called after `didOpen`; the parameter is the new connection epoch.
    var onOpen: ((UInt64) -> Void)?
    /// Called for every state transition; the parameter is the current connection epoch.
    var onStateChange: ((ConnectionState, UInt64) -> Void)?
    var onEpoch: ((UInt64) -> Void)?
    /// Called before `.disconnected` when the connection will not auto-retry.
    var onTerminated: ((ConnectionTerminatedReason) -> Void)?

    private let serverURL: URL
    private let token: String
    private let deviceID: String
    private let connectTimeout: TimeInterval
    private let reconnectScheduler: ReconnectScheduler
    private let heartbeatScheduler: HeartbeatScheduler
    fileprivate let tlsPolicy: TLSPolicy
    private let logger: SdkLogger

    private let stateLock = NSLock()
    private var state: ConnectionState = .disconnected
    private var connectionEpoch: UInt64 = 0
    private var webSocketTask: URLSessionWebSocketTask?
    private var sendDroppedLogged = false

    // Connection-queue-confined state.
    private var session: URLSession?
    private var delegate: WebSocketDelegate?
    private var generation: UInt64 = 0
    private var endedGeneration: UInt64 = 0
    private var shutdown = false
    private var reconnectScheduled = false
    private var reconnectToken: UInt64 = 0
    private var reconnectWorkItem: DispatchWorkItem?
    private var watchdogWorkItem: DispatchWorkItem?
    private var heartbeatTimer: DispatchSourceTimer?

    init(
        serverURL: URL,
        token: String,
        deviceID: String,
        connectTimeout: TimeInterval,
        reconnectConfig: ReconnectConfig,
        keepaliveInterval: TimeInterval,
        keepaliveTimeout: TimeInterval,
        tlsPolicy: TLSPolicy,
        logger: SdkLogger
    ) {
        self.serverURL = serverURL
        self.token = token
        self.deviceID = deviceID
        self.connectTimeout = connectTimeout
        self.reconnectScheduler = ReconnectScheduler(config: reconnectConfig)
        self.heartbeatScheduler = HeartbeatScheduler(
            keepaliveInterval: keepaliveInterval,
            keepaliveTimeout: keepaliveTimeout
        )
        self.tlsPolicy = tlsPolicy
        self.logger = logger
    }

    deinit {
        // The instance may be released without `disconnect()` (e.g. the owner
        // is abandoned). URLSession invalidation is thread-safe; stopping here
        // releases the session/delegate cycle deterministically.
        reconnectWorkItem?.cancel()
        cleanupSession(cancel: true)
    }

    // MARK: - Public API

    func connect() {
        SDKQueue.connection.async { [weak self] in
            guard let self else { return }
            self.shutdown = false
            self.reconnectWorkItem?.cancel()
            self.reconnectWorkItem = nil
            self.reconnectToken &+= 1
            self.reconnectScheduled = false
            self.reconnectScheduler.reset()
            self.setState(.connecting)
            self.connectInternal()
        }
    }

    func disconnect() {
        SDKQueue.connection.async { [weak self] in
            guard let self else { return }
            self.shutdown = true
            self.reconnectWorkItem?.cancel()
            self.reconnectWorkItem = nil
            self.reconnectToken &+= 1
            self.reconnectScheduled = false
            self.endedGeneration = self.generation
            self.cancelWatchdog()
            self.stopHeartbeat()
            self.webSocketTask?.cancel(with: .normalClosure, reason: nil)
            self.cleanupSession(cancel: true)
            self.setState(.disconnected)
        }
    }

    func send(_ text: String) -> Bool {
        stateLock.lock()
        let connected = state == .connected
        let task = webSocketTask
        let shouldLogDrop = !connected && !sendDroppedLogged
        if !connected {
            sendDroppedLogged = true
        } else if task != nil {
            sendDroppedLogged = false
        }
        stateLock.unlock()

        if shouldLogDrop {
            logger.warn("WS", "send dropped: not connected")
        }

        guard connected, let task else {
            return false
        }

        task.send(.string(text)) { [weak self] error in
            if let error {
                self?.logger.warn("WS", "send failed: \(error.localizedDescription)")
            }
        }
        return true
    }

    var currentState: ConnectionState {
        stateLock.lock()
        defer { stateLock.unlock() }
        return state
    }

    var currentEpoch: UInt64 {
        stateLock.lock()
        defer { stateLock.unlock() }
        return connectionEpoch
    }

    // MARK: - Connection internals

    private func connectInternal() {
        cleanupSession(cancel: true)

        generation &+= 1
        let generation = self.generation
        endedGeneration = 0

        var components = URLComponents(url: serverURL, resolvingAgainstBaseURL: false)
        var queryItems = components?.queryItems ?? []
        queryItems.append(URLQueryItem(name: "token", value: token))
        queryItems.append(URLQueryItem(name: "device_id", value: deviceID))
        components?.queryItems = queryItems

        guard let url = components?.url else {
            logger.error("WS", "invalid server URL")
            scheduleReconnect()
            return
        }

        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = connectTimeout
        configuration.waitsForConnectivity = false

        let delegate = WebSocketDelegate(manager: self, generation: generation)
        let session = URLSession(
            configuration: configuration,
            delegate: delegate,
            delegateQueue: nil
        )
        let task = session.webSocketTask(with: url)

        self.session = session
        self.delegate = delegate
        stateLock.lock()
        webSocketTask = task
        stateLock.unlock()

        startWatchdog(generation: generation)
        task.resume()
    }

    fileprivate func handleDidOpen(generation: UInt64) {
        SDKQueue.connection.async { [weak self] in
            self?.didOpen(generation: generation)
        }
    }

    private func didOpen(generation: UInt64) {
        guard generation == self.generation, !shutdown else { return }

        cancelWatchdog()
        reconnectScheduler.reset()
        reconnectScheduled = false

        stateLock.lock()
        state = .connected
        connectionEpoch &+= 1
        let epoch = connectionEpoch
        stateLock.unlock()

        startHeartbeat()

        onOpen?(epoch)
        onStateChange?(.connected, epoch)
        onEpoch?(epoch)

        receiveNext(generation: generation)
    }

    fileprivate func handleDidClose(generation: UInt64, code: Int, reason: String) {
        SDKQueue.connection.async { [weak self] in
            self?.didClose(generation: generation, code: code, reason: reason)
        }
    }

    private func didClose(generation: UInt64, code: Int, reason: String) {
        guard generation == self.generation, !shutdown else { return }
        if endedGeneration == generation { return }
        logger.info("WS", "ws_closed | code=\(code) | \(reason)")
        endConnection(generation: generation, closeCode: code)
    }

    fileprivate func handleDidComplete(generation: UInt64, info: CompletionInfo) {
        SDKQueue.connection.async { [weak self] in
            self?.didComplete(generation: generation, info: info)
        }
    }

    private func didComplete(generation: UInt64, info: CompletionInfo) {
        guard generation == self.generation, !shutdown else { return }
        if endedGeneration == generation { return }

        if info.statusCode == 401 {
            endTerminal(generation: generation, reason: .authFailed)
            return
        }
        if info.wasCancelled {
            return
        }

        // didCloseWith (which carries custom close codes 4001/1000) may arrive
        // just after didCompleteWithError. Delay the fallback briefly so a
        // terminal close code wins over the generic completion path.
        let closeCode = info.errorDescription == nil ? 1000 : nil
        let errorDescription = info.errorDescription
        SDKQueue.connection.asyncAfter(deadline: .now() + 0.1) { [weak self] in
            guard let self,
                  !self.shutdown,
                  generation == self.generation,
                  self.endedGeneration != generation else {
                return
            }
            if let closeCode {
                self.endConnection(generation: generation, closeCode: closeCode)
            } else {
                self.logger.warn("WS", "ws_failure | \(errorDescription ?? "unknown")")
                self.endConnection(generation: generation, closeCode: nil)
            }
        }
    }

    private func endConnection(generation: UInt64, closeCode: Int?) {
        guard generation == self.generation, !shutdown else { return }
        if endedGeneration == generation { return }
        endedGeneration = generation

        cancelWatchdog()
        stopHeartbeat()

        switch closeCode {
        case 1000?:
            endTerminal(generation: generation, reason: .serverClosed)
        case 4001?:
            endTerminal(generation: generation, reason: .authFailed)
        default:
            cleanupSession(cancel: false)
            scheduleReconnect()
        }
    }

    private func endTerminal(generation: UInt64, reason: ConnectionTerminatedReason) {
        guard generation == self.generation, !shutdown else { return }
        endedGeneration = generation
        cancelWatchdog()
        stopHeartbeat()
        onTerminated?(reason)
        cleanupSession(cancel: false)
        setState(.disconnected)
    }

    private func scheduleReconnect() {
        guard !shutdown, !reconnectScheduled else { return }

        // Check the attempt budget before emitting RECONNECTING so an exhausted
        // client goes straight to terminated/disconnected without a transient
        // reconnecting state.
        guard let delay = reconnectScheduler.nextDelay() else {
            logger.warn("WS", "reconnect attempts exhausted")
            onTerminated?(.reconnectExhausted)
            setState(.disconnected)
            return
        }

        reconnectScheduled = true
        setState(.reconnecting)
        logger.info("WS", "reconnect scheduled in \(String(format: "%.2f", delay))s")
        reconnectToken &+= 1
        let token = reconnectToken
        let item = DispatchWorkItem { [weak self] in
            guard let self,
                  !self.shutdown,
                  token == self.reconnectToken else {
                return
            }
            self.reconnectWorkItem = nil
            self.reconnectScheduled = false
            self.connectInternal()
        }
        reconnectWorkItem = item
        SDKQueue.connection.asyncAfter(deadline: .now() + delay, execute: item)
    }

    private func setState(_ newState: ConnectionState) {
        stateLock.lock()
        state = newState
        let epoch = connectionEpoch
        stateLock.unlock()
        onStateChange?(newState, epoch)
    }

    private func cleanupSession(cancel: Bool) {
        cancelWatchdog()
        stopHeartbeat()

        stateLock.lock()
        let task = webSocketTask
        webSocketTask = nil
        let session = self.session
        self.session = nil
        delegate = nil
        stateLock.unlock()

        task?.cancel(with: .goingAway, reason: nil)
        if cancel {
            session?.invalidateAndCancel()
        } else {
            session?.finishTasksAndInvalidate()
        }
    }

    // MARK: - Receive loop

    private func receiveNext(generation: UInt64) {
        guard generation == self.generation, !shutdown else { return }
        stateLock.lock()
        let task = webSocketTask
        stateLock.unlock()
        guard let task else { return }

        task.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case let .success(message):
                switch message {
                case let .string(text):
                    self.handleInbound(generation: generation, text: text)
                case .data:
                    self.logger.warn("WS", "ignoring binary websocket message")
                    self.continueReceiving(generation: generation)
                @unknown default:
                    self.logger.warn("WS", "ignoring unknown websocket message")
                    self.continueReceiving(generation: generation)
                }
            case let .failure(error):
                self.logger.warn("WS", "receive failed: \(error.localizedDescription)")
            }
        }
    }

    /// Keeps the receive loop alive after frames that are not dispatched as
    /// protocol messages (binary / unknown types).
    private func continueReceiving(generation: UInt64) {
        SDKQueue.connection.async { [weak self] in
            guard let self, generation == self.generation, !self.shutdown else { return }
            self.heartbeatScheduler.onInbound()
            self.receiveNext(generation: generation)
        }
    }

    private func handleInbound(generation: UInt64, text: String) {
        SDKQueue.connection.async { [weak self] in
            guard let self, generation == self.generation, !self.shutdown else { return }
            self.heartbeatScheduler.onInbound()
            self.onMessage?(text)
            self.receiveNext(generation: generation)
        }
    }

    // MARK: - Heartbeat

    private func startHeartbeat() {
        stopHeartbeat()
        heartbeatScheduler.start()

        let timer = DispatchSource.makeTimerSource(queue: SDKQueue.connection)
        timer.schedule(deadline: .now() + 1, repeating: 1, leeway: .milliseconds(100))
        timer.setEventHandler { [weak self] in
            self?.heartbeatTick()
        }
        heartbeatTimer = timer
        timer.resume()
    }

    private func stopHeartbeat() {
        heartbeatTimer?.cancel()
        heartbeatTimer = nil
    }

    private func heartbeatTick() {
        guard !shutdown, currentState == .connected else { return }

        if heartbeatScheduler.isTimeout() {
            logger.warn("WS", "keepalive timeout")
            closeWithRawCodeAndReconnect(code: 4000, reason: "keepalive timeout")
            return
        }

        if heartbeatScheduler.shouldSendPing() {
            heartbeatScheduler.onPingSent()
            onPing?()
        }
    }

    /// Closes the active WebSocket with an RFC 6455 application code (4000)
    /// and reconnects. The short delay lets the close frame flush before the
    /// URLSession is invalidated.
    private func closeWithRawCodeAndReconnect(code: Int, reason: String) {
        endedGeneration = generation
        stopHeartbeat()

        stateLock.lock()
        let task = webSocketTask
        stateLock.unlock()
        if let task {
            LivaChatWebSocketCancelWithCode(task, code, Data(reason.utf8))
        }

        let generation = self.generation
        SDKQueue.connection.asyncAfter(deadline: .now() + 0.2) { [weak self] in
            guard let self, generation == self.generation, !self.shutdown else { return }
            self.cleanupSession(cancel: true)
            self.scheduleReconnect()
        }
    }

    // MARK: - Watchdog

    private func startWatchdog(generation: UInt64) {
        cancelWatchdog()
        let item = DispatchWorkItem { [weak self] in
            guard let self,
                  !self.shutdown,
                  generation == self.generation,
                  self.currentState != .connected else {
                return
            }
            self.logger.warn("WS", "connect watchdog timeout")
            self.closeWithRawCodeAndReconnect(code: 4000, reason: "connect timeout")
        }
        watchdogWorkItem = item
        SDKQueue.connection.asyncAfter(
            deadline: .now() + connectTimeout,
            execute: item
        )
    }

    private func cancelWatchdog() {
        watchdogWorkItem?.cancel()
        watchdogWorkItem = nil
    }
}

// MARK: - URLSession delegate

internal struct CompletionInfo: Sendable {
    let statusCode: Int?
    let errorDescription: String?
    let wasCancelled: Bool
}

private final class WebSocketDelegate: NSObject, URLSessionWebSocketDelegate, @unchecked Sendable {
    // Weak: the manager owns the session, and the session retains this
    // delegate until invalidation. A strong reference here would form
    // manager -> session -> delegate -> manager and leak if the owner is
    // released without `disconnect()`.
    private weak var manager: ConnectionManager?
    private let generation: UInt64

    init(manager: ConnectionManager, generation: UInt64) {
        self.manager = manager
        self.generation = generation
    }

    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol proto: String?
    ) {
        manager?.handleDidOpen(generation: generation)
    }

    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
        reason: Data?
    ) {
        let reasonText = reason.flatMap { String(data: $0, encoding: .utf8) } ?? ""
        manager?.handleDidClose(
            generation: generation,
            code: closeCode.rawValue,
            reason: reasonText
        )
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        let info = CompletionInfo(
            statusCode: (task.response as? HTTPURLResponse)?.statusCode,
            errorDescription: error?.localizedDescription,
            wasCancelled: (error as? URLError)?.code == .cancelled
        )
        manager?.handleDidComplete(generation: generation, info: info)
    }

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        if let manager,
           case .allowSelfSigned = manager.tlsPolicy,
           challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.performDefaultHandling, nil)
        }
    }
}
