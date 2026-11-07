import Foundation
import XCTest
@testable import LivaChatSDK

private final class FakeVad: VadDetecting {
    var state: VadState = .speech
    var calls = 0

    func detect(_ pcm: UnsafeBufferPointer<Int16>) -> VadState {
        calls += 1
        return state
    }
}

private final class FakeOpusEncoder: OpusEncoding {
    var frames: [[Int16]] = []

    func encode(_ pcm: UnsafeBufferPointer<Int16>) throws -> Data {
        frames.append(Array(pcm))
        return Data([0xAA])
    }
}

private final class FakeOpusDecoder: OpusDecoding {
    var result: [Int16] = [1, 2, 3]
    var shouldThrow = false

    func decode(_ opus: Data) throws -> [Int16] {
        if shouldThrow {
            throw LivaChatError.audioCodecUnavailable
        }
        return result
    }
}

private final class BoolBox {
    var value = false
}

final class AudioPipelineTests: XCTestCase {
    func testSendPipelineAssemblesVadAndEncodes() {
        let vad = FakeVad()
        let encoder = FakeOpusEncoder()
        var encoded: [(Data, Int, VadState, Bool)] = []
        var vadStates: [VadState] = []

        let pipeline = AudioSendPipeline(
            sampleRate: 16_000,
            frameMs: 20,
            vad: vad,
            encoder: encoder,
            onEncoded: { encoded.append(($0, $1, $2, $3)) },
            onVad: { vadStates.append($0) },
            uplinkAGC: false,
            isAISpeaking: { false }
        )

        pipeline.pushPCM([Int16](repeating: 10, count: 100), muted: false)
        XCTAssertTrue(encoded.isEmpty)

        pipeline.pushPCM([Int16](repeating: 20, count: 220), muted: false)
        XCTAssertEqual(encoded.count, 1)
        XCTAssertEqual(encoded[0].0, Data([0xAA]))
        XCTAssertEqual(encoded[0].1, 20)
        XCTAssertEqual(encoded[0].2, .speech)
        XCTAssertFalse(encoded[0].3)
        XCTAssertEqual(vad.calls, 1)
        XCTAssertEqual(vadStates, [.speech])
        XCTAssertEqual(encoder.frames[0].count, 320)
        XCTAssertEqual(encoder.frames[0].first, 10)
        XCTAssertEqual(encoder.frames[0].last, 20)
    }

    func testMutedFlagIsPropagated() {
        let encoder = FakeOpusEncoder()
        var mutedValues: [Bool] = []
        let pipeline = AudioSendPipeline(
            sampleRate: 16_000,
            frameMs: 20,
            vad: FakeVad(),
            encoder: encoder,
            onEncoded: { _, _, _, muted in mutedValues.append(muted) },
            onVad: { _ in },
            uplinkAGC: false,
            isAISpeaking: { false }
        )

        pipeline.pushPCM([Int16](repeating: 0, count: 320), muted: true)
        pipeline.pushPCM([Int16](repeating: 1, count: 320), muted: false)
        XCTAssertEqual(mutedValues, [true, false])
    }

    func testAGCBoostsQuietSpeech() {
        let encoder = FakeOpusEncoder()
        let pipeline = AudioSendPipeline(
            sampleRate: 16_000,
            frameMs: 20,
            vad: FakeVad(),
            encoder: encoder,
            onEncoded: { _, _, _, _ in },
            onVad: { _ in },
            uplinkAGC: true,
            isAISpeaking: { false }
        )

        let input = (0..<320).map { index in
            Int16(300.0 * sin(2 * Double.pi * 220 * Double(index) / 16_000.0))
        }
        pipeline.pushPCM(input, muted: false)
        let encodedPeak = encoder.frames[0].map { abs(Int($0)) }.max() ?? 0
        XCTAssertGreaterThan(encodedPeak, 300)
    }

    func testAGCIsUnityWhileAISpeaking() {
        let encoder = FakeOpusEncoder()
        let speaking = BoolBox()
        speaking.value = true
        let pipeline = AudioSendPipeline(
            sampleRate: 16_000,
            frameMs: 20,
            vad: FakeVad(),
            encoder: encoder,
            onEncoded: { _, _, _, _ in },
            onVad: { _ in },
            uplinkAGC: true,
            isAISpeaking: { speaking.value }
        )

        let input = (0..<320).map { index in
            Int16(300.0 * sin(2 * Double.pi * 220 * Double(index) / 16_000.0))
        }
        pipeline.pushPCM(input, muted: false)
        XCTAssertEqual(encoder.frames[0], input)
    }

    func testRecvPipelineEmitsFrame() {
        let decoder = FakeOpusDecoder()
        var frames: [AudioPCMFrame] = []
        let pipeline = AudioRecvPipeline(
            sampleRate: 16_000,
            decoder: decoder,
            logger: SdkLogger(logFileURL: nil),
            onPCM: { frames.append($0) }
        )

        pipeline.handleAIAudio(
            Data([1]),
            durationMs: 20,
            sequenceNumber: 7,
            playbackEpoch: 3
        )
        XCTAssertEqual(frames.count, 1)
        XCTAssertEqual(frames[0].samples, [1, 2, 3])
        XCTAssertEqual(frames[0].sampleRate, 16_000)
        XCTAssertEqual(frames[0].sampleBits, 16)
        XCTAssertEqual(frames[0].durationMs, 20)
        XCTAssertEqual(frames[0].sequenceNumber, 7)
        XCTAssertEqual(frames[0].playbackEpoch, 3)
        XCTAssertEqual(frames[0].turnID, "")
    }

    func testRecvPipelineDropsDecodeFailure() {
        let decoder = FakeOpusDecoder()
        decoder.shouldThrow = true
        var frames: [AudioPCMFrame] = []
        let pipeline = AudioRecvPipeline(
            sampleRate: 16_000,
            decoder: decoder,
            logger: SdkLogger(logFileURL: nil),
            onPCM: { frames.append($0) }
        )

        pipeline.handleAIAudio(
            Data([1]),
            durationMs: 20,
            sequenceNumber: 1,
            playbackEpoch: 1
        )
        XCTAssertTrue(frames.isEmpty)
    }
}
