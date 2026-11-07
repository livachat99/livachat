import Combine
import Foundation
import LivaChatSDK

@MainActor
final class ConversationCoordinator {
    private let sdk: LivaChatSDKProviding
    private let viewModel: ConversationViewModel
    private let capture: AudioCapturing
    private let playback: AudioPlaying
    private let audioSession: AudioSessionControlling
    private let haptics: HapticProviding
    private let isForeground: () -> Bool
    private let camera: (any CameraCapturing & CameraPreviewing)?
    private let cameraPreferences: CameraPreferencesProviding?
    private let gallerySender: GalleryPhotoSender?
    private let turnCounter: CaptureTurnCounter
    private let history: ConversationHistoryRecording?
    private let jobStatus: AgentJobStatusModel?
    private let modeModel: ConversationModeModel?
    private var historyCancellable: AnyCancellable?

    enum MediaPipelineState: Equatable {
        case stopped
        case full
    }

    private enum MediaReconcileReason {
        case background
        case foreground
        case muteChanged
        case sessionState
        case playbackActivity
        case sessionReady
        case connectionState
        case interruptionEnded
        case routeChanged
        case mediaServicesReset
        case retry
    }

    private(set) var isStarted = false
    private(set) var serverSessionID: String?
    private(set) var sessionReady = false
    private(set) var sessionEnded = false
    private var awaitingAuthFix = false
    private(set) var pipelineState: MediaPipelineState = .stopped
    var hasCameraResumeIntentForTesting: Bool { cameraResumeIntent }
    private var cameraResumeIntent = false
    private var cameraPausedForPhoto = false
    private var currentAIMessageID: String?
    private var currentAIText = ""
    private var currentAIImage: AiImage?
    private var currentAISource: AITextSource?
    private var appliedAudioSource: TranscribeAudioSource = .voice

    var hapticEnabled: Bool

    init(
        sdk: LivaChatSDKProviding,
        viewModel: ConversationViewModel,
        capture: AudioCapturing,
        playback: AudioPlaying,
        audioSession: AudioSessionControlling,
        haptics: HapticProviding,
        camera: (any CameraCapturing & CameraPreviewing)? = nil,
        cameraPreferences: CameraPreferencesProviding? = nil,
        gallerySender: GalleryPhotoSender? = nil,
        turnCounter: CaptureTurnCounter = CaptureTurnCounter(),
        hapticEnabled: Bool = true,
        history: ConversationHistoryRecording? = nil,
        jobStatus: AgentJobStatusModel? = nil,
        modeModel: ConversationModeModel? = nil,
        isForeground: @escaping () -> Bool = { true }
    ) {
        self.sdk = sdk
        self.viewModel = viewModel
        self.capture = capture
        self.playback = playback
        self.audioSession = audioSession
        self.haptics = haptics
        self.camera = camera
        self.cameraPreferences = cameraPreferences
        self.gallerySender = gallerySender
        self.turnCounter = turnCounter
        self.history = history
        self.jobStatus = jobStatus
        self.modeModel = modeModel
        self.hapticEnabled = hapticEnabled
        self.isForeground = isForeground
        camera?.onFailure = { [weak self] error in
            self?.handleCameraFailure(error)
        }
        if let modeModel {
            modeModel.sendRequest = { [weak self] request in
                self?.sdk.sendSessionChange(
                    mode: request.mode,
                    myLanguage: request.myLanguage,
                    localLanguage: request.localLanguage,
                    usecase: request.usecase
                )
            }
            modeModel.onEffectiveSourceChange = { [weak self] source in
                self?.applyAudioSource(source)
            }
        }
    }

    // MARK: - Lifecycle

    func start() {
        guard !isStarted else { return }
        isStarted = true
        installAudioHooks()
        subscribeToHistorySessionSwitches()
        modeModel?.sessionBecameNotReady()
        sessionReady = false
        sessionEnded = false
        awaitingAuthFix = false
        pipelineState = .stopped
        serverSessionID = nil
        currentAIMessageID = nil
        currentAIText = ""
        currentAIImage = nil
        currentAISource = nil

        viewModel.refreshMicPermission()
        viewModel.setRequiresServerConfig(false)
        viewModel.setSessionEnded(false)
        viewModel.setBusy(true)

        guard viewModel.micPermission == .granted else {
            viewModel.setBusy(false)
            viewModel.showBanner(.fatal(.micUnavailable, action: .openSystemSettings))
            return
        }

        if isForeground() {
            do {
                try startFullMediaFresh()
            } catch {
                teardownMedia()
                viewModel.setBusy(false)
                AppLog.error("Conversation", "media start failed: \(error.localizedDescription)")
                viewModel.showBanner(.fatal(.audioUnavailable, action: .retry))
                return
            }
        }

        sdk.connect()

        if isForeground(), cameraPreferences?.cameraAutoOnJoin == true {
            startCamera()
        }
    }

    func stop() {
        guard isStarted else { return }
        historyCancellable?.cancel()
        historyCancellable = nil
        history?.finishSession()
        jobStatus?.clear()
        modeModel?.sendRequest = nil
        modeModel?.onEffectiveSourceChange = nil
        modeModel?.reset()
        isStarted = false
        sessionReady = false
        sessionEnded = false
        serverSessionID = nil
        viewModel.setBusy(false)
        haltCamera()
        cameraPausedForPhoto = false
        teardownMedia()
        sdk.disconnect()
        uninstallAudioHooks()
    }

    func onBackground() {
        guard isStarted else { return }
        if viewModel.isCameraOn {
            cameraResumeIntent = true
            camera?.stop()
        }
        _ = reconcileMedia(reason: .background)
    }

    func onForeground() {
        guard isStarted, isForeground(), !sessionEnded, !awaitingAuthFix,
              viewModel.micPermission == .granted else { return }
        guard reconcileMedia(reason: .foreground) else { return }
        if sdk.state.connectionState == .disconnected {
            sdk.connect()
        }
        resumeCameraIfPossible()
    }

    func onMicPermissionLost() {
        guard isStarted else { return }
        haltCamera()
        teardownMedia()
    }

    func onMicPermissionRestored() {
        viewModel.refreshMicPermission()
        guard isStarted, viewModel.micPermission == .granted, !sessionEnded, !awaitingAuthFix else { return }
        guard reconcileMedia(reason: .foreground) else { return }
        if sdk.state.connectionState == .disconnected {
            sdk.connect()
        }
    }

    func retry() {
        guard isStarted, !awaitingAuthFix else { return }
        viewModel.dismissBanner()
        viewModel.setSessionEnded(false)
        sessionEnded = false
        guard viewModel.micPermission == .granted else {
            viewModel.showBanner(.fatal(.micUnavailable, action: .openSystemSettings))
            return
        }

        guard reconcileMedia(reason: .retry) else { return }

        switch sdk.state.connectionState {
        case .connected:
            guard !sessionReady else { return }
            modeModel?.sessionBecameNotReady()
            sessionReady = false
            serverSessionID = nil
            sdk.startSession(resumeSessionID: nil)
        case .disconnected:
            sdk.connect()
        case .connecting, .reconnecting:
            break
        }
    }

    // MARK: - User controls

    func toggleMic() {
        setMicMuted(!viewModel.isMicMuted)
    }

    func setMicMuted(_ muted: Bool) {
        viewModel.setMicMuted(muted)
        capture.setMuted(muted)
        _ = reconcileMedia(reason: .muteChanged)
    }

    func toggleSpeaker() {
        let muted = !viewModel.isSpeakerMuted
        viewModel.setSpeakerMuted(muted)
        playback.setMuted(muted)
        _ = reconcileMedia(reason: .muteChanged)
    }

    func startCamera() {
        guard isStarted, !viewModel.isCameraOn, let camera else { return }
        let front = cameraPreferences?.cameraDefaultFront ?? false
        cameraResumeIntent = false
        viewModel.setCameraState(isOn: true, isFront: front)
        turnCounter.next()
        AppLog.info("Conversation", "camera start front=\(front)")
        camera.start(front: front)
    }

    func stopCamera() {
        guard viewModel.isCameraOn else { return }
        camera?.stop()
        viewModel.setCameraState(isOn: false, isFront: viewModel.isFrontCamera)
        cameraResumeIntent = false
        AppLog.info("Conversation", "camera stop")
    }

    func switchCamera() {
        guard viewModel.isCameraOn else { return }
        let front = !viewModel.isFrontCamera
        viewModel.setFrontCamera(front)
        AppLog.info("Conversation", "camera switch front=\(front)")
        camera?.switchCamera(front: front)
    }

    func stopCameraForSettings() {
        guard viewModel.isCameraOn else { return }
        camera?.stop()
        viewModel.setCameraState(isOn: false, isFront: viewModel.isFrontCamera)
        cameraResumeIntent = false
        AppLog.info("Conversation", "camera stop for settings")
    }

    func onCameraPermissionLost() {
        haltCamera()
    }

    /// Pauses the live camera before the system photo picker opens; keeps the
    /// app in camera mode so the picker can resume it afterwards.
    @discardableResult
    func pauseCameraForPhoto() -> Bool {
        guard viewModel.isCameraOn, let camera else { return false }
        cameraPausedForPhoto = true
        cameraResumeIntent = true
        camera.stop()
        AppLog.info("Conversation", "camera paused for photo")
        return true
    }

    func resumeCameraAfterPhoto() {
        cameraPausedForPhoto = false
        resumeCameraIfPossible()
    }

    func sendGalleryPhoto(_ data: Data) {
        guard let gallerySender else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let encoded = GalleryPhotoEncoder.encode(data)
            Task { @MainActor [weak self] in
                guard let self else { return }
                guard let encoded else {
                    AppLog.warn("Conversation", "gallery photo encode failed")
                    self.viewModel.showBanner(
                        .recoverable(.server(titleKey: "cap_bubble_failed", detail: ""))
                    )
                    return
                }
                guard !self.viewModel.isCameraOn else {
                    AppLog.debug("Conversation", "gallery photo rejected: camera on")
                    return
                }
                AppLog.info(
                    "Conversation",
                    "gallery photo send \(encoded.width)x\(encoded.height) bytes=\(encoded.data.count)"
                )
                gallerySender.send(encoded)
            }
        }
    }

    // MARK: - SDK events

    func handle(_ event: LivaChatEvent) {
        switch event {
        case .transcription, .aiText:
            guard history?.isSuppressingMessages != true else { return }
        default:
            break
        }

        switch event {
        case let .connectionStateChanged(state):
            AppLog.info("Conversation", "connection state=\(String(describing: state))")
            viewModel.applyConnectionState(state)
            if state != .connected {
                jobStatus?.clear()
                modeModel?.sessionBecameNotReady()
                sessionReady = false
                playback.flush(reason: .sessionEnded, epoch: sdk.state.playbackEpoch)
            }
            updateTTSInputExpected()
            _ = reconcileMedia(reason: .connectionState)

        case .connectionEpochAdvanced:
            guard isStarted, !awaitingAuthFix, !sessionEnded,
                  sdk.state.connectionState == .connected else { return }
            // A new connection epoch always starts a fresh session; the server
            // resets its mode, so drop readiness until the new session.ready.
            modeModel?.sessionBecameNotReady()
            sessionReady = false
            updateTTSInputExpected()
            sdk.startSession(resumeSessionID: serverSessionID)

        case let .connectionTerminated(reason):
            AppLog.error("Conversation", "connection terminated reason=\(String(describing: reason))")
            jobStatus?.clear()
            modeModel?.sessionBecameNotReady()
            switch reason {
            case .authFailed:
                awaitingAuthFix = true
                serverSessionID = nil
                sessionReady = false
                haltCamera()
                teardownMedia()
                viewModel.setBusy(false)
                viewModel.showBanner(.fatal(.authFailed, action: .openSettings))
            case .reconnectExhausted:
                haltCamera()
                teardownMedia()
                viewModel.setBusy(false)
                viewModel.showBanner(.fatal(.connectionFailed, action: .retry))
            case .serverClosed:
                serverSessionID = nil
                sessionReady = false
                sessionEnded = true
                viewModel.setSessionEnded(true)
                haltCamera()
                teardownMedia()
                modeModel?.sessionEnded()
                viewModel.setBusy(false)
                viewModel.showBanner(.fatal(.connectionClosed, action: .retry))
            }

        case let .sessionStateChanged(state):
            let wasSpeaking = viewModel.sessionState == .speaking
            AppLog.info("Conversation", "session state=\(String(describing: state))")
            viewModel.applySessionState(state)
            if state == .speaking, !wasSpeaking, hapticEnabled {
                haptics.lightTap()
            }
            updateTTSInputExpected()
            if !isForeground(),
               pipelineState == .stopped,
               state == .speaking,
               !viewModel.isMicMuted {
                _ = reconcileMedia(reason: .sessionState)
            }

        case .localVadStateChanged:
            // Local VAD barge-in is intentionally disabled; haptics fire on
            // entering SPEAKING (aligned with Android).
            break

        case let .sessionReady(sessionID, restored):
            AppLog.info("Conversation", "session ready restored=\(restored) id=\(sessionID.prefix(12))")
            serverSessionID = sessionID
            sessionReady = true
            sessionEnded = false
            viewModel.markSessionReady()
            viewModel.setSessionEnded(false)
            viewModel.applySessionState(.listening)
            viewModel.setBusy(false)
            modeModel?.sessionBecameReady()
            updateTTSInputExpected()
            if isForeground(), pipelineState == .stopped {
                _ = reconcileMedia(reason: .sessionReady)
            }

        case .sessionEnded:
            jobStatus?.clear()
            serverSessionID = nil
            sessionReady = false
            sessionEnded = true
            viewModel.applySessionState(.idle)
            viewModel.setSessionEnded(true)
            viewModel.clearTransient()
            haltCamera()
            teardownMedia()
            modeModel?.sessionEnded()
            viewModel.setBusy(false)
            viewModel.showBanner(
                ConversationBanner(
                    kind: .warning,
                    message: .sessionEnded,
                    action: .retry,
                    autoDismissAfter: nil
                )
            )

        case let .transcription(text, isFinal, transcriptionID):
            guard canAcceptMediaEvent(isFinal: isFinal) else { return }
            if isFinal {
                let timestamp = Date()
                let mode = userMessageMode()
                viewModel.finishUserTranscription(
                    id: transcriptionID,
                    text: text,
                    timestamp: timestamp,
                    mode: mode
                )
                history?.recordUserMessage(
                    id: "user-\(transcriptionID)",
                    text: text,
                    timestamp: timestamp,
                    mode: mode
                )
            } else {
                viewModel.setTranscription(text)
            }

        case let .aiText(text, messageID, index, isFinal, image, source):
            guard canAcceptMediaEvent(isFinal: isFinal) else { return }
            if index == 0 || currentAIMessageID != messageID {
                currentAIMessageID = messageID
                currentAIText = text
                currentAIImage = image
                currentAISource = source
            } else {
                currentAIText += text
                if let image {
                    currentAIImage = image
                }
                if let source {
                    currentAISource = source
                }
            }
            viewModel.setStreamingAI(messageID: messageID, text: currentAIText)
            if isFinal {
                let timestamp = Date()
                let mode = assistantMessageMode()
                viewModel.finishAssistantMessage(
                    messageID: messageID,
                    text: currentAIText,
                    image: currentAIImage,
                    source: currentAISource,
                    timestamp: timestamp,
                    mode: mode
                )
                history?.recordAssistantMessage(
                    id: "ai-\(messageID)",
                    text: currentAIText,
                    timestamp: timestamp,
                    image: currentAIImage,
                    source: currentAISource,
                    mode: mode
                )
                resetAIStreaming()
            }

        case let .playbackShouldFlush(reason, epoch):
            playback.flush(reason: reason, epoch: epoch)
            updateTTSInputExpected()

        case let .error(code, message, fatal, recoverable):
            AppLog.error("Conversation", "SDK error code=\(code) fatal=\(fatal) message=\(message)")
            modeModel?.handleRejection(code: code)
            let bannerMessage = ConversationBannerMessage.server(
                titleKey: fatal ? "conv_service_error" : "conv_service_warning",
                detail: message
            )
            if fatal || !recoverable {
                viewModel.showBanner(.fatal(bannerMessage))
            } else {
                viewModel.showBanner(.recoverable(bannerMessage))
            }

        case let .jobStatus(jobs):
            guard isStarted, sdk.state.connectionState == .connected else { return }
            jobStatus?.replaceJobs(jobs)

        case let .sessionChanged(mode, _, _, usecase):
            modeModel?.applyEcho(mode: mode, usecase: usecase)

        case .videoAck:
            break
        }
    }

    // MARK: - Media pipeline

    private var isSessionMediaEligible: Bool {
        isStarted
            && !sessionEnded
            && !awaitingAuthFix
            && viewModel.micPermission == .granted
    }

    private func desiredForegroundPipeline() -> MediaPipelineState {
        isSessionMediaEligible ? .full : .stopped
    }

    /// Background policy: keep the full pipeline (mic + playback) while the
    /// mic is live; release the microphone and stop media when it is muted.
    /// VPIO is full-duplex, so a single engine cannot keep playing while the
    /// microphone is released.
    private func desiredBackgroundPipeline() -> MediaPipelineState {
        guard isSessionMediaEligible else { return .stopped }
        return viewModel.isMicMuted ? .stopped : .full
    }

    @discardableResult
    private func reconcileMedia(reason: MediaReconcileReason) -> Bool {
        guard isStarted else {
            teardownMedia()
            return false
        }
        let foreground = isForeground()
        let target = foreground
            ? desiredForegroundPipeline()
            : desiredBackgroundPipeline()
        AppLog.info(
            "Conversation",
            "media reconcile reason=\(reason) target=\(target) current=\(pipelineState)"
        )

        // Preserve the one-second silence window before the microphone is
        // released on the muted-background transition.
        if !foreground, pipelineState == .full, target != .full, viewModel.isMicMuted {
            capture.topUpSilenceWindow()
        }

        let ok: Bool
        switch target {
        case .stopped:
            teardownMedia()
            ok = false
        case .full:
            ok = transitionToFull()
        }

        updateTTSInputExpected()
        if ok {
            viewModel.clearAudioErrorBanner()
        }
        return ok
    }

    private func startFullMediaFresh() throws {
        try audioSession.activate(captureSource: appliedAudioSource)
        capture.setMuted(viewModel.isMicMuted)
        try startCaptureAndConfirmEngine()
        playback.start()
        playback.setMuted(viewModel.isSpeakerMuted)
        pipelineState = .full
        AppLog.info("Conversation", "pipeline full (fresh)")
    }

    /// `AudioCaptureController.start()` reports transient engine failures
    /// through its bounded retry loop and returns while `lifecycleState` is
    /// `.recovering`. A repeated `engine.start()` must not escalate that state
    /// into a full teardown; the retry loop owns the restart and the playback
    /// pump resumes once it observes the engine running.
    private func startCaptureAndConfirmEngine() throws {
        try capture.start()
        do {
            try audioSession.startEngine()
        } catch {
            guard capture.lifecycleState == .recovering else { throw error }
            AppLog.warn(
                "Conversation",
                "engine start deferred to capture retries: \(error.localizedDescription)"
            )
        }
        // capture.start() established a new generation; reset synchronously on
        // MainActor because the controller's zero baseline is delivered
        // asynchronously and the previous level must not linger until then.
        viewModel.resetMicLevel()
    }

    private func transitionToFull() -> Bool {
        guard isSessionMediaEligible else {
            teardownMedia()
            return false
        }
        let needsSessionApply =
            audioSession.appliedCaptureSource != appliedAudioSource

        // A recovering capture pipeline should not be canceled by a routine
        // foreground/reconcile pass. Keep the playback pump alive; its
        // engine-running guard resumes audio when capture restarts the engine.
        if pipelineState == .full, !needsSessionApply, capture.lifecycleState == .recovering {
            if !playback.isRunning {
                playback.resumePreservingBuffer()
            }
            playback.setMuted(viewModel.isSpeakerMuted)
            return true
        }

        if pipelineState == .full, !needsSessionApply, capture.lifecycleState == .running {
            do {
                if !audioSession.isEngineRunning {
                    // Reinstall the tap as well as restarting the engine:
                    // a stopped engine may retain stale input configuration.
                    playback.prepareForAudioReconfiguration()
                    capture.stop()
                    capture.setMuted(viewModel.isMicMuted)
                    try startCaptureAndConfirmEngine()
                    playback.resumePreservingBuffer()
                } else if !playback.isRunning {
                    playback.resumePreservingBuffer()
                }
                capture.setMuted(viewModel.isMicMuted)
                playback.setMuted(viewModel.isSpeakerMuted)
                return true
            } catch {
                AppLog.error("Conversation", "full pipeline health recovery failed: \(error.localizedDescription)")
                // Playback was already prepared before capture.stop(); the
                // fall-through reconfigure below owns stop/start cleanup.
                capture.stop()
                audioSession.stopEngine()
            }
        }

        do {
            try reconfigureFullMedia()
            return true
        } catch {
            AppLog.error("Conversation", "full media failed: \(error.localizedDescription)")
            teardownMedia()
            showAudioFailureIfForeground()
            return false
        }
    }

    /// Stops playback and capture, reconfigures the single audio engine for
    /// `source`, then restarts capture and playback in order. The engine must
    /// be running before `play()`; capture installs and starts it first.
    /// Coming from `.stopped` discards any audio buffered while media was off
    /// (for example TTS that arrived while backgrounded with the mic muted).
    private func reconfigureFullMedia(source: TranscribeAudioSource? = nil) throws {
        let wasStopped = pipelineState == .stopped
        // `.stopped` implies playback.stop() already ran; start() below is
        // destructive, so preparing again only repeats that teardown.
        if !wasStopped {
            playback.prepareForAudioReconfiguration()
        }
        if capture.lifecycleState != .stopped {
            capture.stop()
        }
        audioSession.stopEngine()
        try audioSession.activate(captureSource: source ?? appliedAudioSource)
        capture.setMuted(viewModel.isMicMuted)
        try startCaptureAndConfirmEngine()
        playback.setMuted(viewModel.isSpeakerMuted)
        if wasStopped {
            playback.start()
        } else {
            playback.resumePreservingBuffer()
        }
        pipelineState = .full
        AppLog.info("Conversation", "pipeline full")
    }

    /// Destructive teardown for stop/session end/failure. Idempotent and never
    /// skipped based on a "media started" flag.
    private func teardownMedia() {
        playback.setTTSInputExpected(false)
        capture.stop()
        viewModel.resetMicLevel()
        playback.stop()
        audioSession.stopEngine()
        audioSession.deactivate()
        pipelineState = .stopped
        AppLog.info("Conversation", "media teardown")
    }

    private func showAudioFailureIfForeground() {
        guard isForeground() else { return }
        viewModel.showBanner(.fatal(.audioUnavailable, action: .retry))
    }

    private func updateTTSInputExpected() {
        let expected = sessionReady
            && sdk.state.connectionState == .connected
            && sdk.state.sessionState == .speaking
        playback.setTTSInputExpected(expected)
    }

    private func handlePlaybackActivity() {
        guard isStarted, isSessionMediaEligible else { return }
        // Background playback only exists while the mic is live.
        if !isForeground(), viewModel.isMicMuted { return }
        if pipelineState == .stopped, playback.hasBufferedOrScheduledAudio {
            _ = reconcileMedia(reason: .playbackActivity)
        }
        updateTTSInputExpected()
    }

    private func resumeCameraIfPossible() {
        guard !cameraPausedForPhoto, cameraResumeIntent else { return }
        guard isStarted else {
            cameraResumeIntent = false
            return
        }
        // Background audio events and a photo flow finishing while
        // backgrounded must not consume the resume intent.
        guard isForeground() else { return }
        guard !sessionEnded, !awaitingAuthFix,
              viewModel.isCameraOn, viewModel.cameraPermission == .granted,
              let camera else {
            cameraResumeIntent = false
            viewModel.setCameraState(isOn: false, isFront: viewModel.isFrontCamera)
            return
        }
        cameraResumeIntent = false
        AppLog.info("Conversation", "camera resume front=\(viewModel.isFrontCamera)")
        camera.start(front: viewModel.isFrontCamera)
    }

    private func haltCamera() {
        camera?.stop()
        cameraResumeIntent = false
        if viewModel.isCameraOn {
            viewModel.setCameraState(isOn: false, isFront: viewModel.isFrontCamera)
        }
    }

    private func handleCameraFailure(_ error: AppErrorMessage) {
        // CameraController already logs the technical detail.
        cameraResumeIntent = false
        viewModel.setCameraState(isOn: false, isFront: viewModel.isFrontCamera)
        // Only a permission denial is recoverable through system settings;
        // input/configuration failures would land the user in an unrelated
        // screen, so leave the banner informational (tap camera to retry).
        let action: BannerAction? = error.key == "conv_camera_permission_denied"
            ? .openSystemSettings
            : nil
        viewModel.showBanner(.fatal(.cameraStartFailed(error), action: action))
    }

    private func installAudioHooks() {
        capture.onLevel = { [weak self] level in
            self?.viewModel.updateMicLevel(level)
        }
        capture.onFailure = { [weak self] error in
            guard let self else { return }
            self.teardownMedia()
            if self.isForeground() {
                self.viewModel.showBanner(.fatal(.captureFailed(error), action: .retry))
            }
        }
        audioSession.onInterruptionBegan = { [weak self] in
            guard let self else { return }
            if self.viewModel.isCameraOn {
                self.cameraResumeIntent = true
                self.camera?.stop()
            }
            self.teardownMedia()
        }
        audioSession.onInterruptionEnded = { [weak self] in
            guard let self else { return }
            if self.reconcileMedia(reason: .interruptionEnded), self.isForeground() {
                self.resumeCameraIfPossible()
            }
        }
        audioSession.onRouteChanged = { [weak self] in
            _ = self?.reconcileMedia(reason: .routeChanged)
        }
        audioSession.onMediaServicesReset = { [weak self] in
            _ = self?.reconcileMedia(reason: .mediaServicesReset)
        }
        playback.onActivityChange = { [weak self] in
            self?.handlePlaybackActivity()
        }
    }

    private func uninstallAudioHooks() {
        capture.onLevel = nil
        capture.onFailure = nil
        audioSession.onInterruptionBegan = nil
        audioSession.onInterruptionEnded = nil
        audioSession.onRouteChanged = nil
        audioSession.onMediaServicesReset = nil
        playback.onActivityChange = nil
    }

    private func canAcceptMediaEvent(isFinal: Bool) -> Bool {
        guard isStarted else { return false }
        if isFinal {
            return true
        }
        return sessionReady && sdk.state.connectionState == .connected
    }

    private func subscribeToHistorySessionSwitches() {
        historyCancellable = history?.sessionSwitched.sink { [weak self] seed in
            MainActor.assumeIsolated {
                guard let self, self.isStarted else { return }
                self.jobStatus?.clear()
                self.resetAIStreaming()
                self.viewModel.replaceConversation(with: seed.messages)
            }
        }
    }

    private func resetAIStreaming() {
        currentAIMessageID = nil
        currentAIText = ""
        currentAIImage = nil
        currentAISource = nil
    }

    private func userMessageMode() -> ChatMessageMode? {
        guard let modeModel else { return nil }
        if modeModel.transcribeEnabled { return .transcribe }
        if modeModel.translateEnabled { return .translate }
        return nil
    }

    private func assistantMessageMode() -> ChatMessageMode? {
        guard modeModel?.translateEnabled == true else { return nil }
        return .translate
    }

    private func applyAudioSource(_ source: TranscribeAudioSource) {
        guard appliedAudioSource != source else { return }
        guard isStarted, !sessionEnded, !awaitingAuthFix else { return }

        let previous = appliedAudioSource
        appliedAudioSource = source
        AppLog.info(
            "Conversation",
            "audio source=\(source.rawValue) pipeline=\(pipelineState)"
        )

        // Media is not running when stopped. Store the new source for the
        // next full transition.
        guard pipelineState == .full else { return }

        do {
            try reconfigureFullMedia(source: source)
        } catch {
            let sourceError = error
            AppLog.error(
                "Conversation",
                "audio source switch failed: \(sourceError.localizedDescription)"
            )
            appliedAudioSource = previous
            modeModel?.noteTranscribeSourceFailure()

            do {
                try reconfigureFullMedia(source: previous)
            } catch {
                teardownMedia()
                showAudioFailureIfForeground()
                return
            }
            viewModel.showBanner(
                .recoverable(
                    .server(
                        titleKey: "conv_audio_source_failed",
                        detail: ""
                    )
                )
            )
        }
    }
}
