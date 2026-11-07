import CoreGraphics
import SwiftUI

struct AvatarView: View {
    let image: CGImage?
    let mode: ActiveConversationMode?
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            ZStack {
                avatarCircle(size: 96)
                if let image {
                    Image(decorative: image, scale: 1)
                        .resizable()
                        .scaledToFill()
                        .frame(width: 96, height: 96)
                        .clipShape(Circle())
                } else {
                    Text("~")
                        .foregroundColor(.white)
                        .appFont(.headline)
                }
            }
            .frame(width: 96, height: 96)
            .overlay(alignment: .topTrailing) {
                ModeBadgeView(mode: mode, size: 26, iconSize: 14)
                    .allowsHitTesting(false)
            }
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(avatarAccessibilityLabel)
        .accessibilityIdentifier("conversation_avatar")
    }

    private var avatarAccessibilityLabel: String {
        let title = L10n.t("conv_avatar_picker_title")
        return LocalizedList.join([title, mode.map(ModeBadgeView.accessibilityName)])
    }

    private func avatarCircle(size: CGFloat) -> some View {
        Circle()
            .fill(
                LinearGradient(
                    colors: [AppColor.brandIndigo, AppColor.brandViolet],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            )
            .frame(width: size, height: size)
    }
}

struct AvatarTitleBarButton: View {
    let image: CGImage?
    let mode: ActiveConversationMode?
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [AppColor.brandIndigo, AppColor.brandViolet],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: 36, height: 36)
                    .overlay {
                        if let image {
                            Image(decorative: image, scale: 1)
                                .resizable()
                                .scaledToFill()
                                .frame(width: 36, height: 36)
                                .clipShape(Circle())
                        } else {
                            Text("~")
                                .foregroundColor(.white)
                                .appFont(.headline)
                        }
                    }
                    .clipShape(Circle())
            }
            .frame(width: 44, height: 44)
            .overlay(alignment: .bottomTrailing) {
                ModeBadgeView(mode: mode, size: 20, iconSize: 11)
                    .allowsHitTesting(false)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(avatarAccessibilityLabel)
        .accessibilityIdentifier("conversation_avatar_hidden")
    }

    private var avatarAccessibilityLabel: String {
        let title = L10n.t("conv_avatar_picker_title")
        return LocalizedList.join([title, mode.map(ModeBadgeView.accessibilityName)])
    }
}

struct ModeBadgeView: View {
    let mode: ActiveConversationMode?
    let size: CGFloat
    let iconSize: CGFloat

    var body: some View {
        if let mode {
            Image(systemName: Self.symbolName(mode))
                .font(.system(size: iconSize, weight: .semibold))
                .foregroundColor(AppColor.textPrimary)
                .frame(width: size, height: size)
                .background(AppColor.brandIndigo.opacity(0.7))
                .clipShape(Circle())
                .overlay {
                    Circle()
                        .stroke(AppColor.surface, lineWidth: 1.5)
                }
                .accessibilityHidden(true)
        }
    }

    static func symbolName(_ mode: ActiveConversationMode) -> String {
        switch mode {
        case .dialogue, .lecture:
            return "character.bubble"
        case .transcribe:
            return "captions.bubble"
        case .omni:
            return "sparkles"
        }
    }

    static func accessibilityName(_ mode: ActiveConversationMode) -> String {
        switch mode {
        case .dialogue, .lecture:
            return L10n.t("conv_mode_translate")
        case .transcribe:
            return L10n.t("conv_mode_transcribe")
        case .omni:
            return L10n.t("conv_mode_omni")
        }
    }
}
