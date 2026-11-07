import SwiftUI

struct GalleryPreviewView: View {
    let data: Data
    let onBack: () -> Void
    let onCancel: () -> Void
    let onSend: () -> Void

    @Environment(\.layoutDirection) private var layoutDirection
    @State private var image: CGImage?
    @State private var failed = false
    @State private var scale: CGFloat = 1
    @State private var lastScale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var lastOffset: CGSize = .zero

    var body: some View {
        ZStack(alignment: .bottom) {
            Color.black.ignoresSafeArea()

            VStack(spacing: 0) {
                topBar
                imageArea
            }

            sendButton
        }
        .interactiveDismissDisabled()
        .onAppear {
            guard image == nil, !failed else { return }
            if let decoded = GalleryPhotoEncoder.previewImage(from: data) {
                image = decoded
            } else {
                failed = true
            }
        }
    }

    private var topBar: some View {
        HStack {
            Button(action: onBack) {
                Image(systemName: "arrow.backward")
                    .foregroundColor(AppColor.textPrimary)
                    .frame(width: 44, height: 44)
            }
            .accessibilityLabel(L10n.t("common_back"))
            .accessibilityIdentifier("gallery_preview_back")

            Spacer()

            Button(action: onCancel) {
                Text(L10n.t("common_cancel"))
                    .foregroundColor(AppColor.textSecondary)
                    .appFont(.label)
                    .frame(minWidth: 44, minHeight: 44)
            }
            .accessibilityIdentifier("gallery_preview_cancel")
        }
        .padding(.horizontal, AppSpacing.s)
        .frame(minHeight: 56)
    }

    private var imageArea: some View {
        GeometryReader { geometry in
            ZStack {
                if let image {
                    Image(decorative: image, scale: 1)
                        .resizable()
                        .scaledToFit()
                        .scaleEffect(scale)
                        .offset(offset)
                        .gesture(magnification)
                        .simultaneousGesture(pan)
                        .accessibilityLabel(L10n.t("conv_gallery_preview_image"))
                } else if failed {
                    Text(L10n.t("conv_gallery_preview_failed"))
                        .foregroundColor(AppColor.textSecondary)
                        .appFont(.body)
                } else {
                    ProgressView()
                        .tint(AppColor.textSecondary)
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipped()
        }
    }

    private var sendButton: some View {
        Button(action: onSend) {
            Text(L10n.t("conv_gallery_send"))
                .foregroundColor(AppColor.textPrimary)
                .appFont(.label)
                .padding(.horizontal, AppSpacing.xl)
                .frame(height: 44)
                .background(AppColor.surfaceVariant.opacity(image == nil ? 0.5 : 1))
                .clipShape(Capsule())
        }
        .disabled(image == nil)
        .padding(.bottom, AppSpacing.xl)
        .accessibilityIdentifier("gallery_preview_send")
    }

    private var magnification: some Gesture {
        MagnificationGesture()
            .onChanged { value in
                scale = min(max(lastScale * value, 1), 5)
            }
            .onEnded { _ in
                lastScale = scale
                if scale <= 1 {
                    scale = 1
                    lastScale = 1
                    offset = .zero
                    lastOffset = .zero
                }
            }
    }

    private var pan: some Gesture {
        DragGesture()
            .onChanged { value in
                guard scale > 1 else { return }
                // `.offset` mirrors x in RTL; negate the physical drag so the
                // zoomed image follows the finger.
                let dx = layoutDirection == .rightToLeft
                    ? -value.translation.width
                    : value.translation.width
                offset = CGSize(
                    width: lastOffset.width + dx,
                    height: lastOffset.height + value.translation.height
                )
            }
            .onEnded { _ in
                guard scale > 1 else { return }
                lastOffset = offset
            }
    }
}
