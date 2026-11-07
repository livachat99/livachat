import SwiftUI

struct PeriodCard: View {
    let digest: PeriodDigest
    let onBodyClick: (() -> Void)?
    let onHotSlotClick: (MylifeHotSlot) -> Void
    let onImageClick: (PeriodImage) -> Void
    var onTopicClick: ((MylifeDayTopic) -> Void)? = nil
    var onAudioClick: (() -> Void)? = nil

    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if digest.hasPending {
                HStack(spacing: AppSpacing.s) {
                    MylifePulsingDot()
                    Text(L10n.t("mylife_analyzing"))
                        .mylifeFont(.body)
                        .foregroundColor(MylifePalette.dust)
                }
                .padding(.bottom, AppSpacing.s)
            }

            titleRow

            if digest.hotSlots.isEmpty == false || audioEntryVisible {
                HStack(alignment: .top, spacing: AppSpacing.s) {
                    if digest.hotSlots.isEmpty {
                        Spacer(minLength: 0)
                    } else {
                        HotSlotChips(slots: digest.hotSlots, onSlotClick: onHotSlotClick)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    if audioEntryVisible, let onAudioClick {
                        AudioEntryButton(count: digest.audioCount, onClick: onAudioClick)
                    }
                }
                .padding(.top, AppSpacing.s)
            }

            summaryBlock

            if digest.images.isEmpty == false {
                imageBlock
            }

            if digest.topics.isEmpty == false {
                TopicChipRow(topics: digest.topics, onTopicClick: onTopicClick)
                    .padding(.top, AppSpacing.s)
            }
        }
        .padding(AppSpacing.m)
        .background(MylifePalette.panel.opacity(0.6))
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
        .contentShape(RoundedRectangle(cornerRadius: AppRadius.m))
        .modifier(PeriodCardBodyTap(enabled: onBodyClick != nil, action: onBodyClick))
        .padding(.horizontal, AppSpacing.m)
        .padding(.vertical, AppSpacing.xs)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("timeline_card_\(digest.key)")
    }

    private var audioEntryVisible: Bool {
        digest.audioCount > 0 && onAudioClick != nil
    }

    private var imageTotalText: String {
        LocalizedNumber.integer(digest.imageTotal, locale: AppLocale.current)
    }

    private var titleRow: some View {
        HStack(alignment: .firstTextBaseline, spacing: AppSpacing.s) {
            Text(digest.rangeLabel)
                .mylifeFont(.cardTitle)
                .foregroundColor(MylifePalette.star)
            if digest.granularity == .week {
                Text(MylifeDateLabels.weekNumberLabel(forWeekKey: digest.key))
                    .mylifeFont(.label)
                    .foregroundColor(MylifePalette.dust)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private var summaryBlock: some View {
        let showAll = digest.granularity == .day || expanded
        let visible = showAll ? digest.summaryLines : Array(digest.summaryLines.prefix(digest.summaryLimit))
        ForEach(Array(visible.enumerated()), id: \.offset) { _, line in
            let text = Text(line)
                .mylifeFont(.body)
                .foregroundColor(MylifePalette.star)
                .lineLimit(showAll ? nil : 2)
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, AppSpacing.xs)
            if digest.granularity == .day {
                text
            } else {
                Button {
                    expanded.toggle()
                } label: {
                    text
                }
                .buttonStyle(.plain)
            }
        }

        let hidden = digest.summaryLines.count - digest.summaryLimit
        if digest.granularity != .day, hidden > 0, !expanded {
            Button {
                expanded = true
            } label: {
                Text(L10n.format("mylife_more_items", hidden))
                    .mylifeFont(.label)
                    .foregroundColor(MylifePalette.gold)
            }
            .buttonStyle(.plain)
            .padding(.top, AppSpacing.xs)
        }
    }

    private var imageBlock: some View {
        let showBadge = digest.imageTotal > digest.images.count
        let rows = chunk(digest.images, size: 3)
        return VStack(alignment: .leading, spacing: AppSpacing.xs) {
            if showBadge {
                Spacer().frame(height: 10)
            }
            ForEach(Array(rows.enumerated()), id: \.offset) { rowIndex, row in
                HStack(spacing: AppSpacing.xs) {
                    ForEach(Array(row.enumerated()), id: \.element.id) { index, image in
                        ZStack(alignment: .topTrailing) {
                            Button {
                                onImageClick(image)
                            } label: {
                                MylifeSquareImage(url: image.url)
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel(L10n.t("chat_ai_image"))
                            .accessibilityIdentifier("timeline_image_\(image.frameId)")

                            if showBadge, rowIndex == 0, index == row.count - 1 {
                                Button {
                                    onImageClick(image)
                                } label: {
                                    Text(imageTotalText)
                                        .mylifeFont(.smallLabel)
                                        .foregroundColor(MylifePalette.star)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 1)
                                        .background(Color.black.opacity(0.6))
                                        .clipShape(Capsule())
                                }
                                .buttonStyle(.plain)
                                // SwiftUI mirrors offset(x:) in RTL, which
                                // keeps the badge on the outer corner.
                                .offset(x: 10, y: -10)
                                .accessibilityLabel(imageTotalText)
                                .accessibilityIdentifier("timeline_image_badge")
                            }
                        }
                        .frame(maxWidth: .infinity)
                    }
                }
            }
        }
        .padding(.top, AppSpacing.s)
    }

    private func chunk<T>(_ values: [T], size: Int) -> [[T]] {
        guard size > 0 else { return [] }
        return stride(from: 0, to: values.count, by: size).map {
            Array(values[$0..<min($0 + size, values.count)])
        }
    }
}

private struct PeriodCardBodyTap: ViewModifier {
    let enabled: Bool
    let action: (() -> Void)?

    @ViewBuilder
    func body(content: Content) -> some View {
        if enabled, let action {
            content.onTapGesture(perform: action)
        } else {
            content
        }
    }
}
