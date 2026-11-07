import Foundation

internal struct ReconnectConfig: Sendable {
    var maxAttempts: Int
    var baseDelay: TimeInterval
    var maxDelay: TimeInterval

    init(
        maxAttempts: Int = 5,
        baseDelay: TimeInterval = 1.0,
        maxDelay: TimeInterval = 30.0
    ) {
        self.maxAttempts = maxAttempts
        self.baseDelay = baseDelay
        self.maxDelay = maxDelay
    }
}

/// Exponential backoff calculator with jitter.
internal final class ReconnectScheduler: @unchecked Sendable {
    private let config: ReconnectConfig
    private let jitterProvider: () -> TimeInterval
    private let lock = NSLock()
    private var attempt = 0

    init(
        config: ReconnectConfig = ReconnectConfig(),
        jitterProvider: @escaping () -> TimeInterval = {
            Double.random(in: -0.5...0.5)
        }
    ) {
        self.config = config
        self.jitterProvider = jitterProvider
    }

    /// Next delay in seconds, or nil when the attempt budget is exhausted.
    func nextDelay() -> TimeInterval? {
        lock.lock()
        defer { lock.unlock() }

        attempt += 1
        guard attempt <= config.maxAttempts else {
            return nil
        }

        let exponential = config.baseDelay * pow(2, Double(attempt - 1))
        let capped = min(exponential, config.maxDelay)
        return max(0, capped + jitterProvider())
    }

    func reset() {
        lock.lock()
        attempt = 0
        lock.unlock()
    }

    var currentAttempt: Int {
        lock.lock()
        defer { lock.unlock() }
        return attempt
    }
}
