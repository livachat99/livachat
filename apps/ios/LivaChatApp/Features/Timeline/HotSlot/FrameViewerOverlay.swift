import NetworkImage
import SwiftUI

struct FrameViewerOverlay: View {
    let frame: HotSlotEvent
    let saveTip: String?
    let saving: Bool
    let onSaveClick: () -> Void
    let onDismiss: () -> Void

    @Environment(\.displayScale) private var displayScale
    @State private var scale: CGFloat = 1
    @State private var lastScale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var lastOffset: CGSize = .zero
    @State private var imageSize: CGSize = .zero
    @State private var viewport: CGSize = .zero
    @State private var captionExpanded = false

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Color.black.ignoresSafeArea()

                imageLayer
                    .frame(width: geometry.size.width, height: geometry.size.height)
                    .clipped()
            }
            .overlay(alignment: .topTrailing) {
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(MylifePalette.star)
                        .frame(width: 44, height: 44)
                        .background(Color.black.opacity(0.35))
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .padding(AppSpacing.m)
                .accessibilityLabel(L10n.t("common_close"))
            }
            .overlay(alignment: .bottom) {
                bottomBar
                    .padding(.horizontal, AppSpacing.l)
                    .padding(.bottom, AppSpacing.l)
            }
            .onAppear { viewport = geometry.size }
            .onChange(of: geometry.size) { viewport = $0 }
        }
        .interactiveDismissDisabled()
    }

    private var imageLayer: some View {
        NetworkImage(url: frame.mediaUrl, scale: displayScale) { state in
            switch state {
            case .empty, .failure:
                Color.clear
            case let .success(image, idealSize):
                image
                    .resizable()
                    .scaledToFit()
                    .onAppear {
                        if imageSize != idealSize { imageSize = idealSize }
                    }
            }
        }
        .scaleEffect(scale)
        .offset(offset)
        .contentShape(Rectangle())
        .gesture(magnification)
        .simultaneousGesture(pan)
        .onTapGesture(perform: onDismiss)
    }

    private var magnification: some Gesture {
        MagnificationGesture()
            .onChanged { value in
                scale = ImageViewerMath.clampedScale(lastScale * value)
                offset = ImageViewerMath.clampedOffset(
                    offset,
                    imageSize: imageSize,
                    scale: scale,
                    viewport: viewport
                )
            }
            .onEnded { _ in
                lastScale = scale
                if scale <= ImageViewerMath.minScale {
                    resetTransform()
                } else {
                    offset = ImageViewerMath.clampedOffset(
                        offset,
                        imageSize: imageSize,
                        scale: scale,
                        viewport: viewport
                    )
                    lastOffset = offset
                }
            }
    }

    private var pan: some Gesture {
        DragGesture(minimumDistance: 10)
            .onChanged { value in
                guard scale > ImageViewerMath.minScale else { return }
                let proposed = CGSize(
                    width: lastOffset.width + value.translation.width,
                    height: lastOffset.height + value.translation.height
                )
                offset = ImageViewerMath.clampedOffset(
                    proposed,
                    imageSize: imageSize,
                    scale: scale,
                    viewport: viewport
                )
            }
            .onEnded { _ in
                guard scale > ImageViewerMath.minScale else { return }
                lastOffset = offset
            }
    }

    private func resetTransform() {
        scale = 1
        lastScale = 1
        offset = .zero
        lastOffset = .zero
    }

    @ViewBuilder
    private var bottomBar: some View {
        VStack(spacing: AppSpacing.s) {
            if let saveTip {
                Text(saveTip)
                    .foregroundColor(MylifePalette.star)
                    .mylifeFont(.button)
                    .padding(.horizontal, AppSpacing.m)
                    .padding(.vertical, AppSpacing.s)
                    .background(Color.black.opacity(0.75))
                    .clipShape(RoundedRectangle(cornerRadius: AppRadius.l))
                    .accessibilityIdentifier("timeline_frame_save_tip")
            }

            HStack(spacing: 0) {
                if let caption = nonBlank(frame.caption) {
                    Button {
                        captionExpanded.toggle()
                    } label: {
                        HStack(spacing: AppSpacing.xs) {
                            Text(caption)
                                .foregroundColor(MylifePalette.star.opacity(0.85))
                                .mylifeFont(.caption)
                                .lineLimit(captionExpanded ? nil : 1)
                                .multilineTextAlignment(.center)
                            Text(captionExpanded ? "⌃" : "⌄")
                                .foregroundColor(MylifePalette.gold)
                                .mylifeFont(.caption)
                        }
                        .padding(.leading, AppSpacing.m)
                        .padding(.vertical, AppSpacing.s)
                    }
                    .buttonStyle(.plain)
                }

                Button(action: onSaveClick) {
                    if saving {
                        ProgressView()
                            .progressViewStyle(CircularProgressViewStyle(tint: MylifePalette.star))
                            .scaleEffect(0.8)
                            .frame(width: 44, height: 44)
                    } else {
                        Image(systemName: "arrow.down.to.line")
                            .font(.system(size: 18, weight: .regular))
                            .foregroundColor(MylifePalette.star)
                            .frame(width: 44, height: 44)
                    }
                }
                .buttonStyle(.plain)
                .accessibilityLabel(L10n.t("common_save_image"))
                .accessibilityIdentifier("timeline_frame_save")
            }
            .padding(.horizontal, AppSpacing.xs)
            .background(Color.black.opacity(0.6))
            .clipShape(FrameViewerBarShape(circular: nonBlank(frame.caption) == nil))
        }
    }

    private func nonBlank(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

private struct FrameViewerBarShape: Shape {
    let circular: Bool

    func path(in rect: CGRect) -> Path {
        if circular {
            return Capsule().path(in: rect)
        }
        return RoundedRectangle(cornerRadius: AppRadius.m).path(in: rect)
    }
}
