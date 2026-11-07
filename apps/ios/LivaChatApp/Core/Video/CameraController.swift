import AVFoundation
import CoreImage
import Foundation
import ImageIO
import LivaChatSDK
import QuartzCore
import UniformTypeIdentifiers

final class CameraController: NSObject, CameraCapturing, CameraPreviewing, @unchecked Sendable {
    private let session = AVCaptureSession()
    private let sessionQueue = DispatchQueue(label: "dev.livachat.camera.session")
    private let videoQueue = DispatchQueue(label: "dev.livachat.camera.video")
    private let ciContext = CIContext()
    private let stateLock = NSLock()
    private let turnCounter: CaptureTurnCounter
    private let orientationMonitor: CameraDeviceOrientationProviding
    private let pushJPEG: @Sendable (Data, Int, Int, CameraFacing, UInt32) -> Void

    private var front = true
    private var running = false
    private var failureHandler: (@MainActor (AppErrorMessage) -> Void)?
    private var latestDeviceOrientation: CameraDeviceOrientation = .portrait
    private var orientationSkipCoalescer = CameraOrientationSkipCoalescer()
    private var pipeline: CameraFramePipeline?
    private var videoOutput: AVCaptureVideoDataOutput?
    /// Stored as `AnyObject` because stored properties cannot carry
    /// `@available`; cast to `AVCaptureDevice.RotationCoordinator` on iOS 17+.
    /// Session-queue confined.
    private var rotationCoordinator: AnyObject?
    /// KVO token for the coordinator's capture angle; session-queue confined.
    private var rotationObservation: NSKeyValueObservation?
    /// Guards callbacks from a coordinator replaced by switch/stop.
    private var rotationGeneration: UInt64 = 0
    /// Under `stateLock`; true once the iOS 17+ coordinator owns the angle.
    private var usesRotationCoordinator = false
    @MainActor private var previewLayer: AVCaptureVideoPreviewLayer?
    private var notificationTokens: [NSObjectProtocol] = []

    var onFailure: (@MainActor (AppErrorMessage) -> Void)? {
        get {
            stateLock.lock()
            defer { stateLock.unlock() }
            return failureHandler
        }
        set {
            stateLock.lock()
            failureHandler = newValue
            stateLock.unlock()
        }
    }

    var isFrontCamera: Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return front
    }

    init(
        turnCounter: CaptureTurnCounter,
        orientationMonitor: CameraDeviceOrientationProviding,
        pushJPEG: @escaping @Sendable (Data, Int, Int, CameraFacing, UInt32) -> Void
    ) {
        self.turnCounter = turnCounter
        self.orientationMonitor = orientationMonitor
        self.pushJPEG = pushJPEG
        super.init()
        session.automaticallyConfiguresApplicationAudioSession = false
        orientationMonitor.onChange = { [weak self] orientation in
            self?.deviceOrientationChanged(orientation)
        }
        observeSession()
    }

    deinit {
        notificationTokens.forEach(NotificationCenter.default.removeObserver)
        orientationMonitor.stop()
    }

    // MARK: - CameraCapturing

    func start(front: Bool) {
        guard AVCaptureDevice.authorizationStatus(for: .video) == .authorized else {
            fail(CameraCaptureError.permissionDenied.appErrorMessage)
            return
        }

        orientationMonitor.start()
        setLatestDeviceOrientation(orientationMonitor.current ?? .portrait)

        let pipeline = CameraFramePipeline(
            facing: front ? .front : .back,
            previousPushedFacing: nil,
            turnCounter: turnCounter,
            ciContext: ciContext,
            pushJPEG: pushJPEG
        )
        stateLock.lock()
        self.pipeline?.invalidate()
        self.pipeline = pipeline
        self.front = front
        self.running = true
        orientationSkipCoalescer.reset()
        usesRotationCoordinator = false
        rotationGeneration &+= 1
        stateLock.unlock()

        AppLog.info("Camera", "start front=\(front)")
        sessionQueue.async { [weak self] in
            self?.configure(front: front, pipeline: pipeline)
        }
        updatePreviewOnMain(front: front)
    }

    func stop() {
        stateLock.lock()
        pipeline?.invalidate()
        pipeline = nil
        running = false
        usesRotationCoordinator = false
        rotationGeneration &+= 1
        stateLock.unlock()
        stopOrientationMonitor()
        AppLog.info("Camera", "stop")

        sessionQueue.async { [weak self] in
            guard let self else { return }
            self.rotationObservation?.invalidate()
            self.rotationObservation = nil
            self.rotationCoordinator = nil
            self.videoOutput?.setSampleBufferDelegate(nil, queue: nil)
            if self.session.isRunning {
                self.session.stopRunning()
            }
        }
    }

    func switchCamera(front: Bool) {
        stateLock.lock()
        guard running else {
            stateLock.unlock()
            return
        }
        let previousFacing = pipeline?.lastPushedFacing
        stateLock.unlock()

        let pipeline = CameraFramePipeline(
            facing: front ? .front : .back,
            previousPushedFacing: previousFacing,
            turnCounter: turnCounter,
            ciContext: ciContext,
            pushJPEG: pushJPEG
        )
        stateLock.lock()
        self.pipeline?.invalidate()
        self.pipeline = pipeline
        self.front = front
        orientationSkipCoalescer.reset()
        usesRotationCoordinator = false
        rotationGeneration &+= 1
        stateLock.unlock()
        AppLog.info("Camera", "switch front=\(front)")

        sessionQueue.async { [weak self] in
            self?.configure(front: front, pipeline: pipeline)
        }
        updatePreviewOnMain(front: front)
    }

    // MARK: - CameraPreviewing

    @MainActor
    func attachPreview(to layer: AVCaptureVideoPreviewLayer) {
        previewLayer = layer
        layer.videoGravity = .resizeAspectFill
        layer.session = session
        configurePreviewConnection(layer, front: isFrontCamera)
    }

    @MainActor
    func detachPreview() {
        previewLayer?.session = nil
        previewLayer = nil
    }

    // MARK: - Session configuration

    private func configure(front: Bool, pipeline: CameraFramePipeline) {
        session.beginConfiguration()
        var failure: AppErrorMessage?

        defer {
            session.commitConfiguration()
            if let failure {
                if session.isRunning {
                    session.stopRunning()
                }
                fail(failure)
            } else {
                if !session.isRunning {
                    session.startRunning()
                }
                videoOutput?.setSampleBufferDelegate(pipeline, queue: videoQueue)
                updatePreviewOnMain(front: front)
            }
        }

        if session.canSetSessionPreset(.hd1280x720) {
            session.sessionPreset = .hd1280x720
        } else if session.canSetSessionPreset(.high) {
            session.sessionPreset = .high
        }

        // Retire the previous coordinator before touching inputs so a queued
        // KVO callback cannot target the new pipeline.
        rotationObservation?.invalidate()
        rotationObservation = nil
        rotationCoordinator = nil
        stateLock.lock()
        usesRotationCoordinator = false
        rotationGeneration &+= 1
        stateLock.unlock()
        session.inputs.forEach(session.removeInput)

        let position: AVCaptureDevice.Position = front ? .front : .back
        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: position) else {
            failure = CameraCaptureError.inputUnavailable.appErrorMessage
            return
        }
        if #available(iOS 17.0, *) {
            let coordinator = AVCaptureDevice.RotationCoordinator(device: device, previewLayer: nil)
            rotationCoordinator = coordinator
            stateLock.lock()
            usesRotationCoordinator = true
            rotationGeneration &+= 1
            let generation = rotationGeneration
            stateLock.unlock()
            // The coordinator delivers KVO updates on the main queue. Observe
            // it so a stale angle can never stick just because a UIDevice
            // notification raced ahead of the coordinator's own update.
            rotationObservation = coordinator.observe(
                \.videoRotationAngleForHorizonLevelCapture,
                options: [.new]
            ) { [weak self] coordinator, _ in
                let angle = coordinator.videoRotationAngleForHorizonLevelCapture
                self?.coordinatorAngleChanged(angle, generation: generation)
            }
        }
        do {
            let input = try AVCaptureDeviceInput(device: device)
            guard session.canAddInput(input) else {
                failure = CameraCaptureError.inputUnavailable.appErrorMessage
                return
            }
            session.addInput(input)
        } catch {
            failure = CameraCaptureError.configurationFailed(error).appErrorMessage
            return
        }

        if videoOutput == nil {
            let output = AVCaptureVideoDataOutput()
            output.alwaysDiscardsLateVideoFrames = true
            output.videoSettings = [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            ]
            guard session.canAddOutput(output) else {
                failure = CameraCaptureError.configurationFailed(
                    NSError(domain: "Camera", code: -1, userInfo: [
                        NSLocalizedDescriptionKey: "Video output unavailable",
                    ])
                ).appErrorMessage
                return
            }
            session.addOutput(output)
            videoOutput = output
        }

        if let connection = videoOutput?.connection(with: .video) {
            configureDataConnection(connection)
        }
    }

    private func configureDataConnection(_ connection: AVCaptureConnection) {
        if #available(iOS 17.0, *),
           let coordinator = rotationCoordinator as? AVCaptureDevice.RotationCoordinator {
            if !applyCoordinatorAngle(coordinator.videoRotationAngleForHorizonLevelCapture, to: connection) {
                applySemanticOrientation(currentDeviceOrientation, to: connection)
            }
        } else {
            applySemanticOrientation(currentDeviceOrientation, to: connection)
        }
        if connection.isVideoMirroringSupported {
            connection.automaticallyAdjustsVideoMirroring = false
            connection.isVideoMirrored = false
        }
    }

    @MainActor
    private func configurePreviewConnection(_ layer: AVCaptureVideoPreviewLayer, front: Bool) {
        guard let connection = layer.connection else { return }
        // The app/preview stay portrait-locked (Android parity). Only the data
        // connection follows the physical device orientation.
        applyPreviewOrientation(to: connection)
        if connection.isVideoMirroringSupported {
            connection.automaticallyAdjustsVideoMirroring = false
            connection.isVideoMirrored = front
        }
    }

    private func applyPreviewOrientation(to connection: AVCaptureConnection) {
        if #available(iOS 17.0, *), connection.isVideoRotationAngleSupported(90) {
            connection.videoRotationAngle = 90
        } else if connection.isVideoOrientationSupported {
            connection.videoOrientation = .portrait
        }
    }

    /// The coordinator accounts for the front/back sensor's native mounting; a
    /// hard-coded back-camera angle is 180° wrong on the front camera.
    @available(iOS 17.0, *)
    @discardableResult
    private func applyCoordinatorAngle(_ angle: CGFloat, to connection: AVCaptureConnection) -> Bool {
        guard connection.isVideoRotationAngleSupported(angle) else {
            AppLog.debug("Camera", "capture rotation angle unsupported \(angle)")
            return false
        }
        connection.videoRotationAngle = angle
        AppLog.debug(
            "Camera",
            "capture rotation device=\(currentDeviceOrientation) coordinatorAngle=\(angle)"
        )
        return true
    }

    /// iOS 15/16 fallback. iOS 17+ uses `AVCaptureDevice.RotationCoordinator`
    /// because the correct angle differs by camera sensor.
    private func applySemanticOrientation(
        _ deviceOrientation: CameraDeviceOrientation,
        to connection: AVCaptureConnection
    ) {
        let videoOrientation = CameraFrameOrientation.videoOrientation(for: deviceOrientation)
        guard connection.isVideoOrientationSupported else { return }
        connection.videoOrientation = videoOrientation
        AppLog.debug(
            "Camera",
            "capture rotation device=\(deviceOrientation) orientation=\(videoOrientation.rawValue) (semantic)"
        )
    }

    private var currentDeviceOrientation: CameraDeviceOrientation {
        stateLock.lock()
        defer { stateLock.unlock() }
        return latestDeviceOrientation
    }

    private func setLatestDeviceOrientation(_ orientation: CameraDeviceOrientation) {
        stateLock.lock()
        latestDeviceOrientation = orientation
        stateLock.unlock()
    }

    private func deviceOrientationChanged(_ orientation: CameraDeviceOrientation) {
        stateLock.lock()
        let previous = latestDeviceOrientation
        let usesCoordinator = usesRotationCoordinator
        stateLock.unlock()
        // Ignore the first valid notification when the camera was started
        // flat/unknown and the connection already uses the seeded portrait
        // orientation; otherwise it would arm a skip for no angle change.
        guard orientation != previous else { return }
        setLatestDeviceOrientation(orientation)
        AppLog.info("Camera", "device orientation \(orientation)")
        // On iOS 17+ the coordinator's KVO is the angle/skip source; UIDevice
        // notifications only maintain state and logging.
        guard !usesCoordinator else { return }
        beginOrientationUpdate { [weak self] in
            guard let self else { return }
            // The coordinator may have become active after the UIDevice event
            // was received; never let the semantic fallback overwrite its angle.
            self.stateLock.lock()
            let usesCoordinator = self.usesRotationCoordinator
            self.stateLock.unlock()
            guard !usesCoordinator,
                  let connection = self.videoOutput?.connection(with: .video) else { return }
            self.applySemanticOrientation(orientation, to: connection)
        }
    }

    /// Called on the main queue by the RotationCoordinator KVO.
    @available(iOS 17.0, *)
    private func coordinatorAngleChanged(_ angle: CGFloat, generation: UInt64) {
        if Thread.isMainThread {
            handleCoordinatorAngle(angle, generation: generation)
        } else {
            DispatchQueue.main.async { [weak self] in
                self?.handleCoordinatorAngle(angle, generation: generation)
            }
        }
    }

    @available(iOS 17.0, *)
    private func handleCoordinatorAngle(_ angle: CGFloat, generation: UInt64) {
        // `videoRotationAngle` only supports right angles. Ignore any
        // intermediate coordinator value rather than suppressing frames for a
        // reconfiguration that cannot be applied.
        guard angle == 0 || angle == 90 || angle == 180 || angle == 270 else {
            AppLog.debug("Camera", "capture rotation angle unsupported \(angle)")
            return
        }
        beginOrientationUpdate(coordinatorGeneration: generation) { [weak self] in
            guard let self else { return }
            self.stateLock.lock()
            let currentGeneration = self.rotationGeneration
            self.stateLock.unlock()
            guard currentGeneration == generation,
                  let connection = self.videoOutput?.connection(with: .video) else { return }
            self.applyCoordinatorAngle(angle, to: connection)
        }
    }

    /// Suppresses pushes for the duration of one angle update and arms at most
    /// one skip per coalescing window. The pipeline's update counter keeps
    /// frames suppressed until every queued reconfiguration has finished, so
    /// the single skip covers the final transition frame.
    private func beginOrientationUpdate(
        coordinatorGeneration: UInt64? = nil,
        apply: @escaping @Sendable () -> Void
    ) {
        stateLock.lock()
        if let coordinatorGeneration, coordinatorGeneration != rotationGeneration {
            stateLock.unlock()
            return
        }
        // A semantic update must not proceed once the iOS 17+ coordinator owns
        // the data connection angle.
        if coordinatorGeneration == nil, usesRotationCoordinator {
            stateLock.unlock()
            return
        }
        let pipeline = self.pipeline
        let armSkip = pipeline != nil
            && orientationSkipCoalescer.shouldRequestSkip(at: CACurrentMediaTime())
        stateLock.unlock()
        guard let pipeline else { return }
        if !armSkip {
            AppLog.debug("Camera", "orientation skip coalesced")
        }
        pipeline.beginOrientationUpdate(armSkip: armSkip)
        sessionQueue.async { [pipeline] in
            defer { pipeline.endOrientationUpdate() }
            apply()
        }
    }

    private func updatePreviewOnMain(front: Bool) {
        Task { @MainActor [weak self] in
            guard let self, let layer = self.previewLayer else { return }
            layer.session = self.session
            self.configurePreviewConnection(layer, front: front)
        }
    }

    // MARK: - Notifications / failure

    private func observeSession() {
        let center = NotificationCenter.default
        notificationTokens.append(
            center.addObserver(
                forName: AVCaptureSession.runtimeErrorNotification,
                object: session,
                queue: .main
            ) { [weak self] _ in
                self?.stop()
                self?.fail(
                    AppErrorMessage(
                        "conv_camera_start_failed",
                        logDetail: "Camera session failed"
                    )
                )
            }
        )
        notificationTokens.append(
            center.addObserver(
                forName: AVCaptureSession.wasInterruptedNotification,
                object: session,
                queue: .main
            ) { [weak self] _ in
                self?.stop()
                self?.fail(
                    AppErrorMessage(
                        "conv_camera_unavailable",
                        logDetail: "Camera interrupted"
                    )
                )
            }
        )
    }


    private func fail(_ error: AppErrorMessage) {
        stateLock.lock()
        running = false
        stateLock.unlock()
        stopOrientationMonitor()
        AppLog.error("Camera", "failure: \(error.logDetail)")
        let handler = onFailure
        Task { @MainActor in
            handler?(error)
        }
    }

    /// UIDevice orientation APIs are main-thread only; `fail` can run on the
    /// session queue, so hop to main when needed.
    private func stopOrientationMonitor() {
        if Thread.isMainThread {
            orientationMonitor.stop()
        } else {
            DispatchQueue.main.async { [weak self] in
                self?.orientationMonitor.stop()
            }
        }
    }
}

// MARK: - Frame pipeline

private final class CameraFramePipeline: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private let turnCounter: CaptureTurnCounter
    private let ciContext: CIContext
    private let pushJPEG: @Sendable (Data, Int, Int, CameraFacing, UInt32) -> Void
    private let facing: CameraFacing

    private var previousPushedFacing: CameraFacing?
    private var streamStartMs: Double = 0
    private var lastPushMs: Double = 0
    private var skipNextPush = false
    private var skipArmedAtMs: Double = 0
    private var activeOrientationUpdates = 0
    private var suppressUntilMs: Double = 0
    private var valid = true

    private static let orientationSettleMs: Double = 500

    init(
        facing: CameraFacing,
        previousPushedFacing: CameraFacing?,
        turnCounter: CaptureTurnCounter,
        ciContext: CIContext,
        pushJPEG: @escaping @Sendable (Data, Int, Int, CameraFacing, UInt32) -> Void
    ) {
        self.facing = facing
        self.previousPushedFacing = previousPushedFacing
        self.turnCounter = turnCounter
        self.ciContext = ciContext
        self.pushJPEG = pushJPEG
        super.init()
    }

    var lastPushedFacing: CameraFacing? {
        lock.lock()
        defer { lock.unlock() }
        return previousPushedFacing
    }

    func invalidate() {
        lock.lock()
        valid = false
        lock.unlock()
    }

    /// The connection reconfiguration for a new orientation can emit a dark or
    /// partially exposed frame. Suppress pushes while the update is in flight
    /// and arm at most one skip per rotation burst. The counter supports
    /// overlapping rapid rotations: frames resume only after every queued
    /// update has finished, and the skip then covers the final transition.
    func beginOrientationUpdate(armSkip: Bool) {
        let nowMs = CACurrentMediaTime() * 1_000
        lock.lock()
        activeOrientationUpdates += 1
        if armSkip {
            skipNextPush = true
        }
        suppressUntilMs = max(suppressUntilMs, nowMs + Self.orientationSettleMs)
        lock.unlock()
    }

    func endOrientationUpdate() {
        let nowMs = CACurrentMediaTime() * 1_000
        lock.lock()
        if activeOrientationUpdates > 0 {
            activeOrientationUpdates -= 1
        }
        if activeOrientationUpdates == 0 {
            // Keep dropping frames briefly after the reconfiguration: the
            // first post-rotation buffer is often dark and can arrive after
            // the angle setter returns.
            let settleUntil = nowMs + Self.orientationSettleMs
            suppressUntilMs = max(suppressUntilMs, settleUntil)
            if skipNextPush {
                // Measure staleness from the end of the settle window.
                skipArmedAtMs = max(settleUntil, suppressUntilMs)
            }
        }
        lock.unlock()
    }

    nonisolated func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        let nowMs = CACurrentMediaTime() * 1_000

        lock.lock()
        guard valid else {
            lock.unlock()
            return
        }
        if streamStartMs == 0 {
            streamStartMs = nowMs
        }
        let elapsed = Int(nowMs - streamStartMs)
        guard !CameraFramePolicy.shouldSkipWarmup(elapsedMs: elapsed) else {
            lock.unlock()
            return
        }
        // Frames delivered while the connection is reconfiguring, or during
        // the short post-reconfiguration settle, are transitional; drop them
        // without consuming a throttle slot.
        guard activeOrientationUpdates == 0, nowMs >= suppressUntilMs else {
            lock.unlock()
            return
        }
        guard CameraFramePolicy.shouldPush(nowMs: Int(nowMs), lastPushMs: Int(lastPushMs)) else {
            lock.unlock()
            return
        }
        if skipNextPush {
            let ageMs = nowMs - skipArmedAtMs
            skipNextPush = false
            // A skip armed long ago (frame-rate stall) is stale; push the
            // healthy frame instead of dropping it.
            if ageMs <= 2_000 {
                // Consume the throttle slot; the next attempt is a settled frame.
                lastPushMs = nowMs
                lock.unlock()
                AppLog.debug("Camera", "frame skipped after orientation change")
                return
            }
        }
        lastPushMs = nowMs
        let shouldBumpTurn = CameraFramePolicy.shouldBumpTurn(
            lastPushedFacing: previousPushedFacing,
            nextFacing: facing
        )
        lock.unlock()

        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer),
              let encoded = CameraImageEncoder.jpegData(from: pixelBuffer, context: ciContext) else {
            return
        }

        lock.lock()
        guard valid else {
            lock.unlock()
            return
        }
        previousPushedFacing = facing
        lock.unlock()

        if shouldBumpTurn {
            turnCounter.next()
        }
        let turnID = turnCounter.current
        AppLog.debug(
            "Camera",
            "frame facing=\(facing.rawValue) \(encoded.width)x\(encoded.height) bytes=\(encoded.data.count) turn=\(turnID)"
        )
        pushJPEG(encoded.data, encoded.width, encoded.height, facing, turnID)
    }
}

// MARK: - Image encoding

enum CameraImageEncoder {
    static let jpegQuality: CGFloat = 70.0 / 100.0

    static func jpegData(
        from pixelBuffer: CVPixelBuffer,
        context: CIContext
    ) -> (data: Data, width: Int, height: Int)? {
        let image = CIImage(cvPixelBuffer: pixelBuffer)
        guard let cgImage = context.createCGImage(image, from: image.extent) else {
            return nil
        }
        let target = CameraFramePolicy.scaledSize(width: cgImage.width, height: cgImage.height)
        let output: CGImage
        if let target, let scaled = scale(cgImage, width: target.width, height: target.height) {
            output = scaled
        } else {
            output = cgImage
        }
        guard let data = jpegData(from: output, quality: jpegQuality) else {
            return nil
        }
        return (data, output.width, output.height)
    }

    static func scale(_ image: CGImage, width: Int, height: Int) -> CGImage? {
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
        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return context.makeImage()
    }

    static func jpegData(from image: CGImage, quality: CGFloat) -> Data? {
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(
            data,
            UTType.jpeg.identifier as CFString,
            1,
            nil
        ) else {
            return nil
        }
        CGImageDestinationAddImage(
            destination,
            image,
            [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary
        )
        guard CGImageDestinationFinalize(destination) else {
            return nil
        }
        return data as Data
    }
}
