import UIKit

/// UIKit is confined to this adapter (docs/ios_dev_rules.md §2).
@MainActor
enum SystemSettingsOpener {
    static func openAppSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }
}
