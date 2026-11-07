import SwiftUI

func segmentBodyText(transcript: String?, summary: String?) -> String? {
    if let transcript, transcript.count > 5 { return transcript }
    return summary
}

func formatTimecode(_ ms: Int64, locale: Locale = AppLocale.current) -> String {
    LocalizedNumber.timecode(ms: ms, locale: locale)
}

struct AudioTimeHeader: View {
    let label: String
    let onTimeClick: (() -> Void)?

    var body: some View {
        HStack {
            Spacer()
            if let onTimeClick {
                Button(action: onTimeClick) {
                    pill
                        .frame(minHeight: 32)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(L10n.format("mylife_audio_time_desc", label))
                .accessibilityAddTraits(.isButton)
                .accessibilityIdentifier("timeline_audio_time_\(label)")
            } else {
                pill
            }
            Spacer()
        }
        .frame(minHeight: 32)
        .padding(.top, AppSpacing.s)
        .padding(.bottom, AppSpacing.xs)
    }

    private var pill: some View {
        HStack(spacing: AppSpacing.xs) {
            Text(label)
                .mylifeFont(.label)
                .foregroundColor(MylifePalette.dust)
            Image(systemName: "chevron.forward.2")
                .imageScale(.small)
                .foregroundColor(MylifePalette.gold)
                .accessibilityHidden(true)
        }
        .padding(.horizontal, AppSpacing.s)
        .padding(.vertical, 2)
        .background(MylifePalette.panel.opacity(0.8))
        .clipShape(Capsule())
    }
}

struct AudioSegmentCard: View {
    let segment: AudioSegment
    let timeLabel: String?
    let player: MylifeAudioPlayer.State
    let scrubMs: Int64?
    let onToggle: () -> Void
    let onScrub: (Int64) -> Void
    let onScrubEnd: (Int64) -> Void
    let onTimeClick: (() -> Void)?

    private var isActive: Bool { player.key == segment.eventRef }
    private var isPlaying: Bool { isActive && player.isPlaying }
    private var durationMs: Int64 {
        if isActive, player.durationMs > 0 { return player.durationMs }
        return segment.durationMs
    }
    private var positionMs: Int64 {
        if let scrubMs { return scrubMs }
        return isActive ? player.positionMs : 0
    }
    private var fraction: CGFloat {
        guard durationMs > 0 else { return 0 }
        return min(max(CGFloat(positionMs) / CGFloat(durationMs), 0), 1)
    }

    var body: some View {
        VStack(spacing: 0) {
            if let timeLabel {
                AudioTimeHeader(label: timeLabel, onTimeClick: onTimeClick)
            }
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: AppSpacing.s) {
                    Button(action: onToggle) {
                        ZStack {
                            Circle()
                                .fill(MylifePalette.panel.opacity(0.9))
                                .frame(width: 32, height: 32)
                            if isActive, player.isBuffering {
                                ProgressView()
                                    .progressViewStyle(CircularProgressViewStyle(tint: MylifePalette.gold))
                                    .scaleEffect(0.7)
                            } else {
                                Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                                    .font(.system(size: 16, weight: .medium))
                                    .foregroundColor(MylifePalette.gold)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(L10n.t(isPlaying ? "common_pause" : "common_play"))
                    .accessibilityIdentifier("timeline_audio_play_\(segment.eventRef)")

                    SegmentProgressBar(
                        fraction: fraction,
                        durationMs: durationMs,
                        envelope: segment.energy,
                        onScrub: onScrub,
                        onScrubEnd: onScrubEnd
                    )

                    Text(timecode)
                        .mylifeFont(.label)
                        .foregroundColor(MylifePalette.dust)
                        .lineLimit(1)
                        .environment(\.layoutDirection, .leftToRight)
                }

                if isActive, let error = player.error {
                    Text(error)
                        .mylifeFont(.caption)
                        .foregroundColor(MylifePalette.red)
                        .padding(.top, AppSpacing.xs)
                }

                if let body = segmentBodyText(transcript: segment.transcript, summary: segment.summary) {
                    Text(body)
                        .mylifeFont(.body)
                        .foregroundColor(MylifePalette.star)
                        .padding(.top, AppSpacing.s)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(AppSpacing.m)
            .background(MylifePalette.panel.opacity(0.6))
            .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
            .padding(.horizontal, AppSpacing.m)
            .padding(.vertical, AppSpacing.xs)
        }
    }

    private var timecode: String {
        if isActive, durationMs > 0 {
            return "\(formatTimecode(positionMs)) / \(formatTimecode(durationMs))"
        }
        return formatTimecode(durationMs)
    }
}
