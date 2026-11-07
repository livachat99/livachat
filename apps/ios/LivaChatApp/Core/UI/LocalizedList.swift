import Foundation

/// Locale-aware joining for composite labels (accessibility and compact UI).
enum LocalizedList {
    static func join(_ parts: [String?], locale: Locale = AppLocale.current) -> String {
        let values = parts
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard !values.isEmpty else { return "" }

        let formatter = ListFormatter()
        formatter.locale = locale
        return formatter.string(from: values) ?? values.joined(separator: ", ")
    }
}
