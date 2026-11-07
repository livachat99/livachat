import SwiftUI

private struct AvatarSheetContentHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

private struct AvatarSheetHeaderHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

struct AvatarCustomizationSheet: View {
    @ObservedObject var model: AvatarCustomizationModel
    @ObservedObject var modeModel: ConversationModeModel
    let isDialogPresented: Bool

    @State private var contentHeight: CGFloat = 0
    @State private var headerHeight: CGFloat = 0
    @Environment(\.sizeCategory) private var sizeCategory

    var body: some View {
        GeometryReader { proxy in
            let maxPanelHeight = proxy.size.height * 0.82
            let panelHeight = min(headerHeight + contentHeight, maxPanelHeight)
            let scrollHeight = max(0, panelHeight - headerHeight)

            ZStack(alignment: .bottom) {
                Color.black.opacity(0.4)
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture { model.dismissSheet() }
                    .accessibilityHidden(true)

                panel(scrollHeight: scrollHeight, panelHeight: panelHeight)
            }
        }
        .accessibilityElement(children: isDialogPresented ? .ignore : .contain)
        .accessibilityAddTraits(isDialogPresented ? [] : .isModal)
        .accessibilityHidden(isDialogPresented)
        .accessibilityIdentifier("avatar_customization_sheet")
        .onPreferenceChange(AvatarSheetContentHeightKey.self) { contentHeight = $0 }
        .onPreferenceChange(AvatarSheetHeaderHeightKey.self) { headerHeight = $0 }
    }

    private func panel(scrollHeight: CGFloat, panelHeight: CGFloat) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: AppSpacing.s) {
                Text(L10n.t("conv_welcome_chip_translate"))
                    .foregroundColor(AppColor.brandIndigo)
                    .appFont(.label)
                Spacer(minLength: 0)
                Button(action: { model.dismissSheet() }) {
                    Image(systemName: "xmark")
                        .font(.system(size: 16, weight: .regular))
                        .foregroundColor(AppColor.textSecondary)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(L10n.t("common_close"))
                .accessibilityIdentifier("avatar_sheet_close")
            }
            .padding(.leading, AppSpacing.l)
            .padding(.trailing, AppSpacing.s)
            .padding(.top, AppSpacing.s)
            .background {
                GeometryReader { proxy in
                    Color.clear.preference(
                        key: AvatarSheetHeaderHeightKey.self,
                        value: proxy.size.height
                    )
                }
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switchRow(
                        title: L10n.t("conv_mode_dialogue"),
                        subtitle: L10n.t("conv_mode_dialogue_desc"),
                        isOn: modeModel.dialogueSwitchOn,
                        isEnabled: modeModel.dialogueSwitchEnabled,
                        identifier: "avatar_mode_dialogue"
                    ) {
                        modeModel.toggleDialogue()
                    }
                    switchRow(
                        title: L10n.t("conv_mode_lecture"),
                        subtitle: L10n.t("conv_mode_lecture_desc"),
                        isOn: modeModel.lectureSwitchOn,
                        isEnabled: modeModel.lectureSwitchEnabled,
                        identifier: "avatar_mode_lecture"
                    ) {
                        modeModel.toggleLecture()
                    }
                    switchRow(
                        title: L10n.t("conv_mode_transcribe"),
                        subtitle: L10n.t("conv_mode_transcribe_desc"),
                        isOn: modeModel.transcribeSwitchOn,
                        isEnabled: modeModel.transcribeSwitchEnabled,
                        identifier: "avatar_mode_transcribe"
                    ) {
                        modeModel.toggleTranscribe()
                    }
                    switchRow(
                        title: L10n.t("conv_omni"),
                        subtitle: L10n.t("conv_omni_desc"),
                        isOn: modeModel.omniSwitchOn,
                        isEnabled: modeModel.omniSwitchEnabled,
                        identifier: "avatar_mode_omni"
                    ) {
                        modeModel.toggleOmni()
                    }

                    Divider()
                        .overlay(AppColor.surfaceVariant)
                        .padding(.vertical, AppSpacing.s)

                    if sizeCategory.isAccessibilityCategory {
                        VStack(alignment: .leading, spacing: AppSpacing.s) {
                            Text(L10n.t("conv_avatar_picker_title"))
                                .foregroundColor(AppColor.brandIndigo)
                                .appFont(.label)
                            visibilityPill
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, AppSpacing.s)
                    } else {
                        HStack(spacing: AppSpacing.s) {
                            Text(L10n.t("conv_avatar_picker_title"))
                                .foregroundColor(AppColor.brandIndigo)
                                .appFont(.label)
                            Spacer(minLength: 0)
                            visibilityPill
                        }
                        .frame(minHeight: 44)
                    }

                    avatarOption(
                        icon: "photo.on.rectangle",
                        title: L10n.t("conv_avatar_from_gallery"),
                        identifier: "avatar_choose_gallery"
                    ) {
                        model.selectFromGallery()
                    }
                    avatarOption(
                        icon: "camera.fill",
                        title: L10n.t("conv_take_photo"),
                        identifier: "avatar_take_photo"
                    ) {
                        model.takePhoto()
                    }
                    if model.hasCustomAvatar {
                        avatarOption(
                            icon: "arrow.uturn.backward",
                            title: L10n.t("conv_avatar_reset"),
                            identifier: "avatar_reset"
                        ) {
                            Task { await model.removeCustomAvatar() }
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.l)
                .padding(.bottom, AppSpacing.l)
                .background {
                    GeometryReader { proxy in
                        Color.clear.preference(
                            key: AvatarSheetContentHeightKey.self,
                            value: proxy.size.height
                        )
                    }
                }
            }
            .frame(height: scrollHeight)
        }
        .frame(height: panelHeight)
        .background(AppColor.surface)
        .clipShape(TopRoundedShape(radius: AppRadius.l))
        .accessibilityAction(named: Text(L10n.t("common_close"))) {
            model.dismissSheet()
        }
    }

    private func switchRow(
        title: String,
        subtitle: String,
        isOn: Bool,
        isEnabled: Bool,
        identifier: String,
        action: @escaping () -> Void
    ) -> some View {
        HStack(spacing: AppSpacing.m) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.label)
                Text(subtitle)
                    .foregroundColor(AppColor.textSecondary)
                    .appFont(.caption)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Toggle(
                "",
                isOn: Binding(
                    get: { isOn },
                    set: { _ in action() }
                )
            )
            .labelsHidden()
            .tint(AppColor.brandIndigo)
            .disabled(!isEnabled)
            .accessibilityLabel(title)
            .accessibilityHint(subtitle)
            .accessibilityIdentifier(identifier)
        }
        .padding(.vertical, AppSpacing.s)
        .frame(minHeight: 48)
    }

    private var visibilityPill: some View {
        HStack(spacing: 0) {
            pillSegment(
                L10n.t("conv_avatar_scale_normal"),
                selected: model.avatarVisible,
                identifier: "avatar_scale_normal"
            ) {
                model.setAvatarVisible(true)
            }
            pillSegment(
                L10n.t("conv_avatar_scale_shrink"),
                selected: !model.avatarVisible,
                identifier: "avatar_scale_shrink"
            ) {
                model.setAvatarVisible(false)
            }
        }
        .padding(2)
        .background {
            RoundedRectangle(cornerRadius: 14)
                .fill(AppColor.surfaceVariant)
                .frame(height: 28)
        }
    }

    private func pillSegment(
        _ title: String,
        selected: Bool,
        identifier: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Text(title)
                .foregroundColor(selected ? AppColor.textPrimary : AppColor.textSecondary)
                .appFont(.label)
                .padding(.horizontal, 12)
                .frame(height: 28)
                .background {
                    if selected {
                        RoundedRectangle(cornerRadius: 14)
                            .fill(AppColor.brandIndigo)
                    }
                }
                .frame(height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(identifier)
    }

    private func avatarOption(
        icon: String,
        title: String,
        identifier: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: AppSpacing.m) {
                Image(systemName: icon)
                    .font(.system(size: 20, weight: .regular))
                    .foregroundColor(AppColor.textSecondary)
                    .frame(width: 24, height: 24)
                    .accessibilityHidden(true)
                Text(title)
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.label)
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier(identifier)
    }
}

private struct TopRoundedShape: Shape {
    let radius: CGFloat

    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + radius))
        path.addQuadCurve(
            to: CGPoint(x: rect.minX + radius, y: rect.minY),
            control: CGPoint(x: rect.minX, y: rect.minY)
        )
        path.addLine(to: CGPoint(x: rect.maxX - radius, y: rect.minY))
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.minY + radius),
            control: CGPoint(x: rect.maxX, y: rect.minY)
        )
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        path.closeSubpath()
        return path
    }
}
