import Foundation

/// Date/number labels for MyLife. The public signatures stay stable; the
/// locale-specific structure comes from string-catalog templates.
enum MylifeDateLabels {
    private static let utc = TimeZone(secondsFromGMT: 0)!

    private enum WeekRangePattern {
        case monthDaySlash
        case dayMonthDot
        case dayMonthDotTrailing
        case dayMonthSlash
    }

    private struct Style {
        let monthYear: LocalizedDate.MonthStyle
        let monthHeader: LocalizedDate.MonthStyle
        let monthDay: LocalizedDate.MonthStyle
        let weekday: LocalizedDate.WeekdayStyle
        let weekdayLowercase: Bool
        let weekRange: WeekRangePattern
    }

    static var effectiveLocale: Locale {
        AppLocale.current
    }

    // MARK: - Date-key helpers (timezone-less local dates)

    static func components(fromDateKey key: String) -> DateComponents? {
        let parts = key.split(separator: "-", omittingEmptySubsequences: false)
        guard parts.count == 3,
              let year = Int(parts[0]), let month = Int(parts[1]), let day = Int(parts[2]),
              (1...9999).contains(year), (1...12).contains(month), (1...31).contains(day),
              let date = date(year: year, month: month, day: day) else {
            return nil
        }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        let validated = calendar.dateComponents([.year, .month, .day], from: date)
        guard validated.year == year, validated.month == month, validated.day == day else {
            return nil
        }
        return validated
    }

    static func dateKey(year: Int, month: Int, day: Int) -> String {
        PosixFormat.string("%04d-%02d-%02d", year, month, day)
    }

    static func date(year: Int, month: Int, day: Int) -> Date? {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        var components = DateComponents()
        components.calendar = calendar
        components.timeZone = utc
        components.year = year
        components.month = month
        components.day = day
        components.hour = 12
        return calendar.date(from: components)
    }

    static func offsetDateKey(year: Int, month: Int, day: Int, days: Int) -> String? {
        guard let base = date(year: year, month: month, day: day) else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        guard let shifted = calendar.date(byAdding: .day, value: days, to: base) else { return nil }
        let c = calendar.dateComponents([.year, .month, .day], from: shifted)
        guard let y = c.year, let m = c.month, let d = c.day else { return nil }
        return dateKey(year: y, month: m, day: d)
    }

    static func daysInMonth(year: Int, month: Int) -> Int {
        guard let first = date(year: year, month: month, day: 1) else { return 30 }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        let range = calendar.range(of: .day, in: .month, for: first)
        return range?.count ?? 30
    }

    static func isoWeekKey(forDateKey key: String) -> String? {
        guard let components = components(fromDateKey: key),
              let y = components.year, let m = components.month, let d = components.day,
              let date = date(year: y, month: m, day: d) else { return nil }
        var calendar = Calendar(identifier: .iso8601)
        calendar.timeZone = utc
        let iso = calendar.dateComponents([.yearForWeekOfYear, .weekOfYear], from: date)
        guard let weekYear = iso.yearForWeekOfYear, let week = iso.weekOfYear else { return nil }
        return PosixFormat.string("%04d-W%02d", weekYear, week)
    }

    static func mondayOfIsoWeek(_ weekKey: String) -> (year: Int, month: Int, day: Int)? {
        let parts = weekKey.components(separatedBy: "-W")
        guard parts.count == 2,
              let weekYear = Int(parts[0]),
              let week = Int(parts[1]),
              (1...53).contains(week) else { return nil }
        var calendar = Calendar(identifier: .iso8601)
        calendar.timeZone = utc
        var components = DateComponents()
        components.calendar = calendar
        components.timeZone = utc
        components.yearForWeekOfYear = weekYear
        components.weekOfYear = week
        components.weekday = 2
        guard let date = calendar.date(from: components) else { return nil }
        let c = calendar.dateComponents([.year, .month, .day], from: date)
        guard let y = c.year, let m = c.month, let d = c.day else { return nil }
        return (y, m, d)
    }

    static func weekRangeLabel(forDateKey key: String, locale: Locale = effectiveLocale) -> String {
        guard let weekKey = isoWeekKey(forDateKey: key),
              let monday = mondayOfIsoWeek(weekKey),
              let sunday = offsetDateKey(year: monday.year, month: monday.month, day: monday.day, days: 6),
              let sundayComponents = components(fromDateKey: sunday) else {
            return ""
        }
        let style = style(for: locale)
        let first = weekRangeDate(
            month: monday.month,
            day: monday.day,
            pattern: style.weekRange,
            locale: locale
        )
        let second = weekRangeDate(
            month: sundayComponents.month ?? 1,
            day: sundayComponents.day ?? 1,
            pattern: style.weekRange,
            locale: locale
        )
        return L10n.format("mylife_week_range_format", locale: locale, first, second)
    }

    static func weekNumberLabel(forWeekKey key: String, locale: Locale = effectiveLocale) -> String {
        let week = Int(key.components(separatedBy: "-W").last ?? "") ?? 0
        return L10n.format(
            "mylife_week_label",
            locale: locale,
            LocalizedNumber.integer(week, locale: locale)
        )
    }

    // MARK: - Display labels

    static func yearText(_ year: Int, locale: Locale = effectiveLocale) -> String {
        L10n.format(
            "mylife_year_format",
            locale: locale,
            LocalizedNumber.integer(year, locale: locale)
        )
    }

    static func monthYearText(year: Int, month: Int, locale: Locale = effectiveLocale) -> String {
        let style = style(for: locale)
        let monthText = LocalizedDate.monthName(month: month, style: style.monthYear, locale: locale)
        let yearText = LocalizedNumber.integer(year, locale: locale)
        return L10n.format("mylife_month_year_format", locale: locale, monthText, yearText)
    }

    static func monthHeader(year: Int, month: Int, locale: Locale = effectiveLocale) -> String {
        let style = style(for: locale)
        let yearText = LocalizedNumber.integer(year, locale: locale)
        let monthText = LocalizedDate.monthName(month: month, style: style.monthHeader, locale: locale)
        return L10n.format("mylife_overview_month", locale: locale, yearText, monthText)
    }

    static func monthDayText(month: Int, day: Int, locale: Locale = effectiveLocale) -> String {
        let style = style(for: locale)
        let monthText = LocalizedDate.monthName(month: month, style: style.monthDay, locale: locale)
        let dayText = LocalizedNumber.integer(day, locale: locale)
        return L10n.format("mylife_month_day_format", locale: locale, monthText, dayText)
    }

    static func monthDayText(
        date: Date,
        locale: Locale = effectiveLocale,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let components = calendar.dateComponents([.month, .day], from: date)
        guard let month = components.month, let day = components.day else { return "" }
        return monthDayText(month: month, day: day, locale: locale)
    }

    static func weekdayText(forDateKey key: String, locale: Locale = effectiveLocale) -> String {
        guard let components = components(fromDateKey: key),
              let y = components.year, let m = components.month, let d = components.day,
              let date = date(year: y, month: m, day: d) else { return "" }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        let weekday = calendar.component(.weekday, from: date)
        let style = style(for: locale)
        return LocalizedDate.weekdayName(
            weekday: weekday,
            style: style.weekday,
            locale: locale,
            lowercase: style.weekdayLowercase
        )
    }

    static func dayLabel(forDateKey key: String, locale: Locale = effectiveLocale) -> String {
        guard let components = components(fromDateKey: key),
              let month = components.month, let day = components.day else { return key }
        return "\(monthDayText(month: month, day: day, locale: locale)) · \(weekdayText(forDateKey: key, locale: locale))"
    }

    static func searchDayLabel(forDateKey key: String, locale: Locale = effectiveLocale) -> String {
        guard let components = components(fromDateKey: key),
              let month = components.month, let day = components.day else { return key }
        return monthDayText(month: month, day: day, locale: locale)
    }

    static func scopeLabel(_ scopeKey: String, locale: Locale = effectiveLocale) -> String {
        if scopeKey.count == 4, let year = Int(scopeKey) {
            return yearText(year, locale: locale)
        }
        if scopeKey.count == 7 {
            let parts = scopeKey.split(separator: "-")
            if parts.count == 2, let year = Int(parts[0]), let month = Int(parts[1]) {
                return monthYearText(year: year, month: month, locale: locale)
            }
        }
        return scopeKey
    }

    static func timeLabel(
        tsMs: Int64,
        locale: Locale = effectiveLocale,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        LocalizedDate.time(tsMs: tsMs, locale: locale, timeZone: timeZone)
    }

    static func hotSlotTitle(
        tsMs: Int64,
        locale: Locale = effectiveLocale,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        let date = Date(timeIntervalSince1970: Double(tsMs) / 1_000)
        return "\(monthDayText(date: date, locale: locale, timeZone: timeZone)) \(timeLabel(tsMs: tsMs, locale: locale, timeZone: timeZone))"
    }

    static func localDateKey(tsMs: Int64, timeZone: TimeZone = .autoupdatingCurrent) -> String {
        let date = Date(timeIntervalSince1970: Double(tsMs) / 1_000)
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let c = calendar.dateComponents([.year, .month, .day], from: date)
        return dateKey(year: c.year ?? 0, month: c.month ?? 0, day: c.day ?? 0)
    }

    static func daysBetweenInclusive(from: String, to: String) -> Int? {
        guard let a = components(fromDateKey: from),
              let b = components(fromDateKey: to),
              let da = date(year: a.year ?? 0, month: a.month ?? 0, day: a.day ?? 0),
              let db = date(year: b.year ?? 0, month: b.month ?? 0, day: b.day ?? 0) else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        let start = calendar.startOfDay(for: da)
        let end = calendar.startOfDay(for: db)
        guard let days = calendar.dateComponents([.day], from: start, to: end).day else { return nil }
        return max(1, days + 1)
    }

    static func dayAfter(_ key: String) -> String? {
        guard let c = components(fromDateKey: key),
              let y = c.year, let m = c.month, let d = c.day else { return nil }
        return offsetDateKey(year: y, month: m, day: d, days: 1)
    }

    // MARK: - Styles

    private static func style(for locale: Locale) -> Style {
        switch AppLocale.styleKey(for: locale) {
        case "zh-Hans", "zh-Hant":
            return Style(
                monthYear: .numeric,
                monthHeader: .numeric,
                monthDay: .numeric,
                weekday: .short,
                weekdayLowercase: false,
                weekRange: .monthDaySlash
            )
        case "ja", "ko":
            return Style(
                monthYear: .numeric,
                monthHeader: .numeric,
                monthDay: .numeric,
                weekday: .full,
                weekdayLowercase: false,
                weekRange: .monthDaySlash
            )
        case "ru":
            return Style(
                monthYear: .fullStandalone,
                monthHeader: .fullStandalone,
                monthDay: .short,
                weekday: .short,
                weekdayLowercase: true,
                weekRange: .dayMonthDot
            )
        case "de":
            return Style(
                monthYear: .full,
                monthHeader: .full,
                monthDay: .short,
                weekday: .short,
                weekdayLowercase: false,
                weekRange: .dayMonthDotTrailing
            )
        case "fr", "pt":
            return Style(
                monthYear: .full,
                monthHeader: .full,
                monthDay: .short,
                weekday: .short,
                weekdayLowercase: false,
                weekRange: .dayMonthSlash
            )
        case "it", "es":
            return Style(
                monthYear: .full,
                monthHeader: .full,
                monthDay: .short,
                weekday: .full,
                weekdayLowercase: false,
                weekRange: .dayMonthSlash
            )
        case "hi", "id", "th":
            return Style(
                monthYear: .full,
                monthHeader: .full,
                monthDay: .short,
                weekday: .short,
                weekdayLowercase: false,
                weekRange: .dayMonthSlash
            )
        case "vi":
            return Style(
                monthYear: .fullStandalone,
                monthHeader: .fullStandalone,
                monthDay: .short,
                weekday: .full,
                weekdayLowercase: false,
                weekRange: .dayMonthSlash
            )
        case "ar":
            return Style(
                monthYear: .full,
                monthHeader: .full,
                monthDay: .short,
                weekday: .full,
                weekdayLowercase: false,
                weekRange: .dayMonthSlash
            )
        default:
            return Style(
                monthYear: .short,
                monthHeader: .short,
                monthDay: .short,
                weekday: .short,
                weekdayLowercase: false,
                weekRange: .monthDaySlash
            )
        }
    }

    private static func weekRangeDate(
        month: Int,
        day: Int,
        pattern: WeekRangePattern,
        locale: Locale
    ) -> String {
        switch pattern {
        case .monthDaySlash:
            return "\(LocalizedNumber.integer(month, locale: locale))/\(LocalizedNumber.integer(day, locale: locale))"
        case .dayMonthDot:
            return "\(LocalizedNumber.integer(day, locale: locale, minimumDigits: 2)).\(LocalizedNumber.integer(month, locale: locale, minimumDigits: 2))"
        case .dayMonthDotTrailing:
            return "\(LocalizedNumber.integer(day, locale: locale, minimumDigits: 2)).\(LocalizedNumber.integer(month, locale: locale, minimumDigits: 2))."
        case .dayMonthSlash:
            return "\(LocalizedNumber.integer(day, locale: locale, minimumDigits: 2))/\(LocalizedNumber.integer(month, locale: locale, minimumDigits: 2))"
        }
    }
}
