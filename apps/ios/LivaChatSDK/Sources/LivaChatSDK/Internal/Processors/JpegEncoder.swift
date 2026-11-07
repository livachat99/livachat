import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

internal protocol JPEGEncoding: AnyObject {
    func encode(_ image: CGImage, quality: Int, maxLongSide: Int?) throws -> Data
}

internal final class JpegEncoderImpl: JPEGEncoding {
    func encode(_ image: CGImage, quality: Int, maxLongSide: Int?) throws -> Data {
        let source = try scaleIfNeeded(image, maxLongSide: maxLongSide)
        guard let mutable = CFDataCreateMutable(nil, 0),
              let destination = CGImageDestinationCreateWithData(
                  mutable,
                  UTType.jpeg.identifier as CFString,
                  1,
                  nil
              ) else {
            throw LivaChatError.invalidConfiguration("cannot create JPEG destination")
        }

        let clampedQuality = min(max(quality, 1), 100)
        let options = [
            kCGImageDestinationLossyCompressionQuality: Double(clampedQuality) / 100.0
        ] as CFDictionary
        CGImageDestinationAddImage(destination, source, options)
        guard CGImageDestinationFinalize(destination) else {
            throw LivaChatError.invalidConfiguration("JPEG finalize failed")
        }
        return mutable as Data
    }

    private func scaleIfNeeded(_ image: CGImage, maxLongSide: Int?) throws -> CGImage {
        guard let maxLongSide, maxLongSide > 0 else { return image }
        let width = image.width
        let height = image.height
        let longSide = max(width, height)
        guard longSide > maxLongSide else { return image }

        let scale = Double(maxLongSide) / Double(longSide)
        let newWidth = max(1, Int((Double(width) * scale).rounded()))
        let newHeight = max(1, Int((Double(height) * scale).rounded()))

        guard let context = CGContext(
            data: nil,
            width: newWidth,
            height: newHeight,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            throw LivaChatError.invalidConfiguration("cannot create image context")
        }
        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: newWidth, height: newHeight))
        guard let scaled = context.makeImage() else {
            throw LivaChatError.invalidConfiguration("image scaling failed")
        }
        return scaled
    }
}
