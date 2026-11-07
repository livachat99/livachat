import SwiftUI

private struct CameraAITextMeasurement: Equatable {
    let text: String
    let height: CGFloat
}

private struct CameraAITextHeightKey: PreferenceKey {
    static let defaultValue = CameraAITextMeasurement(text: "", height: 0)

    static func reduce(
        value: inout CameraAITextMeasurement,
        nextValue: () -> CameraAITextMeasurement
    ) {
        value = nextValue()
    }
}

struct CameraModeView: View {
    @ObservedObject var viewModel: ConversationViewModel

    @State private var initialAIID: String?
    @State private var initialUserID: String?
    @State private var aiTextMeasurement = CameraAITextHeightKey.defaultValue

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .topTrailing) {
                preview

                switchButton
                    .padding(AppSpacing.m)

                VStack(alignment: .leading, spacing: AppSpacing.s) {
                    Spacer()
                    if !cameraAIText.isEmpty {
                        aiTextOverlay(maxHeight: geometry.size.height * 2 / 3)
                    }
                    if !cameraTranscription.isEmpty {
                        transcriptionOverlay
                    }
                }
                .padding(AppSpacing.m)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            }
        }
        .onAppear(perform: resetSnapshots)
        .onChange(of: viewModel.isFrontCamera) { _ in
            resetSnapshots()
        }
    }

    private var preview: some View {
        Group {
            if let source = viewModel.cameraPreviewSource {
                CameraPreviewView(source: source)
            } else {
                ZStack {
                    AppColor.background
                    Image(systemName: "camera.fill")
                        .font(.system(size: 44))
                        .foregroundColor(AppColor.textTertiary)
                }
            }
        }
        .accessibilityHidden(true)
    }

    private var switchButton: some View {
        Button(action: viewModel.switchCamera) {
            Image(systemName: "arrow.triangle.2.circlepath.camera")
                .foregroundColor(AppColor.textPrimary)
                .frame(width: 40, height: 40)
                .background(AppColor.surfaceVariant.opacity(0.8))
                .clipShape(Circle())
                .frame(width: 44, height: 44)
        }
        .accessibilityLabel(
            L10n.t(viewModel.isFrontCamera ? "conv_switch_to_back_camera" : "conv_switch_to_front_camera")
        )
        .accessibilityIdentifier("conversation_switch_camera")
    }

    private func aiTextOverlay(maxHeight: CGFloat) -> some View {
        // Until the current text is measured, lay out at the cap: this avoids
        // both a zero-height flash and reusing the previous message's height.
        let isMeasured = aiTextMeasurement.text == cameraAIText
            && aiTextMeasurement.height > 0
        let height = isMeasured ? min(aiTextMeasurement.height, maxHeight) : maxHeight

        return ScrollView {
            Text(cameraAIText)
                .foregroundColor(AppColor.textPrimary)
                .appFont(.body)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, AppSpacing.m)
                .padding(.vertical, AppSpacing.s)
                .background {
                    GeometryReader { proxy in
                        Color.clear.preference(
                            key: CameraAITextHeightKey.self,
                            value: CameraAITextMeasurement(
                                text: cameraAIText,
                                height: proxy.size.height
                            )
                        )
                    }
                }
        }
        .frame(height: height)
        .background(AppColor.surfaceVariant.opacity(0.7))
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
        .accessibilityIdentifier("conversation_camera_ai_text")
        .onPreferenceChange(CameraAITextHeightKey.self) { aiTextMeasurement = $0 }
    }

    private var transcriptionOverlay: some View {
        HStack(alignment: .center, spacing: AppSpacing.s) {
            Text(cameraTranscription)
                .foregroundColor(.white)
                .appFont(.body)
            if !cameraTranscriptionFinal {
                CameraTypingDots()
            }
        }
        .padding(.horizontal, AppSpacing.m)
        .padding(.vertical, AppSpacing.m - 4)
        .background(AppColor.brandIndigo.opacity(0.88))
        .clipShape(RoundedRectangle(cornerRadius: AppRadius.m))
        .accessibilityIdentifier("conversation_camera_transcription")
    }

    private var cameraAIText: String {
        guard let lastAI = viewModel.messages.last(where: { $0.role == .assistant }) else {
            return ""
        }
        if let initialAIID {
            return lastAI.id == initialAIID ? "" : lastAI.text
        }
        return lastAI.text
    }

    private var cameraTranscription: String {
        if !viewModel.currentTranscription.isEmpty {
            return viewModel.currentTranscription
        }
        guard let lastUser = viewModel.messages.last(where: { $0.role == .user }) else {
            return ""
        }
        if let initialUserID {
            return lastUser.id == initialUserID ? "" : lastUser.text
        }
        return lastUser.text
    }

    /// Live transcription can still change; the last-user-message fallback is final.
    private var cameraTranscriptionFinal: Bool {
        if !viewModel.currentTranscription.isEmpty {
            return viewModel.isTranscriptionFinal
        }
        return true
    }

    private func resetSnapshots() {
        initialAIID = viewModel.messages.last(where: { $0.role == .assistant })?.id
        initialUserID = viewModel.messages.last(where: { $0.role == .user })?.id
    }
}

private struct CameraTypingDots: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var animating = false

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(Color.white)
                    .frame(width: 4, height: 4)
                    .opacity(animating ? 1.0 : 0.3)
                    .animation(
                        reduceMotion
                            ? nil
                            : .easeInOut(duration: 0.6)
                                .repeatForever(autoreverses: true)
                                .delay(Double(index) * 0.15),
                        value: animating
                    )
            }
        }
        .onAppear {
            guard !reduceMotion else { return }
            animating = true
        }
    }
}
