import Foundation

/// Maintains the offset between server UTC time and the local clock.
internal final class ClockOffset: @unchecked Sendable {
    private let lock = NSLock()
    private var offsetMs: Int64 = 0

    func update(serverTimeMs: UInt64, localTimeMs: Int64) {
        let server = Int64(clamping: serverTimeMs)
        lock.lock()
        offsetMs = server - localTimeMs
        lock.unlock()
    }

    func currentOffsetMs() -> Int64 {
        lock.lock()
        defer { lock.unlock() }
        return offsetMs
    }

    func toServerTime(localTimeMs: Int64) -> Int64 {
        localTimeMs + currentOffsetMs()
    }

    func toLocalTime(serverTimeMs: UInt64) -> Int64 {
        Int64(clamping: serverTimeMs) - currentOffsetMs()
    }

    func reset() {
        lock.lock()
        offsetMs = 0
        lock.unlock()
    }
}
