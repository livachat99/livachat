import UIKit

/// UIKit is confined to this platform adapter (docs/ios_dev_rules.md §2).
@MainActor
final class HapticController: HapticProviding {
    private let generator = UIImpactFeedbackGenerator(style: .light)

    func lightTap() {
        generator.prepare()
        generator.impactOccurred()
    }
}
