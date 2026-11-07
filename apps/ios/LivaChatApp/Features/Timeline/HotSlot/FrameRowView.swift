import SwiftUI

struct FrameRowView: View {
    let frames: [HotSlotEvent]
    let onFrameClick: (HotSlotEvent) -> Void

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            ForEach(frames) { frame in
                Button {
                    onFrameClick(frame)
                } label: {
                    MylifeSquareImage(url: frame.mediaUrl)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.plain)
                .frame(maxWidth: .infinity)
                .accessibilityLabel(L10n.t("chat_ai_image"))
            }
            if frames.count < 3 {
                ForEach(0..<(3 - frames.count), id: \.self) { _ in
                    Spacer().frame(maxWidth: .infinity)
                }
            }
        }
        .padding(.vertical, AppSpacing.xs)
        .frame(maxWidth: .infinity)
    }
}
