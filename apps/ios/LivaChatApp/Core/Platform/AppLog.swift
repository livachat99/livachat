import Foundation
import os

/// Unified append-only log file shared by the App and the SDK.
///
/// Each line starts with an ISO 8601 UTC timestamp (microseconds), followed by
/// `<level>/<component>` and the message. Newlines are escaped so one event is
/// always exactly one line.
final class LogFileWriter: @unchecked Sendable {
    static let timestampFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'"

    let fileURL: URL
    private let queue = DispatchQueue(label: "dev.livachat.filelog", qos: .utility)
    private let formatter: DateFormatter
    private var fileDescriptor: Int32 = -1

    init(fileURL: URL) {
        self.fileURL = fileURL

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = Self.timestampFormat
        self.formatter = formatter

        let directory = fileURL.deletingLastPathComponent()
        try? FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        fileDescriptor = open(fileURL.path, O_WRONLY | O_CREAT | O_APPEND, 0o644)
    }

    deinit {
        if fileDescriptor >= 0 {
            close(fileDescriptor)
        }
    }

    func log(level: String, component: String, message: String) {
        let date = Date()
        queue.async { [weak self] in
            guard let self, self.fileDescriptor >= 0 else { return }
            let timestamp = self.formatter.string(from: date)
            let clean = Self.sanitize(message)
            let line = "\(timestamp) \(level)/\(component) \(clean)\n"
            let data = Data(line.utf8)
            data.withUnsafeBytes { buffer in
                guard let base = buffer.baseAddress else { return }
                _ = write(self.fileDescriptor, base, buffer.count)
            }
        }
    }

    func flush() {
        queue.sync {}
    }

    private static func sanitize(_ message: String) -> String {
        let clipped = message.count > 8_192 ? String(message.prefix(8_192)) : message
        return clipped
            .replacingOccurrences(of: "\r", with: "\\r")
            .replacingOccurrences(of: "\n", with: "\\n")
    }
}

enum AppLog {
    static let fileURL: URL = {
        let library = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        return library
            .appendingPathComponent("Logs", isDirectory: true)
            .appendingPathComponent("livachat.log")
    }()

    private static let writer = LogFileWriter(fileURL: fileURL)
    private static let systemSubsystem = "com.livachat.livachatapp"

    static func startSession() {
        info(
            "System",
            "session start app=\(AppInfo.displayVersion) os=\(ProcessInfo.processInfo.operatingSystemVersionString) log=\(fileURL.path)"
        )
    }

    static func debug(_ component: String, _ message: String) {
        write("D", component, message)
    }

    static func info(_ component: String, _ message: String) {
        write("I", component, message)
    }

    static func warn(_ component: String, _ message: String) {
        write("W", component, message)
    }

    static func error(_ component: String, _ message: String) {
        write("E", component, message)
    }

    private static func write(_ level: String, _ component: String, _ message: String) {
        writer.log(level: level, component: "App/\(component)", message: message)

        let logger = Logger(subsystem: systemSubsystem, category: component)
        switch level {
        case "D":
            logger.debug("\(message, privacy: .public)")
        case "W":
            logger.warning("\(message, privacy: .public)")
        case "E":
            logger.error("\(message, privacy: .public)")
        default:
            logger.info("\(message, privacy: .public)")
        }
    }
}
