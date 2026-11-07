import Foundation
import LivaChatSDK

/// Debug-only local-VAD interrupt simulator used by AudioSpike.
/// Production haptics live in ConversationCoordinator (entering SPEAKING).
@MainActor
final class InterruptCoordinator: Interrupting {
    static let throttleInterval: TimeInterval = 0.5

    private let sendInterrupt: () -> Void
    private let haptics: HapticProviding
    private let now: () -> TimeInterval

    private var sessionState: SessionState = .idle
    private var vadState: VadState = .unknown
    private var hapticEnabled = true
    private var lastInterruptAt: TimeInterval = -.infinity

    init(
        sendInterrupt: @escaping () -> Void,
        haptics: HapticProviding,
        now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }
    ) {
        self.sendInterrupt = sendInterrupt
        self.haptics = haptics
        self.now = now
    }

    func start() {
        sessionState = .idle
        vadState = .unknown
        lastInterruptAt = -.infinity
    }

    func stop() {
        sessionState = .idle
        vadState = .unknown
    }

    func update(sessionState: SessionState, vadState: VadState) {
        self.sessionState = sessionState
        self.vadState = vadState

        guard sessionState == .speaking, vadState == .speech else { return }
        let timestamp = now()
        guard timestamp - lastInterruptAt >= Self.throttleInterval else { return }
        lastInterruptAt = timestamp
        sendInterrupt()
        if hapticEnabled {
            haptics.lightTap()
        }
    }

    func setHapticEnabled(_ enabled: Bool) {
        hapticEnabled = enabled
    }
}
