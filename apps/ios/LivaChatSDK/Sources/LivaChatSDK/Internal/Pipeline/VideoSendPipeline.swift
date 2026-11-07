import Foundation

/// Upstream video pipeline: validates and forwards JPEG frames.
/// Queue-confined to `SDKQueue.videoSend`.
internal final class VideoSendPipeline: @unchecked Sendable {
    private let onEncoded: (
        _ jpeg: Data,
        _ frameID: UInt32,
        _ width: Int,
        _ height: Int,
        _ orientation: Orientation,
        _ cameraFacing: CameraFacing,
        _ turnID: UInt32,
        _ text: String?
    ) -> Void
    private let logger: SdkLogger

    init(
        logger: SdkLogger,
        onEncoded: @escaping (
            Data, UInt32, Int, Int, Orientation, CameraFacing, UInt32, String?
        ) -> Void
    ) {
        self.logger = logger
        self.onEncoded = onEncoded
    }

    func pushJPEG(
        _ jpeg: Data,
        width: Int,
        height: Int,
        frameID: UInt32,
        orientation: Orientation,
        cameraFacing: CameraFacing,
        turnID: UInt32,
        text: String?
    ) {
        guard validDimensions(width: width, height: height), !jpeg.isEmpty else {
            logger.warn("Video", "drop invalid JPEG frame | frameID=\(frameID)")
            return
        }
        onEncoded(jpeg, frameID, width, height, orientation, cameraFacing, turnID, text)
    }

    private func validDimensions(width: Int, height: Int) -> Bool {
        (1...65_535).contains(width) && (1...65_535).contains(height)
    }
}
