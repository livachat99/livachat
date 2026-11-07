import Foundation

enum HistoryDateFormatting {
    static func subtitle(
        for session: HistorySession,
        locale: Locale = AppLocale.current,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        let date = LocalizedDate.longDate(
            date: session.startTime,
            locale: locale,
            timeZone: timeZone
        )
        let weekday = LocalizedDate.fullWeekday(
            date: session.startTime,
            locale: locale,
            timeZone: timeZone
        )
        return L10n.format(
            "history_item_meta",
            locale: locale,
            date,
            weekday,
            session.messageCount
        )
    }
}
