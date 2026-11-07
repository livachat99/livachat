import CoreGraphics
import SwiftUI

struct AvatarCropView: View {
    let sourceData: Data
    let onCancel: () -> Void
    let onConfirm: (Data) -> Void

    @State private var image: CGImage?
    @State private var cropAreaSize: CGSize = .zero
    @State private var scale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var dragStartOffset: CGSize?
    @State private var magnifyStartScale: CGFloat?

    private let cropRadius = AvatarCropMath.radius

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button(action: onCancel) {
                    Text(L10n.t("common_cancel"))
                        .foregroundColor(AppColor.textSecondary)
                        .appFont(.label)
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("avatar_crop_cancel")
                Spacer()
            }
            .padding(.horizontal, AppSpacing.l)
            .padding(.vertical, AppSpacing.s)

            GeometryReader { proxy in
                ZStack {
                    cropCanvas
                    Text(L10n.t("conv_avatar_drag_hint"))
                        .foregroundColor(AppColor.textSecondary)
                        .appFont(.caption)
                        .padding(.top, AppSpacing.m)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                }
                .onAppear {
                    cropAreaSize = proxy.size
                    resetTransform(for: proxy.size)
                }
                .onChange(of: proxy.size) { newSize in
                    cropAreaSize = newSize
                    resetTransform(for: newSize)
                }
            }

            Button {
                guard let data = croppedData() else { return }
                onConfirm(data)
            } label: {
                Text(L10n.t("conv_confirm"))
                    .foregroundColor(.white)
                    .appFont(.label)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .background(AppColor.brandIndigo)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .buttonStyle(.plain)
            .disabled(image == nil)
            .padding(.horizontal, AppSpacing.l)
            .padding(.vertical, AppSpacing.m)
            .accessibilityIdentifier("avatar_crop_confirm")
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(AppColor.background.ignoresSafeArea())
        .accessibilityElement(children: .contain)
        .task(id: sourceData) {
            let decoded = await Task.detached(priority: .userInitiated) {
                AvatarImageDecoder.decode(sourceData)
            }.value
            guard let decoded else {
                onCancel()
                return
            }
            image = decoded
            resetTransform(for: cropAreaSize)
        }
        .accessibilityIdentifier("avatar_crop_view")
    }

    private var cropCanvas: some View {
        Canvas { context, canvasSize in
            guard let image else { return }
            let center = CGPoint(
                x: canvasSize.width / 2 + offset.width,
                y: canvasSize.height / 2 + offset.height
            )
            context.drawLayer { layer in
                layer.translateBy(x: center.x, y: center.y)
                layer.scaleBy(x: scale, y: scale)
                layer.draw(
                    Image(decorative: image, scale: 1),
                    in: CGRect(
                        x: -CGFloat(image.width) / 2,
                        y: -CGFloat(image.height) / 2,
                        width: CGFloat(image.width),
                        height: CGFloat(image.height)
                    )
                )
            }

            var overlay = Path(CGRect(origin: .zero, size: canvasSize))
            overlay.addEllipse(in: circleRect(in: canvasSize))
            context.fill(
                overlay,
                with: .color(Color.black.opacity(0.6)),
                style: FillStyle(eoFill: true)
            )
            context.stroke(
                Path(ellipseIn: circleRect(in: canvasSize)),
                with: .color(AppColor.brandIndigo.opacity(0.5)),
                lineWidth: 2
            )
        }
        .clipped()
        .contentShape(Rectangle())
        .gesture(dragGesture)
        .simultaneousGesture(magnificationGesture)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(L10n.t("conv_avatar_crop_image"))
        .accessibilityHint(L10n.t("conv_avatar_crop_hint"))
        .accessibilityAction(named: Text(L10n.t("conv_avatar_move_left"))) {
            move(dx: -10, dy: 0)
        }
        .accessibilityAction(named: Text(L10n.t("conv_avatar_move_right"))) {
            move(dx: 10, dy: 0)
        }
        .accessibilityAction(named: Text(L10n.t("conv_avatar_move_up"))) {
            move(dx: 0, dy: -10)
        }
        .accessibilityAction(named: Text(L10n.t("conv_avatar_move_down"))) {
            move(dx: 0, dy: 10)
        }
        .accessibilityAction(named: Text(L10n.t("conv_avatar_zoom_in"))) {
            zoomBy(1.1)
        }
        .accessibilityAction(named: Text(L10n.t("conv_avatar_zoom_out"))) {
            zoomBy(0.9)
        }
        .accessibilityIdentifier("avatar_crop_image")
    }

    private var dragGesture: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                let base = dragStartOffset ?? offset
                dragStartOffset = base
                offset = clampedOffset(
                    CGSize(
                        width: base.width + value.translation.width,
                        height: base.height + value.translation.height
                    )
                )
            }
            .onEnded { _ in
                dragStartOffset = nil
            }
    }

    private var magnificationGesture: some Gesture {
        MagnificationGesture()
            .onChanged { value in
                guard image != nil else { return }
                let base = magnifyStartScale ?? scale
                magnifyStartScale = base
                let minimum = minimumScale
                let maximum = max(minimum, initialScale * 3)
                scale = min(max(base * value, minimum), maximum)
                offset = clampedOffset(offset)
            }
            .onEnded { _ in
                magnifyStartScale = nil
            }
    }

    private func move(dx: CGFloat, dy: CGFloat) {
        offset = clampedOffset(
            CGSize(width: offset.width + dx, height: offset.height + dy)
        )
    }

    private func zoomBy(_ factor: CGFloat) {
        guard image != nil else { return }
        let minimum = minimumScale
        let maximum = max(minimum, initialScale * 3)
        scale = min(max(scale * factor, minimum), maximum)
        offset = clampedOffset(offset)
    }

    private var minimumScale: CGFloat {
        guard let image else { return 1 }
        return AvatarCropMath.minimumScale(
            imageWidth: image.width,
            imageHeight: image.height
        )
    }

    private var initialScale: CGFloat {
        guard let image else { return minimumScale }
        return AvatarCropMath.initialScale(
            imageWidth: image.width,
            imageHeight: image.height,
            area: cropAreaSize
        )
    }

    private func resetTransform(for size: CGSize) {
        guard let image, size.width > 0, size.height > 0 else { return }
        scale = AvatarCropMath.initialScale(
            imageWidth: image.width,
            imageHeight: image.height,
            area: size
        )
        offset = .zero
    }

    private func clampedOffset(_ candidate: CGSize) -> CGSize {
        guard let image else { return .zero }
        return AvatarCropMath.clampedOffset(
            candidate,
            imageWidth: image.width,
            imageHeight: image.height,
            scale: scale
        )
    }

    private func circleRect(in size: CGSize) -> CGRect {
        CGRect(
            x: size.width / 2 - cropRadius,
            y: size.height / 2 - cropRadius,
            width: cropRadius * 2,
            height: cropRadius * 2
        )
    }

    private func croppedData() -> Data? {
        guard let image,
              let rect = AvatarCropMath.cropRect(
                  imageWidth: image.width,
                  imageHeight: image.height,
                  scale: scale,
                  offset: offset
              ),
              let cropped = image.cropping(to: rect) else {
            return nil
        }
        return CameraImageEncoder.jpegData(from: cropped, quality: 0.9)
    }
}
