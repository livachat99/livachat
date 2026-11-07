import Foundation
import LivaChatProtocol

internal enum ProtocolMapping {
    static func endReason(_ value: LivaChatSessionEndReason) -> LivaChatProtocol.SessionEndReason {
        switch value {
        case .userHangup: return .userQuit
        case .appBackground: return .appBackground
        case .error: return .error
        }
    }

    static func interruptReason(_ value: InterruptReason) -> LivaChatProtocol.InterruptReason {
        switch value {
        case .userVoice: return .userVoice
        case .userTap, .appCancel: return .manual
        }
    }

    static func mode(_ value: SessionMode) -> ClientSessionChangeMode {
        switch value {
        case .chat: return .chat
        case .translate: return .translate
        case .transcribe: return .transcribe
        case .omni: return .omni
        }
    }

    static func usecase(_ value: SessionUsecase?) -> ClientSessionChangeUsecase? {
        switch value {
        case .lecture?: return .lecture
        case .dialogue?: return .dialogue
        case nil: return nil
        }
    }

    static func sessionState(_ value: SessionStatusCode) -> SessionState {
        switch value {
        case .idle: return .idle
        case .listening: return .listening
        case .thinking: return .thinking
        case .speaking: return .speaking
        }
    }

    static func avdetectorSwitch(_ enabled: Bool) -> AvDetectorSwitch {
        enabled ? .enable : .disable
    }

    /// nil = control echo (mode="config"); it must not update the App's mode state.
    static func sessionMode(_ value: ServerSessionChangedMode) -> SessionMode? {
        switch value {
        case .chat: return .chat
        case .translate: return .translate
        case .transcribe: return .transcribe
        case .omni: return .omni
        case .config: return nil
        }
    }

    static func sessionUsecase(_ value: ServerSessionChangedUsecase?) -> SessionUsecase? {
        switch value {
        case .lecture?: return .lecture
        case .dialogue?: return .dialogue
        case nil: return nil
        }
    }

    static func endedReason(_ value: LivaChatProtocol.SessionEndReason) -> SessionEndedReason {
        switch value {
        case .userQuit: return .userQuit
        case .appBackground: return .appBackground
        case .appTerminate: return .appTerminate
        case .serverShutdown: return .serverShutdown
        case .error: return .error
        case .idleTimeout: return .idleTimeout
        }
    }

    static func orientation(_ value: LivaChatProtocol.Orientation) -> Orientation {
        switch value {
        case .degrees0: return .portrait0
        case .degrees90: return .landscape90
        case .degrees180: return .portrait180
        case .degrees270: return .landscape270
        }
    }

    static func cameraFacing(_ value: LivaChatProtocol.CameraFacing) -> CameraFacing {
        switch value {
        case .front: return .front
        case .back: return .back
        case .screen: return .screen
        case .gallery: return .gallery
        }
    }

    static func aiTextSource(_ value: ServerAiTextSource?) -> AITextSource? {
        switch value {
        case .llm?: return .llm
        case .agent?: return .agent
        case nil: return nil
        }
    }

    static func startConfig(
        enableVideo: Bool,
        audioSampleRate: Int,
        vadSensitivity: LivaChatProtocol.VadSensitivity,
        clientVersion: String,
        timezone: String?,
        language: String?,
        avdetector: AvDetectorSwitch? = nil
    ) -> ClientSessionStartConfig {
        ClientSessionStartConfig(
            enableVideo: enableVideo,
            audioSampleRate: LivaChatProtocol.AudioSampleRate(rawValue: audioSampleRate) ?? .hz16000,
            vadSensitivity: vadSensitivity,
            clientVersion: clientVersion,
            timezone: timezone,
            language: language,
            avdetector: avdetector
        )
    }

    static func protocolOrientation(_ value: Orientation) -> LivaChatProtocol.Orientation {
        switch value {
        case .portrait0: return .degrees0
        case .landscape90: return .degrees90
        case .portrait180: return .degrees180
        case .landscape270: return .degrees270
        }
    }

    static func protocolCameraFacing(_ value: CameraFacing) -> LivaChatProtocol.CameraFacing {
        switch value {
        case .front: return .front
        case .back: return .back
        case .screen: return .screen
        case .gallery: return .gallery
        }
    }

    static func protocolVadSensitivity(_ value: VadSensitivity) -> LivaChatProtocol.VadSensitivity {
        switch value {
        case .low: return .low
        case .medium: return .medium
        case .high: return .high
        }
    }

    static func protocolVadState(_ value: VadState, muted: Bool) -> LivaChatProtocol.VadState {
        if muted { return .muted }
        switch value {
        case .speech: return .speech
        case .silence, .unknown: return .silence
        }
    }
}
