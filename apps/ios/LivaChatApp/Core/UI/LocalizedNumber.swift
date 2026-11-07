import Foundation

enum LocalizedNumber {
    static func integer(
        _ value: Int,
        locale: Locale = AppLocale.current,
        minimumDigits: Int = 1,
        grouping: Bool = false
    ) -> String {
        let formatter = NumberFormatter()
        formatter.locale = locale
        formatter.numberStyle = .decimal
        formatter.usesGroupingSeparator = grouping
        formatter.minimumIntegerDigits = max(1, minimumDigits)
        formatter.maximumFractionDigits = 0
        formatter.minimumFractionDigits = 0
        return formatter.string(from: NSNumber(value: value)) ?? String(value)
    }

    static func integer(
        _ value: Int64,
        locale: Locale = AppLocale.current,
        minimumDigits: Int = 1,
        grouping: Bool = false
    ) -> String {
        let formatter = NumberFormatter()
        formatter.locale = locale
        formatter.numberStyle = .decimal
        formatter.usesGroupingSeparator = grouping
        formatter.minimumIntegerDigits = max(1, minimumDigits)
        formatter.maximumFractionDigits = 0
        formatter.minimumFractionDigits = 0
        return formatter.string(from: NSNumber(value: value)) ?? String(value)
    }

    static func timecode(ms: Int64, locale: Locale = AppLocale.current) -> String {
        let totalSeconds = max(ms, 0) / 1_000
        let minutes = integer(totalSeconds / 60, locale: locale)
        let seconds = integer(totalSeconds % 60, locale: locale, minimumDigits: 2)
        return "\(minutes):\(seconds)"
    }

    static func page(index: Int, total: Int, locale: Locale = AppLocale.current) -> String {
        L10n.format(
            "common_page_indicator",
            locale: locale,
            integer(index, locale: locale),
            integer(total, locale: locale)
        )
    }
}
