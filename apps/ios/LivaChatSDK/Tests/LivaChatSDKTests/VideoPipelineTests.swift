import CoreGraphics
import Foundation
import XCTest
@testable import LivaChatSDK

private func makeImage(width: Int, height: Int) -> CGImage {
    let context = CGContext(
        data: nil,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    )!
    context.setFillColor(CGColor(red: 1, green: 0.5, blue: 0.2, alpha: 1))
    context.fill(CGRect(x: 0, y: 0, width: width, height: height))
    return context.makeImage()!
}

final class VideoPipelineTests: XCTestCase {
    private struct Encoded {
        let data: Data
        let frameID: UInt32
        let width: Int
        let height: Int
        let orientation: Orientation
        let cameraFacing: CameraFacing
        let turnID: UInt32
        let text: String?
    }

    func testJPEGPassthrough() {
        var encoded: [Encoded] = []
        let pipeline = VideoSendPipeline(
            logger: SdkLogger(logFileURL: nil),
            onEncoded: { data, frameID, width, height, orientation, facing, turnID, text in
                encoded.append(Encoded(
                    data: data,
                    frameID: frameID,
                    width: width,
                    height: height,
                    orientation: orientation,
                    cameraFacing: facing,
                    turnID: turnID,
                    text: text
                ))
            }
        )

        pipeline.pushJPEG(
            Data([0xFF, 0xD8]),
            width: 100,
            height: 200,
            frameID: 5,
            orientation: .landscape90,
            cameraFacing: .back,
            turnID: 3,
            text: "describe"
        )
        XCTAssertEqual(encoded.count, 1)
        XCTAssertEqual(encoded[0].data, Data([0xFF, 0xD8]))
        XCTAssertEqual(encoded[0].height, 200)
        XCTAssertEqual(encoded[0].orientation, .landscape90)
        XCTAssertEqual(encoded[0].cameraFacing, .back)
        XCTAssertEqual(encoded[0].turnID, 3)
        XCTAssertEqual(encoded[0].text, "describe")
    }

    func testInvalidDimensionsAreDropped() {
        var encoded = 0
        let pipeline = VideoSendPipeline(
            logger: SdkLogger(logFileURL: nil),
            onEncoded: { _, _, _, _, _, _, _, _ in encoded += 1 }
        )
        pipeline.pushJPEG(
            Data([1]),
            width: 0,
            height: 100,
            frameID: 1,
            orientation: .portrait0,
            cameraFacing: .front,
            turnID: 0,
            text: nil
        )
        XCTAssertEqual(encoded, 0)
    }

    func testRealJPEGEncoderScales() throws {
        let image = makeImage(width: 32, height: 16)
        let encoder = JpegEncoderImpl()
        let data = try encoder.encode(image, quality: 70, maxLongSide: 16)
        XCTAssertFalse(data.isEmpty)
        XCTAssertEqual(data.prefix(2), Data([0xFF, 0xD8]))
    }
}
