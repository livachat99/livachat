import AVFoundation
import Combine
import Foundation

@MainActor
final class MylifeAudioPlayer: NSObject, ObservableObject {
    struct State: Equatable {
        var key: String?
        var isPlaying = false
        var isBuffering = false
        var positionMs: Int64 = 0
        var durationMs: Int64 = 0
        var error: String?
    }

    @Published private(set) var state = State()

    private let download: (URL) async throws -> Data
    /// Resolves the live conversation engine at play time. Playing gallery
    /// audio through the same engine keeps it audible inside the
    /// voice-processing session; a standalone AVAudioPlayer is silent there.
    /// Resolved lazily so a timeline opened while the runtime is still
    /// starting (or after an engine restart) still finds the active engine.
    private let engineProvider: @MainActor () -> AVAudioEngine?
    private var player: AVAudioPlayer?
    private var mediaNode: AVAudioPlayerNode?
    private var mediaNodeEngine: AVAudioEngine?
    private var mediaFile: AVAudioFile?
    private var mediaFileURL: URL?
    private var currentURL: URL?
    private var mediaFormat: AVAudioFormat?
    private var mediaGeneration: UInt64 = 0
    private var mediaBaseMs: Int64 = 0
    private var downloadTask: Task<Void, Never>?
    private var ticker: Task<Void, Never>?
    private var pendingSeekMs: Int64?
    private var shouldResumeAfterInterruption = false
    private var fallbackMigrationAttempted = false

    convenience init(engine: AVAudioEngine? = nil, download: @escaping (URL) async throws -> Data) {
        self.init(engineProvider: { engine }, download: download)
    }

    init(
        engineProvider: @escaping @MainActor () -> AVAudioEngine?,
        download: @escaping (URL) async throws -> Data
    ) {
        self.engineProvider = engineProvider
        self.download = download
        super.init()
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleInterruption(_:)),
            name: AVAudioSession.interruptionNotification,
            object: AVAudioSession.sharedInstance()
        )
    }

    func toggle(key: String, url: URL, startAtMs: Int64? = nil) {
        fallbackMigrationAttempted = false
        if state.key != key {
            start(key: key, url: url, positionMs: startAtMs ?? 0)
            return
        }
        if state.error != nil {
            start(key: key, url: url, positionMs: startAtMs ?? 0)
            return
        }
        if state.isPlaying {
            pause()
            return
        }
        if state.isBuffering {
            if let startAtMs { pendingSeekMs = startAtMs }
            return
        }
        if let engine = engineProvider(), engine.isRunning {
            if let mediaNode, mediaFile != nil, mediaNodeEngine === engine {
                _ = resumeEnginePlayback(node: mediaNode)
                return
            }
            // A live engine is available: reload a finished engine track or
            // migrate a silent AVAudioPlayer at its current position.
            let resumeMs = startAtMs
                ?? player.map { Int64($0.currentTime * 1000) }
                ?? state.positionMs
            start(key: key, url: url, positionMs: resumeMs)
            return
        }
        if let player {
            if let startAtMs {
                player.currentTime = clampedSeconds(startAtMs, duration: player.duration)
                state.positionMs = Int64(player.currentTime * 1000)
            }
            ensureSessionActive()
            player.play()
            state.isPlaying = true
            state.error = nil
            startTicker()
            return
        }
        start(key: key, url: url, positionMs: startAtMs ?? state.positionMs)
    }

    func seek(to ms: Int64) {
        let target = max(0, ms)
        state.positionMs = target
        if let player {
            player.currentTime = clampedSeconds(target, duration: player.duration)
            return
        }
        if let mediaNode, mediaFile != nil {
            if let engine = engineProvider(), engine.isRunning, mediaNodeEngine === engine {
                let wasPlaying = state.isPlaying
                mediaBaseMs = clampedMs(target, durationMs: state.durationMs)
                state.positionMs = mediaBaseMs
                mediaGeneration &+= 1
                if scheduleMediaSegment(fromMs: mediaBaseMs, generation: mediaGeneration),
                   wasPlaying {
                    mediaNode.play()
                }
            } else {
                // The engine stopped while paused; drop the stale schedule
                // and let the next play reload at the chosen position.
                releaseMediaFile()
            }
            return
        }
        if state.isBuffering {
            pendingSeekMs = target
        }
    }

    func pause() {
        if let player {
            player.pause()
            state.positionMs = Int64(player.currentTime * 1000)
        } else if let mediaNode {
            if mediaFile != nil,
               let engine = engineProvider(), engine.isRunning,
               mediaNodeEngine === engine {
                if let position = currentMediaPositionMs() {
                    state.positionMs = position
                }
                mediaNode.pause()
            } else {
                // Pausing without a live engine: drop the stale schedule so
                // the next play reloads from the saved position.
                releaseMediaFile()
            }
        }
        state.isPlaying = false
        stopTicker()
    }

    func resume() {
        if let engine = engineProvider(), engine.isRunning {
            if let mediaNode, mediaFile != nil, mediaNodeEngine === engine {
                _ = resumeEnginePlayback(node: mediaNode)
                return
            }
            // Resume on the live engine instead of a now-silent AVAudioPlayer.
            if let key = state.key, let currentURL {
                start(key: key, url: currentURL, positionMs: state.positionMs)
            }
            return
        }
        if let player {
            ensureSessionActive()
            player.play()
            state.isPlaying = true
            state.error = nil
            startTicker()
            return
        }
        if let key = state.key, let currentURL {
            start(key: key, url: currentURL, positionMs: state.positionMs)
        }
    }

    func stop() {
        downloadTask?.cancel()
        downloadTask = nil
        stopTicker()
        player?.stop()
        player = nil
        releaseMediaFile()
        detachMediaNode()
        pendingSeekMs = nil
        shouldResumeAfterInterruption = false
        fallbackMigrationAttempted = false
        currentURL = nil
        state = State()
    }

    // MARK: - Start / download

    private func start(key: String, url: URL, positionMs: Int64) {
        downloadTask?.cancel()
        stopTicker()
        player?.stop()
        player = nil
        releaseMediaFile()
        pendingSeekMs = positionMs > 0 ? positionMs : nil
        currentURL = url
        state = State(key: key, isBuffering: true, positionMs: positionMs)
        AppLog.info(
            "MylifeAudio",
            "start key=\(key) file=\(url.lastPathComponent) m4a=\(url.query?.contains("format=m4a") == true) at=\(positionMs)"
        )

        downloadTask = Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let data = try await self.download(url)
                try Task.checkCancellation()
                guard self.state.key == key else { return }
                AppLog.info("MylifeAudio", "downloaded bytes=\(data.count)")
                let target = self.pendingSeekMs ?? positionMs
                if try self.startOnEngine(data: data, startMs: target, key: key) {
                    return
                }
                self.ensureSessionActive()
                let player = try AVAudioPlayer(data: data)
                player.delegate = self
                player.prepareToPlay()
                player.currentTime = self.clampedSeconds(target, duration: player.duration)
                player.play()
                self.player = player
                self.pendingSeekMs = nil
                self.state = State(
                    key: key,
                    isPlaying: true,
                    isBuffering: false,
                    positionMs: Int64(player.currentTime * 1000),
                    durationMs: Int64(player.duration * 1000),
                    error: nil
                )
                self.startTicker()
            } catch {
                AppLog.error("MylifeAudio", "start failed: \(error.localizedDescription)")
                guard !Task.isCancelled, !isMylifeCancellation(error),
                      self.state.key == key else { return }
                self.state.isBuffering = false
                self.state.isPlaying = false
                self.state.error = L10n.t("player_play_failed")
            }
        }
    }

    // MARK: - Engine-backed playback

    /// Returns false when the conversation engine is unavailable so the caller
    /// can fall back to a standalone AVAudioPlayer.
    private func startOnEngine(data: Data, startMs: Int64, key: String) throws -> Bool {
        guard let engine = engineProvider(), engine.isRunning else { return false }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("livachat-audio-\(UUID().uuidString).m4a")
        try data.write(to: url, options: .atomic)
        do {
            let file = try AVAudioFile(forReading: url)
            let node: AVAudioPlayerNode
            if let mediaNode, mediaNodeEngine === engine {
                node = mediaNode
            } else {
                detachMediaNode()
                node = AVAudioPlayerNode()
                engine.attach(node)
                mediaNode = node
                mediaNodeEngine = engine
            }
            let format = file.processingFormat
            if mediaFormat?.isEqual(format) != true {
                engine.disconnectNodeOutput(node)
                engine.connect(node, to: engine.mainMixerNode, format: format)
                mediaFormat = format
            }
            mediaFileURL = url
            mediaFile = file
            let durationMs = Int64(Double(file.length) / format.sampleRate * 1000)
            mediaBaseMs = clampedMs(startMs, durationMs: durationMs)
            mediaGeneration &+= 1
            guard scheduleMediaSegment(fromMs: mediaBaseMs, generation: mediaGeneration) else {
                // Seeking to (or past) the end: the finished state is already
                // committed; do not overwrite it with a phantom "playing".
                pendingSeekMs = nil
                return true
            }
            try? AVAudioSession.sharedInstance().setActive(true)
            node.play()
            pendingSeekMs = nil
            state = State(
                key: key,
                isPlaying: true,
                isBuffering: false,
                positionMs: mediaBaseMs,
                durationMs: durationMs,
                error: nil
            )
            AppLog.info(
                "MylifeAudio",
                "engine playback format=\(format.sampleRate)Hz/\(format.channelCount)ch duration=\(durationMs)"
            )
            startTicker()
            return true
        } catch {
            try? FileManager.default.removeItem(at: url)
            throw error
        }
    }

    /// Reschedules from the paused position instead of trusting the node's
    /// retained schedule. AVAudioEngine can be stopped and restarted under us
    /// (background, route change, interruption); rescheduling keeps resume
    /// correct in all of those cases.
    @discardableResult
    private func resumeEnginePlayback(node: AVAudioPlayerNode) -> Bool {
        guard let engine = engineProvider(), engine.isRunning,
              mediaNodeEngine === engine, mediaFile != nil else {
            return false
        }
        mediaBaseMs = clampedMs(state.positionMs, durationMs: state.durationMs)
        mediaGeneration &+= 1
        guard scheduleMediaSegment(fromMs: mediaBaseMs, generation: mediaGeneration) else {
            return false
        }
        try? AVAudioSession.sharedInstance().setActive(true)
        node.play()
        state.isPlaying = true
        state.error = nil
        startTicker()
        return true
    }

    /// Returns false when playback already finished at the requested position;
    /// the caller must then not start the node or commit a playing state.
    @discardableResult
    private func scheduleMediaSegment(fromMs: Int64, generation: UInt64) -> Bool {
        guard let node = mediaNode, let file = mediaFile else { return false }
        node.stop()
        let format = file.processingFormat
        let totalFrames = file.length
        let requested = AVAudioFramePosition(
            (Double(fromMs) / 1000 * format.sampleRate).rounded()
        )
        let startFrame = min(max(requested, 0), totalFrames)
        let remaining = totalFrames - startFrame
        guard remaining > 0 else {
            handleMediaFinished(generation: generation)
            return false
        }
        node.scheduleSegment(
            file,
            startingFrame: startFrame,
            frameCount: AVAudioFrameCount(remaining),
            at: nil,
            completionCallbackType: .dataPlayedBack
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.mediaGeneration == generation else { return }
                self.handleMediaFinished(generation: generation)
            }
        }
        return true
    }

    private func currentMediaPositionMs() -> Int64? {
        guard let node = mediaNode,
              let nodeTime = node.lastRenderTime,
              let playerTime = node.playerTime(forNodeTime: nodeTime) else {
            return nil
        }
        let elapsedMs = Double(playerTime.sampleTime) / playerTime.sampleRate * 1000
        return min(mediaBaseMs + Int64(elapsedMs), state.durationMs)
    }

    private func handleMediaFinished(generation: UInt64) {
        guard mediaGeneration == generation else { return }
        AppLog.info("MylifeAudio", "engine playback finished")
        stopTicker()
        state.isPlaying = false
        state.isBuffering = false
        state.positionMs = 0
        // The completion handler must not stop the node (AVAudioPlayerNode can
        // deadlock unscheduling from its own callback); it is already drained.
        releaseMediaFile(stopNode: false)
    }

    private func releaseMediaFile(stopNode: Bool = true) {
        mediaGeneration &+= 1
        if stopNode {
            mediaNode?.stop()
        }
        mediaFile = nil
        if let mediaFileURL {
            try? FileManager.default.removeItem(at: mediaFileURL)
        }
        mediaFileURL = nil
    }

    private func detachMediaNode() {
        mediaGeneration &+= 1
        guard let node = mediaNode else { return }
        node.stop()
        mediaNodeEngine?.disconnectNodeOutput(node)
        mediaNodeEngine?.detach(node)
        mediaNode = nil
        mediaNodeEngine = nil
        mediaFormat = nil
    }

    private func clampedMs(_ ms: Int64, durationMs: Int64) -> Int64 {
        guard durationMs > 0 else { return max(0, ms) }
        return min(max(ms, 0), durationMs)
    }

    private func clampedSeconds(_ ms: Int64, duration: TimeInterval) -> TimeInterval {
        guard duration > 0 else { return 0 }
        return min(max(Double(ms) / 1000, 0), duration)
    }

    private func ensureSessionActive() {
        let session = AVAudioSession.sharedInstance()
        // Never hijack a live conversation session. The conversation keeps
        // `.playAndRecord` configured even while its engine is stopped
        // (background-muted teardown or capture retry); flipping the shared
        // session to `.playback` here would make the capture retry fail and
        // tear down the conversation. The ticker migrates this playback to
        // the conversation engine once it is running.
        if session.category == .playAndRecord {
            try? session.setActive(true)
            return
        }
        // Without a live conversation there is no voice-processing session to
        // respect; use media playback so the silent switch does not mute it.
        if engineProvider()?.isRunning != true, session.category != .playback {
            try? session.setCategory(.playback, mode: .default, options: [])
        }
        try? session.setActive(true)
    }

    // MARK: - Ticker

    private func startTicker() {
        stopTicker()
        ticker = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self, self.state.isPlaying else { return }
                if let player = self.player {
                    // The voice-processing session silences AVAudioPlayer;
                    // switch to the live engine as soon as it is available.
                    if !self.fallbackMigrationAttempted,
                       let key = self.state.key, let url = self.currentURL,
                       let engine = self.engineProvider(), engine.isRunning {
                        // One attempt per user action: if the engine is
                        // flickering, do not keep restarting the download.
                        self.fallbackMigrationAttempted = true
                        self.start(
                            key: key,
                            url: url,
                            positionMs: Int64(player.currentTime * 1000)
                        )
                        return
                    }
                    self.state.positionMs = Int64(player.currentTime * 1000)
                    if player.duration > 0 {
                        self.state.durationMs = Int64(player.duration * 1000)
                    }
                } else if self.mediaNode != nil {
                    guard let engine = self.engineProvider(), engine.isRunning,
                          self.mediaNodeEngine === engine else {
                        // The engine stopped under us; keep the last known
                        // position and let the next play reload.
                        self.state.isPlaying = false
                        self.releaseMediaFile()
                        return
                    }
                    if let position = self.currentMediaPositionMs() {
                        self.state.positionMs = position
                    }
                }
                try? await Task.sleep(nanoseconds: 200_000_000)
            }
        }
    }

    private func stopTicker() {
        ticker?.cancel()
        ticker = nil
    }

    private func handleEnded() {
        AppLog.info("MylifeAudio", "finished time=\(player?.currentTime ?? -1) duration=\(player?.duration ?? -1)")
        player?.pause()
        player?.currentTime = 0
        stopTicker()
        state.isPlaying = false
        state.isBuffering = false
        state.positionMs = 0
    }

    // MARK: - Interruptions

    @objc private nonisolated func handleInterruption(_ notification: Notification) {
        let typeRaw = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
        let optionsRaw = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt
        Task { @MainActor [weak self] in
            guard let self, let typeRaw,
                  let type = AVAudioSession.InterruptionType(rawValue: typeRaw) else { return }
            switch type {
            case .began:
                AppLog.info("MylifeAudio", "interruption began wasPlaying=\(self.state.isPlaying)")
                self.shouldResumeAfterInterruption = self.state.isPlaying
                self.pause()
            case .ended:
                let options = AVAudioSession.InterruptionOptions(rawValue: optionsRaw ?? 0)
                if self.shouldResumeAfterInterruption, options.contains(.shouldResume) {
                    self.shouldResumeAfterInterruption = false
                    self.resume()
                }
            @unknown default:
                break
            }
        }
    }
}

extension MylifeAudioPlayer: AVAudioPlayerDelegate {
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        let reported = ObjectIdentifier(player)
        Task { @MainActor [weak self] in
            guard let self, self.player.map(ObjectIdentifier.init) == reported else { return }
            self.handleEnded()
        }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        let reported = ObjectIdentifier(player)
        AppLog.error("MylifeAudio", "decode error: \(error?.localizedDescription ?? "nil")")
        Task { @MainActor [weak self] in
            guard let self, self.player.map(ObjectIdentifier.init) == reported else { return }
            self.stopTicker()
            self.state.isPlaying = false
            self.state.isBuffering = false
            self.state.error = L10n.t("player_play_failed")
        }
    }
}
