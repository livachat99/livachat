import Foundation
import Photos

protocol PhotoLibrarySaving {
    func save(jpeg: Data, completion: @escaping @MainActor (Bool) -> Void)
}

final class PhotoLibrarySaver: PhotoLibrarySaving, @unchecked Sendable {
    func save(jpeg: Data, completion: @escaping @MainActor (Bool) -> Void) {
        let status = PHPhotoLibrary.authorizationStatus(for: .addOnly)
        switch status {
        case .authorized, .limited:
            performSave(jpeg, completion: completion)
        case .notDetermined:
            PHPhotoLibrary.requestAuthorization(for: .addOnly) { [weak self] status in
                guard status == .authorized || status == .limited else {
                    DispatchQueue.main.async { completion(false) }
                    return
                }
                self?.performSave(jpeg, completion: completion)
            }
        case .denied, .restricted:
            Task { @MainActor in completion(false) }
        @unknown default:
            Task { @MainActor in completion(false) }
        }
    }

    private func performSave(_ data: Data, completion: @escaping @MainActor (Bool) -> Void) {
        PHPhotoLibrary.shared().performChanges {
            let request = PHAssetCreationRequest.forAsset()
            request.addResource(with: .photo, data: data, options: nil)
        } completionHandler: { success, error in
            if let error {
                AppLog.warn("PhotoLibrary", "save failed: \(error.localizedDescription)")
            }
            DispatchQueue.main.async {
                completion(success)
            }
        }
    }
}
