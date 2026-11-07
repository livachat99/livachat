import AVFoundation
import Foundation
import LivaChatSDK

enum CameraPermissionState: Equatable {
    case unknown
    case undetermined
    case granted
    case denied
}

@MainActor
protocol CameraPermissionProviding: AnyObject {
    func current() -> CameraPermissionState
    func request(_ completion: @escaping @MainActor (CameraPermissionState) -> Void)
}

@MainActor
final class SystemCameraPermissionProvider: CameraPermissionProviding {
    func current() -> CameraPermissionState {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            return .granted
        case .notDetermined:
            return .undetermined
        case .denied, .restricted:
            return .denied
        @unknown default:
            return .unknown
        }
    }

    func request(_ completion: @escaping @MainActor (CameraPermissionState) -> Void) {
        AVCaptureDevice.requestAccess(for: .video) { granted in
            Task { @MainActor in
                completion(granted ? .granted : .denied)
            }
        }
    }
}

@MainActor
protocol CameraPreferencesProviding: AnyObject {
    var cameraDefaultFront: Bool { get }
    var cameraAutoOnJoin: Bool { get }
}

extension SettingsStore: CameraPreferencesProviding {}

protocol CameraCapturing: AnyObject {
    var onFailure: (@MainActor (AppErrorMessage) -> Void)? { get set }
    func start(front: Bool)
    func stop()
    func switchCamera(front: Bool)
}

protocol CameraPreviewing: AnyObject {
    var isFrontCamera: Bool { get }
    @MainActor func attachPreview(to layer: AVCaptureVideoPreviewLayer)
    @MainActor func detachPreview()
}

enum CameraCaptureError: LocalizedError {
    case permissionDenied
    case inputUnavailable
    case configurationFailed(Error)

    var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "Camera permission is required"
        case .inputUnavailable:
            return "Camera input is unavailable"
        case let .configurationFailed(error):
            return "Camera configuration failed: \(error.localizedDescription)"
        }
    }

    var appErrorMessage: AppErrorMessage {
        switch self {
        case .permissionDenied:
            return AppErrorMessage(
                "conv_camera_permission_denied",
                logDetail: errorDescription ?? "Camera permission is required"
            )
        case .inputUnavailable:
            return AppErrorMessage(
                "conv_camera_unavailable",
                logDetail: errorDescription ?? "Camera input is unavailable"
            )
        case let .configurationFailed(error):
            return AppErrorMessage(
                "conv_camera_start_failed",
                logDetail: "Camera configuration failed: \(error.localizedDescription)"
            )
        }
    }
}

struct CameraFramePolicy {
    static let warmupMs = 1_000
    static let throttleMs = 900
    static let maxLongSide = 1_280

    static func shouldSkipWarmup(elapsedMs: Int, warmupMs: Int = warmupMs) -> Bool {
        elapsedMs < warmupMs
    }

    static func shouldPush(nowMs: Int, lastPushMs: Int, throttleMs: Int = throttleMs) -> Bool {
        nowMs - lastPushMs >= throttleMs
    }

    static func scaledSize(
        width: Int,
        height: Int,
        maxLongSide: Int = maxLongSide
    ) -> (width: Int, height: Int)? {
        let longSide = max(width, height)
        guard longSide > maxLongSide, longSide > 0 else { return nil }
        let scale = Double(maxLongSide) / Double(longSide)
        return (
            max(1, Int((Double(width) * scale).rounded())),
            max(1, Int((Double(height) * scale).rounded()))
        )
    }

    static func shouldBumpTurn(lastPushedFacing: CameraFacing?, nextFacing: CameraFacing) -> Bool {
        guard let lastPushedFacing else { return false }
        return lastPushedFacing != nextFacing
    }
}

final class CaptureTurnCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var storedValue: UInt32 = 0

    var current: UInt32 {
        lock.lock()
        defer { lock.unlock() }
        return storedValue
    }

    @discardableResult
    func next() -> UInt32 {
        lock.lock()
        defer { lock.unlock() }
        storedValue = storedValue == .max ? 1 : storedValue + 1
        return storedValue
    }
}
