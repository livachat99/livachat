import CoreGraphics
import ImageIO
import LivaChatSDK
import UniformTypeIdentifiers
import XCTest
@testable import LivaChatApp

@MainActor
private final class StubMicPermission: MicPermissionProviding {
    func current() -> MicPermissionState { .granted }
    func request(_ completion: @escaping @MainActor (MicPermissionState) -> Void) {
        completion(.granted)
    }
}

@MainActor
private final class StubCameraPermission: CameraPermissionProviding {
    var state: CameraPermissionState
    var requestResult: CameraPermissionState?
    private(set) var requestCount = 0

    init(_ state: CameraPermissionState) {
        self.state = state
    }

    func current() -> CameraPermissionState { state }

    func request(_ completion: @escaping @MainActor (CameraPermissionState) -> Void) {
        requestCount += 1
        completion(requestResult ?? state)
    }
}

@MainActor
final class M2CameraGalleryTests: XCTestCase {
    func testFramePolicyWarmupThrottleScaleAndTurnBump() {
        XCTAssertTrue(CameraFramePolicy.shouldSkipWarmup(elapsedMs: 999))
        XCTAssertFalse(CameraFramePolicy.shouldSkipWarmup(elapsedMs: 1_000))
        XCTAssertFalse(CameraFramePolicy.shouldPush(nowMs: 1_899, lastPushMs: 1_000))
        XCTAssertTrue(CameraFramePolicy.shouldPush(nowMs: 1_900, lastPushMs: 1_000))

        XCTAssertNil(CameraFramePolicy.scaledSize(width: 720, height: 1_280))
        XCTAssertEqual(CameraFramePolicy.scaledSize(width: 1_920, height: 1_080)?.width, 1_280)
        XCTAssertEqual(CameraFramePolicy.scaledSize(width: 1_920, height: 1_080)?.height, 720)

        XCTAssertFalse(CameraFramePolicy.shouldBumpTurn(lastPushedFacing: nil, nextFacing: .front))
        XCTAssertFalse(CameraFramePolicy.shouldBumpTurn(lastPushedFacing: .front, nextFacing: .front))
        XCTAssertTrue(CameraFramePolicy.shouldBumpTurn(lastPushedFacing: .front, nextFacing: .back))
    }

    func testCaptureTurnCounterIsProcessScoped() {
        let counter = CaptureTurnCounter()
        XCTAssertEqual(counter.current, 0)
        XCTAssertEqual(counter.next(), 1)
        XCTAssertEqual(counter.current, 1)
        XCTAssertEqual(counter.next(), 2)
    }

    func testGalleryEncoderAppliesOrientationAndBoundsSize() throws {
        let image = try makeImage(width: 64, height: 32)
        let jpeg = try makeJPEG(image, orientation: 6)

        let encoded = try XCTUnwrap(GalleryPhotoEncoder.encode(jpeg))
        XCTAssertEqual(encoded.width, 32)
        XCTAssertEqual(encoded.height, 64)
        XCTAssertEqual(encoded.data[0], 0xFF)
        XCTAssertEqual(encoded.data[1], 0xD8)
    }

    func testGalleryEncoderRejectsInvalidData() {
        XCTAssertNil(GalleryPhotoEncoder.encode(Data([0x00, 0x01, 0x02])))
    }

    func testAIImageDecoderDecodesGeneratedJPEG() throws {
        let image = try makeImage(width: 8, height: 8)
        let data = try XCTUnwrap(CameraImageEncoder.jpegData(from: image, quality: 0.8))
        let aiImage = AiImage(
            data: data,
            mime: "image/jpeg",
            width: 8,
            height: 8,
            orientation: .portrait0,
            cameraFacing: .gallery
        )

        let decoded = try XCTUnwrap(AIImageDecoder.decode(aiImage, cacheKey: "test-ai-image"))
        XCTAssertEqual(decoded.width, 8)
        XCTAssertEqual(decoded.height, 8)
    }

    func testImageViewerMath() {
        XCTAssertEqual(ImageViewerMath.clampedScale(0.2), 1)
        XCTAssertEqual(ImageViewerMath.clampedScale(8), 5)

        let fit = ImageViewerMath.fitSize(
            imageSize: CGSize(width: 400, height: 200),
            viewport: CGSize(width: 200, height: 200)
        )
        XCTAssertEqual(fit, CGSize(width: 200, height: 100))

        XCTAssertEqual(
            ImageViewerMath.clampedOffset(
                CGSize(width: 500, height: 500),
                imageSize: CGSize(width: 400, height: 200),
                scale: 1,
                viewport: CGSize(width: 200, height: 200)
            ),
            .zero
        )

        let clamped = ImageViewerMath.clampedOffset(
            CGSize(width: 500, height: 500),
            imageSize: CGSize(width: 400, height: 200),
            scale: 2,
            viewport: CGSize(width: 200, height: 200)
        )
        XCTAssertEqual(clamped.width, 100)
        XCTAssertEqual(clamped.height, 0)
    }

    func testAIImageDecoderRotatesOrientation() throws {
        let image = try makeImage(width: 64, height: 32)
        let data = try XCTUnwrap(CameraImageEncoder.jpegData(from: image, quality: 0.8))
        let aiImage = AiImage(
            data: data,
            mime: "image/jpeg",
            width: 64,
            height: 32,
            orientation: .landscape90,
            cameraFacing: .gallery
        )

        let decoded = try XCTUnwrap(AIImageDecoder.decode(aiImage, cacheKey: "test-ai-rotated"))
        XCTAssertEqual(decoded.width, 32)
        XCTAssertEqual(decoded.height, 64)
    }

    func testAIImageDecoderDownsamplesForBubble() throws {
        let image = try makeImage(width: 2_000, height: 1_000)
        let data = try XCTUnwrap(CameraImageEncoder.jpegData(from: image, quality: 0.8))
        let aiImage = AiImage(
            data: data,
            mime: "image/jpeg",
            width: 2_000,
            height: 1_000,
            orientation: .portrait0,
            cameraFacing: .gallery
        )

        let decoded = try XCTUnwrap(
            AIImageDecoder.decode(
                aiImage,
                cacheKey: "test-ai-thumbnail",
                maxPixelSize: 512
            )
        )

        XCTAssertEqual(decoded.width, 512)
        XCTAssertEqual(decoded.height, 256)
    }

    func testAIImageDecoderDownsamplesAndRotates() throws {
        let image = try makeImage(width: 2_000, height: 1_000)
        let data = try XCTUnwrap(CameraImageEncoder.jpegData(from: image, quality: 0.8))
        let aiImage = AiImage(
            data: data,
            mime: "image/jpeg",
            width: 2_000,
            height: 1_000,
            orientation: .landscape90,
            cameraFacing: .gallery
        )

        let decoded = try XCTUnwrap(
            AIImageDecoder.decode(
                aiImage,
                cacheKey: "test-ai-thumbnail-rotated",
                maxPixelSize: 512
            )
        )

        XCTAssertEqual(decoded.width, 256)
        XCTAssertEqual(decoded.height, 512)
    }

    func testImageOnlyAssistantMessageIsKept() {
        let viewModel = makeViewModel()
        let image = AiImage(
            data: Data([0xFF, 0xD8]),
            mime: "image/jpeg",
            width: 1,
            height: 1,
            orientation: .portrait0,
            cameraFacing: .gallery
        )

        viewModel.finishAssistantMessage(messageID: "m1", text: "", image: image)

        XCTAssertEqual(viewModel.messages.count, 1)
        XCTAssertEqual(viewModel.messages.first?.image, image)
        XCTAssertEqual(viewModel.messages.first?.text, "")
    }

    func testGalleryFlowRequiresCameraOffAndSerializesPresentation() {
        let viewModel = makeViewModel()
        viewModel.setCameraState(isOn: true, isFront: false)
        viewModel.openGalleryPicker()
        XCTAssertFalse(viewModel.galleryPickerPresented)

        viewModel.setCameraState(isOn: false, isFront: false)
        viewModel.openGalleryPicker()
        XCTAssertTrue(viewModel.galleryPickerPresented)

        let data = Data([1, 2, 3])
        viewModel.handleGalleryPickerResult(data)
        XCTAssertFalse(viewModel.galleryPickerPresented)
        XCTAssertNil(viewModel.galleryPreviewData)

        viewModel.galleryPickerDismissed()
        XCTAssertEqual(viewModel.galleryPreviewData, data)

        viewModel.galleryPreviewBack()
        XCTAssertNil(viewModel.galleryPreviewData)
        viewModel.galleryPreviewDismissed()
        XCTAssertTrue(viewModel.galleryPickerPresented)

        viewModel.handleGalleryPickerResult(nil)
        viewModel.galleryPickerDismissed()
        XCTAssertNil(viewModel.galleryPreviewData)
    }

    func testCameraPermissionUndeterminedRequestsOnce() {
        let permission = StubCameraPermission(.undetermined)
        permission.requestResult = .granted
        let viewModel = ConversationViewModel(
            permissionProvider: StubMicPermission(),
            cameraPermissionProvider: permission
        )

        viewModel.toggleCamera()

        XCTAssertEqual(permission.requestCount, 1)
        XCTAssertEqual(viewModel.cameraPermission, .granted)
    }

    private func makeViewModel() -> ConversationViewModel {
        ConversationViewModel(
            permissionProvider: StubMicPermission(),
            cameraPermissionProvider: StubCameraPermission(.granted),
            bannerAutoDismissInterval: 0.05
        )
    }

    private func makeImage(width: Int, height: Int) throws -> CGImage {
        let context = try XCTUnwrap(CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ))
        context.setFillColor(CGColor(red: 0.2, green: 0.4, blue: 0.8, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        return try XCTUnwrap(context.makeImage())
    }

    private func makeJPEG(_ image: CGImage, orientation: Int) throws -> Data {
        let data = NSMutableData()
        let destination = try XCTUnwrap(CGImageDestinationCreateWithData(
            data,
            UTType.jpeg.identifier as CFString,
            1,
            nil
        ))
        CGImageDestinationAddImage(
            destination,
            image,
            [kCGImagePropertyOrientation: orientation] as CFDictionary
        )
        XCTAssertTrue(CGImageDestinationFinalize(destination))
        return data as Data
    }
}
