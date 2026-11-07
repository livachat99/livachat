import Combine
import Foundation
import LivaChatSDK

@MainActor
final class ConversationViewModel: ObservableObject {
    @Published private(set) var connectionState: ConnectionState = .disconnected
    @Published private(set) var sessionState: SessionState = .idle
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var currentTranscription: String = ""
    @Published private(set) var isTranscriptionFinal = false
    @Published private(set) var streamingAIText: StreamingAIText?
    @Published private(set) var isMicMuted = false
    @Published private(set) var isSpeakerMuted = false
    /// Leaf model for the mic level glyph. Not `@Published`: only
    /// `MicVolumeGlyph` observes it, so the ~20Hz level stream cannot
    /// re-render the whole conversation view.
    let micMeter = MicLevelModel()
    @Published private(set) var banner: ConversationBanner?
    @Published private(set) var isFirstSession = true
    @Published private(set) var requiresServerConfig = false
    @Published private(set) var micPermission: MicPermissionState = .unknown
    @Published private(set) var sessionEnded = false
    @Published private(set) var isBusy = false
    @Published private(set) var isCameraOn = false
    @Published private(set) var isFrontCamera = false
    @Published private(set) var cameraPermission: CameraPermissionState = .unknown
    @Published private(set) var cameraPermissionAlert = false
    @Published private(set) var cameraPreviewSource: CameraPreviewing?
    @Published private(set) var galleryPickerPresented = false
    @Published private(set) var galleryPreviewData: Data?
    @Published private(set) var agentJobResultMessageID: String?
    @Published private(set) var serverBaseURL: String = ""

    private let permissionProvider: MicPermissionProviding
    private let cameraPermissionProvider: CameraPermissionProviding
    private let bannerAutoDismissInterval: TimeInterval
    private let now: () -> Date
    private var bannerDismissTask: Task<Void, Never>?
    private var pendingGalleryPreviewData: Data?
    private var pendingPickerAfterPreview = false
    private weak var runtime: ConversationRuntime?

    init(
        permissionProvider: MicPermissionProviding,
        cameraPermissionProvider: CameraPermissionProviding? = nil,
        bannerAutoDismissInterval: TimeInterval = ConversationBanner.recoverableDuration,
        now: @escaping () -> Date = Date.init
    ) {
        self.permissionProvider = permissionProvider
        let cameraProvider = cameraPermissionProvider ?? SystemCameraPermissionProvider()
        self.cameraPermissionProvider = cameraProvider
        self.bannerAutoDismissInterval = bannerAutoDismissInterval
        self.now = now
        self.micPermission = permissionProvider.current()
        self.cameraPermission = cameraProvider.current()
    }

    func refreshMicPermission() {
        micPermission = permissionProvider.current()
    }

    func setMicPermission(_ permission: MicPermissionState) {
        micPermission = permission
    }

    func refreshCameraPermission() {
        cameraPermission = cameraPermissionProvider.current()
    }

    func setCameraPermission(_ permission: CameraPermissionState) {
        cameraPermission = permission
    }

    func setCameraState(isOn: Bool, isFront: Bool) {
        isCameraOn = isOn
        isFrontCamera = isFront
    }

    func setFrontCamera(_ isFront: Bool) {
        isFrontCamera = isFront
    }

    func showCameraPermissionAlert() {
        cameraPermissionAlert = true
    }

    func dismissCameraPermissionAlert() {
        cameraPermissionAlert = false
    }

    func toggleCamera() {
        #if DEBUG
        // UI tests run without a runtime; allow the camera-mode view switch to
        // be exercised deterministically.
        if runtime == nil, AppLaunchOptions.current.isUITest {
            setCameraState(isOn: !isCameraOn, isFront: isFrontCamera)
            return
        }
        #endif
        if isCameraOn {
            runtime?.coordinator.stopCamera()
            return
        }

        refreshCameraPermission()
        switch cameraPermission {
        case .granted:
            runtime?.coordinator.startCamera()
        case .undetermined:
            cameraPermissionProvider.request { [weak self] state in
                guard let self else { return }
                self.cameraPermission = state
                if state == .granted {
                    self.runtime?.coordinator.startCamera()
                } else {
                    self.cameraPermissionAlert = true
                }
            }
        case .denied, .unknown:
            cameraPermissionAlert = true
        }
    }

    func switchCamera() {
        runtime?.coordinator.switchCamera()
    }

    func openGalleryPicker() {
        guard !isCameraOn else { return }
        pendingGalleryPreviewData = nil
        pendingPickerAfterPreview = false
        galleryPickerPresented = true
    }

    func handleGalleryPickerResult(_ data: Data?) {
        let wasPresented = galleryPickerPresented
        galleryPickerPresented = false
        pendingGalleryPreviewData = data
        if !wasPresented, let data {
            // The sheet was dismissed before the async photo load finished;
            // there will be no second onDismiss callback.
            pendingGalleryPreviewData = nil
            galleryPreviewData = data
        }
    }

    func galleryPickerDismissed() {
        guard let data = pendingGalleryPreviewData else {
            pendingGalleryPreviewData = nil
            return
        }
        pendingGalleryPreviewData = nil
        galleryPreviewData = data
    }

    func galleryPreviewBack() {
        galleryPreviewData = nil
        pendingPickerAfterPreview = true
    }

    func galleryPreviewCancel() {
        galleryPreviewData = nil
        pendingPickerAfterPreview = false
    }

    func galleryPreviewDismissed() {
        guard pendingPickerAfterPreview else { return }
        pendingPickerAfterPreview = false
        galleryPickerPresented = true
    }

    func galleryPreviewSend() {
        guard let data = galleryPreviewData else { return }
        pendingPickerAfterPreview = false
        galleryPreviewData = nil
        runtime?.coordinator.sendGalleryPhoto(data)
    }

    func setRequiresServerConfig(_ required: Bool) {
        guard requiresServerConfig != required else { return }
        requiresServerConfig = required
    }

    func setServerBaseURL(_ value: String) {
        guard serverBaseURL != value else { return }
        serverBaseURL = value
    }

    func setBusy(_ busy: Bool) {
        guard isBusy != busy else { return }
        isBusy = busy
    }

    func setSessionEnded(_ ended: Bool) {
        guard sessionEnded != ended else { return }
        sessionEnded = ended
        if ended {
            agentJobResultMessageID = nil
        }
    }

    func applyConnectionState(_ state: ConnectionState) {
        connectionState = state
        if state != .connected {
            clearTransient()
        }
    }

    func applySessionState(_ state: SessionState) {
        sessionState = state
    }

    func markSessionReady() {
        isFirstSession = false
        sessionEnded = false
    }

    func setMessages(_ messages: [ChatMessage]) {
        self.messages = messages
    }

    func replaceConversation(with messages: [ChatMessage]) {
        self.messages = messages
        agentJobResultMessageID = nil
        sessionEnded = false
        clearTransient()
    }

    func appendMessage(_ message: ChatMessage) {
        let hasText = !message.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        guard hasText || message.image != nil else { return }
        guard !messages.contains(where: { $0.id == message.id }) else { return }
        messages.append(message)
    }

    func appendUserMessage(
        id: String,
        text: String,
        timestamp: Date? = nil,
        mode: ChatMessageMode? = nil
    ) {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        appendMessage(
            ChatMessage(
                id: "user-\(id)",
                role: .user,
                text: text,
                isFinal: true,
                timestamp: timestamp ?? now(),
                mode: mode
            )
        )
    }

    func setTranscription(_ text: String) {
        currentTranscription = text
        isTranscriptionFinal = false
    }

    func finishUserTranscription(
        id: String,
        text: String,
        timestamp: Date? = nil,
        mode: ChatMessageMode? = nil
    ) {
        appendUserMessage(id: id, text: text, timestamp: timestamp, mode: mode)
        currentTranscription = ""
        isTranscriptionFinal = true
    }

    func setStreamingAI(messageID: String, text: String) {
        streamingAIText = StreamingAIText(messageID: messageID, text: text)
    }

    func finishAssistantMessage(
        messageID: String,
        text: String,
        image: AiImage? = nil,
        source: AITextSource? = nil,
        timestamp: Date? = nil,
        mode: ChatMessageMode? = nil
    ) {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || image != nil else {
            streamingAIText = nil
            return
        }
        appendMessage(
            ChatMessage(
                id: "ai-\(messageID)",
                role: .assistant,
                text: text,
                isFinal: true,
                timestamp: timestamp ?? now(),
                image: image,
                source: source,
                mode: mode
            )
        )
        streamingAIText = nil
    }

    func clearTransient() {
        if !currentTranscription.isEmpty { currentTranscription = "" }
        isTranscriptionFinal = false
        if streamingAIText != nil { streamingAIText = nil }
    }

    func clearConversation() {
        messages.removeAll()
        agentJobResultMessageID = nil
        clearTransient()
        dismissBanner()
    }

    var agentJobResultMessage: ChatMessage? {
        guard let id = agentJobResultMessageID else { return nil }
        return messages.first { $0.id == id && $0.isAgentJobResult }
    }

    func openAgentJobResult(messageID: String) {
        guard let message = messages.first(where: { $0.id == messageID }),
              message.isAgentJobResult else { return }
        agentJobResultMessageID = messageID
    }

    func closeAgentJobResult() {
        agentJobResultMessageID = nil
    }

    func setMicMuted(_ muted: Bool) {
        guard isMicMuted != muted else { return }
        isMicMuted = muted
        if muted {
            micMeter.reset()
        }
    }

    func setSpeakerMuted(_ muted: Bool) {
        guard isSpeakerMuted != muted else { return }
        isSpeakerMuted = muted
    }

    func updateMicLevel(_ level: Int) {
        // The controller's generation/sequence already filter stale reports;
        // this is the privacy backstop while the mic is muted.
        micMeter.set(isMicMuted ? 0 : level)
    }

    func resetMicLevel() {
        micMeter.reset()
    }

    func showBanner(_ banner: ConversationBanner) {
        bannerDismissTask?.cancel()
        bannerDismissTask = nil
        self.banner = banner

        guard banner.autoDismissAfter != nil else { return }
        let interval = bannerAutoDismissInterval
        bannerDismissTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(max(0, interval) * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.banner = nil
            self?.bannerDismissTask = nil
        }
    }

    func dismissBanner() {
        bannerDismissTask?.cancel()
        bannerDismissTask = nil
        banner = nil
    }

    /// Clears only audio recovery banners, leaving auth/session/camera
    /// messages untouched. Used after media auto-recovery succeeds.
    func clearAudioErrorBanner() {
        guard let banner else { return }
        let isAudioError: Bool
        switch banner.message {
        case .audioUnavailable, .captureFailed:
            isAudioError = true
        case let .server(titleKey, _):
            isAudioError = titleKey == "conv_audio_source_failed"
        default:
            isAudioError = false
        }
        guard isAudioError else { return }
        dismissBanner()
    }

    // MARK: - Runtime bridge

    func attach(runtime: ConversationRuntime?) {
        self.runtime = runtime
        cameraPreviewSource = runtime?.camera
    }

    func startRuntime() {
        runtime?.start()
    }

    func stopRuntime() {
        runtime?.stop()
    }

    func retry() {
        runtime?.coordinator.retry()
    }

    func toggleMic() {
        let muted = !isMicMuted
        if let runtime {
            runtime.coordinator.setMicMuted(muted)
        } else {
            // The runtime is created asynchronously after the conversation
            // appears. Keep the control responsive in that window; the
            // coordinator applies `isMicMuted` when media starts.
            setMicMuted(muted)
        }
    }

    func toggleSpeaker() {
        runtime?.coordinator.toggleSpeaker()
    }

    #if DEBUG
    func configureForUITest(
        connection: ConnectionState,
        session: SessionState,
        messages: [ChatMessage],
        transcription: String,
        streaming: String?,
        banner: ConversationBanner?,
        requiresServerConfig: Bool = false,
        cameraOn: Bool = false,
        cameraFront: Bool = false,
        galleryPreview: Data? = nil,
        aiImages: [AiImage] = []
    ) {
        connectionState = connection
        sessionState = session
        self.messages = messages
        currentTranscription = transcription
        isTranscriptionFinal = false
        streamingAIText = streaming.map { StreamingAIText(messageID: "ui-test", text: $0) }
        self.banner = banner
        self.requiresServerConfig = requiresServerConfig
        micPermission = .granted
        cameraPermission = .granted
        isCameraOn = cameraOn
        isFrontCamera = cameraFront
        galleryPreviewData = galleryPreview
        for (index, aiImage) in aiImages.enumerated() {
            self.messages.append(
                ChatMessage(
                    id: "ui-ai-image-\(index)",
                    role: .assistant,
                    text: "Image reply \(index + 1)",
                    isFinal: true,
                    timestamp: Date(),
                    image: aiImage
                )
            )
        }
        isFirstSession = false
    }
    #endif
}
