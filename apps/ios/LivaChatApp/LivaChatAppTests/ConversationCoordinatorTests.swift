import AVFoundation
import Combine
import LivaChatSDK
import XCTest
@testable import LivaChatApp

private func makeState(
    connection: ConnectionState = .connected,
    epoch: UInt64 = 1,
    session: SessionState = .idle,
    vad: VadState = .unknown,
    playbackEpoch: UInt64 = 1
) -> LivaChatState {
    LivaChatState(
        connectionState: connection,
        connectionEpoch: epoch,
        sessionState: session,
        localVadState: vad,
        playbackEpoch: playbackEpoch
    )
}

private final class FakeSDK: LivaChatSDKProviding, @unchecked Sendable {
    var state: LivaChatState = .initial
    let events: AsyncStream<LivaChatEvent>
    private let continuation: AsyncStream<LivaChatEvent>.Continuation

    var connectCount = 0
    var disconnectCount = 0
    var startSessionCalls: [String?] = []
    var interruptCount = 0
    var sessionChanges: [(mode: SessionMode, myLanguage: String?, localLanguage: String?, usecase: SessionUsecase?)] = []
    var avdetectorChanges: [Bool] = []

    init() {
        var continuation: AsyncStream<LivaChatEvent>.Continuation!
        self.events = AsyncStream { continuation = $0 }
        self.continuation = continuation
    }

    func connect() { connectCount += 1 }
    func disconnect() { disconnectCount += 1 }
    func startSession(resumeSessionID: String?) { startSessionCalls.append(resumeSessionID) }
    func sendInterrupt(reason: InterruptReason) { interruptCount += 1 }
    func sendSessionChange(
        mode: SessionMode,
        myLanguage: String?,
        localLanguage: String?,
        usecase: SessionUsecase?
    ) {
        sessionChanges.append((mode, myLanguage, localLanguage, usecase))
    }
    func setAvdetectorEnabled(_ enabled: Bool) { avdetectorChanges.append(enabled) }

    deinit {
        continuation.finish()
    }
}

private final class FakeCapture: AudioCapturing {
    var onLevel: (@MainActor (Int) -> Void)?
    var onFailure: (@MainActor (AppErrorMessage) -> Void)?
    var startCount = 0
    var stopCount = 0
    var topUpCount = 0
    var mutedValues: [Bool] = []
    var startError: Error?
    var lifecycleAfterStart: CaptureLifecycleState = .running
    var lifecycleState: CaptureLifecycleState = .stopped

    func start() throws {
        startCount += 1
        if let startError {
            lifecycleState = .stopped
            throw startError
        }
        lifecycleState = lifecycleAfterStart
    }

    func stop() {
        stopCount += 1
        lifecycleState = .stopped
    }

    func setMuted(_ muted: Bool) { mutedValues.append(muted) }
    func topUpSilenceWindow() { topUpCount += 1 }
}

private final class FakePlayback: AudioPlaying {
    var startCount = 0
    var stopCount = 0
    var resumeCount = 0
    var prepareCount = 0
    var mutedValues: [Bool] = []
    var flushes: [(PlaybackFlushReason, UInt64)] = []
    var ttsInputExpected: [Bool] = []
    var isRunning = false
    var hasBufferedOrScheduledAudio = false
    var onActivityChange: (@MainActor () -> Void)?

    func start() {
        startCount += 1
        isRunning = true
        // Real start() is destructive: jitter/epoch reset and node stop.
        hasBufferedOrScheduledAudio = false
    }

    func stop() {
        stopCount += 1
        isRunning = false
        hasBufferedOrScheduledAudio = false
    }

    func setMuted(_ muted: Bool) { mutedValues.append(muted) }
    func flush(reason: PlaybackFlushReason, epoch: UInt64) {
        flushes.append((reason, epoch))
        hasBufferedOrScheduledAudio = false
    }

    func setTTSInputExpected(_ expected: Bool) {
        ttsInputExpected.append(expected)
    }

    func resumePreservingBuffer() {
        resumeCount += 1
        isRunning = true
    }

    func prepareForAudioReconfiguration() {
        prepareCount += 1
        isRunning = false
        // Real prepare() preserves the jitter buffer; only stop()/start() clear it.
    }
}

@MainActor
private final class FakeAudioSession: AudioSessionControlling {
    let engine = AVAudioEngine()
    let playbackNode = AVAudioPlayerNode()
    var onInterruptionBegan: (() -> Void)?
    var onInterruptionEnded: (() -> Void)?
    var onRouteChanged: (() -> Void)?
    var onMediaServicesReset: (() -> Void)?

    var activateCount = 0
    var startEngineCount = 0
    var stopEngineCount = 0
    var deactivateCount = 0
    var activateError: Error?
    var engineStartError: Error?
    var failNextActivate = false
    private(set) var appliedCaptureSource: TranscribeAudioSource?
    private(set) var isEngineRunning = false

    func activate(captureSource source: TranscribeAudioSource) throws {
        activateCount += 1
        if failNextActivate {
            failNextActivate = false
            throw NSError(domain: "FakeAudioSession", code: 1)
        }
        if let activateError { throw activateError }
        appliedCaptureSource = source
    }

    func startEngine() throws {
        startEngineCount += 1
        if let engineStartError { throw engineStartError }
        isEngineRunning = true
    }

    func stopEngine() {
        stopEngineCount += 1
        isEngineRunning = false
    }

    func deactivate() {
        deactivateCount += 1
        appliedCaptureSource = nil
        // Real deactivate() does not touch the engine; teardown stops it first.
    }

    func simulateEngineStopped() {
        isEngineRunning = false
    }
}

@MainActor
private final class FakeHaptics: HapticProviding {
    var taps = 0
    func lightTap() { taps += 1 }
}

@MainActor
private final class FakePermission: MicPermissionProviding {
    var state: MicPermissionState
    init(_ state: MicPermissionState) { self.state = state }
    func current() -> MicPermissionState { state }
    func request(_ completion: @escaping @MainActor (MicPermissionState) -> Void) {
        completion(state)
    }
}

final class FakeCamera: CameraCapturing, CameraPreviewing, @unchecked Sendable {
    var onFailure: (@MainActor (AppErrorMessage) -> Void)?
    var isFrontCamera = true
    private(set) var startCalls: [Bool] = []
    private(set) var stopCount = 0
    private(set) var switchCalls: [Bool] = []

    func start(front: Bool) {
        startCalls.append(front)
        isFrontCamera = front
    }

    func stop() {
        stopCount += 1
    }

    func switchCamera(front: Bool) {
        isFrontCamera = front
        switchCalls.append(front)
    }

    @MainActor
    func attachPreview(to layer: AVCaptureVideoPreviewLayer) {}

    @MainActor
    func detachPreview() {}

    @MainActor
    func fail(_ message: String) {
        onFailure?(AppErrorMessage("conv_camera_start_failed", logDetail: message))
    }
}

@MainActor
private final class FakeCameraPreferences: CameraPreferencesProviding {
    var cameraDefaultFront: Bool
    var cameraAutoOnJoin: Bool

    init(defaultFront: Bool, autoOnJoin: Bool) {
        self.cameraDefaultFront = defaultFront
        self.cameraAutoOnJoin = autoOnJoin
    }
}

@MainActor
private final class FakeHistory: ConversationHistoryRecording {
    var isSuppressingMessages = false
    let sessionSwitchedSubject = PassthroughSubject<HistorySeed, Never>()
    var sessionSwitched: AnyPublisher<HistorySeed, Never> {
        sessionSwitchedSubject.eraseToAnyPublisher()
    }

    private(set) var userMessages: [(id: String, text: String, mode: ChatMessageMode?)] = []
    private(set) var assistantMessages: [
        (id: String, text: String, image: AiImage?, source: AITextSource?, mode: ChatMessageMode?)
    ] = []
    private(set) var finishCount = 0

    func recordUserMessage(
        id: String,
        text: String,
        timestamp: Date,
        mode: ChatMessageMode?
    ) {
        userMessages.append((id, text, mode))
    }

    func recordAssistantMessage(
        id: String,
        text: String,
        timestamp: Date,
        image: AiImage?,
        source: AITextSource?,
        mode: ChatMessageMode?
    ) {
        assistantMessages.append((id, text, image, source, mode))
    }

    func finishSession() {
        finishCount += 1
    }

    func emit(_ seed: HistorySeed) {
        sessionSwitchedSubject.send(seed)
    }
}

private final class ForegroundFlag: @unchecked Sendable {
    var value: Bool
    init(_ value: Bool) { self.value = value }
}

@MainActor
private struct SUT {
    let coordinator: ConversationCoordinator
    let sdk: FakeSDK
    let capture: FakeCapture
    let playback: FakePlayback
    let session: FakeAudioSession
    let haptics: FakeHaptics
    let viewModel: ConversationViewModel
    let foreground: ForegroundFlag
    let camera: FakeCamera?
    let turnCounter: CaptureTurnCounter
    let history: FakeHistory?
    let jobStatus: AgentJobStatusModel?
    let modeModel: ConversationModeModel?

    init(
        permission: MicPermissionState = .granted,
        hapticEnabled: Bool = true,
        foreground: Bool = true,
        camera: FakeCamera? = nil,
        cameraPreferences: CameraPreferencesProviding? = nil,
        history: FakeHistory? = nil,
        jobStatus: AgentJobStatusModel? = nil,
        modeModel: ConversationModeModel? = nil
    ) {
        let sdk = FakeSDK()
        let capture = FakeCapture()
        let playback = FakePlayback()
        let session = FakeAudioSession()
        let haptics = FakeHaptics()
        let flag = ForegroundFlag(foreground)
        let turnCounter = CaptureTurnCounter()
        let viewModel = ConversationViewModel(
            permissionProvider: FakePermission(permission),
            bannerAutoDismissInterval: 0.05
        )
        viewModel.setCameraPermission(.granted)
        let coordinator = ConversationCoordinator(
            sdk: sdk,
            viewModel: viewModel,
            capture: capture,
            playback: playback,
            audioSession: session,
            haptics: haptics,
            camera: camera,
            cameraPreferences: cameraPreferences,
            turnCounter: turnCounter,
            hapticEnabled: hapticEnabled,
            history: history,
            jobStatus: jobStatus,
            modeModel: modeModel,
            isForeground: { flag.value }
        )
        self.coordinator = coordinator
        self.sdk = sdk
        self.capture = capture
        self.playback = playback
        self.session = session
        self.haptics = haptics
        self.viewModel = viewModel
        self.foreground = flag
        self.camera = camera
        self.turnCounter = turnCounter
        self.history = history
        self.jobStatus = jobStatus
        self.modeModel = modeModel
    }

    func connectAndReady(sessionID: String = "sess_123", restored: Bool = false) {
        coordinator.start()
        sdk.state = makeState(connection: .connected)
        coordinator.handle(.connectionStateChanged(.connected))
        coordinator.handle(.connectionEpochAdvanced(1))
        coordinator.handle(.sessionReady(sessionID: sessionID, restored: restored))
    }
}

@MainActor
final class ConversationCoordinatorTests: XCTestCase {
    func testStartStartsMediaAndConnectsOnce() {
        let sut = SUT()
        sut.coordinator.start()
        sut.coordinator.start()

        XCTAssertEqual(sut.sdk.connectCount, 1)
        XCTAssertEqual(sut.capture.startCount, 1)
        XCTAssertEqual(sut.playback.startCount, 1)
        XCTAssertEqual(sut.session.activateCount, 1)
        XCTAssertEqual(sut.session.startEngineCount, 1)
        XCTAssertFalse(sut.viewModel.requiresServerConfig)
        XCTAssertTrue(sut.coordinator.isStarted)
    }

    func testStartInBackgroundDefersMediaUntilForeground() {
        let sut = SUT(foreground: false)
        sut.coordinator.start()

        XCTAssertEqual(sut.sdk.connectCount, 1)
        XCTAssertEqual(sut.capture.startCount, 0)
        XCTAssertEqual(sut.playback.startCount, 0)
        XCTAssertEqual(sut.session.activateCount, 0)
        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)

        sut.foreground.value = true
        sut.coordinator.onForeground()

        XCTAssertEqual(sut.coordinator.pipelineState, .full)
        XCTAssertEqual(sut.capture.startCount, 1)
        XCTAssertEqual(sut.playback.startCount, 1)
        XCTAssertEqual(sut.playback.resumeCount, 0)
    }

    func testMissingPermissionBlocksConnect() {
        let sut = SUT(permission: .denied)
        sut.coordinator.start()

        XCTAssertEqual(sut.sdk.connectCount, 0)
        XCTAssertEqual(sut.capture.startCount, 0)
        XCTAssertEqual(sut.viewModel.banner?.message, .micUnavailable)
        XCTAssertEqual(sut.viewModel.banner?.action, .openSystemSettings)
    }

    func testEpochStartsSessionWithResumeID() {
        let sut = SUT()
        sut.coordinator.start()
        sut.sdk.state = makeState(connection: .connected)

        sut.coordinator.handle(.connectionEpochAdvanced(1))
        XCTAssertEqual(sut.sdk.startSessionCalls.count, 1)
        XCTAssertNil(sut.sdk.startSessionCalls[0])

        sut.coordinator.handle(.sessionReady(sessionID: "sess_abc", restored: false))
        sut.coordinator.handle(.connectionEpochAdvanced(2))
        XCTAssertEqual(sut.sdk.startSessionCalls.count, 2)
        XCTAssertEqual(sut.sdk.startSessionCalls[1], "sess_abc")
    }

    func testEpochAfterSessionEndedDoesNotStartNewSession() {
        let sut = SUT()
        sut.connectAndReady()
        sut.coordinator.handle(.sessionEnded(reason: .idleTimeout, sessionID: "sess_123"))
        sut.sdk.state = makeState(connection: .connected)
        sut.sdk.startSessionCalls.removeAll()

        sut.coordinator.handle(.connectionEpochAdvanced(2))

        XCTAssertTrue(sut.sdk.startSessionCalls.isEmpty)
    }

    func testEpochIgnoredWhenNotConnected() {
        let sut = SUT()
        sut.coordinator.start()
        sut.sdk.state = makeState(connection: .reconnecting)
        sut.coordinator.handle(.connectionEpochAdvanced(1))
        XCTAssertTrue(sut.sdk.startSessionCalls.isEmpty)
    }

    func testSessionReadyKeepsLocalConversation() {
        let sut = SUT()
        sut.connectAndReady(restored: false)
        sut.viewModel.appendUserMessage(id: "1", text: "hello")
        XCTAssertEqual(sut.viewModel.messages.count, 1)

        sut.coordinator.handle(.sessionReady(sessionID: "sess_2", restored: true))
        XCTAssertEqual(sut.viewModel.messages.count, 1)

        sut.coordinator.handle(.sessionReady(sessionID: "sess_3", restored: false))
        XCTAssertEqual(sut.viewModel.messages.count, 1)
    }

    func testFinalMessagesAreRecordedToHistory() {
        let history = FakeHistory()
        let sut = SUT(history: history)
        sut.connectAndReady()

        sut.coordinator.handle(.transcription(text: "hello", isFinal: true, transcriptionID: "t1"))
        sut.coordinator.handle(.aiText(text: "Hi", messageID: "m1", index: 0, isFinal: true, image: nil, source: .llm))

        XCTAssertEqual(history.userMessages.first?.id, "user-t1")
        XCTAssertEqual(history.userMessages.first?.text, "hello")
        XCTAssertEqual(history.assistantMessages.first?.id, "ai-m1")
        XCTAssertEqual(history.assistantMessages.first?.text, "Hi")
        XCTAssertEqual(history.assistantMessages.first?.source, .llm)
    }

    func testLateFinalAfterDisconnectIsAcceptedAndRecorded() {
        let history = FakeHistory()
        let sut = SUT(history: history)
        sut.connectAndReady()

        sut.coordinator.handle(.aiText(text: "Hel", messageID: "m1", index: 0, isFinal: false, image: nil, source: nil))
        sut.coordinator.handle(.connectionStateChanged(.reconnecting))
        sut.coordinator.handle(.aiText(text: "lo!", messageID: "m1", index: 1, isFinal: true, image: nil, source: nil))

        XCTAssertEqual(sut.viewModel.messages.last?.id, "ai-m1")
        XCTAssertEqual(sut.viewModel.messages.last?.text, "Hello!")
        XCTAssertEqual(history.assistantMessages.last?.text, "Hello!")

        sut.coordinator.handle(.connectionStateChanged(.connected))
        sut.coordinator.handle(.transcription(text: "hel", isFinal: false, transcriptionID: "t1"))
        sut.coordinator.handle(.connectionStateChanged(.reconnecting))
        sut.coordinator.handle(.transcription(text: "hello", isFinal: true, transcriptionID: "t1"))
        XCTAssertEqual(sut.viewModel.messages.last?.id, "user-t1")
        XCTAssertEqual(history.userMessages.last?.text, "hello")
    }

    func testSuppressedHistoryDropsTranscriptionAndAIText() {
        let history = FakeHistory()
        history.isSuppressingMessages = true
        let sut = SUT(history: history)
        sut.connectAndReady()

        sut.coordinator.handle(.transcription(text: "hello", isFinal: true, transcriptionID: "t1"))
        sut.coordinator.handle(.aiText(text: "Hi", messageID: "m1", index: 0, isFinal: true, image: nil, source: nil))

        XCTAssertTrue(sut.viewModel.messages.isEmpty)
        XCTAssertTrue(history.userMessages.isEmpty)
        XCTAssertTrue(history.assistantMessages.isEmpty)
    }

    func testSessionSwitchReplacesConversationAndClearsTransient() {
        let history = FakeHistory()
        let sut = SUT(history: history)
        sut.connectAndReady()
        sut.viewModel.appendMessage(
            ChatMessage(
                id: "ai-msg_agent_job_1",
                role: .assistant,
                text: "old",
                isFinal: true,
                timestamp: Date(),
                source: .agent
            )
        )
        sut.viewModel.openAgentJobResult(messageID: "ai-msg_agent_job_1")
        sut.viewModel.setTranscription("partial")
        sut.viewModel.setStreamingAI(messageID: "m2", text: "streaming")

        history.emit(
            HistorySeed(
                sessionID: "new-session",
                messages: [
                    ChatMessage(
                        id: "user-new",
                        role: .user,
                        text: "new seed",
                        isFinal: true,
                        timestamp: Date()
                    )
                ]
            )
        )

        XCTAssertEqual(sut.viewModel.messages.map(\.id), ["user-new"])
        XCTAssertNil(sut.viewModel.agentJobResultMessageID)
        XCTAssertEqual(sut.viewModel.currentTranscription, "")
        XCTAssertNil(sut.viewModel.streamingAIText)
    }

    func testSessionSwitchResetsAIAccumulation() {
        let history = FakeHistory()
        let sut = SUT(history: history)
        sut.connectAndReady()

        sut.coordinator.handle(
            .aiText(text: "OLD", messageID: "m1", index: 0, isFinal: false, image: nil, source: nil)
        )
        history.emit(
            HistorySeed(
                sessionID: "new-session",
                messages: [
                    ChatMessage(
                        id: "user-new",
                        role: .user,
                        text: "new seed",
                        isFinal: true,
                        timestamp: Date()
                    )
                ]
            )
        )
        sut.coordinator.handle(
            .aiText(text: "new", messageID: "m1", index: 1, isFinal: false, image: nil, source: nil)
        )
        sut.coordinator.handle(
            .aiText(text: "", messageID: "m1", index: 2, isFinal: true, image: nil, source: nil)
        )

        XCTAssertEqual(sut.viewModel.messages.last?.text, "new")
        XCTAssertEqual(history.assistantMessages.last?.text, "new")
    }

    func testLateFinalAfterSessionReadyUsesAccumulation() {
        let history = FakeHistory()
        let sut = SUT(history: history)
        sut.connectAndReady()

        sut.coordinator.handle(
            .aiText(text: "Hel", messageID: "m1", index: 0, isFinal: false, image: nil, source: nil)
        )
        sut.coordinator.handle(.sessionReady(sessionID: "sess_2", restored: true))
        sut.coordinator.handle(
            .aiText(text: "lo!", messageID: "m1", index: 1, isFinal: true, image: nil, source: nil)
        )

        XCTAssertEqual(sut.viewModel.messages.last?.id, "ai-m1")
        XCTAssertEqual(sut.viewModel.messages.last?.text, "Hello!")
        XCTAssertEqual(history.assistantMessages.last?.text, "Hello!")
    }

    func testStopFinishesHistorySession() {
        let history = FakeHistory()
        let sut = SUT(history: history)
        sut.connectAndReady()
        sut.coordinator.stop()
        XCTAssertEqual(history.finishCount, 1)
    }

    func testSessionEndedClearsSessionAndStopsMedia() {
        let sut = SUT()
        sut.connectAndReady()
        XCTAssertEqual(sut.coordinator.serverSessionID, "sess_123")

        sut.coordinator.handle(.sessionEnded(reason: .serverShutdown, sessionID: "sess_123"))

        XCTAssertNil(sut.coordinator.serverSessionID)
        XCTAssertTrue(sut.coordinator.sessionEnded)
        XCTAssertGreaterThanOrEqual(sut.capture.stopCount, 1)
        XCTAssertGreaterThanOrEqual(sut.playback.stopCount, 1)
        XCTAssertEqual(sut.viewModel.banner?.message, .sessionEnded)
        XCTAssertEqual(sut.viewModel.banner?.action, .retry)
        XCTAssertTrue(sut.viewModel.sessionEnded)
    }

    func testLatePartialAfterDisconnectIsDroppedButFinalIsAccepted() {
        let sut = SUT()
        sut.connectAndReady()
        sut.coordinator.handle(.transcription(text: "hel", isFinal: false, transcriptionID: "t1"))
        XCTAssertEqual(sut.viewModel.currentTranscription, "hel")

        sut.sdk.state = makeState(connection: .reconnecting, epoch: 2)
        sut.coordinator.handle(.connectionStateChanged(.reconnecting))
        XCTAssertEqual(sut.viewModel.currentTranscription, "")

        sut.coordinator.handle(.transcription(text: "hel", isFinal: false, transcriptionID: "t2"))
        XCTAssertEqual(sut.viewModel.currentTranscription, "")

        sut.coordinator.handle(.transcription(text: "hello", isFinal: true, transcriptionID: "t1"))
        XCTAssertEqual(sut.viewModel.messages.last?.id, "user-t1")
    }

    func testFinalTranscriptionAppendsUserMessage() {
        let sut = SUT()
        sut.connectAndReady()

        sut.coordinator.handle(.transcription(text: "hello", isFinal: false, transcriptionID: "t1"))
        sut.coordinator.handle(.transcription(text: "hello there", isFinal: true, transcriptionID: "t1"))

        XCTAssertEqual(sut.viewModel.currentTranscription, "")
        XCTAssertEqual(sut.viewModel.messages.count, 1)
        XCTAssertEqual(sut.viewModel.messages.first?.id, "user-t1")
        XCTAssertEqual(sut.viewModel.messages.first?.text, "hello there")
        XCTAssertEqual(sut.viewModel.messages.first?.role, .user)
    }

    func testAITextDeltasAccumulateAndFinalize() {
        let sut = SUT()
        sut.connectAndReady()

        sut.coordinator.handle(.aiText(text: "Hel", messageID: "m1", index: 0, isFinal: false, image: nil, source: nil))
        sut.coordinator.handle(.aiText(text: "lo!", messageID: "m1", index: 1, isFinal: false, image: nil, source: nil))
        XCTAssertEqual(sut.viewModel.streamingAIText?.text, "Hello!")

        sut.coordinator.handle(.aiText(text: " Done", messageID: "m1", index: 2, isFinal: true, image: nil, source: nil))
        XCTAssertNil(sut.viewModel.streamingAIText)
        XCTAssertEqual(sut.viewModel.messages.last?.id, "ai-m1")
        XCTAssertEqual(sut.viewModel.messages.last?.text, "Hello! Done")
    }

    func testAgentSourceFlowsIntoFinalMessage() {
        let sut = SUT()
        sut.connectAndReady()

        sut.coordinator.handle(
            .aiText(text: "job", messageID: "msg_agent_job_9", index: 0, isFinal: false, image: nil, source: .agent)
        )
        sut.coordinator.handle(
            .aiText(text: "", messageID: "msg_agent_job_9", index: 1, isFinal: true, image: nil, source: .agent)
        )

        XCTAssertEqual(sut.viewModel.messages.last?.source, .agent)
        XCTAssertTrue(sut.viewModel.messages.last?.isAgentJobResult == true)
    }

    func testNewAIMessageIDResetsAccumulation() {
        let sut = SUT()
        sut.connectAndReady()

        sut.coordinator.handle(.aiText(text: "one", messageID: "m1", index: 0, isFinal: false, image: nil, source: nil))
        sut.coordinator.handle(.aiText(text: "two", messageID: "m2", index: 1, isFinal: false, image: nil, source: nil))
        XCTAssertEqual(sut.viewModel.streamingAIText?.text, "two")
    }

    func testPlaybackFlushIsForwarded() {
        let sut = SUT()
        sut.connectAndReady()
        sut.coordinator.handle(.playbackShouldFlush(reason: .interruptAck, epoch: 7))

        XCTAssertEqual(sut.playback.flushes.count, 1)
        XCTAssertEqual(sut.playback.flushes.first?.1, 7)
    }

    func testDisconnectFlushesWithStateEpoch() {
        let sut = SUT()
        sut.connectAndReady()
        sut.playback.flushes.removeAll()

        sut.sdk.state = makeState(connection: .reconnecting, epoch: 2, playbackEpoch: 4)
        sut.coordinator.handle(.connectionStateChanged(.reconnecting))

        XCTAssertEqual(sut.playback.flushes.last?.1, 4)
    }

    func testAuthFailureStopsMediaAndOffersSettings() {
        let sut = SUT()
        sut.connectAndReady()

        sut.coordinator.handle(.connectionTerminated(.authFailed))

        XCTAssertGreaterThanOrEqual(sut.capture.stopCount, 1)
        XCTAssertGreaterThanOrEqual(sut.playback.stopCount, 1)
        XCTAssertEqual(sut.viewModel.banner?.message, .authFailed)
        XCTAssertEqual(sut.viewModel.banner?.action, .openSettings)
    }

    func testRetryAfterSessionEndedStartsNewSession() {
        let sut = SUT()
        sut.connectAndReady()
        sut.coordinator.handle(.sessionEnded(reason: .idleTimeout, sessionID: "sess_123"))
        sut.sdk.state = makeState(connection: .connected)
        sut.sdk.startSessionCalls.removeAll()

        sut.coordinator.retry()

        XCTAssertEqual(sut.sdk.startSessionCalls.count, 1)
        XCTAssertNil(sut.sdk.startSessionCalls[0])
        XCTAssertFalse(sut.viewModel.sessionEnded)
        XCTAssertGreaterThanOrEqual(sut.capture.startCount, 2)
    }

    func testRetryWhenDisconnectedReconnects() {
        let sut = SUT()
        sut.coordinator.start()
        sut.sdk.state = makeState(connection: .disconnected)
        sut.coordinator.retry()
        XCTAssertEqual(sut.sdk.connectCount, 2)
    }

    func testBackgroundUnmutedKeepsFullAndForegroundReconnects() {
        let sut = SUT()
        sut.coordinator.start()
        sut.sdk.state = makeState(connection: .disconnected)

        sut.foreground.value = false
        sut.coordinator.onBackground()

        XCTAssertEqual(sut.capture.stopCount, 0)
        XCTAssertEqual(sut.playback.stopCount, 0)
        XCTAssertEqual(sut.session.deactivateCount, 0)
        XCTAssertEqual(sut.capture.startCount, 1)

        sut.foreground.value = true
        sut.coordinator.onForeground()

        XCTAssertEqual(sut.capture.startCount, 1)
        XCTAssertEqual(sut.playback.startCount, 1)
        XCTAssertEqual(sut.sdk.connectCount, 2)
    }

    func testBackgroundMutedStopsMediaAndReleasesMic() {
        let sut = SUT()
        sut.connectAndReady()
        sut.coordinator.toggleMic()
        XCTAssertTrue(sut.viewModel.isMicMuted)

        sut.foreground.value = false
        sut.coordinator.onBackground()

        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        XCTAssertEqual(sut.capture.topUpCount, 1)
        XCTAssertGreaterThanOrEqual(sut.capture.stopCount, 1)
        XCTAssertGreaterThanOrEqual(sut.playback.stopCount, 1)
        XCTAssertGreaterThanOrEqual(sut.session.stopEngineCount, 1)
        XCTAssertGreaterThanOrEqual(sut.session.deactivateCount, 1)
    }

    func testBackgroundMutedAndSpeakerMutedTearsDown() {
        let sut = SUT()
        sut.connectAndReady()
        sut.viewModel.setMicMuted(true)
        sut.viewModel.setSpeakerMuted(true)
        sut.playback.hasBufferedOrScheduledAudio = true
        sut.sdk.state = makeState(connection: .connected, session: .speaking)

        sut.foreground.value = false
        sut.coordinator.onBackground()

        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        XCTAssertEqual(sut.capture.topUpCount, 1)
        XCTAssertGreaterThanOrEqual(sut.playback.stopCount, 1)
        XCTAssertGreaterThanOrEqual(sut.session.deactivateCount, 1)
    }

    func testBackgroundMutedWithBufferedTTSStillStopsMedia() {
        let sut = SUT()
        sut.connectAndReady()
        sut.coordinator.toggleMic()
        sut.playback.hasBufferedOrScheduledAudio = true
        sut.sdk.state = makeState(connection: .connected, session: .speaking)
        let playbackStops = sut.playback.stopCount

        sut.foreground.value = false
        sut.coordinator.onBackground()

        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        XCTAssertGreaterThan(sut.playback.stopCount, playbackStops)
        XCTAssertNil(sut.session.appliedCaptureSource)
    }

    func testBackgroundMutedRouteChangeDoesNotStartMedia() {
        let sut = SUT()
        sut.connectAndReady()
        sut.viewModel.setMicMuted(true)
        sut.foreground.value = false
        sut.coordinator.onBackground()
        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        let captures = sut.capture.startCount
        let engineStarts = sut.session.startEngineCount

        sut.session.onRouteChanged?()

        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        XCTAssertEqual(sut.capture.startCount, captures)
        XCTAssertEqual(sut.session.startEngineCount, engineStarts)
    }

    func testBackgroundRecoveringCaptureKeepsFull() {
        let sut = SUT()
        sut.connectAndReady()
        sut.capture.lifecycleState = .recovering

        sut.foreground.value = false
        sut.coordinator.onBackground()

        XCTAssertEqual(sut.coordinator.pipelineState, .full)
        XCTAssertEqual(sut.capture.stopCount, 0)
        XCTAssertEqual(sut.playback.stopCount, 0)
        XCTAssertEqual(sut.session.deactivateCount, 0)
    }

    func testBackgroundUnmutedStoppedCaptureRestartsFull() {
        let sut = SUT()
        sut.connectAndReady()
        sut.capture.lifecycleState = .stopped
        XCTAssertFalse(sut.viewModel.isMicMuted)
        let captures = sut.capture.startCount

        sut.foreground.value = false
        sut.coordinator.onBackground()

        XCTAssertEqual(sut.coordinator.pipelineState, .full)
        XCTAssertGreaterThan(sut.capture.startCount, captures)
        XCTAssertEqual(sut.session.appliedCaptureSource, .voice)
        XCTAssertEqual(sut.session.deactivateCount, 0)
    }

    func testForegroundFromStoppedRestartsFull() {
        let sut = SUT()
        sut.connectAndReady()
        sut.viewModel.setMicMuted(true)
        sut.foreground.value = false
        sut.coordinator.onBackground()
        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        let captures = sut.capture.startCount
        let engineStarts = sut.session.startEngineCount
        let prepares = sut.playback.prepareCount

        sut.foreground.value = true
        sut.coordinator.onForeground()

        XCTAssertEqual(sut.coordinator.pipelineState, .full)
        XCTAssertGreaterThan(sut.capture.startCount, captures)
        XCTAssertGreaterThan(sut.session.startEngineCount, engineStarts)
        XCTAssertEqual(sut.session.appliedCaptureSource, .voice)
        XCTAssertGreaterThan(sut.playback.startCount, 1, "background-buffered TTS must be discarded")
        XCTAssertEqual(sut.playback.prepareCount, prepares, "stopped playback has nothing to prepare")
    }

    func testForegroundRestartFailureTearsDownAndRecovers() {
        let sut = SUT()
        sut.connectAndReady()
        sut.viewModel.setMicMuted(true)
        sut.foreground.value = false
        sut.coordinator.onBackground()
        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        sut.capture.startError = NSError(domain: "capture", code: 1)

        sut.foreground.value = true
        sut.coordinator.onForeground()

        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        XCTAssertEqual(sut.viewModel.banner?.message, .audioUnavailable)

        sut.capture.startError = nil
        sut.coordinator.onForeground()
        XCTAssertEqual(sut.coordinator.pipelineState, .full)
        XCTAssertNil(sut.viewModel.banner)
    }

    func testForegroundRestartDefersEngineFailureWhileCaptureRecovering() {
        let sut = SUT()
        sut.connectAndReady()
        sut.viewModel.setMicMuted(true)
        sut.foreground.value = false
        sut.coordinator.onBackground()
        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        let deactivates = sut.session.deactivateCount
        let playbackStarts = sut.playback.startCount

        sut.capture.lifecycleAfterStart = .recovering
        sut.session.engineStartError = NSError(domain: "engine", code: 1)
        sut.foreground.value = true
        sut.coordinator.onForeground()

        XCTAssertEqual(sut.coordinator.pipelineState, .full)
        XCTAssertEqual(sut.capture.lifecycleState, .recovering)
        XCTAssertEqual(sut.session.deactivateCount, deactivates, "capture retries own the engine restart")
        XCTAssertNil(sut.viewModel.banner)
        XCTAssertGreaterThan(sut.playback.startCount, playbackStarts)
    }

    func testBackgroundFullStartFailureTearsDownWithoutBanner() {
        let sut = SUT()
        sut.connectAndReady()
        sut.capture.lifecycleState = .stopped
        sut.session.engineStartError = NSError(domain: "engine", code: 1)
        let deactivates = sut.session.deactivateCount

        sut.foreground.value = false
        sut.coordinator.onBackground()

        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        XCTAssertEqual(sut.session.deactivateCount, deactivates + 1)
        XCTAssertNil(sut.viewModel.banner, "background failures must not surface a foreground banner")
    }

    func testBackgroundMutedNewPCMDoesNotStartMedia() {
        let sut = SUT()
        sut.connectAndReady()
        sut.viewModel.setMicMuted(true)
        sut.playback.hasBufferedOrScheduledAudio = false
        sut.sdk.state = makeState(connection: .connected, session: .idle)
        sut.foreground.value = false
        sut.coordinator.onBackground()
        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        let captures = sut.capture.startCount
        let engineStarts = sut.session.startEngineCount

        sut.playback.hasBufferedOrScheduledAudio = true
        sut.playback.onActivityChange?()

        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        XCTAssertEqual(sut.capture.startCount, captures)
        XCTAssertEqual(sut.session.startEngineCount, engineStarts)
    }

    func testBackgroundUnmutedNewPCMKeepsFull() {
        let sut = SUT()
        sut.connectAndReady()
        sut.capture.lifecycleState = .stopped
        sut.playback.hasBufferedOrScheduledAudio = false
        sut.sdk.state = makeState(connection: .connected, session: .idle)
        sut.foreground.value = false
        sut.coordinator.onBackground()
        XCTAssertEqual(sut.coordinator.pipelineState, .full)

        sut.playback.hasBufferedOrScheduledAudio = true
        sut.playback.onActivityChange?()

        XCTAssertEqual(sut.coordinator.pipelineState, .full)
    }

    func testCaptureFailureTearsDownAndShowsBanner() {
        let sut = SUT()
        sut.connectAndReady()

        let error = AppErrorMessage("conv_capture_failed", logDetail: "boom")
        sut.capture.onFailure?(error)

        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        XCTAssertGreaterThanOrEqual(sut.playback.stopCount, 1)
        XCTAssertGreaterThanOrEqual(sut.session.deactivateCount, 1)
        guard case let .captureFailed(received)? = sut.viewModel.banner?.message else {
            return XCTFail("expected capture failure banner")
        }
        XCTAssertEqual(received, error)
        XCTAssertEqual(received.key, "conv_capture_failed")
        XCTAssertEqual(received.logDetail, "boom")
    }

    func testInterruptionBeganTearsDownAndEndedRestoresFull() {
        let sut = SUT()
        sut.connectAndReady()

        sut.session.onInterruptionBegan?()
        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        XCTAssertGreaterThanOrEqual(sut.session.deactivateCount, 1)

        let captures = sut.capture.startCount
        sut.session.onInterruptionEnded?()

        XCTAssertEqual(sut.coordinator.pipelineState, .full)
        XCTAssertGreaterThan(sut.capture.startCount, captures)
    }

    func testRouteChangeRecoversStoppedEngineWithoutFullTeardown() {
        let sut = SUT()
        sut.connectAndReady()
        sut.session.simulateEngineStopped()
        let playbackStops = sut.playback.stopCount
        let engineStarts = sut.session.startEngineCount

        sut.session.onRouteChanged?()

        XCTAssertEqual(sut.coordinator.pipelineState, .full)
        XCTAssertEqual(sut.playback.stopCount, playbackStops)
        XCTAssertGreaterThan(sut.session.startEngineCount, engineStarts)
        XCTAssertGreaterThan(sut.playback.resumeCount, 0)
    }

    func testStartFailureTearsDownPartialMedia() {
        let sut = SUT()
        sut.session.activateError = NSError(domain: "start", code: 1)

        sut.coordinator.start()

        XCTAssertEqual(sut.coordinator.pipelineState, .stopped)
        XCTAssertGreaterThanOrEqual(sut.capture.stopCount, 1)
        XCTAssertGreaterThanOrEqual(sut.playback.stopCount, 1)
        XCTAssertGreaterThanOrEqual(sut.session.stopEngineCount, 1)
        XCTAssertGreaterThanOrEqual(sut.session.deactivateCount, 1)
        XCTAssertEqual(sut.viewModel.banner?.message, .audioUnavailable)
    }

    func testBackgroundRouteChangeDoesNotConsumeCameraIntent() {
        let camera = FakeCamera()
        let sut = SUT(camera: camera)
        sut.coordinator.start()
        sut.coordinator.startCamera()
        sut.foreground.value = false
        sut.coordinator.onBackground()
        XCTAssertTrue(camera.startCalls.count == 1)

        sut.session.onRouteChanged?()
        XCTAssertEqual(camera.startCalls.count, 1, "background audio event must not resume camera")
        XCTAssertTrue(sut.viewModel.isCameraOn)

        sut.foreground.value = true
        sut.coordinator.onForeground()
        XCTAssertEqual(camera.startCalls.count, 2)
    }

    func testMicAndSpeakerToggles() {
        let sut = SUT()
        sut.coordinator.start()

        sut.coordinator.toggleMic()
        XCTAssertTrue(sut.viewModel.isMicMuted)
        XCTAssertEqual(sut.capture.mutedValues.last, true)

        sut.coordinator.toggleMic()
        XCTAssertFalse(sut.viewModel.isMicMuted)
        XCTAssertEqual(sut.capture.mutedValues.last, false)

        sut.coordinator.toggleSpeaker()
        XCTAssertTrue(sut.viewModel.isSpeakerMuted)
        XCTAssertEqual(sut.playback.mutedValues.last, true)
    }

    func testRecoverableErrorAutoDismisses() async throws {
        let sut = SUT()
        sut.coordinator.start()
        sut.coordinator.handle(.error(code: 100, message: "temporary", fatal: false, recoverable: true))
        XCTAssertEqual(sut.viewModel.banner?.kind, .warning)

        try await Task.sleep(nanoseconds: 200_000_000)
        XCTAssertNil(sut.viewModel.banner)
    }

    func testFatalErrorPersists() {
        let sut = SUT()
        sut.coordinator.start()
        sut.coordinator.handle(.error(code: 500, message: "boom", fatal: true, recoverable: false))
        XCTAssertEqual(sut.viewModel.banner?.kind, .error)
        XCTAssertNil(sut.viewModel.banner?.autoDismissAfter)
    }

    func testCaptureLevelAndFailureReachViewModel() {
        let sut = SUT()
        sut.coordinator.start()
        sut.capture.onLevel?(3)
        XCTAssertEqual(sut.viewModel.micMeter.level, 3)

        let error = AppErrorMessage("conv_capture_failed", logDetail: "converter failed")
        sut.capture.onFailure?(error)
        XCTAssertEqual(sut.viewModel.banner?.message, .captureFailed(error))
        XCTAssertEqual(sut.viewModel.banner?.action, .retry)
    }

    func testMutedMicIgnoresLateLevelReports() {
        let sut = SUT()
        sut.coordinator.start()
        sut.coordinator.toggleMic()
        XCTAssertTrue(sut.viewModel.isMicMuted)

        sut.capture.onLevel?(3)

        XCTAssertEqual(sut.viewModel.micMeter.level, 0)
    }

    func testStopAndRestartResetMicMeter() {
        let sut = SUT()
        sut.coordinator.start()
        sut.capture.onLevel?(3)
        XCTAssertEqual(sut.viewModel.micMeter.level, 3)

        sut.coordinator.stop()
        XCTAssertEqual(sut.viewModel.micMeter.level, 0, "teardown must reset the meter")

        sut.viewModel.micMeter.set(3)
        sut.coordinator.start()
        XCTAssertEqual(
            sut.viewModel.micMeter.level,
            0,
            "restart must reset the meter synchronously, before the zero baseline arrives"
        )
    }

    func testEnteringSpeakingTriggersHaptic() {
        let sut = SUT()
        sut.connectAndReady()

        sut.coordinator.handle(.sessionStateChanged(.thinking))
        XCTAssertEqual(sut.haptics.taps, 0)

        sut.coordinator.handle(.sessionStateChanged(.speaking))
        XCTAssertEqual(sut.haptics.taps, 1)

        sut.coordinator.handle(.sessionStateChanged(.listening))
        sut.coordinator.handle(.sessionStateChanged(.speaking))
        XCTAssertEqual(sut.haptics.taps, 2)

        // Local VAD no longer drives interrupts or haptics.
        sut.coordinator.handle(.localVadStateChanged(.speech))
        XCTAssertEqual(sut.haptics.taps, 2)
        XCTAssertEqual(sut.sdk.interruptCount, 0)
    }

    func testEnteringSpeakingSkipsHapticWhenDisabled() {
        let sut = SUT(hapticEnabled: false)
        sut.connectAndReady()

        sut.coordinator.handle(.sessionStateChanged(.speaking))
        XCTAssertEqual(sut.haptics.taps, 0)
    }

    func testSessionReadySetsListeningAndFirstSessionFlag() {
        let sut = SUT()
        sut.connectAndReady()
        XCTAssertEqual(sut.viewModel.sessionState, .listening)
        XCTAssertFalse(sut.viewModel.isFirstSession)
    }

    func testForegroundAfterSessionEndedDoesNotRestartMedia() {
        let sut = SUT()
        sut.connectAndReady()
        sut.coordinator.handle(.sessionEnded(reason: .serverShutdown, sessionID: "sess_123"))
        let starts = sut.capture.startCount
        let plays = sut.playback.startCount

        sut.coordinator.onForeground()

        XCTAssertEqual(sut.capture.startCount, starts)
        XCTAssertEqual(sut.playback.startCount, plays)
    }

    func testAuthFailureClearsServerSessionID() {
        let sut = SUT()
        sut.connectAndReady()
        XCTAssertNotNil(sut.coordinator.serverSessionID)

        sut.coordinator.handle(.connectionTerminated(.authFailed))

        XCTAssertNil(sut.coordinator.serverSessionID)
    }

    func testForegroundWithoutPermissionDoesNotStartMedia() {
        let sut = SUT(permission: .denied)
        sut.coordinator.start()
        sut.coordinator.onForeground()

        XCTAssertEqual(sut.capture.startCount, 0)
        XCTAssertEqual(sut.playback.startCount, 0)
        XCTAssertEqual(sut.sdk.connectCount, 0)
    }

    func testFinalMessagesCarryTranslateMode() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let history = FakeHistory()
        let sut = SUT(history: history, modeModel: modeModel)
        sut.connectAndReady()
        modeModel.requestTranslate(target: .lecture, language: "en")

        sut.coordinator.handle(.transcription(text: "hello", isFinal: true, transcriptionID: "t1"))
        sut.coordinator.handle(
            .aiText(text: "Hi", messageID: "m1", index: 0, isFinal: true, image: nil, source: .llm)
        )

        XCTAssertEqual(sut.viewModel.messages.first?.mode, .translate)
        XCTAssertEqual(sut.viewModel.messages.last?.mode, .translate)
        XCTAssertEqual(history.userMessages.first?.mode, .translate)
        XCTAssertEqual(history.assistantMessages.first?.mode, .translate)
    }

    func testFinalMessagesCarryTranscribeOnlyForUser() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let history = FakeHistory()
        let sut = SUT(history: history, modeModel: modeModel)
        sut.connectAndReady()
        modeModel.requestTranscribe(source: .voice)

        sut.coordinator.handle(.transcription(text: "hello", isFinal: true, transcriptionID: "t1"))
        sut.coordinator.handle(
            .aiText(text: "Hi", messageID: "m1", index: 0, isFinal: true, image: nil, source: .llm)
        )

        XCTAssertEqual(sut.viewModel.messages.first?.mode, .transcribe)
        XCTAssertNil(sut.viewModel.messages.last?.mode)
        XCTAssertEqual(history.userMessages.first?.mode, .transcribe)
        XCTAssertNil(history.assistantMessages.first?.mode)
    }

    func testModeRequestFlowsThroughCoordinator() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let sut = SUT(modeModel: modeModel)
        sut.connectAndReady()

        modeModel.requestTranslate(target: .dialogue, language: "auto")

        XCTAssertEqual(sut.sdk.sessionChanges.count, 1)
        XCTAssertEqual(sut.sdk.sessionChanges[0].mode.rawValue, SessionMode.translate.rawValue)
        XCTAssertEqual(sut.sdk.sessionChanges[0].myLanguage, "zh")
        XCTAssertEqual(sut.sdk.sessionChanges[0].localLanguage, "auto")
        XCTAssertEqual(sut.sdk.sessionChanges[0].usecase?.rawValue, SessionUsecase.dialogue.rawValue)
    }

    func testSessionChangedEchoUpdatesModeModel() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let sut = SUT(modeModel: modeModel)
        sut.connectAndReady()

        sut.coordinator.handle(
            .sessionChanged(mode: .translate, myLanguage: "zh", localLanguage: "en", usecase: .lecture)
        )

        XCTAssertTrue(modeModel.translateEnabled)
        XCTAssertTrue(modeModel.lectureEnabled)
        XCTAssertEqual(modeModel.activeMode, .lecture)
    }

    func testMode1009RollsBackAndClearsRestore() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let sut = SUT(modeModel: modeModel)
        sut.connectAndReady()
        modeModel.requestTranslate(target: .dialogue, language: "en")

        sut.coordinator.handle(.error(code: 1009, message: "rejected", fatal: false, recoverable: true))

        XCTAssertFalse(modeModel.anyModeActive)
        modeModel.sessionBecameNotReady()
        modeModel.sessionBecameReady()
        XCTAssertEqual(sut.sdk.sessionChanges.count, 1)
    }

    func testSessionEndedClearsModeAndRestoresOnNextReady() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let sut = SUT(modeModel: modeModel)
        sut.connectAndReady()
        modeModel.requestTranslate(target: .lecture, language: "en")
        sut.sdk.sessionChanges.removeAll()

        sut.coordinator.handle(.sessionEnded(reason: .idleTimeout, sessionID: "sess_123"))
        XCTAssertFalse(modeModel.anyModeActive)

        sut.coordinator.handle(.sessionReady(sessionID: "sess_456", restored: false))
        XCTAssertEqual(sut.sdk.sessionChanges.count, 1)
        XCTAssertEqual(sut.sdk.sessionChanges[0].mode.rawValue, SessionMode.translate.rawValue)
        XCTAssertEqual(sut.sdk.sessionChanges[0].usecase?.rawValue, SessionUsecase.lecture.rawValue)
        XCTAssertTrue(modeModel.lectureActive)
    }

    func testServerClosedClearsModeUI() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let sut = SUT(modeModel: modeModel)
        sut.connectAndReady()
        modeModel.requestTranslate(target: .dialogue, language: "en")

        sut.coordinator.handle(.connectionTerminated(.serverClosed))

        XCTAssertFalse(modeModel.anyModeActive)
        XCTAssertFalse(modeModel.isSessionReady)
    }

    func testTranscribeSourceRestartsCapturePreservingBufferedPlayback() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let sut = SUT(modeModel: modeModel)
        sut.connectAndReady()
        let activates = sut.session.activateCount
        let captures = sut.capture.startCount
        let playbackStops = sut.playback.stopCount

        modeModel.requestTranscribe(source: .original)

        XCTAssertEqual(sut.session.appliedCaptureSource, .original)
        XCTAssertGreaterThan(sut.session.activateCount, activates)
        XCTAssertGreaterThan(sut.capture.startCount, captures)
        XCTAssertEqual(sut.playback.stopCount, playbackStops, "source switch must preserve TTS")

        modeModel.exitToChat()

        XCTAssertEqual(sut.session.appliedCaptureSource, .voice)
    }

    func testSourceSwitchFailureFallsBackToVoice() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let sut = SUT(modeModel: modeModel)
        sut.connectAndReady()
        sut.session.failNextActivate = true

        modeModel.requestTranscribe(source: .original)

        XCTAssertEqual(sut.session.appliedCaptureSource, .voice)
        XCTAssertEqual(modeModel.transcribeSource, .voice)
        guard case let .server(titleKey, _)? = sut.viewModel.banner?.message else {
            return XCTFail("expected source failure banner")
        }
        XCTAssertEqual(titleKey, "conv_audio_source_failed")
        XCTAssertEqual(sut.sdk.sessionChanges.last?.mode.rawValue, SessionMode.transcribe.rawValue)
    }

    func testEpochAdvanceResetsModeReadinessAndRestoresOnReady() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let sut = SUT(modeModel: modeModel)
        sut.connectAndReady()
        modeModel.requestTranslate(target: .dialogue, language: "en")
        sut.sdk.sessionChanges.removeAll()

        sut.coordinator.handle(.connectionEpochAdvanced(2))
        XCTAssertFalse(modeModel.isSessionReady)
        XCTAssertTrue(modeModel.dialogueActive)

        sut.coordinator.handle(.sessionReady(sessionID: "sess_new", restored: false))
        XCTAssertTrue(modeModel.isSessionReady)
        XCTAssertTrue(modeModel.dialogueActive)
        XCTAssertEqual(sut.sdk.sessionChanges.count, 1)
        XCTAssertEqual(
            sut.sdk.sessionChanges.last?.mode.rawValue,
            SessionMode.translate.rawValue
        )
    }

    func testNotReadyLocalExitDoesNotSendChat() {
        let modeModel = ConversationModeModel(deviceLanguage: { "zh" })
        let sut = SUT(modeModel: modeModel)
        sut.connectAndReady()
        modeModel.requestTranslate(target: .dialogue, language: "en")
        sut.sdk.sessionChanges.removeAll()

        sut.coordinator.handle(.connectionStateChanged(.reconnecting))
        modeModel.exitToChat()

        XCTAssertTrue(sut.sdk.sessionChanges.isEmpty)
        XCTAssertFalse(modeModel.anyModeActive)
    }

    func testAuthFailureBlocksForegroundAutoReconnect() {
        let sut = SUT()
        sut.connectAndReady()
        sut.coordinator.handle(.connectionTerminated(.authFailed))
        let captureStarts = sut.capture.startCount
        let playbackStarts = sut.playback.startCount
        let connects = sut.sdk.connectCount

        sut.coordinator.onForeground()
        sut.coordinator.retry()

        XCTAssertEqual(sut.capture.startCount, captureStarts)
        XCTAssertEqual(sut.playback.startCount, playbackStarts)
        XCTAssertEqual(sut.sdk.connectCount, connects)
    }

    // MARK: - M2 camera lifecycle

    func testAutoCameraStartsOnJoinWithDefaultFrontAndTurn() {
        let camera = FakeCamera()
        let preferences = FakeCameraPreferences(defaultFront: true, autoOnJoin: true)
        let sut = SUT(camera: camera, cameraPreferences: preferences)

        sut.coordinator.start()

        XCTAssertEqual(camera.startCalls, [true])
        XCTAssertTrue(sut.viewModel.isCameraOn)
        XCTAssertEqual(sut.turnCounter.current, 1)
    }

    func testCameraToggleSwitchAndStop() {
        let camera = FakeCamera()
        let preferences = FakeCameraPreferences(defaultFront: false, autoOnJoin: false)
        let sut = SUT(camera: camera, cameraPreferences: preferences)
        sut.coordinator.start()

        sut.coordinator.startCamera()
        XCTAssertEqual(camera.startCalls, [false])
        XCTAssertTrue(sut.viewModel.isCameraOn)
        XCTAssertEqual(sut.turnCounter.current, 1)

        sut.coordinator.switchCamera()
        XCTAssertEqual(camera.switchCalls, [true])
        XCTAssertTrue(sut.viewModel.isFrontCamera)

        sut.coordinator.stopCamera()
        XCTAssertEqual(camera.stopCount, 1)
        XCTAssertFalse(sut.viewModel.isCameraOn)
    }

    func testBackgroundStopsCameraAndForegroundResumesWithoutTurnBump() {
        let camera = FakeCamera()
        let preferences = FakeCameraPreferences(defaultFront: false, autoOnJoin: false)
        let sut = SUT(camera: camera, cameraPreferences: preferences)
        sut.coordinator.start()
        sut.coordinator.startCamera()
        let turns = sut.turnCounter.current

        sut.coordinator.onBackground()
        XCTAssertEqual(camera.stopCount, 1)
        XCTAssertTrue(sut.viewModel.isCameraOn)

        sut.coordinator.onForeground()
        XCTAssertEqual(camera.startCalls, [false, false])
        XCTAssertEqual(sut.turnCounter.current, turns)
    }

    func testPauseCameraForPhotoResumesAfterFlow() {
        let camera = FakeCamera()
        let sut = SUT(camera: camera)
        sut.connectAndReady()
        sut.coordinator.startCamera()
        XCTAssertEqual(camera.startCalls, [false])

        XCTAssertTrue(sut.coordinator.pauseCameraForPhoto())
        XCTAssertEqual(camera.stopCount, 1)
        XCTAssertTrue(sut.viewModel.isCameraOn)

        sut.coordinator.resumeCameraAfterPhoto()
        XCTAssertEqual(camera.startCalls, [false, false])
        XCTAssertTrue(sut.viewModel.isCameraOn)
    }

    func testPhotoPauseSurvivesForegroundUntilFlowEnds() {
        let camera = FakeCamera()
        let sut = SUT(camera: camera)
        sut.connectAndReady()
        sut.coordinator.startCamera()
        XCTAssertTrue(sut.coordinator.pauseCameraForPhoto())

        sut.coordinator.onBackground()
        sut.coordinator.onForeground()

        XCTAssertEqual(camera.startCalls, [false])
        XCTAssertTrue(sut.viewModel.isCameraOn)

        sut.coordinator.resumeCameraAfterPhoto()
        XCTAssertEqual(camera.startCalls, [false, false])
        XCTAssertTrue(sut.viewModel.isCameraOn)
    }

    func testPhotoFlowFinishingInBackgroundPreservesCameraIntent() {
        let camera = FakeCamera()
        let sut = SUT(camera: camera)
        sut.connectAndReady()
        sut.coordinator.startCamera()
        XCTAssertTrue(sut.coordinator.pauseCameraForPhoto())

        sut.foreground.value = false
        sut.coordinator.onBackground()
        sut.coordinator.resumeCameraAfterPhoto()
        XCTAssertEqual(camera.startCalls, [false], "background resume must not start camera")
        XCTAssertTrue(sut.viewModel.isCameraOn)
        XCTAssertTrue(sut.coordinator.hasCameraResumeIntentForTesting)

        sut.foreground.value = true
        sut.coordinator.onForeground()
        XCTAssertEqual(camera.startCalls, [false, false])
    }

    func testPauseCameraForPhotoNoopsWhenCameraOff() {
        let camera = FakeCamera()
        let sut = SUT(camera: camera)
        sut.connectAndReady()

        XCTAssertFalse(sut.coordinator.pauseCameraForPhoto())
        XCTAssertEqual(camera.stopCount, 0)

        sut.coordinator.resumeCameraAfterPhoto()
        XCTAssertTrue(camera.startCalls.isEmpty)
    }

    func testSessionEndedStopsCamera() {
        let camera = FakeCamera()
        let preferences = FakeCameraPreferences(defaultFront: true, autoOnJoin: true)
        let sut = SUT(camera: camera, cameraPreferences: preferences)
        sut.connectAndReady()
        XCTAssertTrue(sut.viewModel.isCameraOn)

        sut.coordinator.handle(.sessionEnded(reason: .idleTimeout, sessionID: "sess_123"))

        XCTAssertTrue(camera.stopCount > 0)
        XCTAssertFalse(sut.viewModel.isCameraOn)
    }

    func testCameraFailureResetsUIAndShowsBanner() {
        let camera = FakeCamera()
        let preferences = FakeCameraPreferences(defaultFront: false, autoOnJoin: false)
        let sut = SUT(camera: camera, cameraPreferences: preferences)
        sut.coordinator.start()
        sut.coordinator.startCamera()

        camera.fail("boom")

        XCTAssertFalse(sut.viewModel.isCameraOn)
        XCTAssertEqual(
            sut.viewModel.banner?.message,
            .cameraStartFailed(
                AppErrorMessage("conv_camera_start_failed", logDetail: "boom")
            )
        )
        XCTAssertNil(sut.viewModel.banner?.action)
    }

    func testCameraPermissionFailureOffersSystemSettings() {
        let camera = FakeCamera()
        let preferences = FakeCameraPreferences(defaultFront: false, autoOnJoin: false)
        let sut = SUT(camera: camera, cameraPreferences: preferences)
        sut.coordinator.start()
        sut.coordinator.startCamera()

        camera.onFailure?(
            AppErrorMessage("conv_camera_permission_denied", logDetail: "denied")
        )

        XCTAssertFalse(sut.viewModel.isCameraOn)
        XCTAssertEqual(sut.viewModel.banner?.action, .openSystemSettings)
    }

    func testMicPermissionLossStopsCamera() {
        let camera = FakeCamera()
        let preferences = FakeCameraPreferences(defaultFront: false, autoOnJoin: false)
        let sut = SUT(camera: camera, cameraPreferences: preferences)
        sut.coordinator.start()
        sut.coordinator.startCamera()

        sut.coordinator.onMicPermissionLost()

        XCTAssertEqual(camera.stopCount, 1)
        XCTAssertFalse(sut.viewModel.isCameraOn)
    }

    private func makeAgentJob(_ id: String = "job-1") -> AgentJob {
        AgentJob(
            jobID: id,
            agentName: "hermes",
            summary: "Task",
            startedAtMs: 1_000,
            runningTimeMs: 2_000
        )
    }

    func testJobStatusSnapshotReplacesAndDropsDisconnectedFrames() {
        let jobs = AgentJobStatusModel()
        let sut = SUT(jobStatus: jobs)
        sut.coordinator.start()
        sut.sdk.state = makeState(connection: .connected)
        sut.coordinator.handle(.connectionStateChanged(.connected))

        sut.coordinator.handle(.jobStatus([makeAgentJob()]))
        XCTAssertEqual(jobs.jobs.map(\.jobID), ["job-1"])

        sut.sdk.state = makeState(connection: .reconnecting)
        sut.coordinator.handle(.connectionStateChanged(.reconnecting))
        XCTAssertTrue(jobs.jobs.isEmpty)

        sut.coordinator.handle(.jobStatus([makeAgentJob("job-2")]))
        XCTAssertTrue(jobs.jobs.isEmpty)
    }

    func testSessionEndedClearsJobs() {
        let jobs = AgentJobStatusModel()
        let sut = SUT(jobStatus: jobs)
        sut.connectAndReady()
        sut.coordinator.handle(.jobStatus([makeAgentJob()]))
        XCTAssertEqual(jobs.jobs.count, 1)

        sut.coordinator.handle(.sessionEnded(reason: .idleTimeout, sessionID: "sess_123"))
        XCTAssertTrue(jobs.jobs.isEmpty)
    }

    func testHistorySessionSwitchClearsJobs() {
        let history = FakeHistory()
        let jobs = AgentJobStatusModel()
        let sut = SUT(history: history, jobStatus: jobs)
        sut.connectAndReady()
        sut.coordinator.handle(.jobStatus([makeAgentJob()]))
        XCTAssertEqual(jobs.jobs.count, 1)

        history.emit(HistorySeed(sessionID: "new-session", messages: []))
        XCTAssertTrue(jobs.jobs.isEmpty)
    }

    func testStopClearsJobs() {
        let jobs = AgentJobStatusModel()
        let sut = SUT(jobStatus: jobs)
        sut.connectAndReady()
        sut.coordinator.handle(.jobStatus([makeAgentJob()]))
        XCTAssertEqual(jobs.jobs.count, 1)

        sut.coordinator.stop()
        XCTAssertTrue(jobs.jobs.isEmpty)
    }

    func testJobStatusIsNotGatedByHistorySuppression() {
        let history = FakeHistory()
        history.isSuppressingMessages = true
        let jobs = AgentJobStatusModel()
        let sut = SUT(history: history, jobStatus: jobs)
        sut.connectAndReady()

        sut.coordinator.handle(.jobStatus([makeAgentJob()]))

        XCTAssertEqual(jobs.jobs.count, 1)
    }
}
