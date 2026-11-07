import Foundation
import LivaChatSDK

@MainActor
final class ConversationRuntime {
    let sdk: LivaChatSDKProviding
    let coordinator: ConversationCoordinator
    let capture: AudioCapturing
    let playback: AudioPlaying
    let audioSession: AudioSessionControlling
    let camera: CameraController?
    private let adapter: ConversationEventAdapter
    private(set) var isRunning = false

    init(
        sdk: LivaChatSDKProviding,
        coordinator: ConversationCoordinator,
        capture: AudioCapturing,
        playback: AudioPlaying,
        audioSession: AudioSessionControlling,
        camera: CameraController? = nil,
        adapter: ConversationEventAdapter
    ) {
        self.sdk = sdk
        self.coordinator = coordinator
        self.capture = capture
        self.playback = playback
        self.audioSession = audioSession
        self.camera = camera
        self.adapter = adapter
    }

    func start() {
        guard !isRunning else { return }
        isRunning = true
        adapter.start(sdk.events)
        coordinator.start()
    }

    func stop() {
        guard isRunning else { return }
        isRunning = false
        adapter.stop()
        coordinator.stop()
    }
}
