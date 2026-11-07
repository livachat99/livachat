import ImageIO
import LivaChatSDK
import SwiftUI

struct AIImageView: View {
    let image: AiImage
    let cacheKey: String
    var onTap: (() -> Void)? = nil

    @State private var decoded: CGImage?

    var body: some View {
        ZStack {
            if let decoded {
                Image(decorative: decoded, scale: 1)
                    .resizable()
                    .scaledToFit()
                    .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
                    .accessibilityLabel(L10n.t("chat_ai_image"))
                    .accessibilityIdentifier("chat_ai_image")
            }
        }
        .contentShape(Rectangle())
        .onTapGesture {
            onTap?()
        }
        .accessibilityAddTraits(onTap == nil ? [] : .isButton)
        .task(id: cacheKey) {
            let source = image
            let result = await Task.detached(priority: .userInitiated) {
                AIImageDecoder.decode(
                    source,
                    cacheKey: cacheKey,
                    maxPixelSize: AIImageDecoder.bubbleMaxPixelSize
                )
            }.value
            guard !Task.isCancelled else { return }
            decoded = result
        }
    }
}

enum AIImageDecoder {
    /// Bubble thumbnails stay at a chat-width resolution; the full-screen
    /// viewer passes `nil` to keep the original size.
    static let bubbleMaxPixelSize = 1_024

    private static let cacheTotalCostLimit = 64 * 1024 * 1024

    private final class Box {
        let image: CGImage
        let cost: Int

        init(_ image: CGImage) {
            self.image = image
            self.cost = image.bytesPerRow * image.height
        }
    }

    private static func makeCache(countLimit: Int) -> NSCache<NSString, Box> {
        let cache = NSCache<NSString, Box>()
        cache.countLimit = countLimit
        cache.totalCostLimit = cacheTotalCostLimit
        return cache
    }

    // Keep bubble thumbnails and full-screen viewer images in separate
    // caches: the viewer's large full-size decodes must not evict the
    // thumbnails backing the conversation list.
    nonisolated(unsafe) private static let thumbnailCache = makeCache(countLimit: 20)
    nonisolated(unsafe) private static let fullSizeCache = makeCache(countLimit: 3)

    static func clearCache() {
        thumbnailCache.removeAllObjects()
        fullSizeCache.removeAllObjects()
    }

    private static func cache(for maxPixelSize: Int?) -> NSCache<NSString, Box> {
        maxPixelSize == nil ? fullSizeCache : thumbnailCache
    }

    /// Decodes `image`, optionally downsampled to `maxPixelSize`, and applies
    /// the protocol orientation. `maxPixelSize == nil` keeps the original size
    /// for the full-screen viewer; pass a value for bubble/list rendering.
    static func decode(_ image: AiImage, cacheKey: String, maxPixelSize: Int? = nil) -> CGImage? {
        let cache = cache(for: maxPixelSize)
        let key = resolvedCacheKey(cacheKey, maxPixelSize: maxPixelSize) as NSString
        if let cached = cache.object(forKey: key) {
            return cached.image
        }
        guard let decoded = decodeData(image, maxPixelSize: maxPixelSize) else { return nil }
        let box = Box(decoded)
        cache.setObject(box, forKey: key, cost: box.cost)
        return decoded
    }

    private static func resolvedCacheKey(_ cacheKey: String, maxPixelSize: Int?) -> String {
        if let maxPixelSize {
            return "\(cacheKey):thumb:\(maxPixelSize)"
        }
        return "\(cacheKey):full"
    }

    private static func decodeData(_ image: AiImage, maxPixelSize: Int?) -> CGImage? {
        guard let source = CGImageSourceCreateWithData(image.data as CFData, nil) else {
            return nil
        }
        let decoded: CGImage?
        if let maxPixelSize {
            let options: [CFString: Any] = [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: false,
                kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
                kCGImageSourceShouldCacheImmediately: true,
            ]
            decoded = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
                ?? CGImageSourceCreateImageAtIndex(source, 0, nil)
        } else {
            decoded = CGImageSourceCreateImageAtIndex(
                source,
                0,
                [kCGImageSourceShouldCache: true] as CFDictionary
            )
        }
        guard let decoded else { return nil }
        return rotated(decoded, degrees: image.orientation.rawValue)
    }

    private static func rotated(_ image: CGImage, degrees: Int) -> CGImage? {
        guard degrees != 0 else { return image }

        let radians = CGFloat(degrees) * .pi / 180
        let width = abs(degrees) == 90 || abs(degrees) == 270 ? image.height : image.width
        let height = abs(degrees) == 90 || abs(degrees) == 270 ? image.width : image.height
        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return nil
        }
        context.translateBy(x: CGFloat(width) / 2, y: CGFloat(height) / 2)
        context.rotate(by: -radians)
        context.draw(
            image,
            in: CGRect(
                x: -CGFloat(image.width) / 2,
                y: -CGFloat(image.height) / 2,
                width: CGFloat(image.width),
                height: CGFloat(image.height)
            )
        )
        return context.makeImage()
    }
}
