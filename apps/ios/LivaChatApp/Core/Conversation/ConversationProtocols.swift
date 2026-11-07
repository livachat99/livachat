import AVFoundation
import Foundation
import LivaChatSDK

protocol LivaChatSDKProviding: AnyObject, Sendable {
    var state: LivaChatState { get }
    var events: AsyncStream<LivaChatEvent> { get }
    func connect()
    func disconnect()
    func startSession(resumeSessionID: String?)
    func sendInterrupt(reason: InterruptReason)
    func sendSessionChange(
        mode: SessionMode,
        myLanguage: String?,
        localLanguage: String?,
        usecase: SessionUsecase?
    )
    func setAvdetectorEnabled(_ enabled: Bool)
}

extension LivaChatSDK: LivaChatSDKProviding {}

@MainActor
protocol MicPermissionProviding: AnyObject {
    func current() -> MicPermissionState
    func request(_ completion: @escaping @MainActor (MicPermissionState) -> Void)
}

@MainActor
final class SystemMicPermissionProvider: MicPermissionProviding {
    func current() -> MicPermissionState {
        switch AVAudioSession.sharedInstance().recordPermission {
        case .granted:
            return .granted
        case .denied:
            return .denied
        case .undetermined:
            return .undetermined
        @unknown default:
            return .unknown
        }
    }

    func request(_ completion: @escaping @MainActor (MicPermissionState) -> Void) {
        // AVAudioSession invokes this callback on a TCC work queue. Keep the
        // outer closure nonisolated; otherwise Swift 6 infers @MainActor from
        // this method and traps when the callback runs off the main thread.
        AVAudioSession.sharedInstance().requestRecordPermission { @Sendable granted in
            Task { @MainActor in
                completion(granted ? .granted : .denied)
            }
        }
    }
}
