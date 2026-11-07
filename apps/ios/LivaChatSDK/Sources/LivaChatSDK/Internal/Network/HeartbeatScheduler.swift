import Foundation

/// Inbound-silence based keepalive timer.
internal final class HeartbeatScheduler: @unchecked Sendable {
    private let keepaliveInterval: TimeInterval
    private let keepaliveTimeout: TimeInterval
    private let clock: () -> TimeInterval
    private let lock = NSLock()

    private var lastInbound: TimeInterval = 0
    private var started = false
    private var pingSent = false

    init(
        keepaliveInterval: TimeInterval = 15,
        keepaliveTimeout: TimeInterval = 35,
        clock: @escaping () -> TimeInterval = { Date().timeIntervalSince1970 }
    ) {
        self.keepaliveInterval = keepaliveInterval
        self.keepaliveTimeout = keepaliveTimeout
        self.clock = clock
    }

    func start() {
        lock.lock()
        lastInbound = clock()
        started = true
        pingSent = false
        lock.unlock()
    }

    func reset() {
        start()
    }

    func onInbound() {
        lock.lock()
        lastInbound = clock()
        pingSent = false
        lock.unlock()
    }

    func shouldSendPing() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard started, !pingSent else { return false }
        return clock() - lastInbound >= keepaliveInterval
    }

    func onPingSent() {
        lock.lock()
        pingSent = true
        lock.unlock()
    }

    func isTimeout() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard started else { return false }
        return clock() - lastInbound >= keepaliveTimeout
    }
}
