import Foundation
import UIKit

/// Physical-orientation monitor backed by `UIDevice` notifications.
///
/// The app stays portrait-locked while the camera is on, so interface
/// orientation cannot be used; this mirrors Android's `OrientationEventListener`.
/// Call `start`/`stop` on the main thread. `onChange` is delivered on the main
/// thread for changes after `start`; the initial value is exposed through
/// `current` so callers can apply it during session configuration without
/// treating it as a rotation. Flat/unknown orientations are ignored so the last
/// valid direction is retained.
/// `@unchecked Sendable`: every member is main-thread confined; the unchecked
/// conformance only satisfies NotificationCenter's @Sendable observer block.
final class CameraDeviceOrientationMonitor: CameraDeviceOrientationProviding, @unchecked Sendable {
    private(set) var current: CameraDeviceOrientation?
    var onChange: ((CameraDeviceOrientation) -> Void)?

    private let stateLock = NSLock()
    private var token: NSObjectProtocol?
    private var generating = false
    private var sessionID: UInt64 = 0

    func start() {
        stateLock.lock()
        // A deferred stop from a previous failure must not tear down a newer
        // start; bumping the session makes that stale stop a no-op.
        sessionID &+= 1
        guard !generating else {
            stateLock.unlock()
            return
        }
        generating = true
        stateLock.unlock()
        // Callers use the main thread; make that contract explicit to Swift's
        // actor isolation checker.
        MainActor.assumeIsolated {
            UIDevice.current.beginGeneratingDeviceOrientationNotifications()
        }
        token = NotificationCenter.default.addObserver(
            forName: UIDevice.orientationDidChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.handle(UIDevice.current.orientation)
            }
        }
        // Seed `current` without notifying: the camera applies the initial
        // orientation while configuring the connection, and arming a skip here
        // would drop the first post-warmup frame on every camera start.
        let initial = MainActor.assumeIsolated { UIDevice.current.orientation }
        handle(initial, notify: false)
    }

    func stop() {
        // The owner may be deallocated off-main; UIDevice APIs are main-only.
        // Retain self in the hop so a deinit-time stop still completes.
        guard Thread.isMainThread else {
            stateLock.lock()
            let session = sessionID
            stateLock.unlock()
            DispatchQueue.main.async {
                self.stop(session: session)
            }
            return
        }
        stop(session: nil)
    }

    private func stop(session: UInt64?) {
        stateLock.lock()
        if let session, session != sessionID {
            stateLock.unlock()
            return
        }
        guard generating else {
            stateLock.unlock()
            return
        }
        generating = false
        stateLock.unlock()
        if let token {
            NotificationCenter.default.removeObserver(token)
        }
        token = nil
        MainActor.assumeIsolated {
            UIDevice.current.endGeneratingDeviceOrientationNotifications()
        }
    }

    deinit {
        if let token {
            NotificationCenter.default.removeObserver(token)
        }
        // `stop` performs the UIDevice cleanup on the main thread; callers
        // (CameraController deinit/stop/failure) always route it there.
    }

    private func handle(_ orientation: UIDeviceOrientation, notify: Bool = true) {
        guard let mapped = Self.map(orientation) else { return }
        let changed = mapped != current
        current = mapped
        guard notify, changed else { return }
        onChange?(mapped)
    }

    /// Pure mapping for tests; flat/unknown values are deliberately ignored.
    static func map(_ orientation: UIDeviceOrientation) -> CameraDeviceOrientation? {
        switch orientation {
        case .portrait:
            return .portrait
        case .portraitUpsideDown:
            return .portraitUpsideDown
        case .landscapeLeft:
            return .landscapeLeft
        case .landscapeRight:
            return .landscapeRight
        default:
            return nil
        }
    }
}
