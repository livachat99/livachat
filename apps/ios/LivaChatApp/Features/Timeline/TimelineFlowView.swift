import AVFoundation
import Combine
import NetworkImage
import SwiftUI

struct TimelineFlowView: View {
    let store: MylifeServicing
    let serverBaseURL: String
    let realtimeSpeaking: AnyPublisher<Bool, Never>
    let imageLoader: DefaultNetworkImageLoader
    let onOpenLink: (URL) -> Void

    @Environment(\.dismiss) private var dismiss
    @StateObject private var flow: TimelineFlowModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    init(
        store: MylifeServicing,
        serverBaseURL: String,
        realtimeSpeaking: AnyPublisher<Bool, Never>,
        mediaEngineProvider: @escaping @MainActor () -> AVAudioEngine? = { nil },
        imageLoader: DefaultNetworkImageLoader,
        onOpenLink: @escaping (URL) -> Void
    ) {
        self.store = store
        self.serverBaseURL = serverBaseURL
        self.realtimeSpeaking = realtimeSpeaking
        self.imageLoader = imageLoader
        self.onOpenLink = onOpenLink
        _flow = StateObject(wrappedValue: TimelineFlowModel(
            store: store,
            mediaEngineProvider: mediaEngineProvider
        ))
    }

    var body: some View {
        ZStack {
            MylifePalette.abyss.ignoresSafeArea()
            TimelineStarfield().ignoresSafeArea()

            home
                .opacity(flow.path.isEmpty ? 1 : 0)
                .allowsHitTesting(flow.path.isEmpty)

            ForEach(Array(flow.path.enumerated()), id: \.element.id) { index, route in
                let isTop = index == flow.path.count - 1
                subpage(route)
                    .opacity(isTop ? 1 : 0)
                    .allowsHitTesting(isTop)
                    .accessibilityHidden(!isTop)
                    .transition(reduceMotion ? .identity : .move(edge: .trailing))
                    .zIndex(Double(index + 1))
            }
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: flow.path)
        .preferredColorScheme(.dark)
        .networkImageLoader(imageLoader)
        .onDisappear { flow.stop() }
    }

    private var home: some View {
        TimelineHomeView(
            viewModel: flow.home,
            isActive: flow.path.isEmpty,
            onBack: { dismiss() },
            onOpenEventList: { nav in
                flow.push(.eventList(tsMs: nav.tsMs, pending: nav.pending))
            },
            onOpenGallery: { nav in
                flow.push(.gallery(
                    from: nav.from,
                    to: nav.to,
                    label: nav.label,
                    focusFrameId: nav.focusFrameId,
                    pending: nav.pending
                ))
            },
            onOpenHotSlot: { nav in
                flow.push(.hotSlot(startMs: nav.startMs, endMs: nav.endMs, pending: nav.pending))
            },
            onOpenAudio: { nav in
                flow.push(.audio(date: nav.date, label: nav.label, count: nav.count, pending: nav.pending))
            }
        )
        .frame(maxWidth: 600)
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private func subpage(_ route: TimelineRoute) -> some View {
        switch route {
        case let .hotSlot(startMs, endMs, pending):
            HotSlotView(
                store: flow.store,
                startMs: startMs,
                endMs: endMs,
                pending: pending,
                serverBaseURL: serverBaseURL,
                onBack: { flow.pop() },
                onOpenLink: onOpenLink
            )
            .frame(maxWidth: 600)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(MylifePalette.abyss.ignoresSafeArea())
        case let .eventList(tsMs, pending):
            EventListView(
                store: flow.store,
                tsMs: tsMs,
                pending: pending,
                serverBaseURL: serverBaseURL,
                onBack: { flow.pop() },
                onOpenLink: onOpenLink
            )
            .frame(maxWidth: 600)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(MylifePalette.abyss.ignoresSafeArea())
        case let .gallery(from, to, label, focusFrameId, pending):
            FrameGalleryView(
                store: flow.store,
                from: from,
                to: to,
                label: label,
                focusFrameId: focusFrameId,
                pending: pending,
                onBack: { flow.pop() }
            )
            .frame(maxWidth: 600)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(MylifePalette.abyss.ignoresSafeArea())
        case let .audio(date, label, count, pending):
            AudioGalleryView(
                store: flow.store,
                player: flow.audioPlayer,
                realtimeSpeaking: realtimeSpeaking,
                date: date,
                label: label,
                count: count,
                pending: pending,
                onBack: { flow.pop() },
                onOpenEventList: { tsMs, pending in
                    flow.push(.eventList(tsMs: tsMs, pending: pending))
                }
            )
            .frame(maxWidth: 600)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(MylifePalette.abyss.ignoresSafeArea())
        }
    }

}
