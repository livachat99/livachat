import CoreGraphics
import UIKit
import XCTest
@testable import LivaChatApp

private actor FakeAvatarStore: AvatarStoring {
    private var data: Data?
    private var saveError: Error?
    private var removeError: Error?
    private(set) var saved: [Data] = []

    init(data: Data? = nil) {
        self.data = data
    }

    func setSaveError(_ error: Error?) {
        saveError = error
    }

    func setRemoveError(_ error: Error?) {
        removeError = error
    }

    func loadLatestData() async -> Data? {
        data
    }

    func save(_ data: Data) async throws -> URL {
        if let saveError { throw saveError }
        self.data = data
        saved.append(data)
        return URL(fileURLWithPath: "/tmp/avatar.jpg")
    }

    func removeAll() async throws {
        if let removeError { throw removeError }
        data = nil
    }

    func hasStoredAvatar() async -> Bool {
        data != nil
    }
}

@MainActor
private final class FakeAvatarSettings: AvatarVisibilityStoring {
    var avatarVisible: Bool

    init(_ visible: Bool) {
        self.avatarVisible = visible
    }
}

@MainActor
private final class FakeAvatarCameraPermission: CameraPermissionProviding {
    var state: CameraPermissionState
    var requestResult: CameraPermissionState?
    private(set) var requestCount = 0

    init(_ state: CameraPermissionState) {
        self.state = state
    }

    func current() -> CameraPermissionState {
        state
    }

    func request(_ completion: @escaping @MainActor (CameraPermissionState) -> Void) {
        requestCount += 1
        completion(requestResult ?? state)
    }
}

@MainActor
final class AvatarCustomizationModelTests: XCTestCase {
    func testInitialVisibilityAndPersistence() {
        let settings = FakeAvatarSettings(false)
        let model = AvatarCustomizationModel(store: FakeAvatarStore(), settings: settings)
        XCTAssertFalse(model.avatarVisible)

        model.setAvatarVisible(true)
        XCTAssertTrue(model.avatarVisible)
        XCTAssertTrue(settings.avatarVisible)
    }

    func testLoadSetsImageAndCustomFlag() async throws {
        let data = try makeJPEG(color: .red)
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(data: data),
            settings: FakeAvatarSettings(true)
        )

        await model.load()

        XCTAssertNotNil(model.avatarImage)
        XCTAssertTrue(model.hasCustomAvatar)
    }

    func testLoadCorruptDataKeepsCustomFlagWithoutImage() async {
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(data: Data("broken".utf8)),
            settings: FakeAvatarSettings(true)
        )

        await model.load()

        XCTAssertNil(model.avatarImage)
        XCTAssertTrue(model.hasCustomAvatar)
    }

    func testLoadMissingDataClearsState() async {
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true)
        )

        await model.load()

        XCTAssertNil(model.avatarImage)
        XCTAssertFalse(model.hasCustomAvatar)
    }

    func testGalleryRouteSequencing() throws {
        let data = try makeJPEG(color: .blue)
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true)
        )
        model.presentSheet()
        model.selectFromGallery()
        XCTAssertFalse(model.isSheetPresented)
        XCTAssertEqual(model.route, .galleryPicker)

        model.handleGalleryData(data)
        XCTAssertNil(model.route)
        model.pickerDismissed()
        XCTAssertEqual(model.route, .crop)
        XCTAssertEqual(model.cropSourceData, data)

        model.cancelCrop()
        XCTAssertNil(model.route)
        XCTAssertNil(model.cropSourceData)
    }

    func testLateGalleryDataPresentsCropDirectly() throws {
        let data = try makeJPEG(color: .green)
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true)
        )
        model.selectFromGallery()
        model.pickerDismissed()
        XCTAssertNil(model.route)

        model.handleGalleryData(data)
        XCTAssertEqual(model.route, .crop)
        XCTAssertEqual(model.cropSourceData, data)
    }

    func testCameraUnavailableClosesSheetAndShowsError() {
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true),
            cameraAvailable: { false }
        )
        model.presentSheet()
        model.takePhoto()

        XCTAssertFalse(model.isSheetPresented)
        XCTAssertNil(model.route)
        XCTAssertNotNil(model.errorMessage)
    }

    func testCameraDeniedClosesSheetAndShowsError() {
        let permission = FakeAvatarCameraPermission(.denied)
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true),
            cameraPermissionProvider: permission
        )
        model.presentSheet()
        model.takePhoto()

        XCTAssertFalse(model.isSheetPresented)
        XCTAssertNil(model.route)
        XCTAssertNotNil(model.errorMessage)
        XCTAssertEqual(permission.requestCount, 0)
    }

    func testCameraUndeterminedRequestsAndPresentsWhenGranted() {
        let permission = FakeAvatarCameraPermission(.undetermined)
        permission.requestResult = .granted
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true),
            cameraPermissionProvider: permission
        )
        model.presentSheet()
        model.takePhoto()

        XCTAssertEqual(permission.requestCount, 1)
        XCTAssertFalse(model.isSheetPresented)
        XCTAssertEqual(model.route, .camera)
    }

    func testCameraUndeterminedDeniedShowsError() {
        let permission = FakeAvatarCameraPermission(.undetermined)
        permission.requestResult = .denied
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true),
            cameraPermissionProvider: permission
        )
        model.presentSheet()
        model.takePhoto()

        XCTAssertNil(model.route)
        XCTAssertNotNil(model.errorMessage)
    }

    func testCameraRouteSequencing() throws {
        let data = try makeJPEG(color: .yellow)
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true)
        )
        model.takePhoto()
        XCTAssertEqual(model.route, .camera)

        model.handleCameraPhoto(data)
        XCTAssertNil(model.route)
        model.pickerDismissed()
        XCTAssertEqual(model.route, .crop)
    }

    func testCameraFlowPausesAndResumesAroundPicker() throws {
        var pauses = 0
        var resumes = 0
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true)
        )
        model.setCameraFlowHooks(
            pause: {
                pauses += 1
                return true
            },
            resume: { resumes += 1 }
        )
        model.takePhoto()
        XCTAssertEqual(model.route, .camera)
        XCTAssertEqual(pauses, 1)
        XCTAssertEqual(resumes, 0)

        model.handleCameraPhoto(nil)
        model.pickerDismissed()
        XCTAssertNil(model.route)
        XCTAssertEqual(pauses, 1)
        XCTAssertEqual(resumes, 1)
    }

    func testCameraCropKeepsLiveCameraPausedUntilCropEnds() throws {
        let data = try makeJPEG(color: .yellow)
        var resumes = 0
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true)
        )
        model.setCameraFlowHooks(pause: { true }, resume: { resumes += 1 })
        model.takePhoto()
        model.handleCameraPhoto(data)
        model.pickerDismissed()
        XCTAssertEqual(model.route, .crop)
        XCTAssertEqual(resumes, 0)

        model.cancelCrop()
        XCTAssertEqual(resumes, 1)
    }

    func testCameraConfirmCropResumesLiveCamera() async throws {
        let data = try makeJPEG(color: .blue)
        var resumes = 0
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true)
        )
        model.setCameraFlowHooks(pause: { true }, resume: { resumes += 1 })
        model.takePhoto()
        model.handleCameraPhoto(data)
        model.pickerDismissed()
        XCTAssertEqual(model.route, .crop)

        await model.confirmCrop(data)
        XCTAssertEqual(resumes, 1)
    }

    func testGalleryFlowDoesNotPauseLiveCamera() throws {
        let data = try makeJPEG(color: .green)
        var pauses = 0
        var resumes = 0
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true)
        )
        model.setCameraFlowHooks(
            pause: {
                pauses += 1
                return true
            },
            resume: { resumes += 1 }
        )
        model.selectFromGallery()
        model.handleGalleryData(data)
        model.pickerDismissed()
        XCTAssertEqual(model.route, .crop)

        model.cancelCrop()
        XCTAssertEqual(pauses, 0)
        XCTAssertEqual(resumes, 0)
    }

    func testCameraPermissionDenialDoesNotPauseLiveCamera() {
        var pauses = 0
        let permission = FakeAvatarCameraPermission(.denied)
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true),
            cameraPermissionProvider: permission
        )
        model.setCameraFlowHooks(
            pause: {
                pauses += 1
                return true
            },
            resume: {}
        )
        model.takePhoto()

        XCTAssertNil(model.route)
        XCTAssertEqual(pauses, 0)
    }

    func testCancelPickerClearsRoute() {
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true)
        )
        model.selectFromGallery()
        model.handleGalleryData(nil)
        model.pickerDismissed()
        XCTAssertNil(model.route)
        XCTAssertNil(model.cropSourceData)
    }

    func testConfirmCropSavesAndUpdatesAvatar() async throws {
        let data = try makeJPEG(color: .purple)
        let store = FakeAvatarStore()
        let model = AvatarCustomizationModel(store: store, settings: FakeAvatarSettings(true))
        makeCrop(model, data: data)

        await model.confirmCrop(data)

        let saved = await store.saved
        XCTAssertEqual(saved, [data])
        XCTAssertNotNil(model.avatarImage)
        XCTAssertTrue(model.hasCustomAvatar)
        XCTAssertNil(model.route)
        XCTAssertFalse(model.isSaving)
        XCTAssertNil(model.errorMessage)
    }

    func testConfirmCropFailureKeepsOldAvatar() async throws {
        let old = try makeJPEG(color: .red)
        let store = FakeAvatarStore(data: old)
        let model = AvatarCustomizationModel(store: store, settings: FakeAvatarSettings(true))
        await model.load()
        await store.setSaveError(NSError(domain: "test", code: 7))
        let new = try makeJPEG(color: .blue)
        makeCrop(model, data: new)

        await model.confirmCrop(new)

        XCTAssertNotNil(model.avatarImage)
        XCTAssertTrue(model.hasCustomAvatar)
        XCTAssertEqual(model.errorMessage, L10n.t("conv_avatar_save_failed"))
        XCTAssertFalse(model.errorMessage?.contains("%") ?? true)
        XCTAssertNil(model.route)
    }

    func testRemoveAvatarClearsState() async throws {
        let data = try makeJPEG(color: .green)
        let store = FakeAvatarStore(data: data)
        let model = AvatarCustomizationModel(store: store, settings: FakeAvatarSettings(true))
        await model.load()
        XCTAssertNotNil(model.avatarImage)

        await model.removeCustomAvatar()

        XCTAssertNil(model.avatarImage)
        XCTAssertFalse(model.hasCustomAvatar)
        let remaining = await store.loadLatestData()
        XCTAssertNil(remaining)
    }

    func testRemoveFailureKeepsAvatarAndError() async throws {
        let data = try makeJPEG(color: .red)
        let store = FakeAvatarStore(data: data)
        let model = AvatarCustomizationModel(store: store, settings: FakeAvatarSettings(true))
        await model.load()
        await store.setRemoveError(NSError(domain: "test", code: 8))

        await model.removeCustomAvatar()

        XCTAssertNotNil(model.avatarImage)
        XCTAssertTrue(model.hasCustomAvatar)
        XCTAssertNotNil(model.errorMessage)
    }

    func testDismissOverlaysClearsRoutes() throws {
        let data = try makeJPEG(color: .blue)
        let model = AvatarCustomizationModel(
            store: FakeAvatarStore(),
            settings: FakeAvatarSettings(true)
        )
        model.presentSheet()
        makeCrop(model, data: data)

        model.dismissOverlays()

        XCTAssertFalse(model.isSheetPresented)
        XCTAssertNil(model.route)
        XCTAssertNil(model.cropSourceData)
    }

    func testConfirmWithoutCropRouteIsIgnored() async throws {
        let data = try makeJPEG(color: .red)
        let store = FakeAvatarStore()
        let model = AvatarCustomizationModel(store: store, settings: FakeAvatarSettings(true))

        await model.confirmCrop(data)

        let saved = await store.saved
        XCTAssertTrue(saved.isEmpty)
        XCTAssertFalse(model.hasCustomAvatar)
    }

    private func makeCrop(_ model: AvatarCustomizationModel, data: Data) {
        model.selectFromGallery()
        model.handleGalleryData(data)
        model.pickerDismissed()
        XCTAssertEqual(model.route, .crop)
    }

    private func makeJPEG(color: UIColor) throws -> Data {
        guard let context = CGContext(
            data: nil,
            width: 8,
            height: 8,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            throw NSError(domain: "AvatarCustomizationModelTests", code: 1)
        }
        context.setFillColor(color.cgColor)
        context.fill(CGRect(x: 0, y: 0, width: 8, height: 8))
        guard let image = context.makeImage(),
              let data = CameraImageEncoder.jpegData(from: image, quality: 0.9) else {
            throw NSError(domain: "AvatarCustomizationModelTests", code: 2)
        }
        return data
    }
}
