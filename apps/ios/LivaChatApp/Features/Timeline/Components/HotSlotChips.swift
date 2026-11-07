import SwiftUI

struct HotSlotChips: View {
    let slots: [MylifeHotSlot]
    let onSlotClick: (MylifeHotSlot) -> Void

    var body: some View {
        MylifeFlowLayout(items: slots) { slot in
            Button {
                onSlotClick(slot)
            } label: {
                HStack(spacing: 2) {
                    Image(systemName: "chevron.forward")
                        .imageScale(.small)
                        .accessibilityHidden(true)
                    Text(MylifeDateLabels.timeLabel(tsMs: slot.startMs))
                }
                .mylifeFont(.label)
                .foregroundColor(MylifePalette.gold)
                .padding(.horizontal, AppSpacing.s)
                .padding(.vertical, 2)
                .background(MylifePalette.panel.opacity(0.8))
                .clipShape(Capsule())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(L10n.format("mylife_hot_slot_desc", MylifeDateLabels.timeLabel(tsMs: slot.startMs)))
            .accessibilityAddTraits(.isButton)
            .accessibilityIdentifier("timeline_hot_slot_chip_\(slot.startMs)")
        }
    }
}
