import CoreGraphics

enum AvatarCropMath {
    static let radius: CGFloat = 96

    static func minimumScale(imageWidth: Int, imageHeight: Int) -> CGFloat {
        let minDimension = CGFloat(min(imageWidth, imageHeight))
        guard minDimension > 0 else { return 1 }
        return (2 * radius) / minDimension
    }

    static func initialScale(imageWidth: Int, imageHeight: Int, area: CGSize) -> CGFloat {
        guard imageWidth > 0, imageHeight > 0, area.width > 0, area.height > 0 else {
            return minimumScale(imageWidth: imageWidth, imageHeight: imageHeight)
        }
        let fill = max(
            area.width / CGFloat(imageWidth),
            area.height / CGFloat(imageHeight)
        )
        return max(
            minimumScale(imageWidth: imageWidth, imageHeight: imageHeight),
            fill
        )
    }

    static func clampedOffset(
        _ candidate: CGSize,
        imageWidth: Int,
        imageHeight: Int,
        scale: CGFloat
    ) -> CGSize {
        guard imageWidth > 0, imageHeight > 0, scale > 0 else { return .zero }
        let maxX = max(0, CGFloat(imageWidth) * scale / 2 - radius)
        let maxY = max(0, CGFloat(imageHeight) * scale / 2 - radius)
        return CGSize(
            width: min(max(candidate.width, -maxX), maxX),
            height: min(max(candidate.height, -maxY), maxY)
        )
    }

    static func cropRect(
        imageWidth: Int,
        imageHeight: Int,
        scale: CGFloat,
        offset: CGSize
    ) -> CGRect? {
        let width = CGFloat(imageWidth)
        let height = CGFloat(imageHeight)
        guard width > 0, height > 0, scale > 0 else { return nil }
        let offset = clampedOffset(
            offset,
            imageWidth: imageWidth,
            imageHeight: imageHeight,
            scale: scale
        )
        let centerX = width / 2 - offset.width / scale
        let centerY = height / 2 - offset.height / scale
        let imageRadius = radius / scale

        let minX = max(0, centerX - imageRadius)
        let maxX = min(width, centerX + imageRadius)
        let minY = max(0, centerY - imageRadius)
        let maxY = min(height, centerY + imageRadius)
        let side = min(maxX - minX, maxY - minY)
        guard side >= 1 else { return nil }

        let x = floor(minX + (maxX - minX - side) / 2)
        let y = floor(minY + (maxY - minY - side) / 2)
        let pixelSide = floor(min(side, width - x, height - y))
        guard pixelSide >= 1 else { return nil }
        return CGRect(x: x, y: y, width: pixelSide, height: pixelSide)
    }
}
