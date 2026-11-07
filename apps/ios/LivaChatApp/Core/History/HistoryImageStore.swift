import CryptoKit
import Foundation

enum HistoryImageStoreError: Error, LocalizedError {
    case invalidFilename(String)
    case writeFailed(String)

    var errorDescription: String? {
        switch self {
        case let .invalidFilename(name): return "invalid history image filename \(name)"
        case let .writeFailed(message): return "history image write failed: \(message)"
        }
    }
}

actor HistoryImageStore {
    private let directory: URL
    private let fileManager = FileManager.default

    init(directory: URL) throws {
        self.directory = directory
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    static func filename(for messageID: String) -> String {
        let digest = SHA256.hash(data: Data(messageID.utf8))
        return digest.map { PosixFormat.string("%02x", $0) }.joined() + ".jpg"
    }

    func write(_ data: Data, messageID: String) throws -> String {
        let filename = Self.filename(for: messageID)
        let url = directory.appendingPathComponent(filename, isDirectory: false)
        do {
            try data.write(to: url, options: [.atomic])
            return filename
        } catch {
            throw HistoryImageStoreError.writeFailed(error.localizedDescription)
        }
    }

    /// Reads a stored image. When `maxBytes` is provided, oversized files are
    /// skipped before allocating their contents. This bounds a single file,
    /// not the aggregate — historical images are still loaded eagerly.
    func read(filename: String, maxBytes: Int? = nil) throws -> Data? {
        guard isSafe(filename) else {
            throw HistoryImageStoreError.invalidFilename(filename)
        }
        let url = directory.appendingPathComponent(filename, isDirectory: false)
        guard fileManager.fileExists(atPath: url.path) else { return nil }
        if let maxBytes {
            let attributes = try? fileManager.attributesOfItem(atPath: url.path)
            let size = (attributes?[.size] as? NSNumber)?.intValue ?? 0
            guard size <= maxBytes else {
                AppLog.warn(
                    "History",
                    "skip oversized image \(filename) bytes=\(size) limit=\(maxBytes)"
                )
                return nil
            }
        }
        return try Data(contentsOf: url)
    }

    func delete(filenames: [String]) {
        for filename in filenames where isSafe(filename) {
            let url = directory.appendingPathComponent(filename, isDirectory: false)
            try? fileManager.removeItem(at: url)
        }
    }

    func deleteAll() {
        try? fileManager.removeItem(at: directory)
        try? fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    func resetForTests() {
        deleteAll()
    }

    private func isSafe(_ filename: String) -> Bool {
        guard !filename.isEmpty,
              filename == (filename as NSString).lastPathComponent,
              !filename.contains("/"),
              !filename.contains("\\"),
              !filename.contains("..") else {
            return false
        }
        return true
    }
}
