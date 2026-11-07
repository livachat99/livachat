import CoreGraphics
import Foundation
import XCTest
@testable import LivaChatSDK

#if os(macOS)
private final class FacadeFakeVad: VadDetecting {
    func detect(_ pcm: UnsafeBufferPointer<Int16>) -> VadState { .speech }
}

private final class FacadeFakeEncoder: OpusEncoding {
    func encode(_ pcm: UnsafeBufferPointer<Int16>) throws -> Data {
        Data([1, 2, 3])
    }
}

private final class FacadeFakeDecoder: OpusDecoding {
    func decode(_ opus: Data) throws -> [Int16] { [1, 2, 3] }
}

private final class FacadeFakeJpeg: JPEGEncoding {
    func encode(_ image: CGImage, quality: Int, maxLongSide: Int?) throws -> Data {
        Data([0xFF, 0xD8])
    }
}

private final class RecordingJpegEncoder: JPEGEncoding {
    private(set) var calls: [(quality: Int, maxLongSide: Int?)] = []

    func encode(_ image: CGImage, quality: Int, maxLongSide: Int?) throws -> Data {
        calls.append((quality, maxLongSide))
        return Data([0xFF, 0xD8])
    }
}

private func makeFacadeImage(width: Int, height: Int) -> CGImage {
    let context = CGContext(
        data: nil,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    )!
    context.fill(CGRect(x: 0, y: 0, width: width, height: height))
    return context.makeImage()!
}

private final class FacadeEventCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var handler: ((LivaChatEvent) -> Void)?

    func setHandler(_ handler: @escaping (LivaChatEvent) -> Void) {
        lock.lock()
        self.handler = handler
        lock.unlock()
    }

    func handle(_ event: LivaChatEvent) {
        lock.lock()
        let handler = self.handler
        lock.unlock()
        handler?(event)
    }
}

private final class FacadeTestAudioSink: LivaChatAudioSink, @unchecked Sendable {
    private let lock = NSLock()
    private let onFrame: () -> Void

    init(onFrame: @escaping () -> Void) {
        self.onFrame = onFrame
    }

    func livaChat(_ sdk: LivaChatSDK, didReceiveAudio frame: AudioPCMFrame) {
        lock.lock()
        let handler = onFrame
        lock.unlock()
        handler()
    }
}

final class FacadeE2ETests: XCTestCase {
    private var server: TestWebSocketServer!

    override func tearDown() {
        server?.stop()
        server = nil
        super.tearDown()
    }

    func testFacadeConnectsStartsSessionAndDeliversEvents() throws {
        let server = try TestWebSocketServer()
        self.server = server
        try server.start()

        let ready = expectation(description: "ready")
        let listening = expectation(description: "listening")
        let transcription = expectation(description: "transcription")
        let flush = expectation(description: "flush")
        let audioText = expectation(description: "ai text")
        let audioFrame = expectation(description: "audio frame")
        let noTermination = expectation(description: "no termination")
        noTermination.isInverted = true

        let sink = FacadeTestAudioSink { audioFrame.fulfill() }
        let config = LivaChatConfig(
            serverURL: URL(string: "ws://127.0.0.1:\(server.port)/v1/ws/stream")!,
            token: "token",
            deviceID: "uuid",
            clientVersion: "ios-test"
        )
        let sdk = try LivaChatSDK(
            config: config,
            audioSink: sink,
            encoder: FacadeFakeEncoder(),
            decoder: FacadeFakeDecoder(),
            vad: FacadeFakeVad(),
            jpegEncoder: FacadeFakeJpeg()
        )

        let collector = FacadeEventCollector()
        collector.setHandler { event in
            switch event {
            case .connectionEpochAdvanced:
                // Mirrors the App: start a session after every connection.
                sdk.startSession()
            case .sessionReady:
                ready.fulfill()
            case .sessionStateChanged(.listening):
                listening.fulfill()
            case let .transcription(text, isFinal, _):
                if text == "hello", isFinal { transcription.fulfill() }
            case .playbackShouldFlush(.interruptAck, _):
                flush.fulfill()
            case .aiText:
                audioText.fulfill()
            case .connectionTerminated:
                noTermination.fulfill()
            default:
                break
            }
        }
        let eventTask = Task {
            for await event in sdk.events {
                collector.handle(event)
            }
        }

        server.onText = { [weak server] text in
            guard let server else { return }
            let object = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any]
            switch object?["type"] as? String {
            case "session.start":
                server.sendText(#"{"type":"session.ready","seq":1,"timestamp":0,"payload":{"session_id":"sess_test","session_restored":false,"server_time_ms":1000,"keepalive_interval_s":15}}"#)
                server.sendText(#"{"type":"status","seq":2,"timestamp":0,"payload":{"session_status":"idle","reason":"connected"}}"#)
                server.sendText(#"{"type":"status","seq":3,"timestamp":0,"payload":{"session_status":"listening","reason":"connected"}}"#)
            case "audio":
                server.sendText(#"{"type":"transcription","seq":4,"timestamp":0,"payload":{"text":"hello","is_final":true,"transcription_id":"t1"}}"#)
                server.sendText(#"{"type":"status","seq":5,"timestamp":0,"payload":{"session_status":"thinking","reason":"connected"}}"#)
                server.sendText(#"{"type":"ai_text","seq":6,"timestamp":0,"payload":{"text":"hi","message_id":"m1","index":0,"is_final":true}}"#)
                server.sendText(#"{"type":"ai_audio","seq":7,"timestamp":0,"payload":{"data":"AQID","codec":"opus","message_id":"m1","index":0,"duration_ms":20,"is_final":true}}"#)
            default:
                break
            }
        }

        sdk.connect()
        wait(for: [ready, listening], timeout: 5)
        XCTAssertEqual(sdk.state.connectionState, .connected)
        XCTAssertEqual(sdk.state.sessionState, .listening)

        sdk.pushAudioPCM([Int16](repeating: 0, count: 320))
        wait(for: [transcription, flush, audioText, audioFrame], timeout: 5)
        XCTAssertEqual(sdk.state.playbackEpoch, 2)

        sdk.disconnect()
        wait(for: [noTermination], timeout: 0.3)
        eventTask.cancel()
    }

    func testPushVideoImageClampsQualityAndUsesConfiguredLongSide() throws {
        let encoder = RecordingJpegEncoder()
        let config = LivaChatConfig(
            serverURL: URL(string: "ws://127.0.0.1:1/v1/ws/stream")!,
            token: "token",
            deviceID: "uuid",
            clientVersion: "ios-test"
        )
        let sdk = try LivaChatSDK(
            config: config,
            audioSink: nil,
            encoder: FacadeFakeEncoder(),
            decoder: FacadeFakeDecoder(),
            vad: FacadeFakeVad(),
            jpegEncoder: encoder
        )

        sdk.pushVideoImage(
            makeFacadeImage(width: 8, height: 8),
            orientation: .portrait0,
            quality: 500
        )

        XCTAssertEqual(encoder.calls.count, 1)
        XCTAssertEqual(encoder.calls[0].quality, 100)
        XCTAssertEqual(encoder.calls[0].maxLongSide, config.videoMaxLongSide)
    }
}
#endif
