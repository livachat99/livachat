import AVFoundation
import CoreGraphics

/// Physical device orientation relevant to camera capture. Kept free of UIKit
/// so the media layer does not depend on `UIDeviceOrientation`.
enum CameraDeviceOrientation: Equatable, Sendable {
    case portrait
    case portraitUpsideDown
    case landscapeLeft
    case landscapeRight
}

/// Supplies the current physical device orientation while the camera is on.
/// All members must be used on the main thread; `onChange` is delivered there.
protocol CameraDeviceOrientationProviding: AnyObject {
    var current: CameraDeviceOrientation? { get }
    var onChange: ((CameraDeviceOrientation) -> Void)? { get set }
    func start()
    func stop()
}

/// Coalesces rapid consecutive device-orientation changes into a single
/// dark-frame skip. Once a skip is requested, further changes within `window`
/// extend the burst and do not request another skip; the next change after a
/// quiet period arms a new one. This keeps one rotation gesture from consuming
/// multiple 900ms throttle slots.
struct CameraOrientationSkipCoalescer {
    static let window: CFTimeInterval = 2.0

    private var lastRequestAt: CFTimeInterval?

    mutating func shouldRequestSkip(at now: CFTimeInterval) -> Bool {
        if let lastRequestAt, now - lastRequestAt < Self.window {
            // Extend the burst so a slow continuous rotation still counts once.
            self.lastRequestAt = now
            return false
        }
        lastRequestAt = now
        return true
    }

    mutating func reset() {
        lastRequestAt = nil
    }
}

enum CameraFrameOrientation {
    /// Standard `UIDeviceOrientation` → `AVCaptureVideoOrientation` mapping:
    /// landscape left/right are intentionally swapped.
    static func videoOrientation(for device: CameraDeviceOrientation) -> AVCaptureVideoOrientation {
        switch device {
        case .portrait:
            return .portrait
        case .portraitUpsideDown:
            return .portraitUpsideDown
        case .landscapeLeft:
            return .landscapeRight
        case .landscapeRight:
            return .landscapeLeft
        }
    }

}
