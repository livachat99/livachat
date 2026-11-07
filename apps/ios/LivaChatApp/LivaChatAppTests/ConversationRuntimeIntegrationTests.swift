import AVFoundation
import LivaChatSDK
import XCTest
@testable import LivaChatApp

private final class IntegrationSink: LivaChatAudioSink, @unchecked Sendable {
    func livaChat(_ sdk: LivaChatSDK, didReceiveAudio frame: AudioPCMFrame) {}
}

private final class IntegrationCapture: AudioCapturing {
    var onLevel: (@MainActor (Int) -> Void)?
    var onFailure: (@MainActor (AppErrorMessage) -> Void)?
    var lifecycleState: CaptureLifecycleState = .stopped
    func start() throws { lifecycleState = .running }
    func stop() { lifecycleState = .stopped }
    func setMuted(_ muted: Bool) {}
    func topUpSilenceWindow() {}
}

private final class IntegrationPlayback: AudioPlaying {
    var isRunning = false
    var hasBufferedOrScheduledAudio = false
    var onActivityChange: (@MainActor () -> Void)?
    func start() { isRunning = true }
    func stop() {
        isRunning = false
        hasBufferedOrScheduledAudio = false
    }
    func setMuted(_ muted: Bool) {}
    func flush(reason: PlaybackFlushReason, epoch: UInt64) {
        hasBufferedOrScheduledAudio = false
    }
    func setTTSInputExpected(_ expected: Bool) {}
    func resumePreservingBuffer() { isRunning = true }
    func prepareForAudioReconfiguration() { isRunning = false }
}

@MainActor
private final class IntegrationSession: AudioSessionControlling {
    let engine = AVAudioEngine()
    let playbackNode = AVAudioPlayerNode()
    var onInterruptionBegan: (() -> Void)?
    var onInterruptionEnded: (() -> Void)?
    var onRouteChanged: (() -> Void)?
    var onMediaServicesReset: (() -> Void)?
    private(set) var appliedCaptureSource: TranscribeAudioSource?
    private(set) var isEngineRunning = false
    func activate(captureSource source: TranscribeAudioSource) throws {
        appliedCaptureSource = source
    }
    func startEngine() throws { isEngineRunning = true }
    func stopEngine() { isEngineRunning = false }
    func deactivate() {
        isEngineRunning = false
        appliedCaptureSource = nil
    }
}

@MainActor
private final class IntegrationPermission: MicPermissionProviding {
    func current() -> MicPermissionState { .granted }
    func request(_ completion: @escaping @MainActor (MicPermissionState) -> Void) {
        completion(.granted)
    }
}

@MainActor
private final class IntegrationHaptics: HapticProviding {
    func lightTap() {}
}

@MainActor
final class ConversationRuntimeIntegrationTests: XCTestCase {
    private func mockServerConfiguration() -> (URL, String)? {
        let environment = ProcessInfo.processInfo.environment
        let urlString = environment["LIVACHAT_MOCK_SERVER_URL"]
            ?? (try? String(contentsOfFile: "/tmp/livachat_mock_server_url", encoding: .utf8))
        let token = environment["LIVACHAT_MOCK_TOKEN"]
            ?? (try? String(contentsOfFile: "/tmp/livachat_mock_token", encoding: .utf8))
        guard
            let urlString = urlString?.trimmingCharacters(in: .whitespacesAndNewlines),
            let token = token?.trimmingCharacters(in: .whitespacesAndNewlines),
            !urlString.isEmpty,
            !token.isEmpty,
            let url = URL(string: urlString)
        else {
            return nil
        }
        return (url, token)
    }

    private func waitUntil(
        timeout: TimeInterval = 5,
        condition: @MainActor () -> Bool
    ) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        XCTFail("condition not met within \(timeout)s")
    }

    func testMockServerTextFlow() async throws {
        guard let (url, token) = mockServerConfiguration() else {
            throw XCTSkip("set LIVACHAT_MOCK_SERVER_URL/TOKEN or /tmp/livachat_mock_server_url/token")
        }

        let config = LivaChatConfig(
            serverURL: url,
            token: token,
            deviceID: UUID().uuidString,
            clientVersion: "ios-1.0.0",
            enableVideo: false,
            connectTimeout: 3,
            reconnectMaxAttempts: 1,
            reconnectBaseDelay: 0.2,
            reconnectMaxDelay: 0.5,
            keepaliveInterval: 5,
            keepaliveTimeout: 10,
            tlsPolicy: .system
        )
        let sink = IntegrationSink()
        let sdk = try LivaChatSDK(config: config, audioSink: sink)
        let viewModel = ConversationViewModel(permissionProvider: IntegrationPermission())
        let coordinator = ConversationCoordinator(
            sdk: sdk,
            viewModel: viewModel,
            capture: IntegrationCapture(),
            playback: IntegrationPlayback(),
            audioSession: IntegrationSession(),
            haptics: IntegrationHaptics()
        )
        let adapter = ConversationEventAdapter { event in
            coordinator.handle(event)
        }
        adapter.start(sdk.events)
        coordinator.start()

        defer {
            adapter.stop()
            coordinator.stop()
        }

        try await waitUntil { viewModel.messages.contains(where: { $0.role == .user }) }
        try await waitUntil { viewModel.messages.contains(where: { $0.role == .assistant }) }
        try await waitUntil { viewModel.sessionState == .listening }

        XCTAssertNotNil(coordinator.serverSessionID)
        XCTAssertEqual(viewModel.connectionState, .connected)
        XCTAssertTrue(viewModel.messages.contains(where: { $0.role == .user && $0.text.contains("你好") }))
        XCTAssertTrue(viewModel.messages.contains(where: { $0.role == .assistant && !$0.text.isEmpty }))
    }
}
