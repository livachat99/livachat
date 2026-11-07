import Foundation
import LivaChatProtocol
import XCTest
@testable import LivaChatSDK

private final class RecordingEventSink: EventSink, @unchecked Sendable {
    private let lock = NSLock()
    private var events: [LivaChatEvent] = []
    private var state: SessionState = .idle

    func emit(_ event: LivaChatEvent) {
        lock.lock()
        events.append(event)
        lock.unlock()
    }

    func updateSessionState(_ state: SessionState) {
        lock.lock()
        self.state = state
        lock.unlock()
    }

    var snapshot: [LivaChatEvent] {
        lock.lock()
        defer { lock.unlock() }
        return events
    }

    var sessionState: SessionState {
        lock.lock()
        defer { lock.unlock() }
        return state
    }
}

private final class RecordingSender: @unchecked Sendable {
    private let lock = NSLock()
    private var messages: [String] = []
    var result = true

    func send(_ text: String) -> Bool {
        lock.lock()
        messages.append(text)
        let result = self.result
        lock.unlock()
        return result
    }

    var snapshot: [String] {
        lock.lock()
        defer { lock.unlock() }
        return messages
    }
}

final class SessionManagerTests: XCTestCase {
    private var sender: RecordingSender!
    private var sink: RecordingEventSink!
    private var stateStore: StateStore!
    private var audioFrames: [(Data, Int, UInt64, UInt64)] = []

    private let startConfig = ClientSessionStartConfig(
        enableVideo: true,
        audioSampleRate: .hz16000,
        vadSensitivity: .medium,
        clientVersion: "ios-test",
        timezone: nil,
        language: "zh-Hans"
    )

    override func setUp() {
        super.setUp()
        sender = RecordingSender()
        sink = RecordingEventSink()
        stateStore = StateStore()
        audioFrames = []
    }

    private func makeManager(
        now: @escaping () -> Int64 = { 9_000 },
        configProvider: (() -> ClientSessionStartConfig)? = nil
    ) -> SessionManager {
        let provider = configProvider ?? { [startConfig] in startConfig }
        return SessionManager(
            codec: MessageCodec(),
            sendRaw: { [weak sender] text in sender?.send(text) ?? false },
            eventSink: sink,
            stateStore: stateStore,
            onAIAudio: { [weak self] data, duration, sequence, epoch in
                self?.audioFrames.append((data, duration, sequence, epoch))
            },
            now: now,
            configProvider: provider,
            logger: SdkLogger(logFileURL: nil)
        )
    }

    private func json(_ text: String) -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any] ?? [:]
    }

    private func payload(_ text: String) -> [String: Any] {
        json(text)["payload"] as? [String: Any] ?? [:]
    }

    private func type(_ text: String) -> String {
        json(text)["type"] as? String ?? ""
    }

    func testStartSessionEncodesAndOpensGate() throws {
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)

        let sent = try XCTUnwrap(sender.snapshot.first)
        XCTAssertEqual(type(sent), "session.start")
        XCTAssertEqual(json(sent)["seq"] as? UInt32, 1)
        XCTAssertTrue(payload(sent)["session_id"] is NSNull)
        let config = payload(sent)["config"] as? [String: Any]
        XCTAssertEqual(config?["language"] as? String, "zh-Hans")
        XCTAssertTrue(manager.isSessionStarted)
    }

    func testStartSessionUsesLatestProviderLanguage() {
        var language = "zh-Hans"
        let manager = makeManager(configProvider: {
            ClientSessionStartConfig(
                enableVideo: true,
                audioSampleRate: .hz16000,
                vadSensitivity: .medium,
                clientVersion: "ios-test",
                timezone: nil,
                language: language
            )
        })

        manager.startSession(resumeSessionID: nil)
        language = "ja"
        manager.startSession(resumeSessionID: nil)

        let sent = sender.snapshot
        XCTAssertEqual(sent.count, 2)
        let firstConfig = payload(sent[0])["config"] as? [String: Any]
        let secondConfig = payload(sent[1])["config"] as? [String: Any]
        XCTAssertEqual(firstConfig?["language"] as? String, "zh-Hans")
        XCTAssertEqual(secondConfig?["language"] as? String, "ja")
    }

    func testProtocolMappingCarriesLanguage() {
        let config = ProtocolMapping.startConfig(
            enableVideo: true,
            audioSampleRate: 16000,
            vadSensitivity: .medium,
            clientVersion: "ios-test",
            timezone: "Asia/Shanghai",
            language: "zh-Hant"
        )

        XCTAssertEqual(config.language, "zh-Hant")
    }

    func testStartSessionFailureKeepsGateClosed() {
        sender.result = false
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)
        XCTAssertFalse(manager.isSessionStarted)

        manager.sendAudio(ClientAudioPayload(
            data: Data([1, 2, 3]),
            codec: "opus",
            durationMs: 20,
            vadState: .speech
        ))
        XCTAssertEqual(sender.snapshot.count, 1)
    }

    func testFailedStartSessionResendKeepsGateOpen() {
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)
        XCTAssertTrue(manager.isSessionStarted)

        sender.result = false
        manager.startSession(resumeSessionID: "sess_old")
        XCTAssertTrue(manager.isSessionStarted)

        manager.sendAudio(ClientAudioPayload(
            data: Data([1]),
            codec: "opus",
            durationMs: 20,
            vadState: .speech
        ))
        XCTAssertEqual(sender.snapshot.map { type($0) }, ["session.start", "session.start", "audio"])
    }

    func testGateBlocksControlAndAudioUntilStarted() {
        let manager = makeManager()
        manager.sendInterrupt(reason: .userVoice)
        manager.sendSessionChange(mode: .chat, myLanguage: nil, localLanguage: nil, usecase: nil)
        manager.sendAudio(ClientAudioPayload(
            data: Data([1]),
            codec: "opus",
            durationMs: 20,
            vadState: .speech
        ))
        XCTAssertTrue(sender.snapshot.isEmpty)

        manager.startSession(resumeSessionID: nil)
        manager.sendAudio(ClientAudioPayload(
            data: Data([1]),
            codec: "opus",
            durationMs: 20,
            vadState: .speech
        ))
        manager.sendInterrupt(reason: .userTap)
        manager.sendSessionChange(mode: .translate, myLanguage: "zh", localLanguage: "en", usecase: .lecture)
        manager.endSession(reason: .userHangup)

        let types = sender.snapshot.map { type($0) }
        XCTAssertEqual(types, ["session.start", "audio", "interrupt", "session.change", "session.end"])
        XCTAssertFalse(manager.isSessionStarted)

        XCTAssertEqual(payload(sender.snapshot[2])["reason"] as? String, "manual")
        let change = payload(sender.snapshot[3])
        XCTAssertEqual(change["mode"] as? String, "translate")
        XCTAssertEqual(change["usecase"] as? String, "lecture")
        XCTAssertEqual(payload(sender.snapshot[4])["reason"] as? String, "user_quit")
    }

    func testGateResetHonorsConnectionEpoch() {
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)
        XCTAssertTrue(manager.isSessionStarted)

        manager.resetSessionGate(connectionEpoch: 2)
        XCTAssertFalse(manager.isSessionStarted)

        manager.startSession(resumeSessionID: "sess_old")
        XCTAssertTrue(manager.isSessionStarted)

        manager.resetSessionGate(connectionEpoch: 1)
        XCTAssertTrue(manager.isSessionStarted)

        manager.resetSessionGate(connectionEpoch: 3)
        XCTAssertFalse(manager.isSessionStarted)
    }

    func testStatusThinkingUpdatesStateAndFlushes() {
        let manager = makeManager()
        manager.handleInbound(.status(
            seq: 1,
            timestamp: 0,
            payload: ServerStatusPayload(sessionStatus: .thinking, reason: .connected)
        ))

        XCTAssertEqual(sink.sessionState, .thinking)
        XCTAssertEqual(stateStore.snapshot().playbackEpoch, 2)
        XCTAssertTrue(sink.snapshot.contains { event in
            if case .sessionStateChanged(.thinking) = event { return true }
            return false
        })
        XCTAssertTrue(sink.snapshot.contains { event in
            if case .playbackShouldFlush(.interruptAck, epoch: 2) = event { return true }
            return false
        })
    }

    func testSessionReadyUpdatesClockAndJobStatus() throws {
        let manager = makeManager()
        manager.handleInbound(.sessionReady(
            seq: 1,
            timestamp: 0,
            payload: ServerSessionReadyPayload(
                sessionId: "sess_test",
                sessionRestored: false,
                serverTimeMs: 10_000,
                keepaliveIntervalS: 15
            )
        ))
        XCTAssertEqual(manager.sessionID, "sess_test")

        manager.handleInbound(.jobStatus(
            seq: 2,
            timestamp: 0,
            payload: ServerJobStatusPayload(jobs: [
                ServerJobStatusJobsItem(
                    jobId: "job_1",
                    agentName: "hermes",
                    summary: "test",
                    startedAtMs: 9_500,
                    runningTimeMs: 500
                )
            ])
        ))

        let jobEvent = try XCTUnwrap(sink.snapshot.compactMap { event -> [AgentJob]? in
            if case let .jobStatus(jobs) = event { return jobs }
            return nil
        }.last)
        XCTAssertEqual(jobEvent.count, 1)
        XCTAssertEqual(jobEvent[0].startedAtMs, 8_500)
        XCTAssertEqual(jobEvent[0].runningTimeMs, 500)
    }

    func testSessionEndedFlushesAndClosesGate() {
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)
        manager.handleInbound(.sessionEnded(
            seq: 2,
            timestamp: 0,
            payload: ServerSessionEndedPayload(reason: .userQuit, sessionId: "sess_test")
        ))

        XCTAssertFalse(manager.isSessionStarted)
        XCTAssertEqual(stateStore.snapshot().playbackEpoch, 2)
        XCTAssertTrue(sink.snapshot.contains { event in
            if case .sessionEnded(.userQuit, "sess_test") = event { return true }
            return false
        })
        XCTAssertTrue(sink.snapshot.contains { event in
            if case .playbackShouldFlush(.sessionEnded, epoch: 2) = event { return true }
            return false
        })
    }

    func testFatalErrorFlushesAndClosesGate() {
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)
        manager.handleInbound(.error(
            seq: 2,
            timestamp: 0,
            payload: ServerErrorPayload(
                code: 2001,
                message: "fatal",
                fatal: true,
                recoverable: false
            )
        ))

        XCTAssertFalse(manager.isSessionStarted)
        XCTAssertEqual(stateStore.snapshot().playbackEpoch, 2)
        XCTAssertTrue(sink.snapshot.contains { event in
            if case .playbackShouldFlush(.serverAbort, epoch: 2) = event { return true }
            return false
        })
    }

    func testServerPingIsPongedWithoutGate() throws {
        let manager = makeManager()
        manager.handleInbound(.ping(seq: 7, timestamp: 0, payload: ServerPingPayload()))

        let sent = try XCTUnwrap(sender.snapshot.first)
        XCTAssertEqual(type(sent), "pong")
        XCTAssertEqual(payload(sent)["ping_seq"] as? UInt32, 7)
    }

    func testAIAudioAssignsSequenceAndPlaybackEpoch() {
        let manager = makeManager()
        let payload = ServerAiAudioPayload(
            data: Data([1, 2]),
            codec: "opus",
            messageId: "msg",
            index: 0,
            durationMs: 20,
            isFinal: false
        )
        manager.handleInbound(.aiAudio(seq: 1, timestamp: 0, payload: payload))
        manager.handleInbound(.aiAudio(seq: 2, timestamp: 0, payload: payload))

        XCTAssertEqual(audioFrames.count, 2)
        XCTAssertEqual(audioFrames[0].2, 1)
        XCTAssertEqual(audioFrames[1].2, 2)
        XCTAssertEqual(audioFrames[0].3, 1)
    }

    func testAiTextAndSessionChangedMappings() throws {
        let manager = makeManager()
        manager.handleInbound(.aiText(
            seq: 1,
            timestamp: 0,
            payload: ServerAiTextPayload(
                text: "hello",
                messageId: "msg",
                index: 3,
                isFinal: true,
                source: .agent,
                image: ServerAiTextImage(
                    data: Data([9]),
                    mime: "image/jpeg",
                    width: 100,
                    height: 200,
                    orientation: .degrees90,
                    cameraFacing: .back
                )
            )
        ))
        manager.handleInbound(.sessionChanged(
            seq: 2,
            timestamp: 0,
            payload: ServerSessionChangedPayload(
                mode: .translate,
                myLanguage: "zh",
                localLanguage: "en",
                usecase: .lecture
            )
        ))

        let textEvent = try XCTUnwrap(sink.snapshot.compactMap { event -> (String, AITextSource?, AiImage?)? in
            if case let .aiText(text, _, _, _, image, source) = event {
                return (text, source, image)
            }
            return nil
        }.last)
        XCTAssertEqual(textEvent.0, "hello")
        XCTAssertEqual(textEvent.1, .agent)
        XCTAssertEqual(textEvent.2?.orientation, .landscape90)
        XCTAssertEqual(textEvent.2?.cameraFacing, .back)

        XCTAssertTrue(sink.snapshot.contains { event in
            if case .sessionChanged(.translate, "zh", "en", .lecture) = event { return true }
            return false
        })
    }

    func testVideoAckIsEmitted() {
        let manager = makeManager()
        manager.handleInbound(.aiVideoAck(
            seq: 1,
            timestamp: 0,
            payload: ServerAiVideoAckPayload(frameId: 5, ackFrameId: 5, processed: true)
        ))
        XCTAssertTrue(sink.snapshot.contains { event in
            if case .videoAck(5, true) = event { return true }
            return false
        })
    }
}

extension SessionManagerTests {
    private func readyMessage(manager: SessionManager) {
        manager.handleInbound(.sessionReady(
            seq: 1, timestamp: 0,
            payload: ServerSessionReadyPayload(
                sessionId: "sess_test", sessionRestored: false,
                serverTimeMs: 10_000, keepaliveIntervalS: 15
            )
        ))
    }

    private func errorMessage(manager: SessionManager, code: UInt16) {
        manager.handleInbound(.error(
            seq: 2, timestamp: 0,
            payload: ServerErrorPayload(
                code: code, message: "x",
                fatal: false, recoverable: true
            )
        ))
    }

    func testStartConfigCarriesAvdetectorDisable() {
        let manager = makeManager(configProvider: {
            ClientSessionStartConfig(
                enableVideo: true, audioSampleRate: .hz16000,
                vadSensitivity: .medium, clientVersion: "ios-test",
                timezone: nil, language: nil, avdetector: .disable
            )
        })
        manager.startSession(resumeSessionID: nil)
        let config = payload(sender.snapshot.first ?? "")["config"] as? [String: Any]
        XCTAssertEqual(config?["avdetector"] as? String, "disable")
    }

    func testToggleBeforeReadyIsFlushedAfterReady() {
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)
        manager.setAvdetectorEnabled(false)
        XCTAssertEqual(sender.snapshot.count, 1)

        readyMessage(manager: manager)
        XCTAssertEqual(sender.snapshot.count, 2)
        XCTAssertEqual(type(sender.snapshot[1]), "session.change")
        let p = payload(sender.snapshot[1])
        XCTAssertEqual(p["mode"] as? String, "config")
        XCTAssertEqual(p["avdetector"] as? String, "disable")
    }

    func testConfigEchoDoesNotEmitModeEvent() {
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)
        readyMessage(manager: manager)
        manager.setAvdetectorEnabled(false)
        manager.handleInbound(.sessionChanged(
            seq: 3, timestamp: 0,
            payload: ServerSessionChangedPayload(mode: .config, avdetector: .disable)
        ))
        XCTAssertFalse(sink.snapshot.contains { event in
            if case .sessionChanged = event { return true }
            return false
        })
    }

    func testConfigError1011IsSwallowed() {
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)
        readyMessage(manager: manager)
        manager.setAvdetectorEnabled(false)
        errorMessage(manager: manager, code: 1011)
        XCTAssertFalse(sink.snapshot.contains { event in
            if case .error = event { return true }
            return false
        })
    }

    func testToggleAfterEndSessionDoesNotSendConfig() {
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)
        readyMessage(manager: manager)
        manager.endSession(reason: .userHangup)
        let afterEnd = sender.snapshot.count
        XCTAssertEqual(type(sender.snapshot.last ?? ""), "session.end")

        manager.setAvdetectorEnabled(false)

        XCTAssertEqual(sender.snapshot.count, afterEnd)
    }

    func testConfigError1006MarksUnsupportedAndStopsRetry() {
        let manager = makeManager()
        manager.startSession(resumeSessionID: nil)
        readyMessage(manager: manager)
        manager.setAvdetectorEnabled(false)
        let before = sender.snapshot.count
        errorMessage(manager: manager, code: 1006)
        XCTAssertFalse(sink.snapshot.contains { event in
            if case .error = event { return true }
            return false
        })
        manager.setAvdetectorEnabled(true)
        XCTAssertEqual(sender.snapshot.count, before)
    }
}
