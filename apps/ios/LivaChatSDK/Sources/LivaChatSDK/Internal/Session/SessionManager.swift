import Foundation
import LivaChatProtocol

/// Protocol/session state machine.
///
/// With the exception of `sendVideo` (which allocates its sequence number and
/// encodes JSON on the video send queue), all methods must run on
/// `SDKQueue.session`.
internal final class SessionManager: @unchecked Sendable {
    private let codec: MessageCodec
    private let videoCodec = MessageCodec()
    private let sendRaw: (String) -> Bool
    private let eventSink: EventSink
    private let stateStore: StateStore
    private let onAIAudio: (Data, Int, UInt64, UInt64) -> Void
    private let now: () -> Int64
    private let configProvider: () -> ClientSessionStartConfig
    private let avdetectorState: AvdetectorState
    private let logger: SdkLogger

    private let seqGenerator = SeqGenerator()
    private let clockOffset = ClockOffset()

    private var sessionStateValue: SessionState = .idle
    private var sessionIDValue: String?
    private var currentConnectionEpochValue: UInt64 = 0
    private var sessionStartedOnConnection = false
    // AVDetector session config state (all accessed on SDKQueue.session)
    private var sessionReady = false
    private var pendingAvdetectorChange: Bool?
    private var configInFlight = false
    private var queuedConfigValue: Bool?
    private var sessionConfigUnsupported = false

    private var audioInSequence: UInt64 = 1
    private var lastSentVideoFrameID: UInt32 = 0
    private var lastAckedVideoFrameID: UInt32 = 0
    private var audioGateDropLogged = false

    init(
        codec: MessageCodec,
        sendRaw: @escaping (String) -> Bool,
        eventSink: EventSink,
        stateStore: StateStore,
        onAIAudio: @escaping (Data, Int, UInt64, UInt64) -> Void,
        now: @escaping () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        configProvider: @escaping () -> ClientSessionStartConfig,
        avdetectorState: AvdetectorState = AvdetectorState(true),
        logger: SdkLogger
    ) {
        self.codec = codec
        self.sendRaw = sendRaw
        self.eventSink = eventSink
        self.stateStore = stateStore
        self.onAIAudio = onAIAudio
        self.now = now
        self.configProvider = configProvider
        self.avdetectorState = avdetectorState
        self.logger = logger
    }

    var sessionState: SessionState { sessionStateValue }
    var sessionID: String? { sessionIDValue }
    var currentConnectionEpoch: UInt64 { currentConnectionEpochValue }
    var isSessionStarted: Bool { sessionStartedOnConnection }

    // MARK: - Outbound

    func startSession(resumeSessionID: String?) {
        resetAvdetectorSessionState()
        let payload = ClientSessionStartPayload(
            sessionId: resumeSessionID,
            config: configProvider()
        )
        let sent = encodeAndSend { seq, timestamp in
            ClientMessage.sessionStart(seq: seq, timestamp: timestamp, payload: payload)
        }
        if sent {
            sessionStartedOnConnection = true
            audioGateDropLogged = false
        } else {
            // A failed resend must not close an already-open gate: the server
            // may still have the previous session alive.
            logger.warn("Session", "session.start send failed; gate unchanged")
        }
    }

    /// 更新 AVDetector 开关：单飞合并；ready 后立即发 config，start 已发/ready 未到则 pending。
    func setAvdetectorEnabled(_ enabled: Bool) {
        avdetectorState.enabled = enabled
        if sessionConfigUnsupported { return }
        if configInFlight {
            queuedConfigValue = enabled
            return
        }
        if sessionReady {
            configInFlight = true
            queuedConfigValue = nil
            sendAvdetectorConfig(enabled)
            return
        }
        if sessionStartedOnConnection {
            pendingAvdetectorChange = enabled
        }
    }

    private func sendAvdetectorConfig(_ enabled: Bool) {
        let payload = ClientSessionChangePayload(
            mode: .config,
            avdetector: ProtocolMapping.avdetectorSwitch(enabled)
        )
        _ = encodeAndSend { seq, timestamp in
            ClientMessage.sessionChange(seq: seq, timestamp: timestamp, payload: payload)
        }
        logger.debug("Session", "send session.change(config) avdetector=\(enabled)")
    }

    private func resetAvdetectorSessionState() {
        sessionReady = false
        pendingAvdetectorChange = nil
        configInFlight = false
        queuedConfigValue = nil
    }

    private func resetAvdetectorConfigState() {
        resetAvdetectorSessionState()
        sessionConfigUnsupported = false
    }

    func endSession(reason: LivaChatSessionEndReason) {
        guard sessionStartedOnConnection else {
            logger.warn("Session", "session.end ignored: no active session")
            return
        }
        let payload = ClientSessionEndPayload(reason: ProtocolMapping.endReason(reason))
        _ = encodeAndSend { seq, timestamp in
            ClientMessage.sessionEnd(seq: seq, timestamp: timestamp, payload: payload)
        }
        sessionStartedOnConnection = false
        // session.end 后服务端不再处理本会话业务消息；关闭 config 门，避免挂断与
        // 连接关闭之间的设置切换发出无归属的 session.change(config)。
        resetAvdetectorSessionState()
    }

    func sendInterrupt(reason: InterruptReason) {
        guard sessionStartedOnConnection else {
            logger.warn("Session", "interrupt dropped: no active session")
            return
        }
        let payload = ClientInterruptPayload(reason: ProtocolMapping.interruptReason(reason))
        _ = encodeAndSend { seq, timestamp in
            ClientMessage.interrupt(seq: seq, timestamp: timestamp, payload: payload)
        }
    }

    func sendSessionChange(
        mode: SessionMode,
        myLanguage: String?,
        localLanguage: String?,
        usecase: SessionUsecase?
    ) {
        guard sessionStartedOnConnection else {
            logger.warn("Session", "session.change dropped: no active session")
            return
        }
        let payload = ClientSessionChangePayload(
            mode: ProtocolMapping.mode(mode),
            myLanguage: myLanguage,
            localLanguage: localLanguage,
            usecase: ProtocolMapping.usecase(usecase)
        )
        _ = encodeAndSend { seq, timestamp in
            ClientMessage.sessionChange(seq: seq, timestamp: timestamp, payload: payload)
        }
    }

    func sendAudio(_ payload: ClientAudioPayload) {
        guard sessionStartedOnConnection else {
            // 50Hz path: log once per closed-gate segment, not per frame.
            if !audioGateDropLogged {
                audioGateDropLogged = true
                logger.warn("Session", "audio dropped: no active session")
            }
            return
        }
        _ = encodeAndSend { seq, timestamp in
            ClientMessage.audio(seq: seq, timestamp: timestamp, payload: payload)
        }
    }

    /// Called on `videoSendQueue`: sequence allocation and JSON encoding happen
    /// there; only the final send hops to `sessionQueue`.
    func sendVideo(_ payload: ClientVideoPayload) {
        let seq = seqGenerator.next()
        let message = ClientMessage.video(
            seq: seq,
            timestamp: timestamp(),
            payload: payload
        )
        let json: String
        do {
            json = try videoCodec.encode(message)
        } catch {
            logger.error("Session", "video encode failed: \(payload.frameId)", error: error)
            return
        }

        SDKQueue.session.async { [weak self] in
            guard let self, self.sessionStartedOnConnection else { return }
            if payload.frameId > self.lastSentVideoFrameID {
                self.lastSentVideoFrameID = payload.frameId
            }
            _ = self.sendRaw(json)
        }
    }

    func sendPing() {
        _ = encodeAndSend { seq, timestamp in
            ClientMessage.ping(seq: seq, timestamp: timestamp, payload: ClientPingPayload())
        }
    }

    func resetSessionGate(connectionEpoch: UInt64) {
        guard connectionEpoch >= currentConnectionEpochValue else { return }
        currentConnectionEpochValue = connectionEpoch
        sessionStartedOnConnection = false
        resetAvdetectorConfigState()
    }

    // MARK: - Inbound

    func handleInbound(_ message: ServerMessage) {
        switch message {
        case let .sessionReady(_, _, payload):
            sessionIDValue = payload.sessionId
            clockOffset.update(serverTimeMs: payload.serverTimeMs, localTimeMs: now())
            sessionReady = true
            let pending = pendingAvdetectorChange
            pendingAvdetectorChange = nil
            if let pending, !sessionConfigUnsupported {
                configInFlight = true
                sendAvdetectorConfig(pending)
            }
            eventSink.emit(.sessionReady(
                sessionID: payload.sessionId,
                restored: payload.sessionRestored
            ))

        case let .sessionEnded(_, _, payload):
            let reason = ProtocolMapping.endedReason(payload.reason)
            // 先关闭会话门与 config 状态，再通知 App：事件处理方若立即切换设置，
            // 不得再向已结束的会话发送 config。
            sessionStartedOnConnection = false
            resetAvdetectorConfigState()
            eventSink.emit(.sessionEnded(
                reason: reason,
                sessionID: payload.sessionId
            ))
            flush(.sessionEnded)

        case let .sessionChanged(_, _, payload):
            if payload.mode == .config {
                let confirmed = payload.avdetector != .disable
                configInFlight = false
                let queued = queuedConfigValue
                queuedConfigValue = nil
                if let queued, queued != confirmed {
                    configInFlight = true
                    sendAvdetectorConfig(queued)
                }
                logger.debug("Session", "recv session.changed(config) avdetector=\(confirmed)")
                // 控制回声：不进入 App 的模式状态机
                break
            }
            guard let mode = ProtocolMapping.sessionMode(payload.mode) else { break }
            eventSink.emit(.sessionChanged(
                mode: mode,
                myLanguage: payload.myLanguage,
                localLanguage: payload.localLanguage,
                usecase: ProtocolMapping.sessionUsecase(payload.usecase)
            ))

        case let .transcription(_, _, payload):
            eventSink.emit(.transcription(
                text: payload.text,
                isFinal: payload.isFinal,
                transcriptionID: payload.transcriptionId
            ))

        case let .aiText(_, _, payload):
            let image = payload.image.map { image in
                AiImage(
                    data: image.data,
                    mime: image.mime,
                    width: image.width,
                    height: image.height,
                    orientation: ProtocolMapping.orientation(image.orientation),
                    cameraFacing: ProtocolMapping.cameraFacing(image.cameraFacing)
                )
            }
            eventSink.emit(.aiText(
                text: payload.text,
                messageID: payload.messageId,
                index: payload.index,
                isFinal: payload.isFinal,
                image: image,
                source: ProtocolMapping.aiTextSource(payload.source)
            ))

        case let .aiAudio(_, _, payload):
            let sequence = audioInSequence
            audioInSequence &+= 1
            onAIAudio(
                payload.data,
                Int(payload.durationMs),
                sequence,
                stateStore.snapshot().playbackEpoch
            )

        case let .aiVideoAck(_, _, payload):
            let frameID = payload.frameId
            if lastAckedVideoFrameID > 0,
               frameID > lastAckedVideoFrameID,
               frameID - lastAckedVideoFrameID > 1 {
                let from = lastAckedVideoFrameID + 1
                let to = min(frameID - 1, lastSentVideoFrameID)
                if to >= from {
                    logger.warn("Session", "video frames lost | range=\(from)..\(to)")
                }
            }
            if frameID > lastAckedVideoFrameID {
                lastAckedVideoFrameID = frameID
            }
            eventSink.emit(.videoAck(
                frameID: frameID,
                processed: payload.processed
            ))

        case let .jobStatus(_, _, payload):
            let jobs = payload.jobs.map { item in
                AgentJob(
                    jobID: item.jobId,
                    agentName: item.agentName,
                    summary: item.summary,
                    startedAtMs: clockOffset.toLocalTime(serverTimeMs: item.startedAtMs),
                    runningTimeMs: Int64(clamping: item.runningTimeMs)
                )
            }
            eventSink.emit(.jobStatus(jobs))

        case let .status(_, _, payload):
            let newState = ProtocolMapping.sessionState(payload.sessionStatus)
            sessionStateValue = newState
            eventSink.updateSessionState(newState)
            eventSink.emit(.sessionStateChanged(newState))
            if newState == .thinking {
                flush(.interruptAck)
            }

        case let .error(_, _, payload):
            // config 错误由 SDK 消费：不得进入 App 的 1009 模式回滚/通用错误横幅。
            if !payload.fatal && payload.code == 1011 {
                configInFlight = false
                queuedConfigValue = nil
                logger.warn("Session", "session.config rejected (1011): \(payload.message)")
                break
            }
            if !payload.fatal && payload.code == 1006 && configInFlight {
                configInFlight = false
                queuedConfigValue = nil
                sessionConfigUnsupported = true
                logger.warn("Session", "session.config unsupported by server (1006); disabling live config")
                break
            }
            eventSink.emit(.error(
                code: payload.code,
                message: payload.message,
                fatal: payload.fatal,
                recoverable: payload.recoverable
            ))
            if payload.fatal {
                sessionStartedOnConnection = false
                flush(.serverAbort)
            }

        case let .ping(seq, _, _):
            let payload = ClientPongPayload(pingSeq: seq)
            _ = encodeAndSend { pongSeq, timestamp in
                ClientMessage.pong(seq: pongSeq, timestamp: timestamp, payload: payload)
            }

        case .pong:
            break
        }
    }

    // MARK: - Helpers

    private func timestamp() -> UInt64 {
        UInt64(clamping: now())
    }

    @discardableResult
    private func encodeAndSend(
        _ build: (UInt32, UInt64) -> ClientMessage
    ) -> Bool {
        let seq = seqGenerator.next()
        let message = build(seq, timestamp())
        do {
            let json = try codec.encode(message)
            return sendRaw(json)
        } catch {
            logger.error("Session", "encode failed", error: error)
            return false
        }
    }

    private func flush(_ reason: PlaybackFlushReason) {
        let epoch = stateStore.incrementPlaybackEpoch()
        eventSink.emit(.playbackShouldFlush(reason: reason, epoch: epoch))
    }
}
