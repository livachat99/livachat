import CoreGraphics
import Foundation
import LivaChatProtocol

/// 线程安全的 AVDetector 开关状态盒：configProvider 与 SessionManager 共享。
internal final class AvdetectorState: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Bool

    init(_ value: Bool) { self.value = value }

    var enabled: Bool {
        get { lock.lock(); defer { lock.unlock() }; return value }
        set { lock.lock(); value = newValue; lock.unlock() }
    }
}

/// LivaChat iOS SDK facade.
///
/// Public methods are thread-safe. Most are non-blocking; `pushVideoImage`
/// performs JPEG encoding synchronously on the caller's thread.
public final class LivaChatSDK: @unchecked Sendable {
    /// All non-audio events. Single consumer; audio PCM is delivered through
    /// `audioSink` instead.
    public let events: AsyncStream<LivaChatEvent>

    /// Decoded PCM sink. Called on the audio receive serial queue.
    public weak var audioSink: LivaChatAudioSink?

    private let config: LivaChatConfig
    private let logger: SdkLogger
    private let stateStore: StateStore
    private let eventContinuation: AsyncStream<LivaChatEvent>.Continuation

    private let connectionManager: ConnectionManager
    private let sessionManager: SessionManager
    private let audioPipeline: AudioSendPipeline
    private let audioRecvPipeline: AudioRecvPipeline
    private let videoPipeline: VideoSendPipeline
    private let jpegEncoder: JPEGEncoding
    private let frameIDGenerator = SeqGenerator()
    private let sinkBox: AudioSinkBox

    public convenience init(
        config: LivaChatConfig,
        audioSink: LivaChatAudioSink?
    ) throws {
        try config.validate()
        try self.init(
            config: config,
            audioSink: audioSink,
            encoder: try OpusProcessorFactory.makeEncoder(
                sampleRate: config.audioSampleRate.rawValue
            ),
            decoder: try OpusProcessorFactory.makeDecoder(
                sampleRate: config.audioSampleRate.rawValue
            ),
            vad: try VadProcessorFactory.make(
                sampleRate: config.audioSampleRate.rawValue,
                sensitivity: config.vadSensitivity
            ),
            jpegEncoder: JpegEncoderImpl()
        )
    }

    /// Designated initializer with injectable processors (host-side tests).
    internal init(
        config: LivaChatConfig,
        audioSink: LivaChatAudioSink?,
        encoder: OpusEncoding,
        decoder: OpusDecoding,
        vad: VadDetecting,
        jpegEncoder: JPEGEncoding
    ) throws {
        try config.validate()

        let logger = SdkLogger(logFileURL: config.logFileURL)
        let stateStore = StateStore()

        var continuation: AsyncStream<LivaChatEvent>.Continuation!
        let events = AsyncStream(bufferingPolicy: .unbounded) {
            continuation = $0
        }
        let eventSink = SDKEventSink(
            stateStore: stateStore,
            continuation: continuation
        )
        let connectionCodec = MessageCodec()
        let sessionCodec = MessageCodec()

        let connectionManager = ConnectionManager(
            serverURL: config.serverURL,
            token: config.token,
            deviceID: config.deviceID,
            connectTimeout: config.connectTimeout,
            reconnectConfig: ReconnectConfig(
                maxAttempts: config.reconnectMaxAttempts,
                baseDelay: config.reconnectBaseDelay,
                maxDelay: config.reconnectMaxDelay
            ),
            keepaliveInterval: config.keepaliveInterval,
            keepaliveTimeout: config.keepaliveTimeout,
            tlsPolicy: config.tlsPolicy,
            logger: logger
        )

        let avdetectorState = AvdetectorState(config.avdetectorEnabled)
        let sinkBox = AudioSinkBox()
        let audioRecvPipeline = AudioRecvPipeline(
            sampleRate: config.audioSampleRate.rawValue,
            decoder: decoder,
            logger: logger
        ) { frame in
            guard let sdk = sinkBox.sdk, let sink = sdk.audioSink else { return }
            sink.livaChat(sdk, didReceiveAudio: frame)
        }

        let sessionManager = SessionManager(
            codec: sessionCodec,
            sendRaw: { [weak connectionManager] json in
                connectionManager?.send(json) ?? false
            },
            eventSink: eventSink,
            stateStore: stateStore,
            onAIAudio: { data, durationMs, sequence, epoch in
                SDKQueue.audioRecv.async {
                    audioRecvPipeline.handleAIAudio(
                        data,
                        durationMs: durationMs,
                        sequenceNumber: sequence,
                        playbackEpoch: epoch
                    )
                }
            },
            configProvider: {
                ProtocolMapping.startConfig(
                    enableVideo: config.enableVideo,
                    audioSampleRate: config.audioSampleRate.rawValue,
                    vadSensitivity: ProtocolMapping.protocolVadSensitivity(config.vadSensitivity),
                    clientVersion: config.clientVersion,
                    timezone: config.timezone ?? TimeZone.current.identifier,
                    language: config.languageProvider?() ?? config.language,
                    avdetector: ProtocolMapping.avdetectorSwitch(avdetectorState.enabled)
                )
            },
            avdetectorState: avdetectorState,
            logger: logger
        )

        let audioPipeline = AudioSendPipeline(
            sampleRate: config.audioSampleRate.rawValue,
            frameMs: config.audioFrameMs,
            vad: vad,
            encoder: encoder,
            onEncoded: { opus, durationMs, vadState, muted in
                let payload = ClientAudioPayload(
                    data: opus,
                    codec: "opus",
                    durationMs: durationMs,
                    vadState: ProtocolMapping.protocolVadState(vadState, muted: muted)
                )
                SDKQueue.session.async {
                    sessionManager.sendAudio(payload)
                }
            },
            onVad: { vadState in
                let previous = stateStore.snapshot().localVadState
                guard previous != vadState else { return }
                stateStore.update { state in
                    state = LivaChatState(
                        connectionState: state.connectionState,
                        connectionEpoch: state.connectionEpoch,
                        sessionState: state.sessionState,
                        localVadState: vadState,
                        playbackEpoch: state.playbackEpoch
                    )
                }
                continuation.yield(.localVadStateChanged(vadState))
            },
            uplinkAGC: config.uplinkAGC,
            isAISpeaking: {
                stateStore.snapshot().sessionState == .speaking
            },
            logger: logger
        )

        let videoPipeline = VideoSendPipeline(
            logger: logger,
            onEncoded: { jpeg, frameID, width, height, orientation, cameraFacing, turnID, text in
                guard let width16 = UInt16(exactly: width),
                      let height16 = UInt16(exactly: height) else {
                    logger.warn("SDK", "drop video frame with invalid dimensions | frameID=\(frameID)")
                    return
                }
                let payload = ClientVideoPayload(
                    data: jpeg,
                    frameId: frameID,
                    width: width16,
                    height: height16,
                    orientation: ProtocolMapping.protocolOrientation(orientation),
                    cameraFacing: ProtocolMapping.protocolCameraFacing(cameraFacing),
                    quality: UInt8(clamping: config.videoQuality),
                    turnId: turnID,
                    text: text
                )
                sessionManager.sendVideo(payload)
            }
        )

        connectionManager.onMessage = { text in
            guard let message = connectionCodec.decode(text) else {
                logger.warn("SDK", "drop malformed or unknown server message")
                return
            }
            SDKQueue.session.async {
                sessionManager.handleInbound(message)
            }
        }

        connectionManager.onPing = {
            SDKQueue.session.async {
                sessionManager.sendPing()
            }
        }

        connectionManager.onOpen = { epoch in
            SDKQueue.session.async {
                sessionManager.resetSessionGate(connectionEpoch: epoch)
            }
        }

        connectionManager.onStateChange = { newState, epoch in
            // Atomic: connection state + playbackEpoch increment happen under
            // one StateStore lock.
            stateStore.transitionConnectionState(
                newState,
                connectionEpoch: epoch
            )
            continuation.yield(.connectionStateChanged(newState))

            if newState != .connected {
                SDKQueue.session.async {
                    sessionManager.resetSessionGate(connectionEpoch: epoch)
                }
            }
        }

        connectionManager.onEpoch = { epoch in
            stateStore.update { state in
                state = LivaChatState(
                    connectionState: state.connectionState,
                    connectionEpoch: epoch,
                    sessionState: state.sessionState,
                    localVadState: state.localVadState,
                    playbackEpoch: state.playbackEpoch
                )
            }
            continuation.yield(.connectionEpochAdvanced(epoch))
        }

        connectionManager.onTerminated = { reason in
            continuation.yield(.connectionTerminated(reason))
        }

        self.config = config
        self.logger = logger
        self.stateStore = stateStore
        self.events = events
        self.eventContinuation = continuation
        self.connectionManager = connectionManager
        self.sessionManager = sessionManager
        self.audioPipeline = audioPipeline
        self.audioRecvPipeline = audioRecvPipeline
        self.videoPipeline = videoPipeline
        self.jpegEncoder = jpegEncoder
        self.sinkBox = sinkBox
        self.audioSink = audioSink

        sinkBox.sdk = self

        if config.serverURL.scheme?.lowercased() == "ws" {
            logger.warn("SDK", "ws:// scheme requires an ATS exception; prefer wss://")
        }
        if case .allowSelfSigned = config.tlsPolicy {
            logger.warn("SDK", "TLS certificate validation is disabled (allowSelfSigned)")
        }
    }

    deinit {
        connectionManager.disconnect()
        sinkBox.sdk = nil
        eventContinuation.finish()
    }

    // MARK: - Connection / session lifecycle

    public func connect() {
        connectionManager.connect()
    }

    public func disconnect() {
        connectionManager.disconnect()
    }

    public func startSession(resumeSessionID: String? = nil) {
        SDKQueue.session.async { [sessionManager] in
            sessionManager.startSession(resumeSessionID: resumeSessionID)
        }
    }

    public func endSession(reason: LivaChatSessionEndReason = .userHangup) {
        SDKQueue.session.async { [sessionManager] in
            sessionManager.endSession(reason: reason)
        }
    }

    // MARK: - Upstream audio

    public func pushAudioPCM(_ pcm: [Int16], muted: Bool = false) {
        let copy = pcm
        SDKQueue.audioSend.async { [audioPipeline] in
            audioPipeline.pushPCM(copy, muted: muted)
        }
    }

    // MARK: - Upstream video

    public func pushVideoJPEG(
        _ jpeg: Data,
        width: Int,
        height: Int,
        orientation: Orientation,
        cameraFacing: CameraFacing = .front,
        turnID: UInt32 = 0,
        text: String? = nil
    ) {
        let copy = jpeg
        let frameID = frameIDGenerator.next()
        SDKQueue.videoSend.async { [videoPipeline] in
            videoPipeline.pushJPEG(
                copy,
                width: width,
                height: height,
                frameID: frameID,
                orientation: orientation,
                cameraFacing: cameraFacing,
                turnID: turnID,
                text: text
            )
        }
    }

    public func pushVideoImage(
        _ image: CGImage,
        orientation: Orientation,
        quality: Int? = nil,
        cameraFacing: CameraFacing = .front,
        turnID: UInt32 = 0,
        text: String? = nil
    ) {
        let clampedQuality = min(max(quality ?? config.videoQuality, 1), 100)
        do {
            let jpeg = try jpegEncoder.encode(
                image,
                quality: clampedQuality,
                maxLongSide: config.videoMaxLongSide
            )
            pushVideoJPEG(
                jpeg,
                width: image.width,
                height: image.height,
                orientation: orientation,
                cameraFacing: cameraFacing,
                turnID: turnID,
                text: text
            )
        } catch {
            logger.warn("SDK", "video image encoding failed", error: error)
        }
    }

    // MARK: - Control

    public func sendInterrupt(reason: InterruptReason = .userVoice) {
        SDKQueue.session.async { [sessionManager] in
            sessionManager.sendInterrupt(reason: reason)
        }
    }

    /// 更新“允许 AI 主动搭话”会话级开关；已 ready 时立即发 config，否则影响下一次 start。
    public func setAvdetectorEnabled(_ enabled: Bool) {
        SDKQueue.session.async { [sessionManager] in
            sessionManager.setAvdetectorEnabled(enabled)
        }
    }

    public func sendSessionChange(
        mode: SessionMode,
        myLanguage: String? = nil,
        localLanguage: String? = nil,
        usecase: SessionUsecase? = nil
    ) {
        SDKQueue.session.async { [sessionManager] in
            sessionManager.sendSessionChange(
                mode: mode,
                myLanguage: myLanguage,
                localLanguage: localLanguage,
                usecase: usecase
            )
        }
    }

    // MARK: - State

    public var state: LivaChatState {
        stateStore.snapshot()
    }

    // MARK: - Logging

    public func logDebug(tag: String, _ message: String) {
        logger.debug(tag, message)
    }

    public func logInfo(tag: String, _ message: String) {
        logger.info(tag, message)
    }

    public func logWarn(tag: String, _ message: String, error: Error? = nil) {
        logger.warn(tag, message, error: error)
    }

    public func logError(tag: String, _ message: String, error: Error? = nil) {
        logger.error(tag, message, error: error)
    }
}

// MARK: - Internal plumbing

private final class SDKEventSink: EventSink, @unchecked Sendable {
    private let stateStore: StateStore
    private let continuation: AsyncStream<LivaChatEvent>.Continuation

    init(
        stateStore: StateStore,
        continuation: AsyncStream<LivaChatEvent>.Continuation
    ) {
        self.stateStore = stateStore
        self.continuation = continuation
    }

    func emit(_ event: LivaChatEvent) {
        continuation.yield(event)
    }

    func updateSessionState(_ state: SessionState) {
        stateStore.update { current in
            current = LivaChatState(
                connectionState: current.connectionState,
                connectionEpoch: current.connectionEpoch,
                sessionState: state,
                localVadState: current.localVadState,
                playbackEpoch: current.playbackEpoch
            )
        }
    }
}

private final class AudioSinkBox: @unchecked Sendable {
    weak var sdk: LivaChatSDK?
}
