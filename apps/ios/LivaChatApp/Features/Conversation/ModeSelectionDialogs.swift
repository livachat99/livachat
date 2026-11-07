import SwiftUI

private struct ModeDialogContentHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

private struct ModeDialogCard<Content: View>: View {
    let identifier: String
    let onCancel: () -> Void
    @ViewBuilder let content: Content

    @State private var contentHeight: CGFloat = 0

    var body: some View {
        GeometryReader { proxy in
            let maxHeight = max(0, proxy.size.height * 0.85)
            let height = min(contentHeight, maxHeight)

            ZStack {
                Color.black.opacity(0.4)
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .onTapGesture(perform: onCancel)
                    .accessibilityHidden(true)

                ScrollView {
                    content
                        .padding(AppSpacing.l)
                        .frame(maxWidth: 360)
                        .background(AppColor.surface)
                        .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
                        .background {
                            GeometryReader { measured in
                                Color.clear.preference(
                                    key: ModeDialogContentHeightKey.self,
                                    value: measured.size.height
                                )
                            }
                        }
                        .frame(maxWidth: .infinity)
                }
                .frame(maxWidth: 360)
                .padding(.horizontal, AppSpacing.m)
                .frame(height: height)
                .onPreferenceChange(ModeDialogContentHeightKey.self) { contentHeight = $0 }
                .accessibilityElement(children: .contain)
                .accessibilityAddTraits(.isModal)
                .accessibilityIdentifier(identifier)
            }
        }
    }
}

struct LanguagePickerDialog: View {
    let target: ConversationLanguageTarget
    let onCancel: () -> Void
    let onConfirm: (String) -> Void

    @State private var selectedCode = "en"

    static let languageCodes = [
        "auto", "zh", "en", "ja", "ko", "fr", "it", "de", "es", "pt", "ru", "hi", "id", "th", "vi", "ar",
    ]

    private let languages = Self.languageCodes

    private var uiLocale: Locale {
        AppLocale.displayLocale
    }

    private var title: String {
        L10n.t(
            target == .lecture
                ? "conv_select_speaker_language"
                : "conv_select_peer_language"
        )
    }

    var body: some View {
        ModeDialogCard(identifier: "language_picker_dialog", onCancel: onCancel) {
            VStack(spacing: AppSpacing.m) {
                Text(title)
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.title)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)

                Menu {
                    ForEach(languages, id: \.self) { code in
                        Button(languageName(code)) {
                            selectedCode = code
                        }
                    }
                } label: {
                    HStack(spacing: AppSpacing.s) {
                        Text(languageName(selectedCode))
                            .foregroundColor(AppColor.textPrimary)
                            .appFont(.body)
                        Spacer(minLength: 0)
                        Image(systemName: "chevron.down")
                            .foregroundColor(AppColor.textSecondary)
                    }
                    .padding(.horizontal, AppSpacing.m)
                    .frame(minHeight: 48)
                    .background(AppColor.surfaceVariant)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                }
                .accessibilityIdentifier("language_picker_select")

                HStack(spacing: AppSpacing.m) {
                    Spacer()
                    dialogButton(
                        L10n.t("common_cancel"),
                        color: AppColor.textSecondary,
                        action: onCancel
                    )
                    .accessibilityIdentifier("language_picker_cancel")
                    dialogButton(
                        L10n.t("conv_picker_confirm"),
                        color: AppColor.brandIndigo
                    ) {
                        onConfirm(selectedCode)
                    }
                    .accessibilityIdentifier("language_picker_confirm")
                    Spacer()
                }
            }
        }
    }

    private func languageName(_ code: String) -> String {
        if code == "auto" {
            return L10n.t("conv_language_auto")
        }
        return uiLocale.localizedString(forLanguageCode: code) ?? code.uppercased()
    }
}

struct TranscribeSourceDialog: View {
    let initialSource: TranscribeAudioSource
    let onCancel: () -> Void
    let onConfirm: (TranscribeAudioSource) -> Void

    @State private var selected: TranscribeAudioSource

    init(
        initialSource: TranscribeAudioSource,
        onCancel: @escaping () -> Void,
        onConfirm: @escaping (TranscribeAudioSource) -> Void
    ) {
        self.initialSource = initialSource
        self.onCancel = onCancel
        self.onConfirm = onConfirm
        _selected = State(initialValue: initialSource)
    }

    var body: some View {
        ModeDialogCard(identifier: "transcribe_source_dialog", onCancel: onCancel) {
            VStack(spacing: AppSpacing.m) {
                Text(L10n.t("conv_transcribe_source_title"))
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.title)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)

                sourceRow(
                    title: L10n.t("conv_transcribe_source_original"),
                    subtitle: L10n.t("conv_transcribe_source_original_desc"),
                    source: .original,
                    identifier: "transcribe_source_original"
                )
                sourceRow(
                    title: L10n.t("conv_transcribe_source_voice"),
                    subtitle: L10n.t("conv_transcribe_source_voice_desc"),
                    source: .voice,
                    identifier: "transcribe_source_voice"
                )

                HStack(spacing: AppSpacing.m) {
                    Spacer()
                    dialogButton(
                        L10n.t("common_cancel"),
                        color: AppColor.textSecondary,
                        action: onCancel
                    )
                    .accessibilityIdentifier("transcribe_source_cancel")
                    dialogButton(
                        L10n.t("conv_picker_confirm"),
                        color: AppColor.brandIndigo
                    ) {
                        onConfirm(selected)
                    }
                    .accessibilityIdentifier("transcribe_source_confirm")
                    Spacer()
                }
            }
        }
    }

    private func sourceRow(
        title: String,
        subtitle: String,
        source: TranscribeAudioSource,
        identifier: String
    ) -> some View {
        Button {
            selected = source
        } label: {
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
                Image(systemName: selected == source ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 22))
                    .foregroundColor(
                        selected == source ? AppColor.brandIndigo : AppColor.textSecondary
                    )
            }
            .frame(minHeight: 48)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected == source ? .isSelected : [])
        .accessibilityIdentifier(identifier)
    }
}

struct OmniIntroDialog: View {
    let onCancel: () -> Void
    let onEnable: () -> Void

    var body: some View {
        ModeDialogCard(identifier: "omni_intro_dialog", onCancel: onCancel) {
            VStack(spacing: AppSpacing.m) {
                Text(L10n.t("conv_omni"))
                    .foregroundColor(AppColor.textPrimary)
                    .appFont(.title)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)
                Text(L10n.t("conv_omni_intro"))
                    .foregroundColor(AppColor.textSecondary)
                    .appFont(.body)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)

                HStack(spacing: AppSpacing.m) {
                    Spacer()
                    dialogButton(
                        L10n.t("common_cancel"),
                        color: AppColor.textSecondary,
                        action: onCancel
                    )
                    .accessibilityIdentifier("omni_dialog_cancel")
                    dialogButton(
                        L10n.t("conv_enable"),
                        color: AppColor.brandIndigo,
                        action: onEnable
                    )
                    .accessibilityIdentifier("omni_dialog_enable")
                    Spacer()
                }
            }
        }
    }
}

@MainActor
private func dialogButton(
    _ title: String,
    color: Color,
    action: @escaping () -> Void
) -> some View {
    Button(action: action) {
        Text(title)
            .foregroundColor(color)
            .appFont(.label)
            .frame(minWidth: 44, minHeight: 44)
            .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
}

