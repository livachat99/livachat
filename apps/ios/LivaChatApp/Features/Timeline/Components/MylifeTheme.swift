import SwiftUI

enum MylifePalette {
    static let abyss = Color(hex: 0x0A0E1A)
    static let panel = Color(hex: 0x141A2E)
    static let line = Color(hex: 0x232C47)
    static let star = Color(hex: 0xE8ECFF)
    static let dust = Color(hex: 0x8B93B8)
    static let gold = Color(hex: 0xF5C86E)
    static let red = Color(hex: 0xF56E6E)
}

enum MylifeTextStyle {
    case pageTitle
    case sectionHeader
    case subpageTitle
    case cardTitle
    case body
    case bubbleBody
    case label
    case smallLabel
    case button
    case caption
}

private struct MylifeFontModifier: ViewModifier {
    @ScaledMetric(relativeTo: .title) private var pageTitleSize: CGFloat = 22
    @ScaledMetric(relativeTo: .title) private var sectionHeaderSize: CGFloat = 28
    @ScaledMetric(relativeTo: .title2) private var subpageTitleSize: CGFloat = 16
    @ScaledMetric(relativeTo: .title2) private var cardTitleSize: CGFloat = 16
    @ScaledMetric(relativeTo: .callout) private var bodySize: CGFloat = 14
    @ScaledMetric(relativeTo: .callout) private var bubbleBodySize: CGFloat = 16
    @ScaledMetric(relativeTo: .subheadline) private var labelSize: CGFloat = 12
    @ScaledMetric(relativeTo: .caption) private var smallLabelSize: CGFloat = 11
    @ScaledMetric(relativeTo: .subheadline) private var buttonSize: CGFloat = 14
    @ScaledMetric(relativeTo: .caption) private var captionSize: CGFloat = 12

    let style: MylifeTextStyle

    func body(content: Content) -> some View {
        content
            .font(.system(size: size, weight: weight))
            .lineSpacing(lineSpacing)
    }

    private var size: CGFloat {
        switch style {
        case .pageTitle: return pageTitleSize
        case .sectionHeader: return sectionHeaderSize
        case .subpageTitle: return subpageTitleSize
        case .cardTitle: return cardTitleSize
        case .body: return bodySize
        case .bubbleBody: return bubbleBodySize
        case .label: return labelSize
        case .smallLabel: return smallLabelSize
        case .button: return buttonSize
        case .caption: return captionSize
        }
    }

    private var weight: Font.Weight {
        switch style {
        case .pageTitle: return .semibold
        case .sectionHeader: return .regular
        case .subpageTitle, .cardTitle, .label, .smallLabel, .button: return .medium
        case .body, .bubbleBody, .caption: return .regular
        }
    }

    private var lineSpacing: CGFloat {
        switch style {
        case .pageTitle: return 6
        case .sectionHeader: return 8
        case .subpageTitle, .cardTitle: return 8
        case .body: return 6
        case .bubbleBody: return 8
        case .label: return 4
        case .smallLabel: return 5
        case .button: return 6
        case .caption: return 4
        }
    }
}

extension View {
    func mylifeFont(_ style: MylifeTextStyle) -> some View {
        modifier(MylifeFontModifier(style: style))
    }
}

struct TimelineStarfield: View {
    var body: some View {
        GeometryReader { geometry in
            Canvas { context, size in
                let count = min(max(Int(size.width * size.height / 9000), 24), 90)
                var random = SplitMix64(seed: 42)
                for _ in 0..<count {
                    let x = CGFloat(Double(random.next() % 1_000_000) / 1_000_000) * size.width
                    let y = CGFloat(Double(random.next() % 1_000_000) / 1_000_000) * size.height
                    let radius = CGFloat(Double(random.next() % 1_000_000) / 1_000_000) * 1.4 + 0.6
                    let alpha = 0.10 * Double(radius)
                    let rect = CGRect(
                        x: x - radius * 0.8,
                        y: y - radius * 0.8,
                        width: radius * 1.6,
                        height: radius * 1.6
                    )
                    context.fill(Path(ellipseIn: rect), with: .color(.white.opacity(alpha)))
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

struct MylifeLoadingHint: View {
    let text: String

    var body: some View {
        Text(text)
            .mylifeFont(.label)
            .foregroundColor(MylifePalette.dust)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .multilineTextAlignment(.center)
            .padding(AppSpacing.m)
    }
}

struct MylifeErrorHint: View {
    let message: String
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: AppSpacing.m) {
            Text(message)
                .mylifeFont(.body)
                .foregroundColor(MylifePalette.red)
                .multilineTextAlignment(.center)
            Button(action: onRetry) {
                Text(L10n.t("common_retry"))
                    .mylifeFont(.button)
                    .foregroundColor(MylifePalette.gold)
                    .padding(.horizontal, AppSpacing.m)
                    .frame(minHeight: 44)
                    .overlay {
                        RoundedRectangle(cornerRadius: AppRadius.l)
                            .stroke(MylifePalette.gold, lineWidth: 1)
                    }
            }
            .buttonStyle(.plain)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(AppSpacing.l)
    }
}

struct MylifeEmptyState: View {
    let title: String
    var subtitle: String? = nil
    var icon: String = "info.circle"

    var body: some View {
        VStack(spacing: AppSpacing.m) {
            Image(systemName: icon)
                .font(.system(size: 64, weight: .regular))
                .foregroundColor(MylifePalette.dust)
                .accessibilityHidden(true)
            Text(title)
                .mylifeFont(.pageTitle)
                .foregroundColor(MylifePalette.star)
                .multilineTextAlignment(.center)
            if let subtitle {
                Text(subtitle)
                    .mylifeFont(.bubbleBody)
                    .foregroundColor(MylifePalette.dust)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(AppSpacing.xl)
    }
}

struct MylifePulsingDot: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var dimmed = false

    var body: some View {
        Circle()
            .fill(MylifePalette.gold)
            .frame(width: 8, height: 8)
            .opacity(reduceMotion ? 1 : (dimmed ? 0.3 : 1))
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) {
                    dimmed = true
                }
            }
            .accessibilityHidden(true)
    }
}
