import Foundation
import LivaChatSDK

enum AgentJobFormatting {
    static func badgeCount(_ count: Int, locale: Locale = AppLocale.current) -> String {
        if count > 99 {
            return "\(LocalizedNumber.integer(99, locale: locale))+"
        }
        return LocalizedNumber.integer(count, locale: locale)
    }

    static func runningMilliseconds(for job: AgentJob, nowMs: Int64) -> Int64 {
        let elapsed = nowMs - job.startedAtMs
        return elapsed >= 0 ? elapsed : job.runningTimeMs
    }

    static func startedTime(
        milliseconds: Int64,
        locale: Locale = AppLocale.current,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        LocalizedDate.time(
            date: Date(timeIntervalSince1970: Double(milliseconds) / 1_000),
            locale: locale,
            timeZone: timeZone,
            withSeconds: true
        )
    }

    static func clockTime(
        date: Date,
        locale: Locale = AppLocale.current,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        LocalizedDate.time(date: date, locale: locale, timeZone: timeZone)
    }

    static func elapsed(milliseconds: Int64, locale: Locale = AppLocale.current) -> String {
        let totalSeconds = Int(max(0, milliseconds / 1_000))
        let hours = totalSeconds / 3_600
        let minutes = totalSeconds % 3_600 / 60
        let seconds = totalSeconds % 60
        if hours > 0 {
            return "\(LocalizedNumber.integer(hours, locale: locale))h \(LocalizedNumber.integer(minutes, locale: locale))m"
        }
        if totalSeconds >= 60 {
            return "\(LocalizedNumber.integer(minutes, locale: locale))m \(LocalizedNumber.integer(seconds, locale: locale))s"
        }
        return "\(LocalizedNumber.integer(seconds, locale: locale))s"
    }
}
