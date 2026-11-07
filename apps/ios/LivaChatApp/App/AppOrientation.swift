import UIKit

/// Dynamic orientation support for the agent job full-screen viewer.
///
/// The app is portrait-only by default (`AppDelegate` reports `.portrait`).
/// While the agent job result overlay is presented, the supported mask widens
/// to all iPhone orientations (Android uses `FULL_SENSOR`) and the window
/// geometry is updated; dismissal restores portrait. `Info.plist` lists
/// portrait + landscape so the system can honor the wider mask.
@MainActor
final class AppOrientationController {
    static let shared = AppOrientationController()

    private(set) var supportedOrientations: UIInterfaceOrientationMask = .portrait

    func unlockForAgentJobFullScreen() {
        apply(.allButUpsideDown)
    }

    func restorePortrait() {
        apply(.portrait)
    }

    private func apply(_ mask: UIInterfaceOrientationMask) {
        guard supportedOrientations != mask else { return }
        supportedOrientations = mask
        guard let scene = activeWindowScene,
              let root = scene.windows.first(where: \.isKeyWindow)?.rootViewController else {
            return
        }
        if #available(iOS 16.0, *) {
            root.setNeedsUpdateOfSupportedInterfaceOrientations()
            scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { error in
                AppLog.warn("Orientation", "geometry update failed: \(error.localizedDescription)")
            }
        } else {
            UIViewController.attemptRotationToDeviceOrientation()
        }
    }

    private var activeWindowScene: UIWindowScene? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        return scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        supportedInterfaceOrientationsFor window: UIWindow?
    ) -> UIInterfaceOrientationMask {
        AppOrientationController.shared.supportedOrientations
    }
}
