import Foundation
import LivaChatSDK

/// Sole consumer of `LivaChatSDK.events`. Forwards every event on the main
/// actor; all business decisions live in the coordinator.
@MainActor
final class ConversationEventAdapter {
    private let onEvent: @MainActor (LivaChatEvent) -> Void
    private var task: Task<Void, Never>?

    init(onEvent: @escaping @MainActor (LivaChatEvent) -> Void) {
        self.onEvent = onEvent
    }

    func start(_ events: AsyncStream<LivaChatEvent>) {
        guard task == nil else { return }
        task = Task { @MainActor [weak self] in
            for await event in events {
                guard !Task.isCancelled, let self else { break }
                self.onEvent(event)
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
    }
}
