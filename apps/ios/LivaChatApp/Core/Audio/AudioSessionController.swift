import AVFoundation
import Foundation

// MARK: - Test / portability seams

@MainActor
protocol AudioSessionBacking: AnyObject {
    var category: AVAudioSession.Category { get }
    var mode: AVAudioSession.Mode { get }
    var categoryOptions: AVAudioSession.CategoryOptions { get }
    func setCategory(
        _ category: AVAudioSession.Category,
        mode: AVAudioSession.Mode,
        options: AVAudioSession.CategoryOptions
    ) throws
    func setActive(_ active: Bool, options: AVAudioSession.SetActiveOptions) throws
}

@MainActor
protocol VoiceProcessingBacking: AnyObject {
    var isEnabled: Bool { get }
    func setEnabled(_ enabled: Bool) throws
}

@MainActor
private final class AVAudioSessionBackingAdapter: AudioSessionBacking {
    private let session: AVAudioSession

    init(session: AVAudioSession) {
        self.session = session
    }

    var category: AVAudioSession.Category { session.category }
    var mode: AVAudioSession.Mode { session.mode }
    var categoryOptions: AVAudioSession.CategoryOptions { session.categoryOptions }

    func setCategory(
        _ category: AVAudioSession.Category,
        mode: AVAudioSession.Mode,
        options: AVAudioSession.CategoryOptions
    ) throws {
        try session.setCategory(category, mode: mode, options: options)
    }

    func setActive(_ active: Bool, options: AVAudioSession.SetActiveOptions) throws {
        try session.setActive(active, options: options)
    }
}

@MainActor
private final class EngineVoiceProcessingBacking: VoiceProcessingBacking {
    private let inputNode: AVAudioInputNode

    init(inputNode: AVAudioInputNode) {
        self.inputNode = inputNode
    }

    var isEnabled: Bool { inputNode.isVoiceProcessingEnabled }

    func setEnabled(_ enabled: Bool) throws {
        try inputNode.setVoiceProcessingEnabled(enabled)
    }
}

// MARK: - Configuration

struct AudioSessionDescriptor: Equatable {
    let category: AVAudioSession.Category
    let mode: AVAudioSession.Mode
    let options: AVAudioSession.CategoryOptions
    let wantsVoiceProcessing: Bool
}

enum AudioConfigurationApplyPath: String, Equatable {
    case noChange
    case inPlace
    case reactivated
}

enum AudioSessionControllerError: LocalizedError {
    case engineMustBeStoppedForVoiceProcessing
    case rollbackFailed(original: Error, rollback: Error)

    var errorDescription: String? {
        switch self {
        case .engineMustBeStoppedForVoiceProcessing:
            return "Voice processing can only be changed while the audio engine is stopped"
        case let .rollbackFailed(original, rollback):
            return "Audio session apply failed (\(original.localizedDescription)) and rollback failed (\(rollback.localizedDescription))"
        }
    }
}

@MainActor
final class AudioSessionController: NSObject, AudioSessionControlling {

    let engine: AVAudioEngine
    let playbackNode: AVAudioPlayerNode
    private let session: AVAudioSession
    private let sessionBacking: AudioSessionBacking
    private var voiceProcessingBacking: VoiceProcessingBacking?
    private let engineRunningOverride: (() -> Bool)?
    private var routeDebounce: Task<Void, Never>?
    private var sessionActive = false
    private var suppressRouteChangesUntil = Date.distantPast
    private(set) var voiceProcessingEnabled = false
    private(set) var appliedCaptureSource: TranscribeAudioSource?
    private(set) var lastApplyPath: AudioConfigurationApplyPath = .noChange

    var onInterruptionBegan: (() -> Void)?
    var onInterruptionEnded: (() -> Void)?
    var onRouteChanged: (() -> Void)?
    var onMediaServicesReset: (() -> Void)?

    init(
        engine: AVAudioEngine = AVAudioEngine(),
        session: AVAudioSession = .sharedInstance(),
        sessionBacking: AudioSessionBacking? = nil,
        voiceProcessingBacking: VoiceProcessingBacking? = nil,
        engineRunningOverride: (() -> Bool)? = nil
    ) {
        self.engine = engine
        self.playbackNode = AVAudioPlayerNode()
        self.session = session
        self.sessionBacking = sessionBacking ?? AVAudioSessionBackingAdapter(session: session)
        self.voiceProcessingBacking = voiceProcessingBacking
        self.engineRunningOverride = engineRunningOverride
        super.init()

        if let voiceProcessingBacking {
            voiceProcessingEnabled = voiceProcessingBacking.isEnabled
        }

        engine.attach(playbackNode)
        engine.connect(playbackNode, to: engine.mainMixerNode, format: AudioFormats.playback)
        registerObservers()
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }

    var isEngineRunning: Bool {
        engineRunningOverride?() ?? engine.isRunning
    }

    static func descriptor(for source: TranscribeAudioSource) -> AudioSessionDescriptor {
        let mode: AVAudioSession.Mode = source == .voice ? .voiceChat : .measurement
        return AudioSessionDescriptor(
            category: .playAndRecord,
            mode: mode,
            options: [.defaultToSpeaker, .allowBluetoothHFP, .allowBluetoothA2DP],
            wantsVoiceProcessing: source == .voice
        )
    }

    func activate(captureSource source: TranscribeAudioSource) throws {
        let target = Self.descriptor(for: source)
        let current = AudioSessionDescriptor(
            category: sessionBacking.category,
            mode: sessionBacking.mode,
            options: sessionBacking.categoryOptions,
            wantsVoiceProcessing: effectiveVoiceProcessingBacking.isEnabled
        )

        if sessionActive, current == target {
            appliedCaptureSource = source
            lastApplyPath = .noChange
            return
        }

        let voiceProcessingChange = current.wantsVoiceProcessing != target.wantsVoiceProcessing
        if voiceProcessingChange, isEngineRunning {
            throw AudioSessionControllerError.engineMustBeStoppedForVoiceProcessing
        }

        let previous = (descriptor: current, active: sessionActive, applied: appliedCaptureSource)
        suppressRouteChangesUntil = Date().addingTimeInterval(1.5)

        do {
            // Disable first, while the engine is guaranteed to be stopped.
            if voiceProcessingChange, !target.wantsVoiceProcessing {
                try effectiveVoiceProcessingBacking.setEnabled(false)
                voiceProcessingEnabled = false
            }

            let path = try setCategory(
                target,
                currentlyActive: sessionActive
            )
            lastApplyPath = path

            if !sessionActive {
                try sessionBacking.setActive(true, options: [])
                sessionActive = true
                if path == .noChange {
                    lastApplyPath = .inPlace
                }
            }

            if target.wantsVoiceProcessing, voiceProcessingChange {
                do {
                    try effectiveVoiceProcessingBacking.setEnabled(true)
                    voiceProcessingEnabled = true
                    AppLog.info("AudioSession", "voice processing enabled")
                } catch {
                    // AEC is an optimization for voice capture; keep the
                    // existing non-fatal behavior when enabling it.
                    AppLog.error(
                        "AudioSession",
                        "voice processing unavailable: \(error.localizedDescription)"
                    )
                }
            } else if !target.wantsVoiceProcessing {
                voiceProcessingEnabled = effectiveVoiceProcessingBacking.isEnabled
            }

            appliedCaptureSource = source
        } catch {
            let original = error
            do {
                try rollback(to: previous)
            } catch {
                throw AudioSessionControllerError.rollbackFailed(
                    original: original,
                    rollback: error
                )
            }
            throw original
        }
    }

    private func setCategory(
        _ descriptor: AudioSessionDescriptor,
        currentlyActive: Bool
    ) throws -> AudioConfigurationApplyPath {
        if currentlyActive {
            do {
                try sessionBacking.setCategory(
                    descriptor.category,
                    mode: descriptor.mode,
                    options: descriptor.options
                )
                return .inPlace
            } catch {
                AppLog.warn(
                    "AudioSession",
                    "in-place category change failed; reactivating: \(error.localizedDescription)"
                )
            }
        } else {
            try sessionBacking.setCategory(
                descriptor.category,
                mode: descriptor.mode,
                options: descriptor.options
            )
            return .inPlace
        }

        try sessionBacking.setActive(false, options: [.notifyOthersOnDeactivation])
        sessionActive = false
        try sessionBacking.setCategory(
            descriptor.category,
            mode: descriptor.mode,
            options: descriptor.options
        )
        try sessionBacking.setActive(true, options: [])
        sessionActive = true
        return .reactivated
    }

    private func rollback(
        to previous: (
            descriptor: AudioSessionDescriptor,
            active: Bool,
            applied: TranscribeAudioSource?
        )
    ) throws {
        if effectiveVoiceProcessingBacking.isEnabled != previous.descriptor.wantsVoiceProcessing {
            try effectiveVoiceProcessingBacking.setEnabled(previous.descriptor.wantsVoiceProcessing)
        }

        try sessionBacking.setCategory(
            previous.descriptor.category,
            mode: previous.descriptor.mode,
            options: previous.descriptor.options
        )

        if previous.active != sessionActive {
            try sessionBacking.setActive(
                previous.active,
                options: previous.active ? [] : [.notifyOthersOnDeactivation]
            )
            sessionActive = previous.active
        }

        voiceProcessingEnabled = effectiveVoiceProcessingBacking.isEnabled
        appliedCaptureSource = previous.applied
    }

    private var effectiveVoiceProcessingBacking: VoiceProcessingBacking {
        if let voiceProcessingBacking {
            return voiceProcessingBacking
        }
        let backing = EngineVoiceProcessingBacking(inputNode: engine.inputNode)
        voiceProcessingBacking = backing
        return backing
    }

    func startEngine() throws {
        guard !isEngineRunning else { return }
        engine.prepare()
        try engine.start()
        suppressRouteChangesUntil = Date().addingTimeInterval(1.5)
        AppLog.info(
            "AudioSession",
            "engine started, input=\(self.engine.inputNode.outputFormat(forBus: 0).description)"
        )
    }

    func stopEngine() {
        suppressRouteChangesUntil = Date().addingTimeInterval(0.5)
        routeDebounce?.cancel()
        routeDebounce = nil
        if isEngineRunning {
            engine.stop()
            AppLog.info("AudioSession", "engine stopped")
        }
    }

    func deactivate() {
        routeDebounce?.cancel()
        routeDebounce = nil
        do {
            try sessionBacking.setActive(false, options: [.notifyOthersOnDeactivation])
        } catch {
            AppLog.error("AudioSession", "deactivate failed: \(error.localizedDescription)")
        }
        sessionActive = false
        appliedCaptureSource = nil
    }

    private func registerObservers() {
        let center = NotificationCenter.default
        center.addObserver(
            self,
            selector: #selector(handleInterruption(_:)),
            name: AVAudioSession.interruptionNotification,
            object: session
        )
        center.addObserver(
            self,
            selector: #selector(handleRouteChange(_:)),
            name: AVAudioSession.routeChangeNotification,
            object: session
        )
        center.addObserver(
            self,
            selector: #selector(handleMediaServicesReset(_:)),
            name: AVAudioSession.mediaServicesWereResetNotification,
            object: session
        )
        center.addObserver(
            self,
            selector: #selector(handleEngineConfigurationChange(_:)),
            name: .AVAudioEngineConfigurationChange,
            object: engine
        )
    }

    @objc private nonisolated func handleInterruption(_ notification: Notification) {
        let typeRaw = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
        let optionsRaw = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt
        Task { @MainActor [weak self] in
            guard let self, let typeRaw,
                  let type = AVAudioSession.InterruptionType(rawValue: typeRaw) else { return }
            switch type {
            case .began:
                self.onInterruptionBegan?()
            case .ended:
                let options = AVAudioSession.InterruptionOptions(rawValue: optionsRaw ?? 0)
                if options.contains(.shouldResume) {
                    self.onInterruptionEnded?()
                }
            @unknown default:
                break
            }
        }
    }

    @objc private nonisolated func handleRouteChange(_ notification: Notification) {
        let reasonRaw = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
        Task { @MainActor [weak self] in
            guard let self, self.sessionActive else { return }
            guard Date() >= self.suppressRouteChangesUntil else {
                AppLog.info("AudioSession", "route change suppressed: \(reasonRaw ?? 0)")
                return
            }
            AppLog.info("AudioSession", "route changed: \(reasonRaw ?? 0)")
            self.scheduleRouteChange()
        }
    }

    @objc private nonisolated func handleEngineConfigurationChange(_ notification: Notification) {
        Task { @MainActor [weak self] in
            guard let self, self.sessionActive else { return }
            guard Date() >= self.suppressRouteChangesUntil else {
                AppLog.info("AudioSession", "engine configuration change suppressed")
                return
            }
            AppLog.info("AudioSession", "engine configuration change")
            self.scheduleRouteChange()
        }
    }

    @objc private nonisolated func handleMediaServicesReset(_ notification: Notification) {
        Task { @MainActor [weak self] in
            AppLog.error("AudioSession", "media services reset")
            guard let self else { return }
            self.routeDebounce?.cancel()
            self.routeDebounce = nil
            self.engine.stop()
            self.sessionActive = false
            self.voiceProcessingEnabled = false
            self.appliedCaptureSource = nil
            self.onMediaServicesReset?()
        }
    }

    private func scheduleRouteChange() {
        routeDebounce?.cancel()
        routeDebounce = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled, let self else { return }
            guard self.sessionActive else { return }
            guard Date() >= self.suppressRouteChangesUntil else { return }
            self.onRouteChanged?()
        }
    }
}
