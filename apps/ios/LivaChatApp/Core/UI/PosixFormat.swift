import Foundation

/// Formatting for technical/data values that must stay ASCII regardless of
/// the app or device locale (date keys, ISO week keys, hex digests).
enum PosixFormat {
    static let locale = Locale(identifier: "en_US_POSIX")

    static func string(_ format: String, _ args: CVarArg...) -> String {
        String(format: format, locale: locale, arguments: args)
    }
}
