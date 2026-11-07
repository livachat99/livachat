import Foundation
import LivaChatSDK

@MainActor
enum ConversationRuntimeFactory {
    static func make(
        config: LivaChatConfig,
        viewModel: ConversationViewModel,
        cameraPreferences: CameraPreferencesProviding? = nil,
        turnCounter: CaptureTurnCounter = CaptureTurnCounter(),
        hapticEnabled: Bool = true,
        history: ConversationHistoryRecording? = nil,
        jobStatus: AgentJobStatusModel? = nil,
        modeModel: ConversationModeModel? = nil,
        isForeground: @escaping () -> Bool = { true }
    ) throws -> ConversationRuntime {
        let audioSession = AudioSessionController()
        let engine = audioSession.engine
        let playback = AudioPlaybackController(
            playerNode: audioSession.playbackNode,
            initialEpoch: 1,
            engineIsRunning: { engine.isRunning }
        )
        let sdk = try LivaChatSDK(config: config, audioSink: playback)
        let capture = AudioCaptureController(engine: engine) { pcm, muted in
            sdk.pushAudioPCM(pcm, muted: muted)
        }
        let camera = CameraController(
            turnCounter: turnCounter,
            orientationMonitor: CameraDeviceOrientationMonitor()
        ) { data, width, height, facing, turnID in
            // The capture connection rotates pixels to the physical device
            // orientation, so the JPEG is already upright: protocol
            // orientation is always 0 (Android parity).
            sdk.pushVideoJPEG(
                data,
                width: width,
                height: height,
                orientation: .portrait0,
                cameraFacing: facing,
                turnID: turnID,
                text: nil
            )
        }
        let gallerySender = GalleryPhotoSender(turnCounter: turnCounter) { data, width, height, facing, turnID, text in
            sdk.pushVideoJPEG(
                data,
                width: width,
                height: height,
                orientation: .portrait0,
                cameraFacing: facing,
                turnID: turnID,
                text: text
            )
        }
        let haptics = HapticController()
        let coordinator = ConversationCoordinator(
            sdk: sdk,
            viewModel: viewModel,
            capture: capture,
            playback: playback,
            audioSession: audioSession,
            haptics: haptics,
            camera: camera,
            cameraPreferences: cameraPreferences,
            gallerySender: gallerySender,
            turnCounter: turnCounter,
            hapticEnabled: hapticEnabled,
            history: history,
            jobStatus: jobStatus,
            modeModel: modeModel,
            isForeground: isForeground
        )
        let adapter = ConversationEventAdapter { event in
            coordinator.handle(event)
        }
        return ConversationRuntime(
            sdk: sdk,
            coordinator: coordinator,
            capture: capture,
            playback: playback,
            audioSession: audioSession,
            camera: camera,
            adapter: adapter
        )
    }
}
