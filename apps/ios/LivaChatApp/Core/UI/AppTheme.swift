import SwiftUI

enum AppColor {
    static let brandIndigo = Color(hex: 0x6366F1)
    static let brandViolet = Color(hex: 0x8B5CF6)
    static let success = Color(hex: 0x10B981)
    static let warning = Color(hex: 0xF59E0B)
    static let error = Color(hex: 0xEF4444)
    static let info = Color(hex: 0x3B82F6)
    static let gold = Color(hex: 0xFFD700)
    static let background = Color(hex: 0x0F172A)
    static let surface = Color(hex: 0x1E293B)
    static let surfaceVariant = Color(hex: 0x334155)
    static let textPrimary = Color(hex: 0xF8FAFC)
    static let textSecondary = Color(hex: 0x94A3B8)
    static let textTertiary = Color(hex: 0x64748B)
}

enum AppSpacing {
    static let xs: CGFloat = 4
    static let s: CGFloat = 8
    static let m: CGFloat = 16
    static let l: CGFloat = 24
    static let xl: CGFloat = 32
    static let xxl: CGFloat = 48
}

enum AppRadius {
    static let xs: CGFloat = 4
    static let s: CGFloat = 8
    static let m: CGFloat = 12
    static let l: CGFloat = 16
    static let xl: CGFloat = 24
}

enum AppLayout {
    /// Android `MessageBubble` / `HotSlotRowItems`: `widthIn(max = screenWidth * 0.75)`.
    /// Shared by conversation, History detail and Hot Slot so message bubbles
    /// have identical maximum widths (including their inner padding).
    static let messageBubbleMaxWidthRatio: CGFloat = 0.75

    static func messageBubbleMaxWidth(containerWidth: CGFloat) -> CGFloat {
        containerWidth * messageBubbleMaxWidthRatio
    }
}

enum AppFontStyle {
    case headline
    case title
    case body
    case label
    case caption
}

private struct AppFontModifier: ViewModifier {
    @ScaledMetric(relativeTo: .title) private var headlineSize: CGFloat = 28
    @ScaledMetric(relativeTo: .title2) private var titleSize: CGFloat = 22
    @ScaledMetric(relativeTo: .callout) private var bodySize: CGFloat = 16
    @ScaledMetric(relativeTo: .subheadline) private var labelSize: CGFloat = 14
    @ScaledMetric(relativeTo: .caption) private var captionSize: CGFloat = 12

    let style: AppFontStyle

    func body(content: Content) -> some View {
        content
            .font(.system(size: size, weight: weight))
            .lineSpacing(lineSpacing)
    }

    private var size: CGFloat {
        switch style {
        case .headline: return headlineSize
        case .title: return titleSize
        case .body: return bodySize
        case .label: return labelSize
        case .caption: return captionSize
        }
    }

    private var weight: Font.Weight {
        switch style {
        case .headline: return .bold
        case .title: return .semibold
        case .body: return .regular
        case .label: return .medium
        case .caption: return .regular
        }
    }

    private var lineSpacing: CGFloat {
        switch style {
        case .headline: return 8
        case .title: return 6
        case .body: return 8
        case .label: return 6
        case .caption: return 4
        }
    }
}

extension View {
    func appFont(_ style: AppFontStyle) -> some View {
        modifier(AppFontModifier(style: style))
    }
}

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: opacity
        )
    }
}
