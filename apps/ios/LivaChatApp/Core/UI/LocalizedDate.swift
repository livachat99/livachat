import Foundation

/// Locale-aware date symbols and clock formatting. Formatter instances are
/// never shared; only immutable symbol arrays are cached.
enum LocalizedDate {
    enum MonthStyle {
        case short
        case full
        case fullStandalone
        case numeric
    }

    enum WeekdayStyle {
        case short
        case full
    }

    private struct Symbols {
        let months: [String]
        let standaloneMonths: [String]
        let shortMonths: [String]
        let weekdays: [String]
        let shortWeekdays: [String]
    }

    private final class SymbolCache: @unchecked Sendable {
        private let lock = NSLock()
        private var values: [String: Symbols] = [:]

        func symbols(for key: String, make: () -> Symbols) -> Symbols {
            lock.lock()
            if let cached = values[key] {
                lock.unlock()
                return cached
            }
            lock.unlock()

            let created = make()
            lock.lock()
            if let cached = values[key] {
                lock.unlock()
                return cached
            }
            values[key] = created
            lock.unlock()
            return created
        }
    }

    private static let symbolCache = SymbolCache()

    static func monthName(month: Int, style: MonthStyle, locale: Locale) -> String {
        guard (1...12).contains(month) else { return String(month) }

        if case .numeric = style {
            return LocalizedNumber.integer(month, locale: locale)
        }

        let symbols = symbols(for: locale)
        let index = month - 1
        let names: [String]
        switch style {
        case .short:
            names = symbols.shortMonths
        case .full:
            names = symbols.months
        case .fullStandalone:
            names = symbols.standaloneMonths
        case .numeric:
            return LocalizedNumber.integer(month, locale: locale)
        }
        return names.indices.contains(index) ? names[index] : String(month)
    }

    static func weekdayName(
        weekday: Int,
        style: WeekdayStyle,
        locale: Locale,
        lowercase: Bool = false
    ) -> String {
        guard (1...7).contains(weekday) else { return "" }
        let symbols = symbols(for: locale)
        let index = weekday - 1
        let names = style == .short ? symbols.shortWeekdays : symbols.weekdays
        guard names.indices.contains(index) else { return "" }
        let value = names[index]
        return lowercase ? value.lowercased(with: locale) : value
    }

    static func time(
        date: Date,
        locale: Locale = AppLocale.current,
        timeZone: TimeZone = .autoupdatingCurrent,
        withSeconds: Bool = false
    ) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.locale = locale
        calendar.timeZone = timeZone
        let components = calendar.dateComponents([.hour, .minute, .second], from: date)
        let hour = LocalizedNumber.integer(components.hour ?? 0, locale: locale, minimumDigits: 2)
        let minute = LocalizedNumber.integer(components.minute ?? 0, locale: locale, minimumDigits: 2)
        if withSeconds {
            let second = LocalizedNumber.integer(components.second ?? 0, locale: locale, minimumDigits: 2)
            return "\(hour):\(minute):\(second)"
        }
        return "\(hour):\(minute)"
    }

    static func time(
        tsMs: Int64,
        locale: Locale = AppLocale.current,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        time(
            date: Date(timeIntervalSince1970: Double(tsMs) / 1_000),
            locale: locale,
            timeZone: timeZone
        )
    }

    static func longDate(
        date: Date,
        locale: Locale = AppLocale.current,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = timeZone
        formatter.dateStyle = .long
        formatter.timeStyle = .none
        return formatter.string(from: date)
    }

    static func fullWeekday(
        date: Date,
        locale: Locale = AppLocale.current,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.locale = locale
        calendar.timeZone = timeZone
        let weekday = calendar.component(.weekday, from: date)
        return weekdayName(weekday: weekday, style: .full, locale: locale)
    }

    private static func symbols(for locale: Locale) -> Symbols {
        let key = locale.identifier
        return symbolCache.symbols(for: key) {
            let formatter = DateFormatter()
            formatter.locale = locale
            formatter.calendar = Calendar(identifier: .gregorian)
            formatter.timeZone = TimeZone(secondsFromGMT: 0)
            return Symbols(
                months: formatter.monthSymbols ?? [],
                standaloneMonths: formatter.standaloneMonthSymbols ?? [],
                shortMonths: formatter.shortMonthSymbols ?? [],
                weekdays: formatter.weekdaySymbols ?? [],
                shortWeekdays: formatter.shortWeekdaySymbols ?? []
            )
        }
    }
}
