import SwiftUI

/// Android-parity 4-segment mic glyph (24 pt viewport). Only this view
/// observes `MicLevelModel`, so level updates never re-render the
/// conversation view.
struct MicVolumeGlyph: View {
    @ObservedObject var model: MicLevelModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private static let size: CGFloat = 24
    private static let ringLeft: CGFloat = 7.8
    private static let ringTop: CGFloat = 0.5
    private static let ringWidth: CGFloat = 8.4
    private static let ringHeight: CGFloat = 13.1
    private static let ringCorner: CGFloat = 2.5
    private static let ringThickness: CGFloat = 1.7
    private static let barStartY: CGFloat = 2.2
    private static let barHeight: CGFloat = 1.9
    private static let barGap: CGFloat = 0.9
    private static let barWidth: CGFloat = 5.8
    private static let barCorner: CGFloat = 0.8
    private static let bellDY: CGFloat = 0.9
    private static let stemWidth: CGFloat = 1.7
    private static let stemTopY: CGFloat = 21.4
    private static let stemHeight: CGFloat = 1.7

    var body: some View {
        ZStack(alignment: .topLeading) {
            MicBellShape()
                .fill(AppColor.brandIndigo)
                .frame(width: Self.size, height: Self.size)
                .offset(y: Self.bellDY)

            RoundedRectangle(cornerRadius: Self.stemWidth / 2)
                .fill(AppColor.brandIndigo)
                .frame(width: Self.stemWidth, height: Self.stemHeight)
                .offset(
                    x: Self.size / 2 - Self.stemWidth / 2,
                    y: Self.stemTopY
                )

            RoundedRectangle(cornerRadius: Self.ringCorner)
                .stroke(AppColor.brandIndigo, lineWidth: Self.ringThickness)
                .frame(width: Self.ringWidth, height: Self.ringHeight)
                .offset(x: Self.ringLeft, y: Self.ringTop)

            ForEach(0..<MicLevelModel.levels, id: \.self) { index in
                RoundedRectangle(cornerRadius: Self.barCorner)
                    .fill(
                        index >= MicLevelModel.levels - model.level
                            ? AppColor.brandIndigo
                            : AppColor.surfaceVariant
                    )
                    .frame(width: Self.barWidth, height: Self.barHeight)
                    .offset(
                        x: Self.size / 2 - Self.barWidth / 2,
                        y: Self.barStartY
                            + CGFloat(index) * (Self.barHeight + Self.barGap)
                    )
            }
        }
        .frame(width: Self.size, height: Self.size)
        // Fixed-coordinate icon art: never mirror its internal offsets.
        .environment(\.layoutDirection, .leftToRight)
        .animation(
            reduceMotion ? nil : .easeOut(duration: 0.12),
            value: model.level
        )
        .accessibilityHidden(true)
    }
}

/// Material mic bell path from Android's `MIC_BELL_PATH`, translated from
/// the 24-unit SVG viewport into the shape's rect.
private struct MicBellShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: point(17.3, 11, in: rect))
        path.addCurve(
            to: point(12, 16.1, in: rect),
            control1: point(17.3, 14, in: rect),
            control2: point(14.76, 16.1, in: rect)
        )
        path.addCurve(
            to: point(6.7, 11, in: rect),
            control1: point(9.24, 16.1, in: rect),
            control2: point(6.7, 14, in: rect)
        )
        path.addLine(to: point(5, 11, in: rect))
        path.addCurve(
            to: point(11, 17.72, in: rect),
            control1: point(5, 14.41, in: rect),
            control2: point(7.72, 17.23, in: rect)
        )
        path.addLine(to: point(11, 21, in: rect))
        path.addLine(to: point(13, 21, in: rect))
        path.addLine(to: point(13, 17.72, in: rect))
        path.addCurve(
            to: point(19, 11, in: rect),
            control1: point(16.28, 17.24, in: rect),
            control2: point(19, 14.42, in: rect)
        )
        path.addLine(to: point(17.3, 11, in: rect))
        path.closeSubpath()
        return path
    }

    private func point(_ x: CGFloat, _ y: CGFloat, in rect: CGRect) -> CGPoint {
        CGPoint(
            x: rect.minX + x / 24 * rect.width,
            y: rect.minY + y / 24 * rect.height
        )
    }
}
