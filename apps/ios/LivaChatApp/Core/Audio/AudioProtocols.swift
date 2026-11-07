import AVFoundation
import Foundation
import LivaChatSDK

enum AudioFormats {
    /// SDK uplink format: 16 kHz mono signed 16-bit.
    static var capture: AVAudioFormat {
        AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16_000,
            channels: 1,
            interleaved: true
        )!
    }

    /// Player-node connection format. AVAudioPlayerNode's mixer bus rejects
    /// Int16; float32 mono at 16 kHz is converted by the engine to hardware.
    static var playback: AVAudioFormat {
        AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1)!
    }
}

enum CaptureLifecycleState: Equatable, Sendable {
    case stopped
    case recovering
    case running
}

protocol AudioCapturing: AnyObject {
    var lifecycleState: CaptureLifecycleState { get }
    var onLevel: (@MainActor (Int) -> Void)? { get set }
    var onFailure: (@MainActor (AppErrorMessage) -> Void)? { get set }
    func start() throws
    func stop()
    func setMuted(_ muted: Bool)
    func topUpSilenceWindow()
}

protocol AudioPlaying: AnyObject {
    var isRunning: Bool { get }
    var hasBufferedOrScheduledAudio: Bool { get }
    var onActivityChange: (@MainActor () -> Void)? { get set }
    func start()
    func stop()
    func setMuted(_ muted: Bool)
    func flush(reason: PlaybackFlushReason, epoch: UInt64)
    func setTTSInputExpected(_ expected: Bool)
    func resumePreservingBuffer()
    func prepareForAudioReconfiguration()
}

@MainActor
protocol AudioSessionControlling: AnyObject {
    var engine: AVAudioEngine { get }
    var playbackNode: AVAudioPlayerNode { get }
    var onInterruptionBegan: (() -> Void)? { get set }
    var onInterruptionEnded: (() -> Void)? { get set }
    var onRouteChanged: (() -> Void)? { get set }
    var onMediaServicesReset: (() -> Void)? { get set }
    var appliedCaptureSource: TranscribeAudioSource? { get }
    var isEngineRunning: Bool { get }
    func activate(captureSource source: TranscribeAudioSource) throws
    func startEngine() throws
    func stopEngine()
    func deactivate()
}

@MainActor
protocol HapticProviding: AnyObject {
    func lightTap()
}

@MainActor
protocol Interrupting: AnyObject {
    func start()
    func stop()
    func update(sessionState: SessionState, vadState: VadState)
    func setHapticEnabled(_ enabled: Bool)
}
