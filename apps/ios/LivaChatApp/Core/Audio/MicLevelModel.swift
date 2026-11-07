import Combine
import Foundation

/// Leaf observable owned by `ConversationViewModel`. Only `MicVolumeGlyph`
/// observes it, so ~20 Hz level updates do not re-render the conversation
/// view.
@MainActor
final class MicLevelModel: ObservableObject {
    nonisolated static let levels = MicLevelMeter.levels

    @Published private(set) var level = 0

    func set(_ value: Int) {
        let clamped = min(max(value, 0), Self.levels)
        guard clamped != level else { return }
        level = clamped
    }

    func reset() {
        set(0)
    }
}
