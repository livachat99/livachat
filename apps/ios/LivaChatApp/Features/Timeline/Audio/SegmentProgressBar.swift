import SwiftUI

private let energyFloorDb: Float = 40
private let barSlotDp: CGFloat = 1.5
private let barWidthRatio: CGFloat = 0.62
private let maxBars = 512

func envelopeBars(_ envelope: EnergyEnvelope, barCount: Int) -> [CGFloat] {
    let n = envelope.values.count
    guard n > 0, barCount > 0 else { return [] }
    let span = max(envelope.dbMax - envelope.dbMin, 0.001)

    func db(at index: Int) -> Float {
        let value = Float(envelope.values[min(max(index, 0), n - 1)])
        return envelope.dbMin + value / 255 * span
    }

    let peaks: [Float] = (0..<barCount).map { bar in
        let from = bar * n / barCount
        var to = (bar + 1) * n / barCount - 1
        if to < from { to = from }
        var peak = -Float.greatestFiniteMagnitude
        for index in from...min(to, n - 1) {
            peak = max(peak, db(at: index))
        }
        return peak
    }

    guard let peak = peaks.max() else { return [] }
    let floor = max(peak - energyFloorDb, envelope.dbMin)
    let normalizedSpan = max(peak - floor, 0.001)
    let heights = peaks.map { CGFloat(min(max(($0 - floor) / normalizedSpan, 0), 1)) }
    return heights.allSatisfy { $0 <= 0 } ? [] : heights
}

/// Direction-aware geometry for the waveform and scrubber. Time 0 sits at the
/// leading edge: left in LTR, right in RTL.
enum SegmentProgressMath {
    static func fraction(atX x: CGFloat, width: CGFloat, isRTL: Bool) -> CGFloat {
        guard width > 0 else { return 0 }
        let clamped = min(max(x, 0), width)
        return isRTL ? (width - clamped) / width : clamped / width
    }

    static func x(forFraction fraction: CGFloat, width: CGFloat, isRTL: Bool) -> CGFloat {
        let clamped = min(max(fraction, 0), 1)
        return (isRTL ? 1 - clamped : clamped) * width
    }

    static func ms(atX x: CGFloat, width: CGFloat, durationMs: Int64, isRTL: Bool) -> Int64 {
        guard width > 0, durationMs > 0 else { return 0 }
        return Int64(fraction(atX: x, width: width, isRTL: isRTL) * CGFloat(durationMs))
    }

    static func barX(
        index: Int,
        slot: CGFloat,
        barWidth: CGFloat,
        width: CGFloat,
        isRTL: Bool
    ) -> CGFloat {
        let ltr = CGFloat(index) * slot + (slot - barWidth) / 2
        return isRTL ? width - ltr - barWidth : ltr
    }
}

struct SegmentProgressBar: View {
    let fraction: CGFloat
    let durationMs: Int64
    let envelope: EnergyEnvelope?
    let onScrub: (Int64) -> Void
    let onScrubEnd: (Int64) -> Void

    @Environment(\.layoutDirection) private var layoutDirection
    @State private var dragging = false

    private var isRTL: Bool { layoutDirection == .rightToLeft }

    var body: some View {
        GeometryReader { geometry in
            let width = max(geometry.size.width, 1)
            let bars = bars(for: width)
            Canvas { context, size in
                let centerY = size.height / 2
                let f = min(max(fraction, 0), 1)
                if !bars.isEmpty {
                    let slot = size.width / CGFloat(bars.count)
                    let barWidth = slot * barWidthRatio
                    let half = size.height / 2 - 1
                    let playedBars = CGFloat(bars.count) * f
                    for (index, height) in bars.enumerated() {
                        let y = height * half
                        if y <= 0.5 { continue }
                        let rect = CGRect(
                            x: SegmentProgressMath.barX(
                                index: index,
                                slot: slot,
                                barWidth: barWidth,
                                width: size.width,
                                isRTL: isRTL
                            ),
                            y: centerY - y,
                            width: barWidth,
                            height: y * 2
                        )
                        let color = CGFloat(index) < playedBars ? MylifePalette.gold : MylifePalette.line
                        context.fill(Path(roundedRect: rect, cornerRadius: barWidth / 3), with: .color(color))
                    }
                } else {
                    let trackHeight: CGFloat = 4
                    let radius = trackHeight / 2
                    let track = CGRect(x: 0, y: centerY - radius, width: size.width, height: trackHeight)
                    context.fill(Path(roundedRect: track, cornerRadius: radius), with: .color(MylifePalette.line))
                    if f > 0 {
                        let playedWidth = size.width * f
                        let playedX = isRTL ? size.width - playedWidth : 0
                        let played = CGRect(
                            x: playedX,
                            y: centerY - radius,
                            width: playedWidth,
                            height: trackHeight
                        )
                        context.fill(Path(roundedRect: played, cornerRadius: radius), with: .color(MylifePalette.gold))
                    }
                }

                let thumbRadius: CGFloat = 7
                let center = CGPoint(
                    x: SegmentProgressMath.x(
                        forFraction: f,
                        width: size.width,
                        isRTL: isRTL
                    ),
                    y: centerY
                )
                context.fill(
                    Path(ellipseIn: CGRect(
                        x: center.x - thumbRadius - 1.5,
                        y: center.y - thumbRadius - 1.5,
                        width: (thumbRadius + 1.5) * 2,
                        height: (thumbRadius + 1.5) * 2
                    )),
                    with: .color(MylifePalette.abyss)
                )
                context.fill(
                    Path(ellipseIn: CGRect(
                        x: center.x - thumbRadius,
                        y: center.y - thumbRadius,
                        width: thumbRadius * 2,
                        height: thumbRadius * 2
                    )),
                    with: .color(MylifePalette.gold)
                )
            }
            .contentShape(Rectangle())
            .gesture(gesture(width: width))
        }
        .frame(height: 40)
        .accessibilityHidden(true)
    }

    private func bars(for width: CGFloat) -> [CGFloat] {
        guard let envelope else { return [] }
        let count = Int(width / barSlotDp)
        return envelopeBars(envelope, barCount: min(max(count, 1), maxBars))
    }

    private func ms(at x: CGFloat, width: CGFloat) -> Int64 {
        SegmentProgressMath.ms(
            atX: x,
            width: width,
            durationMs: durationMs,
            isRTL: isRTL
        )
    }

    private func gesture(width: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 0, coordinateSpace: .local)
            .onChanged { value in
                guard durationMs > 0 else { return }
                let dx = abs(value.translation.width)
                let dy = abs(value.translation.height)
                if !dragging {
                    guard dx > 4, dx > dy else { return }
                    dragging = true
                }
                onScrub(ms(at: value.location.x, width: width))
            }
            .onEnded { value in
                guard durationMs > 0 else { return }
                let dx = abs(value.translation.width)
                let dy = abs(value.translation.height)
                if dragging || (dx <= 4 && dy <= 4) {
                    onScrubEnd(ms(at: value.location.x, width: width))
                }
                dragging = false
            }
    }
}
