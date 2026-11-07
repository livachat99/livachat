import AVFoundation
import Combine
import Foundation
import LivaChatSDK
import NetworkImage
import SwiftUI

@MainActor
final class AppContainer: ObservableObject {
    let router: AppRouter
    let settingsStore: SettingsStore
    let launchOptions: AppLaunchOptions
    let historyService: HistoryService
    let agentJobStatusModel = AgentJobStatusModel()
    let agentJobResultCenter = AgentJobResultWindowCenter()
    let modeModel = ConversationModeModel()
    let avatarModel: AvatarCustomizationModel

    @Published private(set) var conversationViewModel: ConversationViewModel?
    @Published private(set) var settingsViewModel: SettingsViewModel?
    @Published private(set) var micPermission: MicPermissionState
    @Published private(set) var cameraPermission: CameraPermissionState
    @Published private(set) var mylifeStore: MylifeServicing?
    @Published private(set) var mylifeImageLoader: DefaultNetworkImageLoader?
    @Published private(set) var isRealtimeSpeaking = false
    @Published var presentedWebLink: WebLink?
    @Published var presentedWebPage: WebPage?

    private let credentials: CredentialStoring
    private let permissionProvider: MicPermissionProviding
    private let cameraPermissionProvider: CameraPermissionProviding
    private let probe: ServerProbing
    private let turnCounter = CaptureTurnCounter()
    private var runtime: ConversationRuntime?
    private var runtimeStartTask: Task<Void, Never>?
    private var hasBootstrapped = false
    private var isForeground = true
    private var preferSavedServerConfig = false
    private var serverBase: ServerBase?
    private var realtimeSpeechCancellable: AnyCancellable?
    private let splashStartedAt = Date()

    init(
        launchOptions: AppLaunchOptions = .current,
        settingsStore: SettingsStore? = nil,
        credentials: CredentialStoring? = nil,
        permissionProvider: MicPermissionProviding? = nil,
        cameraPermissionProvider: CameraPermissionProviding? = nil,
        probe: ServerProbing? = nil
    ) {
        self.launchOptions = launchOptions
        self.router = AppRouter()
        self.historyService = HistoryService(
            baseDirectory: Self.historyBaseDirectory(isUITest: launchOptions.isUITest)
        )
        let settingsStore = settingsStore ?? SettingsStore()
        self.settingsStore = settingsStore
        #if DEBUG
        if launchOptions.isUITest {
            settingsStore.avatarVisible = !launchOptions.uiTestAvatarHidden
        }
        #endif
        let avatarStore = AvatarStore(
            directory: Self.avatarDirectory(isUITest: launchOptions.isUITest)
        )
        #if DEBUG
        if launchOptions.allowSelfSignedTLS {
            settingsStore.tlsAllowSelfSigned = true
        }
        #endif
        let credentials = credentials ?? KeychainCredentialStore()
        self.credentials = credentials
        let permissionProvider = permissionProvider ?? SystemMicPermissionProvider()
        self.permissionProvider = permissionProvider
        let cameraPermissionProvider = cameraPermissionProvider ?? SystemCameraPermissionProvider()
        self.cameraPermissionProvider = cameraPermissionProvider
        self.avatarModel = AvatarCustomizationModel(
            store: avatarStore,
            settings: settingsStore,
            cameraPermissionProvider: cameraPermissionProvider,
            cameraAvailable: { CameraPhotoPicker.isAvailable }
        )
        self.probe = probe ?? URLSessionServerProbe(
            tlsPolicy: launchOptions.serverOverride?.tlsPolicy ?? .system
        )
        self.micPermission = launchOptions.uiTestPermission ?? permissionProvider.current()
        self.cameraPermission = launchOptions.uiTestCameraPermission ?? cameraPermissionProvider.current()
        AppLog.startSession()
        avatarModel.setCameraFlowHooks(
            pause: { [weak self] in
                self?.runtime?.coordinator.pauseCameraForPhoto() ?? false
            },
            resume: { [weak self] in
                self?.runtime?.coordinator.resumeCameraAfterPhoto()
            }
        )
        Task { @MainActor [avatarModel] in
            await avatarModel.load()
        }
    }

    func bootstrap() async {
        guard !hasBootstrapped else { return }
        hasBootstrapped = true

        #if DEBUG
        if launchOptions.resetAINotice {
            UserDefaults.standard.removeObject(forKey: AIGeneratedNotice.shownKey)
        }
        #endif

        if let screen = launchOptions.uiTestScreen {
            if !launchOptions.skipSplash {
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
            #if DEBUG
            if screen == .history, !launchOptions.uiTestHistoryEmpty {
                await historyService.seedUITestData(imageData: Self.uiTestImageData())
            }
            #endif
            configureUITestScreen(screen)
            return
        }

        let elapsed = Date().timeIntervalSince(splashStartedAt)
        let remaining = launchOptions.skipSplash ? 0 : max(0, 1.0 - elapsed)
        if remaining > 0 {
            try? await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000))
        }

        micPermission = currentPermission()
        if settingsStore.onboardingCompleted, micPermission == .granted {
            enterConversation()
        } else {
            router.phase = .permission
        }
    }

    func handleScenePhase(_ phase: ScenePhase) {
        switch phase {
        case .active:
            isForeground = true
            micPermission = currentPermission()
            refreshCameraPermission()
            guard router.phase == .conversation else { return }
            conversationViewModel?.setMicPermission(micPermission)
            conversationViewModel?.setCameraPermission(cameraPermission)
            if cameraPermission != .granted {
                runtime?.coordinator.onCameraPermissionLost()
            }
            if micPermission == .granted {
                runtime?.coordinator.onForeground()
            } else {
                runtime?.coordinator.onMicPermissionLost()
                conversationViewModel?.showBanner(.fatal(.micUnavailable, action: .openSystemSettings))
            }
        case .background:
            isForeground = false
            runtime?.coordinator.onBackground()
        case .inactive:
            break
        @unknown default:
            break
        }
    }

    func startOrRequestPermission() {
        micPermission = currentPermission()
        switch micPermission {
        case .granted:
            settingsStore.markOnboardingCompleted()
            enterConversation()
        case .undetermined, .unknown:
            requestPermission()
        case .denied:
            if launchOptions.isUITest {
                micPermission = .granted
                settingsStore.markOnboardingCompleted()
                enterConversation()
            }
        }
    }

    func refreshMicPermission() {
        micPermission = currentPermission()
    }

    func refreshCameraPermission() {
        cameraPermission = launchOptions.uiTestCameraPermission ?? cameraPermissionProvider.current()
    }

    func requestCameraPermission() {
        refreshCameraPermission()
        switch cameraPermission {
        case .granted, .unknown:
            return
        case .undetermined:
            cameraPermissionProvider.request { [weak self] state in
                self?.cameraPermission = state
            }
        case .denied:
            openSystemSettings()
        }
    }

    private func currentPermission() -> MicPermissionState {
        launchOptions.uiTestPermission ?? permissionProvider.current()
    }

    func openSystemSettings() {
        SystemSettingsOpener.openAppSettings()
    }

    func openWebLink(_ url: URL) {
        guard WebLinkPolicy.isSafe(url) else { return }
        // Markdown links call this synchronously from OpenURLAction, inside a
        // SwiftUI update. Publishing the presentation state there triggers
        // "Publishing changes from within view updates" and can reset the
        // presented screen's state; defer one main-actor turn.
        Task { @MainActor [weak self] in
            self?.presentWebLink(url)
        }
    }

    private func presentWebLink(_ url: URL) {
        if let serverBase, serverBase.isWebrootURL(url) {
            presentedWebLink = nil
            presentedWebPage = WebPage(
                url: url,
                serverHost: serverBase.host,
                serverPort: serverBase.port,
                allowsSelfSigned: serverBase.allowsSelfSigned
            )
        } else {
            presentedWebPage = nil
            presentedWebLink = WebLink(url: url)
        }
    }

    /// Routes a link through a cover-local presenter so the Safari sheet is
    /// presented above the cover instead of dismissing it.
    func presentWebLink(_ url: URL, using presenter: InAppLinkPresenter) {
        guard WebLinkPolicy.isSafe(url) else { return }
        Task { @MainActor [weak self, weak presenter] in
            guard let self, let presenter else { return }
            if let serverBase = self.serverBase, serverBase.isWebrootURL(url) {
                presenter.showWebPage(
                    WebPage(
                        url: url,
                        serverHost: serverBase.host,
                        serverPort: serverBase.port,
                        allowsSelfSigned: serverBase.allowsSelfSigned
                    )
                )
            } else {
                SafariLinkPresenter.shared.present(url)
            }
        }
    }

    var currentServerBaseURL: String {
        serverBase?.baseURLString ?? ""
    }

    func openHistory() {
        agentJobStatusModel.dismissPanel()
        avatarModel.dismissOverlays()
        modeModel.dismissOverlays()
        router.showHistory = true
    }

    func openTimeline() {
        guard mylifeStore != nil, mylifeImageLoader != nil else {
            openSettings()
            return
        }
        agentJobStatusModel.dismissPanel()
        avatarModel.dismissOverlays()
        modeModel.dismissOverlays()
        router.showTimeline = true
    }

    var realtimeSpeakingPublisher: AnyPublisher<Bool, Never> {
        $isRealtimeSpeaking.eraseToAnyPublisher()
    }

    /// Media played while the realtime conversation is live must go through
    /// the same playback engine; a standalone AVAudioPlayer is silent in that
    /// session. Nil when no engine is running.
    var timelineMediaEngine: AVAudioEngine? {
        guard let runtime, runtime.isRunning else { return nil }
        let engine = runtime.audioSession.engine
        return engine.isRunning ? engine : nil
    }

    private func bindRealtimeSpeech(to viewModel: ConversationViewModel) {
        realtimeSpeechCancellable = viewModel.$sessionState
            .map { $0 == .speaking }
            .removeDuplicates()
            .sink { [weak self] speaking in
                self?.isRealtimeSpeaking = speaking
            }
    }

    func openSettings() {
        agentJobStatusModel.dismissPanel()
        avatarModel.dismissOverlays()
        modeModel.dismissOverlays()
        runtime?.coordinator.stopCameraForSettings()
        settingsViewModel = SettingsViewModel(
            store: settingsStore,
            credentials: credentials,
            probe: probe,
            probeFactory: { draftPolicy in
                // The DEBUG server override is for the mock URL only; settings
                // checks must use the user's current self-signed choice.
                URLSessionServerProbe(tlsPolicy: draftPolicy)
            },
            onApply: { [weak self] in
                self?.applyServerSettingsAndReconnect()
            },
            onHapticChanged: { [weak self] enabled in
                self?.runtime?.coordinator.hapticEnabled = enabled
            },
            onAvdetectorChanged: { [weak self] enabled in
                self?.runtime?.sdk.setAvdetectorEnabled(enabled)
            }
        )
        router.showSettings = true
    }

    // MARK: - Conversation lifecycle

    private func enterConversation() {
        openConversationIfNeeded()
        router.phase = .conversation
    }

    private func requestPermission() {
        if launchOptions.isUITest {
            micPermission = .granted
            settingsStore.markOnboardingCompleted()
            enterConversation()
            return
        }
        permissionProvider.request { [weak self] state in
            guard let self else { return }
            self.micPermission = state
            // Stay on the permission screen after the system prompt. The
            // user explicitly taps "Start chat" to continue.
        }
    }

    private func openConversationIfNeeded() {
        guard conversationViewModel == nil else {
            router.phase = .conversation
            return
        }

        let viewModel = ConversationViewModel(
            permissionProvider: permissionProvider,
            cameraPermissionProvider: cameraPermissionProvider
        )
        viewModel.setMicPermission(micPermission)
        viewModel.setCameraPermission(cameraPermission)
        conversationViewModel = viewModel
        bindRealtimeSpeech(to: viewModel)

        #if DEBUG
        // UI tests must never open a socket or the microphone.
        if launchOptions.isUITest {
            configurePreview(viewModel)
            return
        }
        #endif

        let launch: (URL, String, String, TLSPolicy)?
        if !preferSavedServerConfig, let override = launchOptions.serverOverride {
            launch = (override.webSocketURL, override.token, override.deviceID, override.tlsPolicy)
        } else {
            do {
                if let saved = try settingsStore.loadSavedConfig(credentials: credentials) {
                    launch = (saved.webSocketURL, saved.wireToken, saved.deviceID, settingsStore.tlsPolicy)
                } else {
                    launch = nil
                }
            } catch {
                AppLog.error("App", "load settings failed: \(error.localizedDescription)")
                agentJobStatusModel.setCanceler(nil)
                agentJobStatusModel.clear()
                mylifeStore?.stopPolling()
                mylifeStore = nil
                discardMylifeImageLoader()
                viewModel.setRequiresServerConfig(true)
                viewModel.showBanner(
                    .fatal(.server(titleKey: "conv_service_error", detail: ""), action: .openSettings)
                )
                return
            }
        }

        guard let (url, token, deviceID, tlsPolicy) = launch else {
            serverBase = nil
            agentJobStatusModel.setCanceler(nil)
            agentJobStatusModel.clear()
            mylifeStore?.stopPolling()
            mylifeStore = nil
            discardMylifeImageLoader()
            viewModel.setServerBaseURL("")
            viewModel.setRequiresServerConfig(true)
            return
        }

        serverBase = ServerBase(
            webSocketURL: url,
            allowsSelfSigned: tlsPolicy == .allowSelfSigned
        )
        viewModel.setServerBaseURL(serverBase?.baseURLString ?? "")
        if let serverBase {
            agentJobStatusModel.setCanceler(
                AgentJobCancelClient(
                    serverBase: serverBase,
                    token: token,
                    deviceID: deviceID
                )
            )
            mylifeStore?.stopPolling()
            mylifeStore = MylifeStore(serverBase: serverBase, token: token)
            discardMylifeImageLoader()
            mylifeImageLoader = MylifeImageLoader.make(serverBase: serverBase)
        } else {
            agentJobStatusModel.setCanceler(nil)
            mylifeStore?.stopPolling()
            mylifeStore = nil
            discardMylifeImageLoader()
        }

        let config = LivaChatConfig(
            serverURL: url,
            token: token,
            deviceID: deviceID,
            clientVersion: AppInfo.clientVersion,
            enableVideo: true,
            logFileURL: AppLog.fileURL,
            timezone: TimeZone.current.identifier,
            tlsPolicy: tlsPolicy,
            language: AppLocale.resourceCode,
            languageProvider: { AppLocale.resourceCode },
            avdetectorEnabled: settingsStore.avdetectorEnabled
        )

        startRuntime(config: config, viewModel: viewModel)
    }

    private func startRuntime(config: LivaChatConfig, viewModel: ConversationViewModel) {
        runtimeStartTask?.cancel()
        viewModel.setBusy(true)
        runtimeStartTask = Task { @MainActor [weak self] in
            guard let self else { return }
            if viewModel.micPermission == .granted {
                let seed = await self.historyService.beginSession()
                guard !Task.isCancelled,
                      self.conversationViewModel === viewModel else { return }
                if let seed {
                    viewModel.replaceConversation(with: seed.messages)
                }
            }
            guard !Task.isCancelled,
                  self.conversationViewModel === viewModel else { return }
            do {
                let runtime = try ConversationRuntimeFactory.make(
                    config: config,
                    viewModel: viewModel,
                    cameraPreferences: self.settingsStore,
                    turnCounter: self.turnCounter,
                    hapticEnabled: self.settingsStore.hapticEnabled,
                    history: self.historyService,
                    jobStatus: self.agentJobStatusModel,
                    modeModel: self.modeModel,
                    isForeground: { [weak self] in self?.isForeground ?? true }
                )
                self.runtime = runtime
                viewModel.attach(runtime: runtime)
                runtime.start()
            } catch {
                AppLog.error("App", "runtime construction failed: \(error.localizedDescription)")
                viewModel.setRequiresServerConfig(true)
                viewModel.showBanner(
                    .fatal(.server(titleKey: "conv_service_error", detail: ""), action: .openSettings)
                )
            }
        }
    }

    private func applyServerSettingsAndReconnect() {
        preferSavedServerConfig = true
        agentJobStatusModel.setCanceler(nil)
        agentJobStatusModel.clear()
        stopRuntime()
        realtimeSpeechCancellable?.cancel()
        realtimeSpeechCancellable = nil
        isRealtimeSpeaking = false
        mylifeStore?.stopPolling()
        mylifeStore = nil
        discardMylifeImageLoader()
        router.showTimeline = false
        modeModel.reset()
        conversationViewModel?.attach(runtime: nil)
        conversationViewModel = nil
        serverBase = nil
        presentedWebPage = nil
        presentedWebLink = nil
        MarkdownMessageView.clearCache()
        AgentJobCollapse.clearCache()
        router.showSettings = false
        openConversationIfNeeded()
        router.phase = .conversation
    }

    private func stopRuntime() {
        runtimeStartTask?.cancel()
        runtimeStartTask = nil
        runtime?.stop()
        runtime = nil
        agentJobStatusModel.clear()
        isRealtimeSpeaking = false
    }

    /// Drops the timeline image loader and invalidates its URLSession.
    /// The loader is an actor, so shutdown is enqueued; the task keeps the
    /// loader alive until the session has been invalidated.
    private func discardMylifeImageLoader() {
        if let loader = mylifeImageLoader {
            Task { await loader.shutdown() }
        }
        mylifeImageLoader = nil
    }

    private static func historyBaseDirectory(isUITest: Bool) -> URL {
        if isUITest {
            return FileManager.default.temporaryDirectory
                .appendingPathComponent("LivaChatUITest-\(UUID().uuidString)", isDirectory: true)
        }
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return support.appendingPathComponent("LivaChat", isDirectory: true)
    }

    private static func avatarDirectory(isUITest: Bool) -> URL {
        historyBaseDirectory(isUITest: isUITest)
            .appendingPathComponent("avatar", isDirectory: true)
    }

    // MARK: - UI test routing

    private func configureUITestScreen(_ screen: UITestScreen) {
        switch screen {
        case .splash:
            router.phase = .splash
        case .permission:
            router.phase = .permission
        case .conversation:
            openConversationIfNeeded()
            router.phase = .conversation
        case .settings:
            openConversationIfNeeded()
            router.phase = .conversation
            openSettings()
        case .history:
            openConversationIfNeeded()
            router.phase = .conversation
            openHistory()
        case .timeline:
            openConversationIfNeeded()
            router.phase = .conversation
            openTimeline()
        }
    }

    #if DEBUG
    private static let uiTestServerBase = ServerBase(
        webSocketURL: URL(string: "wss://webroot.invalid")!,
        allowsSelfSigned: true
    )

    private func configurePreview(_ viewModel: ConversationViewModel) {
        serverBase = Self.uiTestServerBase
        viewModel.setServerBaseURL(Self.uiTestServerBase?.baseURLString ?? "")
        if launchOptions.uiTestScreen == .timeline {
            mylifeStore = UITestMylifeStore(fixture: launchOptions.uiTestTimelineFixture ?? "home")
            if let serverBase = Self.uiTestServerBase {
                discardMylifeImageLoader()
                mylifeImageLoader = MylifeImageLoader.make(serverBase: serverBase)
            }
        }

        let connection: ConnectionState
        let session: SessionState
        switch launchOptions.uiTestConnection ?? "disconnected" {
        case "listening":
            connection = .connected
            session = .listening
        case "thinking":
            connection = .connected
            session = .thinking
        case "speaking":
            connection = .connected
            session = .speaking
        case "idle":
            connection = .connected
            session = .idle
        default:
            connection = .disconnected
            session = .idle
        }

        var messages: [ChatMessage] = []
        if launchOptions.uiTestMessages {
            messages = [
                ChatMessage(
                    id: "ui-user",
                    role: .user,
                    text: "Hello",
                    isFinal: true,
                    timestamp: Date()
                ),
                ChatMessage(
                    id: "ui-ai",
                    role: .assistant,
                    text: "Hi! How can I help you today?",
                    isFinal: true,
                    timestamp: Date()
                )
            ]
        }
        if launchOptions.uiTestLongMessages {
            messages = (0..<40).map { index in
                let isUser = index % 2 == 0
                let text = isUser
                    ? "User message \(index)"
                    : "AI message \(index). "
                        + String(repeating: "This is a long message for scroll testing. ", count: 2)
                return ChatMessage(
                    id: "ui-long-\(index)",
                    role: isUser ? .user : .assistant,
                    text: text,
                    isFinal: true,
                    timestamp: Date()
                )
            }
        }
        if launchOptions.uiTestMarkdown {
            messages.append(
                ChatMessage(
                    id: "ui-user-markdown",
                    role: .user,
                    text: "**not bold**",
                    isFinal: true,
                    timestamp: Date()
                )
            )
            messages.append(
                ChatMessage(
                    id: "ui-markdown",
                    role: .assistant,
                    text: Self.uiTestMarkdownText,
                    isFinal: true,
                    timestamp: Date()
                )
            )
        }
        if launchOptions.uiTestAgentJob {
            messages.append(
                ChatMessage(
                    id: "ai-msg_agent_ui_prompt",
                    role: .assistant,
                    text: "Agent task started.",
                    isFinal: true,
                    timestamp: Date(),
                    source: .agent
                )
            )
            messages.append(
                ChatMessage(
                    id: "ai-msg_agent_job_ui_short",
                    role: .assistant,
                    text: "**Done.** Inventory checked.\n\n[Open report](https://example.com/report)",
                    isFinal: true,
                    timestamp: Date(),
                    source: .agent
                )
            )
            messages.append(
                ChatMessage(
                    id: "ai-msg_agent_job_ui_long",
                    role: .assistant,
                    text: Self.uiTestLongAgentMarkdown,
                    isFinal: true,
                    timestamp: Date(),
                    source: .agent
                )
            )
        }

        let banner: ConversationBanner?
        switch launchOptions.uiTestBanner {
        case "warning":
            banner = .recoverable(.server(titleKey: "conv_service_warning", detail: "Temporary issue"))
        case "captureFailed":
            banner = .fatal(
                .captureFailed(
                    AppErrorMessage(
                        "conv_capture_failed",
                        logDetail: "injected technical detail"
                    )
                ),
                action: .retry
            )
        case "error":
            banner = .fatal(.connectionFailed, action: .retry)
        default:
            banner = nil
        }

        let wantsAIImage = launchOptions.uiTestAIImage || launchOptions.uiTestAIImagePair
        let previewImage = launchOptions.uiTestGalleryPreview || wantsAIImage
            ? Self.uiTestImageData()
            : nil
        var aiImages: [AiImage] = []
        if wantsAIImage, let data = previewImage {
            let image = AiImage(
                data: data,
                mime: "image/jpeg",
                width: 8,
                height: 8,
                orientation: .portrait0,
                cameraFacing: .gallery
            )
            aiImages = launchOptions.uiTestAIImagePair ? [image, image] : [image]
        }

        let lectureFixture = launchOptions.uiTestMode == "lecture"
        viewModel.configureForUITest(
            connection: connection,
            session: session,
            messages: messages,
            transcription: lectureFixture
                ? "你好，请问"
                : (session == .listening ? "你好，请问" : ""),
            streaming: lectureFixture
                ? "Hello, how can I help?"
                : (session == .thinking ? "Here is what I think..." : nil),
            banner: banner,
            cameraOn: launchOptions.uiTestCameraOn,
            cameraFront: launchOptions.uiTestCameraFront,
            galleryPreview: launchOptions.uiTestGalleryPreview ? previewImage : nil,
            aiImages: aiImages
        )
        configureJobStatusForUITest()
        configureAvatarAndModeForUITest()
    }

    private func configureAvatarAndModeForUITest() {
        let modeReady = launchOptions.uiTestModeReady
            || launchOptions.uiTestMode != nil
            || launchOptions.uiTestModePicker != nil
        if modeReady {
            modeModel.sessionBecameReady()
        }

        if let mode = launchOptions.uiTestMode {
            switch mode {
            case "dialogue":
                modeModel.applyEcho(mode: .translate, usecase: .dialogue)
            case "lecture":
                modeModel.applyEcho(mode: .translate, usecase: .lecture)
            case "transcribe":
                modeModel.applyEcho(mode: .transcribe, usecase: nil)
            case "omni":
                modeModel.applyEcho(mode: .omni, usecase: nil)
            default:
                break
            }
        }

        if let picker = launchOptions.uiTestModePicker {
            switch picker {
            case "dialogue":
                modeModel.presentLanguagePicker(.dialogue)
            case "lecture":
                modeModel.presentLanguagePicker(.lecture)
            case "transcribe":
                modeModel.presentTranscribeSourcePicker()
            case "omni":
                modeModel.presentOmniIntro()
            default:
                break
            }
        }

        if launchOptions.uiTestAvatarCrop, let data = Self.uiTestImageData() {
            avatarModel.configureCropForUITest(data)
            return
        }

        if launchOptions.uiTestAvatar, let data = Self.uiTestImageData() {
            Task { @MainActor [avatarModel] in
                await avatarModel.seedForUITest(data)
            }
        }

        if launchOptions.uiTestAvatarSheet || launchOptions.uiTestModePicker != nil {
            avatarModel.presentSheet()
        }
    }

    private func configureJobStatusForUITest() {
        let count = launchOptions.uiTestJobStatusCount
            ?? (launchOptions.uiTestJobStatus ? 2 : nil)
        guard let count, count > 0 else {
            agentJobStatusModel.setCanceler(nil)
            agentJobStatusModel.clear()
            return
        }

        let nowMs = Int64(Date().timeIntervalSince1970 * 1000)
        let jobs = (1...count).map { index in
            let ageMs = Int64((count - index + 1) * 10_000)
            return AgentJob(
                jobID: "ui-job-\(index)",
                agentName: "hermes",
                summary: index == 1
                    ? "Checking inventory levels"
                    : "Summarizing meeting notes \(index)",
                startedAtMs: nowMs - ageMs,
                runningTimeMs: ageMs
            )
        }
        agentJobStatusModel.replaceJobs(jobs)

        let outcome = UITestAgentJobCancelOutcome(
            rawValue: launchOptions.uiTestJobCancelResult ?? "success"
        ) ?? .success
        agentJobStatusModel.setCanceler(
            UITestAgentJobCanceler(outcome: outcome) { [weak agentJobStatusModel] jobID in
                Task { @MainActor in
                    guard let model = agentJobStatusModel else { return }
                    model.replaceJobs(model.jobs.filter { $0.jobID != jobID })
                }
            }
        )
    }

    private static let uiTestMarkdownText = """
    # Trip Summary

    **Bold**, *italic*, and ~~struck~~ text.

    - First item
    - Second item

    > A quoted line.

    ```swift
    print("hello")
    ```

    | City | Days |
    | --- | --- |
    | Paris | 3 |
    | Rome | 2 |

    [Example link](https://example.com)

    [Open page](livachat_url://webroot/01a0f54b-4fe4-7c4f-80a2-88e711556d69/)
    """

    private static let uiTestLongAgentMarkdown: String = {
        var paragraphs = ["## Inventory Report"]
        for index in 1...12 {
            paragraphs.append(
                "Paragraph \(index): the agent inspected inventory records, reconciled totals, and recorded the differences for review."
            )
        }
        return paragraphs.joined(separator: "\n\n")
    }()

    private static func uiTestImageData() -> Data? {
        let width = 8
        let height = 8
        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return nil
        }
        context.setFillColor(UIColor.systemIndigo.cgColor)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        guard let image = context.makeImage() else { return nil }
        return CameraImageEncoder.jpegData(from: image, quality: 0.8)
    }
    #endif
}

#if DEBUG
private enum UITestAgentJobCancelOutcome: String {
    case success
    case successNoSnapshot = "success-no-snapshot"
    case failure
    case notFound = "not_found"
    case conflict
    case incomplete
}

private final class UITestAgentJobCanceler: AgentJobCanceling, @unchecked Sendable {
    private let outcome: UITestAgentJobCancelOutcome
    private let onSuccess: @Sendable (String) -> Void

    init(
        outcome: UITestAgentJobCancelOutcome,
        onSuccess: @escaping @Sendable (String) -> Void
    ) {
        self.outcome = outcome
        self.onSuccess = onSuccess
    }

    func cancel(jobID: String) async throws {
        try? await Task.sleep(nanoseconds: 8_000_000_000)
        guard !Task.isCancelled else { return }

        switch outcome {
        case .success:
            onSuccess(jobID)
        case .successNoSnapshot:
            return
        case .failure:
            throw NSError(domain: "dev.livachat.uitest", code: 1)
        case .notFound:
            throw AgentJobCancelError(statusCode: 404, code: "not_found", message: "")
        case .conflict:
            throw AgentJobCancelError(statusCode: 409, code: "conflict", message: "")
        case .incomplete:
            throw AgentJobCancelError(
                statusCode: 503,
                code: "cancel_incomplete",
                message: ""
            )
        }
    }
}
#endif
