import Combine
import CoreGraphics
import Foundation

@MainActor
protocol AvatarVisibilityStoring: AnyObject {
    var avatarVisible: Bool { get set }
}

extension SettingsStore: AvatarVisibilityStoring {}

enum AvatarRoute: String, Equatable, Identifiable {
    case galleryPicker
    case camera
    case crop

    var id: String { rawValue }
}

@MainActor
final class AvatarCustomizationModel: ObservableObject {
    @Published private(set) var avatarImage: CGImage?
    @Published private(set) var avatarVisible: Bool
    @Published private(set) var hasCustomAvatar = false
    @Published private(set) var isSaving = false
    @Published private(set) var route: AvatarRoute?
    @Published private(set) var cropSourceData: Data?
    @Published private(set) var errorMessage: String?
    @Published var isSheetPresented = false

    private let store: AvatarStoring
    private let settings: AvatarVisibilityStoring
    private let cameraPermissionProvider: CameraPermissionProviding?
    private let cameraAvailable: () -> Bool
    private var pendingCropData: Data?
    private var photoFlowActive = false
    private var liveCameraPaused = false
    private var pauseLiveCamera: (@MainActor () -> Bool)?
    private var resumeLiveCamera: (@MainActor () -> Void)?

    init(
        store: AvatarStoring,
        settings: AvatarVisibilityStoring,
        cameraPermissionProvider: CameraPermissionProviding? = nil,
        cameraAvailable: @escaping () -> Bool = { true }
    ) {
        self.store = store
        self.settings = settings
        self.cameraPermissionProvider = cameraPermissionProvider
        self.cameraAvailable = cameraAvailable
        self.avatarVisible = settings.avatarVisible
    }

    func load() async {
        let data = await store.loadLatestData()
        hasCustomAvatar = data != nil
        guard let data else {
            avatarImage = nil
            return
        }
        avatarImage = await Task.detached(priority: .userInitiated) {
            AvatarImageDecoder.decode(data)
        }.value
    }

    func setAvatarVisible(_ visible: Bool) {
        guard avatarVisible != visible else { return }
        avatarVisible = visible
        settings.avatarVisible = visible
    }

    func setCameraFlowHooks(
        pause: @escaping @MainActor () -> Bool,
        resume: @escaping @MainActor () -> Void
    ) {
        pauseLiveCamera = pause
        resumeLiveCamera = resume
    }

    func presentSheet() {
        isSheetPresented = true
    }

    func dismissSheet() {
        isSheetPresented = false
    }

    func dismissOverlays() {
        isSheetPresented = false
        route = nil
        cropSourceData = nil
        pendingCropData = nil
        endPhotoFlow()
    }

    func selectFromGallery() {
        isSheetPresented = false
        route = .galleryPicker
    }

    func takePhoto() {
        isSheetPresented = false
        guard cameraAvailable() else {
            errorMessage = L10n.t("conv_avatar_camera_unavailable")
            return
        }
        guard let cameraPermissionProvider else {
            presentSystemCamera()
            return
        }
        switch cameraPermissionProvider.current() {
        case .granted:
            presentSystemCamera()
        case .undetermined:
            cameraPermissionProvider.request { [weak self] state in
                guard let self else { return }
                guard state == .granted else {
                    self.errorMessage = L10n.t("conv_avatar_camera_permission")
                    return
                }
                self.presentSystemCamera()
            }
        case .denied, .unknown:
            errorMessage = L10n.t("conv_avatar_camera_permission")
        }
    }

    func handleGalleryData(_ data: Data?) {
        handlePickedData(data)
    }

    func handleCameraPhoto(_ data: Data?) {
        if data != nil {
            beginPhotoFlow()
        }
        handlePickedData(data)
    }

    func pickerDismissStarted() {
        if route == .galleryPicker || route == .camera {
            route = nil
        }
    }

    func pickerDismissed() {
        guard let data = pendingCropData else {
            if route == .galleryPicker || route == .camera {
                route = nil
            }
            endPhotoFlow()
            return
        }
        pendingCropData = nil
        cropSourceData = data
        route = .crop
    }

    func cancelCrop() {
        route = nil
        cropSourceData = nil
        pendingCropData = nil
        endPhotoFlow()
    }

    func confirmCrop(_ data: Data) async {
        guard route == .crop, !isSaving else { return }
        route = nil
        cropSourceData = nil
        pendingCropData = nil
        endPhotoFlow()
        isSaving = true
        defer { isSaving = false }

        do {
            _ = try await store.save(data)
            hasCustomAvatar = true
            avatarImage = await Task.detached(priority: .userInitiated) {
                AvatarImageDecoder.decode(data)
            }.value
        } catch {
            AppLog.error("Avatar", "save failed: \(error.localizedDescription)")
            errorMessage = L10n.t("conv_avatar_save_failed")
        }
    }

    func removeCustomAvatar() async {
        isSheetPresented = false
        route = nil
        cropSourceData = nil
        pendingCropData = nil
        isSaving = true
        defer { isSaving = false }

        do {
            try await store.removeAll()
            avatarImage = nil
            hasCustomAvatar = false
        } catch {
            AppLog.error("Avatar", "remove failed: \(error.localizedDescription)")
            errorMessage = L10n.t("conv_avatar_save_failed")
        }
    }

    func clearError() {
        errorMessage = nil
    }

    private func handlePickedData(_ data: Data?) {
        guard let data else {
            pendingCropData = nil
            cropSourceData = nil
            route = nil
            return
        }
        pendingCropData = data
        if route == .galleryPicker || route == .camera {
            route = nil
        } else {
            pendingCropData = nil
            cropSourceData = data
            route = .crop
        }
    }

    private func presentSystemCamera() {
        beginPhotoFlow()
        route = .camera
    }

    /// The system camera must not compete with the live capture session; keep it
    /// paused until the whole photo -> crop flow leaves the screen.
    private func beginPhotoFlow() {
        guard !photoFlowActive else { return }
        photoFlowActive = true
        liveCameraPaused = pauseLiveCamera?() ?? false
    }

    private func endPhotoFlow() {
        guard photoFlowActive else { return }
        photoFlowActive = false
        guard liveCameraPaused else { return }
        liveCameraPaused = false
        resumeLiveCamera?()
    }

    #if DEBUG
    func seedForUITest(_ data: Data) async {
        _ = try? await store.save(data)
        await load()
    }

    func configureCropForUITest(_ data: Data) {
        isSheetPresented = false
        cropSourceData = data
        route = .crop
    }
    #endif
}
