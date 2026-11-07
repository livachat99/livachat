import Combine
import SwiftUI

struct AudioGalleryView: View {
    @StateObject private var viewModel: AudioGalleryViewModel
    let label: String
    let onBack: () -> Void
    let onOpenEventList: (Int64, Bool) -> Void

    @Environment(\.scenePhase) private var scenePhase

    init(
        store: MylifeServicing,
        player: MylifeAudioPlayer,
        realtimeSpeaking: AnyPublisher<Bool, Never>,
        date: String,
        label: String,
        count: Int,
        pending: Bool,
        onBack: @escaping () -> Void,
        onOpenEventList: @escaping (Int64, Bool) -> Void
    ) {
        _viewModel = StateObject(wrappedValue: AudioGalleryViewModel(
            store: store,
            player: player,
            realtimeSpeaking: realtimeSpeaking,
            date: date,
            count: count,
            pending: pending
        ))
        self.label = label
        self.onBack = onBack
        self.onOpenEventList = onOpenEventList
    }

    var body: some View {
        ZStack {
            MylifePalette.abyss.ignoresSafeArea()
            VStack(spacing: 0) {
                header
                content
            }
        }
        .preferredColorScheme(.dark)
        .onChange(of: scenePhase) { phase in
            if phase == .background {
                viewModel.pauseForBackground()
            }
        }
    }

    private var header: some View {
        HStack(spacing: AppSpacing.s) {
            Button(action: onBack) {
                Image(systemName: "chevron.backward")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundColor(MylifePalette.dust)
                    .frame(width: 44, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L10n.t("common_back"))
            .accessibilityIdentifier("timeline_audio_back")

            Text(label)
                .mylifeFont(.subpageTitle)
                .foregroundColor(MylifePalette.gold)
                .lineLimit(2)
                .truncationMode(.tail)
                .accessibilityIdentifier("timeline_audio_title")

            Spacer()

            if viewModel.pending {
                Text(L10n.t("mylife_analyzing"))
                    .mylifeFont(.label)
                    .foregroundColor(MylifePalette.gold.opacity(0.8))
                    .padding(.trailing, AppSpacing.xs)
            }

            Text(L10n.plural("mylife_segments_count", viewModel.headerCount))
                .mylifeFont(.label)
                .foregroundColor(MylifePalette.dust)
                .padding(.trailing, AppSpacing.s)
                .accessibilityIdentifier("timeline_audio_count")
        }
        .padding(.horizontal, AppSpacing.s)
        .frame(minHeight: 52)
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.state {
        case .loading:
            MylifeLoadingHint(text: L10n.t("mylife_loading"))
        case .empty:
            MylifeEmptyState(
                title: L10n.t("mylife_audio_empty_title"),
                subtitle: L10n.t("mylife_analysis_pending"),
                icon: "waveform"
            )
        case let .error(message):
            MylifeErrorHint(message: message) { viewModel.retry() }
        case let .data(segments, _, _, loadingMore):
            list(segments: segments, loadingMore: loadingMore)
        }
    }

    private func list(segments: [AudioSegment], loadingMore: Bool) -> some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(Array(segments.enumerated()), id: \.element.id) { index, segment in
                    AudioSegmentCard(
                        segment: segment,
                        timeLabel: MylifeDateLabels.timeLabel(tsMs: segment.tsMs),
                        player: viewModel.playerState,
                        scrubMs: viewModel.scrubRef == segment.eventRef ? viewModel.scrubMs : nil,
                        onToggle: { viewModel.onToggle(segment) },
                        onScrub: { viewModel.onScrub(segment, ms: $0) },
                        onScrubEnd: { viewModel.onScrubEnd(segment, ms: $0) },
                        onTimeClick: {
                            onOpenEventList(segment.tsMs, viewModel.pending)
                        }
                    )
                    .onAppear {
                        if index >= segments.count - 4 {
                            viewModel.loadMore()
                        }
                    }
                }
                if loadingMore {
                    MylifeLoadingHint(text: L10n.t("mylife_loading"))
                        .frame(height: 44)
                }
            }
            .padding(.bottom, AppSpacing.xl)
        }
    }
}
