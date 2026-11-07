import Foundation

/// Thread-safe monotonic C2S sequence generator.
///
/// `video` allocates sequence numbers on `videoSendQueue` while all other
/// messages allocate on `sessionQueue`, so the counter must be thread-safe.
internal final class SeqGenerator: @unchecked Sendable {
    private let lock = NSLock()
    private var value: UInt32 = 1

    func next() -> UInt32 {
        lock.lock()
        defer { lock.unlock() }
        let current = value
        value &+= 1
        if value == 0 {
            value = 1
        }
        return current
    }
}
