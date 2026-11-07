import Foundation
import ImageIO
import LivaChatSDK
import UniformTypeIdentifiers

struct EncodedGalleryPhoto {
    let data: Data
    let width: Int
    let height: Int
}

enum GallerySendResult {
    case sent
    case rejected
    case failed
}

enum GalleryPhotoEncoder {
    static let maxLongSide = 1_920
    static let jpegQuality: CGFloat = 0.7
    /// User instruction sent with every gallery photo. Resolved at send time so
    /// it follows the app's current language. English is the string catalog's
    /// source language; the localization guard keeps this key in all 15 locales.
    static var actionText: String {
        L10n.t("conv_gallery_action_text")
    }

    static func encode(_ data: Data) -> EncodedGalleryPhoto? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Int,
              let height = properties[kCGImagePropertyPixelHeight] as? Int,
              width > 0, height > 0 else {
            return nil
        }

        let maxPixel = min(maxLongSide, max(width, height))
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixel,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(
            source,
            0,
            options as CFDictionary
        ) else {
            return nil
        }
        guard let jpeg = CameraImageEncoder.jpegData(from: image, quality: jpegQuality) else {
            return nil
        }
        return EncodedGalleryPhoto(data: jpeg, width: image.width, height: image.height)
    }

    static func previewImage(from data: Data) -> CGImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxLongSide,
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }
}

final class GalleryPhotoSender: @unchecked Sendable {
    private let turnCounter: CaptureTurnCounter
    private let push: @Sendable (Data, Int, Int, CameraFacing, UInt32, String?) -> Void

    init(
        turnCounter: CaptureTurnCounter,
        push: @escaping @Sendable (Data, Int, Int, CameraFacing, UInt32, String?) -> Void
    ) {
        self.turnCounter = turnCounter
        self.push = push
    }

    func send(_ photo: EncodedGalleryPhoto) {
        push(
            photo.data,
            photo.width,
            photo.height,
            .gallery,
            turnCounter.current,
            GalleryPhotoEncoder.actionText
        )
    }
}
