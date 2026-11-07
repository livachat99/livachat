import AVFoundation
import Combine
import Foundation

@MainActor
final class TimelineFlowModel: ObservableObject {
    let home: TimelineHomeViewModel
    let audioPlayer: MylifeAudioPlayer
    @Published private(set) var path: [TimelineRoute] = []

    let store: MylifeServicing

    init(
        store: MylifeServicing,
        mediaEngineProvider: @escaping @MainActor () -> AVAudioEngine? = { nil }
    ) {
        self.store = store
        self.audioPlayer = MylifeAudioPlayer(engineProvider: mediaEngineProvider) { url in
            try await store.downloadMedia(url: url)
        }
        self.home = TimelineHomeViewModel(store: store)
        store.startPolling()
    }

    func push(_ route: TimelineRoute) {
        path.append(route)
    }

    func pop() {
        guard let route = path.popLast() else { return }
        if case .audio = route {
            audioPlayer.stop()
        }
    }

    func stop() {
        store.stopPolling()
        audioPlayer.stop()
    }
}
