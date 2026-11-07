import XCTest
@testable import LivaChatApp

final class SilenceFlushGateTests: XCTestCase {
    func testMuteEdgeEmitsFiftySilenceFramesThenDrops() {
        var gate = SilenceFlushGate()
        gate.resetBaseline(muted: false)

        for index in 0..<SilenceFlushGate.windowFrames {
            XCTAssertEqual(gate.decide(muted: true), .sendSilence, "frame \(index)")
        }
        XCTAssertEqual(gate.decide(muted: true), .drop)
        XCTAssertEqual(gate.pendingSilenceFrames, 0)
    }

    func testUnmuteRestoresRealFramesImmediately() {
        var gate = SilenceFlushGate()
        gate.resetBaseline(muted: false)
        _ = gate.decide(muted: true)
        XCTAssertEqual(gate.decide(muted: false), .sendReal)
        XCTAssertEqual(gate.decide(muted: true), .sendSilence)
    }

    func testStartingMutedDoesNotEmitWindow() {
        var gate = SilenceFlushGate()
        gate.resetBaseline(muted: true)
        XCTAssertEqual(gate.decide(muted: true), .drop)
        XCTAssertEqual(gate.pendingSilenceFrames, 0)
    }

    func testTopUpConsumesRemainingWindow() {
        var gate = SilenceFlushGate()
        gate.resetBaseline(muted: false)
        for _ in 0..<10 {
            _ = gate.decide(muted: true)
        }
        XCTAssertEqual(gate.consumeRemainingForTopUp(), 40)
        XCTAssertEqual(gate.consumeRemainingForTopUp(), 0)
        XCTAssertEqual(gate.decide(muted: true), .drop)
    }
}
