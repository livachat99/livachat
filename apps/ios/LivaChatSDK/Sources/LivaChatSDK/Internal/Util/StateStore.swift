import Foundation

/// Thread-safe state snapshot store. The playback epoch is part of the same
/// locked snapshot so `sdk.state` reads are atomic.
internal final class StateStore: @unchecked Sendable {
    private let lock = NSLock()
    private var state: LivaChatState

    init() {
        state = .initial
    }

    func snapshot() -> LivaChatState {
        lock.lock()
        defer { lock.unlock() }
        return state
    }

    func update(_ mutate: (inout LivaChatState) -> Void) {
        lock.lock()
        mutate(&state)
        lock.unlock()
    }

    /// Increments the playback epoch and returns the new value.
    @discardableResult
    func incrementPlaybackEpoch() -> UInt64 {
        lock.lock()
        defer { lock.unlock() }
        let next = state.playbackEpoch &+ 1
        state = LivaChatState(
            connectionState: state.connectionState,
            connectionEpoch: state.connectionEpoch,
            sessionState: state.sessionState,
            localVadState: state.localVadState,
            playbackEpoch: next
        )
        return next
    }

    /// Atomically transitions the connection state; increments the playback
    /// epoch when leaving `.connected`.
    @discardableResult
    func transitionConnectionState(
        _ newState: ConnectionState,
        connectionEpoch: UInt64
    ) -> UInt64 {
        lock.lock()
        defer { lock.unlock() }
        let leavingConnected = state.connectionState == .connected && newState != .connected
        let nextEpoch = leavingConnected ? state.playbackEpoch &+ 1 : state.playbackEpoch
        state = LivaChatState(
            connectionState: newState,
            connectionEpoch: connectionEpoch,
            sessionState: state.sessionState,
            localVadState: state.localVadState,
            playbackEpoch: nextEpoch
        )
        return nextEpoch
    }
}
