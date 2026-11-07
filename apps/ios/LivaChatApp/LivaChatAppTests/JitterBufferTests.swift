import LivaChatSDK
import XCTest
@testable import LivaChatApp

private func makeFrame(
    sequence: UInt64,
    epoch: UInt64 = 1,
    sampleRate: Int = 16_000,
    durationMs: Int = 20,
    samples: Int = 320,
    value: Int16 = 0
) -> AudioPCMFrame {
    AudioPCMFrame(
        samples: Array(repeating: value, count: samples),
        sampleRate: sampleRate,
        sampleBits: 16,
        durationMs: durationMs,
        sequenceNumber: sequence,
        playbackEpoch: epoch,
        turnID: ""
    )
}

final class JitterBufferTests: XCTestCase {
    private func frame(
        sequence: UInt64,
        epoch: UInt64 = 1,
        sampleRate: Int = 16_000,
        durationMs: Int = 20,
        samples: Int = 320,
        value: Int16 = 0
    ) -> AudioPCMFrame {
        makeFrame(
            sequence: sequence,
            epoch: epoch,
            sampleRate: sampleRate,
            durationMs: durationMs,
            samples: samples,
            value: value
        )
    }

    func testPullsInSequenceOrder() {
        let buffer = JitterBuffer()
        buffer.push(frame(sequence: 3))
        buffer.push(frame(sequence: 1))
        buffer.push(frame(sequence: 2))

        XCTAssertEqual(buffer.pull()?.sequenceNumber, 1)
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 2)
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 3)
        XCTAssertNil(buffer.pull())
    }

    func testLateFrameIsDroppedAfterPlaybackStarted() {
        let buffer = JitterBuffer()
        buffer.push(frame(sequence: 2))
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 2)
        buffer.push(frame(sequence: 1))
        XCTAssertEqual(buffer.currentStats().droppedLate, 1)
        XCTAssertNil(buffer.pull())
    }

    func testGapSkipsToNextAvailableFrame() {
        let buffer = JitterBuffer()
        buffer.push(frame(sequence: 1))
        buffer.push(frame(sequence: 3))
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 1)
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 3)
    }

    func testNewEpochDropsQueuedOldFrames() {
        let buffer = JitterBuffer(initialEpoch: 1)
        buffer.push(frame(sequence: 1, epoch: 1))
        buffer.push(frame(sequence: 2, epoch: 1))
        buffer.push(frame(sequence: 3, epoch: 2))

        XCTAssertEqual(buffer.currentAcceptedEpoch(), 2)
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 3)
        XCTAssertNil(buffer.pull())
    }

    func testFlushKeepsNewEpochAndDropsOld() {
        let buffer = JitterBuffer(initialEpoch: 1)
        buffer.push(frame(sequence: 1, epoch: 1))
        buffer.push(frame(sequence: 2, epoch: 1))

        XCTAssertEqual(buffer.flush(epoch: 2), 2)
        XCTAssertEqual(buffer.currentAcceptedEpoch(), 2)

        buffer.push(frame(sequence: 3, epoch: 2))
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 3)
    }

    func testNewEpochPushDropsOldBeforeFlushArrives() {
        let buffer = JitterBuffer(initialEpoch: 1)
        buffer.push(frame(sequence: 1, epoch: 1))
        buffer.push(frame(sequence: 2, epoch: 1))
        buffer.push(frame(sequence: 3, epoch: 2))

        XCTAssertEqual(buffer.currentAcceptedEpoch(), 2)
        XCTAssertEqual(buffer.flush(epoch: 2), 0, "old frames already dropped on epoch increase")
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 3)
    }

    func testFlushWithOlderEpochDoesNotRegress() {
        let buffer = JitterBuffer(initialEpoch: 3)
        buffer.push(frame(sequence: 1, epoch: 3))
        XCTAssertEqual(buffer.flush(epoch: 2), 0)
        XCTAssertEqual(buffer.currentAcceptedEpoch(), 3)
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 1)
    }

    func testOutOfOrderEpochArrivalKeepsNewFrame() {
        let buffer = JitterBuffer(initialEpoch: 1)
        buffer.push(frame(sequence: 1, epoch: 2))
        buffer.push(frame(sequence: 2, epoch: 2))
        buffer.push(frame(sequence: 3, epoch: 1))

        XCTAssertEqual(buffer.currentAcceptedEpoch(), 2)
        XCTAssertEqual(buffer.currentStats().droppedEpoch, 1)
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 1)
        XCTAssertEqual(buffer.pull()?.sequenceNumber, 2)
    }

    func testWrongSampleRateIsDropped() {
        let buffer = JitterBuffer()
        buffer.push(frame(sequence: 1, sampleRate: 48_000))
        XCTAssertEqual(buffer.currentStats().droppedEpoch, 1)
        XCTAssertNil(buffer.pull())
    }

    func testOverflowIsCounted() {
        let buffer = JitterBuffer(maxLatencyMs: 40)
        buffer.push(frame(sequence: 1))
        buffer.push(frame(sequence: 2))
        buffer.push(frame(sequence: 3))
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 1)
        XCTAssertEqual(buffer.size(), 2)
        XCTAssertEqual(buffer.currentBufferedDurationMs(), 40)
    }

    func testLatencyBoundUsesFrameDuration() {
        let buffer = JitterBuffer(maxLatencyMs: 1_000)
        for sequence in 1...60 {
            buffer.push(frame(sequence: UInt64(sequence)))
        }

        XCTAssertEqual(buffer.size(), 50)
        XCTAssertEqual(buffer.currentBufferedDurationMs(), 1_000)
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 10)
    }

    func testDefaultLatencyAllowsFullTTSBurst() {
        // The server sends TTS frames as fast as the provider produces them,
        // not at real time. A normal 10 s utterance must not be truncated.
        let buffer = JitterBuffer()
        for sequence in 1...500 {
            buffer.push(frame(sequence: UInt64(sequence)))
        }

        XCTAssertEqual(buffer.size(), 500)
        XCTAssertEqual(buffer.currentBufferedDurationMs(), 10_000)
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 0)
    }

    func testLatencyBoundScalesWithLongerFrames() {
        let buffer = JitterBuffer(maxLatencyMs: 1_000)
        for sequence in 1...20 {
            buffer.push(frame(sequence: UInt64(sequence), durationMs: 60, samples: 960))
        }

        XCTAssertEqual(buffer.size(), 16)
        XCTAssertEqual(buffer.currentBufferedDurationMs(), 960)
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 4)
    }

    func testSampleCountOverridesUnderreportedServerDuration() {
        let buffer = JitterBuffer(maxLatencyMs: 1_000)
        for sequence in 1...20 {
            buffer.push(frame(sequence: UInt64(sequence), durationMs: 20, samples: 960))
        }

        XCTAssertEqual(buffer.size(), 16)
        XCTAssertEqual(buffer.currentBufferedDurationMs(), 960)
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 4)
    }

    func testOversizedFirstFrameDoesNotBlockFollowingFrames() {
        let buffer = JitterBuffer(maxLatencyMs: 1_000)
        buffer.push(frame(sequence: 1, samples: 20_000))

        XCTAssertEqual(buffer.size(), 0)
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 1)

        buffer.push(frame(sequence: 2))
        XCTAssertEqual(buffer.size(), 1)
        XCTAssertEqual(buffer.currentBufferedDurationMs(), 20)
    }

    func testEmptyFramesAreDropped() {
        let buffer = JitterBuffer(maxLatencyMs: 1_000)
        buffer.push(frame(sequence: 1, samples: 0))

        XCTAssertEqual(buffer.size(), 0)
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 1)
    }

    func testContinuousEmptyFramesDoNotGrowTheBuffer() {
        let buffer = JitterBuffer(maxLatencyMs: 1_000)
        for sequence in 1...100 {
            buffer.push(frame(sequence: UInt64(sequence), samples: 0))
        }

        XCTAssertEqual(buffer.size(), 0)
        XCTAssertEqual(buffer.currentBufferedDurationMs(), 0)
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 100)
    }

    func testNonIntegerFrameDurationRoundsUp() {
        let buffer = JitterBuffer(maxLatencyMs: 3)
        buffer.push(frame(sequence: 1, samples: 24)) // 1.5 ms -> 2 ms
        buffer.push(frame(sequence: 2, samples: 24))

        XCTAssertEqual(buffer.size(), 1)
        XCTAssertEqual(buffer.currentBufferedDurationMs(), 2)
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 1)
    }

    func testZeroDurationFallsBackToSampleCount() {
        let buffer = JitterBuffer(maxLatencyMs: 1_000)
        for sequence in 1...60 {
            buffer.push(frame(sequence: UInt64(sequence), durationMs: 0))
        }

        XCTAssertEqual(buffer.size(), 50)
        XCTAssertEqual(buffer.currentBufferedDurationMs(), 1_000)
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 10)
    }

    func testSubMillisecondFramesStillConsumeLatencyBudget() {
        let buffer = JitterBuffer(maxLatencyMs: 10)
        for sequence in 1...20 {
            buffer.push(
                AudioPCMFrame(
                    samples: [0],
                    sampleRate: 16_000,
                    sampleBits: 16,
                    durationMs: 0,
                    sequenceNumber: UInt64(sequence),
                    playbackEpoch: 1,
                    turnID: ""
                )
            )
        }

        XCTAssertEqual(buffer.size(), 10)
        XCTAssertEqual(buffer.currentBufferedDurationMs(), 10)
        XCTAssertEqual(buffer.currentStats().droppedOverflow, 10)
    }

    func testPullRestoresBufferedDuration() {
        let buffer = JitterBuffer(maxLatencyMs: 1_000)
        buffer.push(frame(sequence: 1))
        buffer.push(frame(sequence: 2))
        _ = buffer.pull()

        XCTAssertEqual(buffer.currentBufferedDurationMs(), 20)
    }

    func testResetClearsFramesAndStats() {
        let buffer = JitterBuffer()
        buffer.push(frame(sequence: 1))
        _ = buffer.pull()
        buffer.push(frame(sequence: 1))
        buffer.reset(acceptedEpoch: 5)

        XCTAssertNil(buffer.pull())
        XCTAssertEqual(buffer.currentAcceptedEpoch(), 5)
        XCTAssertEqual(buffer.currentStats(), JitterBuffer.Stats())
    }

    func testConcurrentPushAndPullDoesNotCrash() {
        let buffer = JitterBuffer()
        DispatchQueue.concurrentPerform(iterations: 200) { index in
            buffer.push(makeFrame(sequence: UInt64(index + 1)))
            _ = buffer.pull()
        }
        XCTAssertLessThanOrEqual(buffer.size(), 200)
    }
}
