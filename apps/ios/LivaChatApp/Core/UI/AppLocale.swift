import Foundation
import SwiftUI

/// App-language facts. The app language is the bundle's preferred localization,
/// not the device locale, because per-app language can differ from the system.
enum AppLocale {
    static let supportedCodes = [
        "en", "zh-Hans", "zh-Hant", "ja", "ko", "ru", "de",
        "fr", "it", "es", "pt", "hi", "id", "th", "vi", "ar",
    ]

    /// The bundle localization code, e.g. `zh-Hant` or `ar`.
    static var resourceCode: String {
        resolvePreferred(Bundle.main.preferredLocalizations)
    }

    /// The locale used for formatting (digits, month names, plural rules).
    static var current: Locale {
        formattingLocale(for: resourceCode)
    }

    /// Locale used for language display names in pickers.
    static var displayLocale: Locale {
        Locale(identifier: resourceCode)
    }

    static var languageCode: String {
        current.languageCode ?? "en"
    }

    static var isRTL: Bool {
        isRTL(forResourceCode: resourceCode)
    }

    static var calendar: Calendar {
        gregorian(timeZone: .autoupdatingCurrent)
    }

    static func resolvePreferred(_ preferred: [String]) -> String {
        for identifier in preferred {
            if supportedCodes.contains(identifier) {
                return identifier
            }
            let style = styleKey(for: Locale(identifier: identifier))
            if supportedCodes.contains(style) {
                return style
            }
        }
        return "en"
    }

    static func formattingLocale(for code: String) -> Locale {
        switch styleKey(for: Locale(identifier: code)) {
        case "ar":
            return Locale(identifier: "ar_SA")
        case "pt":
            return Locale(identifier: "pt-BR")
        case "id":
            return Locale(identifier: "id")
        default:
            return Locale(identifier: code)
        }
    }

    /// Normalizes a locale to the style key used by the date-label table.
    static func styleKey(for locale: Locale) -> String {
        let identifier = locale.identifier
            .replacingOccurrences(of: "_", with: "-")
            .lowercased()
        let parts = identifier.split(separator: "-").map(String.init)

        if parts.first == "zh" {
            let isTraditional = parts.dropFirst().contains { part in
                part == "hant" || part == "tw" || part == "hk" || part == "mo"
            }
            return isTraditional ? "zh-Hant" : "zh-Hans"
        }
        if parts.first == "in" {
            return "id"
        }
        return parts.first ?? "en"
    }

    static func gregorian(timeZone: TimeZone) -> Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.locale = current
        calendar.timeZone = timeZone
        return calendar
    }

    static func isRTL(forResourceCode code: String) -> Bool {
        let language = styleKey(for: Locale(identifier: code))
        return Locale.characterDirection(forLanguage: language) == .rightToLeft
    }
}

extension View {
    /// Applies the app language for formatting and layout direction.
    /// Cover presentations receive this explicitly so tests and iOS 15
    /// presentation behavior are deterministic.
    func appLocalizationEnvironment() -> some View {
        environment(\.locale, AppLocale.current)
            .environment(
                \.layoutDirection,
                AppLocale.isRTL ? .rightToLeft : .leftToRight
            )
    }
}
