#if DEBUG
import AVFoundation
import Foundation
import SwiftUI

@MainActor
final class AudioSpikeViewModel: ObservableObject {
    @Published private(set) var level: Int = 0
    @Published private(set) var status = "stopped"
    @Published private(set) var lastError: String?
    @Published private(set) var voiceProcessing = "unknown"
    @Published private(set) var captureSource: TranscribeAudioSource = .voice
    @Published private(set) var pipelineStatus = "pipeline=none"
    @Published var muted = false {
        didSet { capture.setMuted(muted) }
    }
    @Published var loopback = false {
        didSet { state.setLoopback(loopback) }
    }

    private let state = SpikeState()
    private let session: AudioSessionController
    private let playback: AudioPlaybackController
    private let capture: AudioCaptureController
    private let interrupt: InterruptCoordinator
    private let haptics = HapticController()
    private var started = false

    init() {
        let session = AudioSessionController()
        let state = self.state
        let engine = session.engine
        let playback = AudioPlaybackController(
            playerNode: session.playbackNode,
            engineIsRunning: { engine.isRunning }
        )
        self.session = session
        self.playback = playback
        self.capture = AudioCaptureController(engine: engine) { samples, muted in
            state.recordUplink()
            guard !muted, state.shouldLoopback else { return }
            playback.pushTestSamples(samples)
        }
        self.interrupt = InterruptCoordinator(
            sendInterrupt: { state.recordInterrupt() },
            haptics: haptics
        )
        capture.onLevel = { [weak self] level in
            self?.level = level
        }
        capture.onFailure = { [weak self] error in
            self?.lastError = error.logDetail
            self?.status = "failed"
        }
    }

    func start() {
        guard !started else { return }
        lastError = nil

        let permission = AVAudioSession.sharedInstance().recordPermission
        guard permission == .granted else {
            requestPermission()
            return
        }

        do {
            state.reset()
            try session.activate(captureSource: captureSource)
            if session.voiceProcessingEnabled {
                voiceProcessing = "enabled"
            } else {
                voiceProcessing = captureSource == .voice ? "unavailable" : "disabled"
            }
            try capture.start()
            do {
                try session.startEngine()
            } catch {
                // capture.start() defers transient engine failures to its
                // retry loop; do not turn that state into a failed spike.
                guard capture.lifecycleState == .recovering else { throw error }
            }
            playback.start()
            interrupt.start()
            started = true
            status = "capturing"
            updatePipelineStatus()
        } catch {
            capture.stop()
            playback.stop()
            session.stopEngine()
            session.deactivate()
            started = false
            lastError = error.localizedDescription
            status = "failed"
            updatePipelineStatus()
        }
    }

    func stop() {
        guard started else { return }
        capture.stop()
        playback.stop()
        session.stopEngine()
        session.deactivate()
        interrupt.stop()
        started = false
        status = "stopped"
        level = 0
        updatePipelineStatus()
    }

    func playTone() {
        queueTone(seconds: 1)
        status = "tone queued"
    }

    func playLongTone() {
        queueTone(seconds: 10)
        status = "long tone queued"
    }

    func playShortBlip() {
        let frame = Array(repeating: Int16(1_000), count: 320)
        playback.setTTSInputExpected(false)
        playback.pushTestSamples(frame)
        playback.pushTestSamples(frame)
        status = "short blip queued"
    }

    private func queueTone(seconds: Int) {
        playback.setTTSInputExpected(true)
        let sampleCount = 16_000 * seconds
        var sequence: [Int16] = []
        sequence.reserveCapacity(sampleCount)
        for index in 0..<sampleCount {
            let value = sin(2.0 * Double.pi * 440.0 * Double(index) / 16_000.0)
            sequence.append(Int16(value * 8_000))
        }
        for offset in stride(from: 0, to: sequence.count, by: 320) {
            playback.pushTestSamples(Array(sequence[offset..<min(offset + 320, sequence.count)]))
        }
    }

    func simulateInterrupt() {
        interrupt.update(sessionState: .speaking, vadState: .speech)
        interrupt.update(sessionState: .speaking, vadState: .silence)
        interrupt.update(sessionState: .speaking, vadState: .speech)
        status = "interrupt simulated interrupts=\(state.counters.interrupts)"
    }

    func toggleCaptureSource() {
        let next: TranscribeAudioSource = captureSource == .voice ? .original : .voice
        let wasStarted = started
        if wasStarted {
            stop()
        }
        captureSource = next
        if wasStarted {
            start()
        } else {
            status = "source=\(next.rawValue)"
        }
    }

    func refreshStats() {
        let stats = playback.stats()
        let debug = playback.debugState()
        let counters = state.counters
        let captureStats = capture.debugStats()
        status = "uplink=\(counters.uplink) interrupts=\(counters.interrupts) taps=\(captureStats.taps) samples=\(captureStats.samples) empty=\(captureStats.empty) convErr=\(captureStats.errors) underrun=\(stats.underruns) scheduled=\(stats.scheduled) buffered=\(stats.buffered) pending=\(stats.pending) player=\(debug.isPlaying ? "playing" : "stopped")"
        updatePipelineStatus()
    }

    private func updatePipelineStatus() {
        let pipeline = started ? "full" : "none"
        let vp = session.voiceProcessingEnabled ? "enabled" : "disabled"
        let audioSession = AVAudioSession.sharedInstance()
        let input = audioSession.isInputAvailable ? "available" : "unavailable"
        let engine = session.isEngineRunning ? "running" : "stopped"
        let player = playback.isRunning ? "running" : "stopped"
        let route = audioSession.currentRoute.outputs.first?.portType.rawValue ?? "none"
        pipelineStatus = "pipeline=\(pipeline) apply=\(session.lastApplyPath.rawValue) category=\(audioSession.category.rawValue) mode=\(audioSession.mode.rawValue) engine=\(engine) player=\(player) capture=\(capture.lifecycleState) vp=\(vp) input=\(input) route=\(route)"
    }

    private func requestPermission() {
        // The TCC callback runs off the main thread; the @Sendable annotation
        // keeps this closure from inheriting @MainActor isolation.
        AVAudioSession.sharedInstance().requestRecordPermission { @Sendable [weak self] granted in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if granted {
                    self.start()
                } else {
                    self.lastError = "Microphone permission denied"
                    self.status = "permission denied"
                }
            }
        }
    }
}

private final class SpikeState: @unchecked Sendable {
    struct Counters {
        var uplink: UInt64 = 0
        var interrupts: UInt64 = 0
    }

    private let lock = NSLock()
    private var loopback = false
    private var uplink: UInt64 = 0
    private var interrupts: UInt64 = 0

    var shouldLoopback: Bool {
        lock.lock()
        defer { lock.unlock() }
        return loopback
    }

    var counters: Counters {
        lock.lock()
        defer { lock.unlock() }
        return Counters(uplink: uplink, interrupts: interrupts)
    }

    func setLoopback(_ enabled: Bool) {
        lock.lock()
        loopback = enabled
        lock.unlock()
    }

    func recordUplink() {
        lock.lock()
        uplink &+= 1
        lock.unlock()
    }

    func recordInterrupt() {
        lock.lock()
        interrupts &+= 1
        lock.unlock()
    }

    func reset() {
        lock.lock()
        uplink = 0
        interrupts = 0
        lock.unlock()
    }
}

@MainActor
struct AudioSpikeView: View {
    @StateObject private var model = AudioSpikeViewModel()

    var body: some View {
        VStack(spacing: 16) {
            Text("Audio Spike")
                .font(.title.bold())

            Text(model.status)
                .font(.caption)
                .foregroundColor(.secondary)
                .accessibilityIdentifier("audio_spike_status")

            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.gray.opacity(0.25))
                    Capsule()
                        .fill(Color.accentColor)
                        .frame(
                            width: proxy.size.width
                                * CGFloat(model.level) / CGFloat(MicLevelMeter.levels)
                        )
                }
            }
            .frame(height: 12)

            HStack {
                Button("Start") { model.start() }
                    .accessibilityIdentifier("audio_spike_start")
                Button("Stop") { model.stop() }
                    .accessibilityIdentifier("audio_spike_stop")
                Button(model.muted ? "Unmute" : "Mute") {
                    model.muted.toggle()
                }
                .accessibilityIdentifier("audio_spike_mute")
            }
            .buttonStyle(.bordered)

            Toggle("Loopback (mic to speaker)", isOn: $model.loopback)
            Toggle("Muted", isOn: $model.muted)

            Button("Play 440 Hz tone") { model.playTone() }
                .accessibilityIdentifier("audio_spike_tone")
            Button("Play 10 s tone") { model.playLongTone() }
                .accessibilityIdentifier("audio_spike_long_tone")
            Button("Play 40 ms blip") { model.playShortBlip() }
                .accessibilityIdentifier("audio_spike_short_blip")
            Button("Simulate speaking + VAD") { model.simulateInterrupt() }
                .accessibilityIdentifier("audio_spike_interrupt")
            Button("Refresh playback stats") { model.refreshStats() }
                .accessibilityIdentifier("audio_spike_stats")
            Button("Toggle capture source") { model.toggleCaptureSource() }
                .accessibilityIdentifier("audio_spike_source")

            if let lastError = model.lastError {
                Text(lastError)
                    .font(.caption)
                    .foregroundColor(.red)
                    .accessibilityIdentifier("audio_spike_error")
            }

            Text(model.pipelineStatus)
                .font(.caption2)
                .foregroundColor(.secondary)
                .accessibilityIdentifier("audio_spike_pipeline")
            Text("AEC: \(model.voiceProcessing)")
                .font(.caption2)
                .foregroundColor(.secondary)
                .accessibilityIdentifier("audio_spike_aec")
            Text("Source: \(model.captureSource.rawValue)")
                .font(.caption2)
                .foregroundColor(.secondary)
                .accessibilityIdentifier("audio_spike_source_status")

            Spacer()
        }
        .padding(24)
        .onDisappear { model.stop() }
    }
}
#endif
