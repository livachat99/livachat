import SwiftUI

struct AudioEntryButton: View {
    let count: Int
    let onClick: () -> Void

    private var accessibilityLabel: String {
        L10n.plural("mylife_audio_entry_desc", count)
    }

    var body: some View {
        Button(action: onClick) {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "waveform")
                    .font(.system(size: 18, weight: .regular))
                    .foregroundColor(MylifePalette.gold)
                    .padding(.horizontal, AppSpacing.s)
                    .padding(.vertical, 3)
                    .background(MylifePalette.panel.opacity(0.8))
                    .clipShape(Circle())
                    .accessibilityHidden(true)
                Text(LocalizedNumber.integer(count, locale: AppLocale.current))
                    .mylifeFont(.label)
                    .foregroundColor(MylifePalette.dust)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel)
        .accessibilityAddTraits(.isButton)
        .accessibilityIdentifier("timeline_audio_entry")
    }
}
