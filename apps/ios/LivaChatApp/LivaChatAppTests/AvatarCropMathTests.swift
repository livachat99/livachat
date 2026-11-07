import CoreGraphics
import XCTest
@testable import LivaChatApp

final class AvatarCropMathTests: XCTestCase {
    func testMinimumScaleCoversCropCircle() {
        XCTAssertEqual(
            AvatarCropMath.minimumScale(imageWidth: 1024, imageHeight: 2048),
            192.0 / 1024.0,
            accuracy: 0.0001
        )
        XCTAssertEqual(
            AvatarCropMath.minimumScale(imageWidth: 0, imageHeight: 10),
            1
        )
    }

    func testInitialScaleUsesFillWhenLargerThanMinimum() {
        let scale = AvatarCropMath.initialScale(
            imageWidth: 100,
            imageHeight: 200,
            area: CGSize(width: 300, height: 300)
        )
        XCTAssertEqual(scale, 3, accuracy: 0.0001)
    }

    func testInitialScaleNeverBelowMinimum() {
        let scale = AvatarCropMath.initialScale(
            imageWidth: 100,
            imageHeight: 100,
            area: CGSize(width: 100, height: 100)
        )
        XCTAssertEqual(scale, 1.92, accuracy: 0.0001)
    }

    func testOffsetIsClampedToImageBounds() {
        let clamped = AvatarCropMath.clampedOffset(
            CGSize(width: 500, height: -500),
            imageWidth: 400,
            imageHeight: 300,
            scale: 1
        )
        XCTAssertEqual(clamped.width, 104, accuracy: 0.0001)
        XCTAssertEqual(clamped.height, -54, accuracy: 0.0001)
    }

    func testOffsetIsZeroWhenImageIsSmallerThanCropCircle() {
        let clamped = AvatarCropMath.clampedOffset(
            CGSize(width: 20, height: 20),
            imageWidth: 100,
            imageHeight: 100,
            scale: 1
        )
        XCTAssertEqual(clamped, .zero)
    }

    func testCropRectIsCenteredSquare() throws {
        let rect = try XCTUnwrap(
            AvatarCropMath.cropRect(
                imageWidth: 400,
                imageHeight: 400,
                scale: 1,
                offset: .zero
            )
        )
        XCTAssertEqual(rect, CGRect(x: 104, y: 104, width: 192, height: 192))
    }

    func testCropRectTracksOffset() throws {
        let rect = try XCTUnwrap(
            AvatarCropMath.cropRect(
                imageWidth: 400,
                imageHeight: 400,
                scale: 1,
                offset: CGSize(width: 20, height: -30)
            )
        )
        XCTAssertEqual(rect, CGRect(x: 84, y: 134, width: 192, height: 192))
    }

    func testCropRectClampsAtEdgesAndRemainsSquare() throws {
        let rect = try XCTUnwrap(
            AvatarCropMath.cropRect(
                imageWidth: 400,
                imageHeight: 400,
                scale: 1,
                offset: CGSize(width: 10_000, height: 10_000)
            )
        )
        XCTAssertEqual(rect.width, rect.height)
        XCTAssertGreaterThanOrEqual(rect.minX, 0)
        XCTAssertGreaterThanOrEqual(rect.minY, 0)
        XCTAssertLessThanOrEqual(rect.maxX, 400)
        XCTAssertLessThanOrEqual(rect.maxY, 400)
    }

    func testCropRectNilForInvalidScale() {
        XCTAssertNil(
            AvatarCropMath.cropRect(
                imageWidth: 100,
                imageHeight: 100,
                scale: 0,
                offset: .zero
            )
        )
    }
}
