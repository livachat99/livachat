import CoreGraphics
import XCTest
@testable import LivaChatApp

final class LocalizationRTLTests: XCTestCase {
    // iOS SwiftUI mirrors `HStack`/`.offset(x:)`/SF Symbols for
    // `\.layoutDirection`, but NOT custom `Shape.path(in:)` geometry
    // (verified in the iOS 18.0 simulator: the tail stayed on the LTR
    // corner for both user and AI bubbles). `BubbleShape` therefore takes
    // `isRTL` explicitly.
    func testBubbleTailFollowsReadingDirection() {
        XCTAssertTrue(BubbleShape.tailAtBottomRight(isUser: true, isRTL: false))
        XCTAssertFalse(BubbleShape.tailAtBottomRight(isUser: false, isRTL: false))
        XCTAssertFalse(BubbleShape.tailAtBottomRight(isUser: true, isRTL: true))
        XCTAssertTrue(BubbleShape.tailAtBottomRight(isUser: false, isRTL: true))
    }

    func testLocalizedListJoiningUsesLocalePunctuationAndDropsEmptyParts() {
        XCTAssertEqual(
            LocalizedList.join(["A", "B", "C"], locale: Locale(identifier: "en")),
            "A, B, and C"
        )
        XCTAssertEqual(
            LocalizedList.join(["A", nil, "  ", "B"], locale: Locale(identifier: "en")),
            "A and B"
        )
        XCTAssertEqual(LocalizedList.join(["A"], locale: Locale(identifier: "en")), "A")
        XCTAssertEqual(LocalizedList.join([], locale: Locale(identifier: "en")), "")
        XCTAssertEqual(
            LocalizedList.join(["A", "B", "C"], locale: Locale(identifier: "fr")),
            "A, B et C"
        )
        XCTAssertEqual(
            LocalizationTestSupport.strippingBidiControls(
                LocalizedList.join(["A", "B", "C"], locale: Locale(identifier: "ar_SA"))
            ),
            "A وB وC"
        )
    }

    func testHistorySwipeMirrorsDirectionAndClampsReveal() {
        XCTAssertEqual(
            HistorySwipeMath.revealOffset(CGSize(width: -100, height: 0), isRTL: false),
            -100
        )
        XCTAssertEqual(
            HistorySwipeMath.revealOffset(CGSize(width: 100, height: 0), isRTL: true),
            -100
        )
        XCTAssertNil(HistorySwipeMath.revealOffset(CGSize(width: 100, height: 0), isRTL: false))
        XCTAssertNil(HistorySwipeMath.revealOffset(CGSize(width: -100, height: 0), isRTL: true))
        XCTAssertNil(HistorySwipeMath.revealOffset(CGSize(width: -100, height: 200), isRTL: false))
        XCTAssertEqual(
            HistorySwipeMath.revealOffset(CGSize(width: -200, height: 0), isRTL: false),
            -HistorySwipeMath.revealLimit
        )
        XCTAssertEqual(
            HistorySwipeMath.revealOffset(CGSize(width: 200, height: 0), isRTL: true),
            -HistorySwipeMath.revealLimit
        )

        XCTAssertTrue(HistorySwipeMath.shouldDelete(CGSize(width: -80, height: 0), isRTL: false))
        XCTAssertFalse(HistorySwipeMath.shouldDelete(CGSize(width: -79, height: 0), isRTL: false))
        XCTAssertTrue(HistorySwipeMath.shouldDelete(CGSize(width: 80, height: 0), isRTL: true))
        XCTAssertFalse(HistorySwipeMath.shouldDelete(CGSize(width: 80, height: 0), isRTL: false))
        XCTAssertFalse(HistorySwipeMath.shouldDelete(CGSize(width: -100, height: 200), isRTL: false))
    }

    func testImageViewerPagingMirrorsDirectionAndResistsAtEdges() {
        XCTAssertEqual(
            ImageViewerMath.pageChange(translationX: -100, pageIndex: 0, pageCount: 2, isRTL: false),
            1
        )
        XCTAssertEqual(
            ImageViewerMath.pageChange(translationX: 100, pageIndex: 0, pageCount: 2, isRTL: true),
            1
        )
        XCTAssertEqual(
            ImageViewerMath.pageChange(translationX: 100, pageIndex: 1, pageCount: 2, isRTL: false),
            -1
        )
        XCTAssertEqual(
            ImageViewerMath.pageChange(translationX: -100, pageIndex: 1, pageCount: 2, isRTL: true),
            -1
        )
        XCTAssertEqual(
            ImageViewerMath.pageChange(translationX: 100, pageIndex: 0, pageCount: 2, isRTL: false),
            0
        )
        XCTAssertEqual(
            ImageViewerMath.pageChange(translationX: -100, pageIndex: 1, pageCount: 2, isRTL: false),
            0
        )
        XCTAssertEqual(
            ImageViewerMath.pageChange(translationX: -50, pageIndex: 0, pageCount: 2, isRTL: false),
            0
        )

        XCTAssertEqual(ImageViewerMath.neighborOffset(direction: 1, width: 300), 300)
        XCTAssertEqual(ImageViewerMath.settleOffset(direction: 1, width: 300), -300)
        XCTAssertEqual(
            ImageViewerMath.dragOffset(
                translationX: -100, pageIndex: 0, pageCount: 2, width: 300, isRTL: false
            ),
            -100
        )
        XCTAssertEqual(
            ImageViewerMath.dragOffset(
                translationX: 100, pageIndex: 0, pageCount: 2, width: 300, isRTL: true
            ),
            -100
        )
        XCTAssertEqual(
            ImageViewerMath.dragOffset(
                translationX: 100, pageIndex: 0, pageCount: 2, width: 300, isRTL: false
            ),
            25
        )
        XCTAssertEqual(
            ImageViewerMath.dragOffset(
                translationX: -100, pageIndex: 0, pageCount: 2, width: 300, isRTL: true
            ),
            25
        )
    }

    func testSegmentProgressMirrorsDrawingAndScrubbing() {
        XCTAssertEqual(SegmentProgressMath.fraction(atX: 0, width: 100, isRTL: false), 0)
        XCTAssertEqual(SegmentProgressMath.fraction(atX: 50, width: 100, isRTL: false), 0.5)
        XCTAssertEqual(SegmentProgressMath.fraction(atX: 100, width: 100, isRTL: false), 1)
        XCTAssertEqual(SegmentProgressMath.fraction(atX: 0, width: 100, isRTL: true), 1)
        XCTAssertEqual(SegmentProgressMath.fraction(atX: 50, width: 100, isRTL: true), 0.5)
        XCTAssertEqual(SegmentProgressMath.fraction(atX: 100, width: 100, isRTL: true), 0)

        XCTAssertEqual(SegmentProgressMath.x(forFraction: 0, width: 100, isRTL: false), 0)
        XCTAssertEqual(SegmentProgressMath.x(forFraction: 1, width: 100, isRTL: false), 100)
        XCTAssertEqual(SegmentProgressMath.x(forFraction: 0, width: 100, isRTL: true), 100)
        XCTAssertEqual(SegmentProgressMath.x(forFraction: 1, width: 100, isRTL: true), 0)

        XCTAssertEqual(
            SegmentProgressMath.ms(atX: 50, width: 100, durationMs: 10_000, isRTL: false),
            5_000
        )
        XCTAssertEqual(
            SegmentProgressMath.ms(atX: 50, width: 100, durationMs: 10_000, isRTL: true),
            5_000
        )
        XCTAssertEqual(
            SegmentProgressMath.ms(atX: 100, width: 100, durationMs: 10_000, isRTL: true),
            0
        )

        XCTAssertEqual(
            SegmentProgressMath.barX(index: 0, slot: 10, barWidth: 6, width: 100, isRTL: false),
            2
        )
        XCTAssertEqual(
            SegmentProgressMath.barX(index: 0, slot: 10, barWidth: 6, width: 100, isRTL: true),
            92
        )
        XCTAssertEqual(
            SegmentProgressMath.barX(index: 9, slot: 10, barWidth: 6, width: 100, isRTL: true),
            2
        )
    }

    func testFlowLayoutMirrorsOriginsAndKeepsRows() {
        let sizes: [CGSize?] = [
            CGSize(width: 100, height: 40),
            CGSize(width: 100, height: 40),
            CGSize(width: 100, height: 40),
        ]
        let ltr = FlowLayoutMath.frames(sizes: sizes, width: 250, spacing: 10, isRTL: false)
        XCTAssertEqual(ltr.frames[0]?.origin, CGPoint(x: 0, y: 0))
        XCTAssertEqual(ltr.frames[1]?.origin, CGPoint(x: 110, y: 0))
        XCTAssertEqual(ltr.frames[2]?.origin, CGPoint(x: 0, y: 50))
        XCTAssertEqual(ltr.height, 90)

        let rtl = FlowLayoutMath.frames(sizes: sizes, width: 250, spacing: 10, isRTL: true)
        XCTAssertEqual(rtl.frames[0]?.origin, CGPoint(x: 150, y: 0))
        XCTAssertEqual(rtl.frames[1]?.origin, CGPoint(x: 40, y: 0))
        XCTAssertEqual(rtl.frames[2]?.origin, CGPoint(x: 150, y: 50))
        XCTAssertEqual(rtl.height, 90)

        let partial: [CGSize?] = [nil, CGSize(width: 100, height: 40)]
        let partialResult = FlowLayoutMath.frames(
            sizes: partial,
            width: 250,
            spacing: 10,
            isRTL: false
        )
        XCTAssertNil(partialResult.frames[0])
        XCTAssertEqual(partialResult.frames[1]?.origin, CGPoint(x: 0, y: 0))
        XCTAssertEqual(partialResult.height, 40)
    }

    func testCameraCaptureErrorsMapToLocalizedKeys() {
        XCTAssertEqual(
            CameraCaptureError.permissionDenied.appErrorMessage.key,
            "conv_camera_permission_denied"
        )
        XCTAssertEqual(
            CameraCaptureError.inputUnavailable.appErrorMessage.key,
            "conv_camera_unavailable"
        )
        let configuration = CameraCaptureError.configurationFailed(
            NSError(domain: "test", code: 1)
        )
        XCTAssertEqual(configuration.appErrorMessage.key, "conv_camera_start_failed")
        XCTAssertTrue(
            configuration.appErrorMessage.logDetail.contains("Camera configuration failed")
        )
    }
}
