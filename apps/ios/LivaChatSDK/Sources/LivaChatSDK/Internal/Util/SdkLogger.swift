import Foundation
import os

/// SDK logger: os.Logger plus an optional unified append-only file.
///
/// File format (one event per line):
/// `<ISO8601 UTC microseconds> <level>/SDK/<tag> <message>`
internal final class SdkLogger: @unchecked Sendable {
    private enum Level: String {
        case debug = "D"
        case info = "I"
        case warn = "W"
        case error = "E"
    }

    static let timestampFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'"

    private let logger: Logger
    private let queue = SDKQueue.log
    private let formatter: DateFormatter
    private var fileDescriptor: Int32 = -1

    init(logFileURL: URL?) {
        logger = Logger(subsystem: "dev.livachat.sdk", category: "SDK")

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = Self.timestampFormat
        self.formatter = formatter

        if let url = logFileURL {
            let directory = url.deletingLastPathComponent()
            try? FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true
            )
            fileDescriptor = open(url.path, O_WRONLY | O_CREAT | O_APPEND, 0o644)
        }
    }

    deinit {
        // The last strong reference may be the `guard let self` inside a queued
        // log block, so deinit can run on the log queue itself. Enqueue the close
        // instead; FIFO ordering places it after every pending write.
        let fileDescriptor = fileDescriptor
        if fileDescriptor >= 0 {
            queue.async {
                close(fileDescriptor)
            }
        }
    }

    func debug(_ tag: String, _ message: String) {
        log(.debug, tag: tag, message: message, error: nil)
    }

    func info(_ tag: String, _ message: String) {
        log(.info, tag: tag, message: message, error: nil)
    }

    func warn(_ tag: String, _ message: String, error: Error? = nil) {
        log(.warn, tag: tag, message: message, error: error)
    }

    func error(_ tag: String, _ message: String, error: Error? = nil) {
        log(.error, tag: tag, message: message, error: error)
    }

    private func log(_ level: Level, tag: String, message: String, error: Error?) {
        let text = "LivaChat-\(tag): \(message)"
        switch level {
        case .debug:
            logger.debug("\(text, privacy: .public)")
        case .info:
            logger.info("\(text, privacy: .public)")
        case .warn:
            logger.warning("\(text, privacy: .public)")
        case .error:
            logger.error("\(text, privacy: .public)")
        }

        let date = Date()
        queue.async { [weak self] in
            guard let self, self.fileDescriptor >= 0 else { return }
            var body = message
            if let error {
                body += " | \(String(describing: error))"
            }
            let clean = Self.sanitize(body)
            let timestamp = self.formatter.string(from: date)
            let line = "\(timestamp) \(level.rawValue)/SDK/\(tag) \(clean)\n"
            let data = Data(line.utf8)
            data.withUnsafeBytes { buffer in
                guard let base = buffer.baseAddress else { return }
                _ = write(self.fileDescriptor, base, buffer.count)
            }
        }
    }

    private static func sanitize(_ message: String) -> String {
        let clipped = message.count > 8_192 ? String(message.prefix(8_192)) : message
        return clipped
            .replacingOccurrences(of: "\r", with: "\\r")
            .replacingOccurrences(of: "\n", with: "\\n")
    }
}
