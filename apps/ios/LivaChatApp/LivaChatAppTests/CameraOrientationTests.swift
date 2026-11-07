import AVFoundation
import UIKit
import XCTest
@testable import LivaChatApp

final class CameraOrientationTests: XCTestCase {
    func testDeviceOrientationMapsToCaptureOrientation() {
        XCTAssertEqual(
            CameraFrameOrientation.videoOrientation(for: .portrait),
            .portrait
        )
        XCTAssertEqual(
            CameraFrameOrientation.videoOrientation(for: .portraitUpsideDown),
            .portraitUpsideDown
        )
        XCTAssertEqual(
            CameraFrameOrientation.videoOrientation(for: .landscapeLeft),
            .landscapeRight
        )
        XCTAssertEqual(
            CameraFrameOrientation.videoOrientation(for: .landscapeRight),
            .landscapeLeft
        )
    }

    func testOrientationSkipCoalescesContinuousRotations() {
        var coalescer = CameraOrientationSkipCoalescer()
        XCTAssertTrue(coalescer.shouldRequestSkip(at: 10.0))
        // Observed real-device sequence: portrait -> landscapeRight 1.5s apart.
        XCTAssertFalse(coalescer.shouldRequestSkip(at: 11.5))
        // Each coalesced change extends the burst window.
        XCTAssertFalse(coalescer.shouldRequestSkip(at: 13.0))
        // A change after the quiet window arms a new skip.
        XCTAssertTrue(coalescer.shouldRequestSkip(at: 15.0))

        coalescer.reset()
        XCTAssertTrue(coalescer.shouldRequestSkip(at: 16.0))
    }

    func testDeviceOrientationMonitorIgnoresFlatAndUnknown() {
        XCTAssertEqual(CameraDeviceOrientationMonitor.map(.portrait), .portrait)
        XCTAssertEqual(
            CameraDeviceOrientationMonitor.map(.portraitUpsideDown),
            .portraitUpsideDown
        )
        XCTAssertEqual(
            CameraDeviceOrientationMonitor.map(.landscapeLeft),
            .landscapeLeft
        )
        XCTAssertEqual(
            CameraDeviceOrientationMonitor.map(.landscapeRight),
            .landscapeRight
        )
        XCTAssertNil(CameraDeviceOrientationMonitor.map(.faceUp))
        XCTAssertNil(CameraDeviceOrientationMonitor.map(.faceDown))
        XCTAssertNil(CameraDeviceOrientationMonitor.map(.unknown))
    }
}
