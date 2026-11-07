import CoreGraphics
import Foundation
import ImageIO

protocol AvatarStoring: Sendable {
    func loadLatestData() async -> Data?
    func save(_ data: Data) async throws -> URL
    func removeAll() async throws
    func hasStoredAvatar() async -> Bool
}

actor AvatarStore: AvatarStoring {
    private let directory: URL
    private let filePrefix = "custom_avatar_"

    init(directory: URL) {
        self.directory = directory
    }

    func loadLatestData() async -> Data? {
        guard let url = latestURL() else { return nil }
        return try? Data(contentsOf: url)
    }

    func save(_ data: Data) async throws -> URL {
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        let url = uniqueURL()
        do {
            try data.write(to: url, options: [.atomic])
        } catch {
            try? FileManager.default.removeItem(at: url)
            throw error
        }
        // Compare file names, not URLs: on device `applicationSupportDirectory`
        // can be `/var/...` while `contentsOfDirectory` returns `/private/var/...`,
        // so URL equality would delete the file we just wrote.
        let newName = url.lastPathComponent
        for old in storedFiles() where old.lastPathComponent != newName {
            try? FileManager.default.removeItem(at: old)
        }
        return url
    }

    func removeAll() async throws {
        for url in storedFiles() {
            try FileManager.default.removeItem(at: url)
        }
    }

    func hasStoredAvatar() async -> Bool {
        latestURL() != nil
    }

    private func storedFiles() -> [URL] {
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.contentModificationDateKey, .isRegularFileKey],
            options: [.skipsHiddenFiles]
        )) ?? []
        return urls.filter { $0.lastPathComponent.hasPrefix(filePrefix) }
    }

    private func latestURL() -> URL? {
        storedFiles().max { lhs, rhs in
            let left = modificationDate(lhs)
            let right = modificationDate(rhs)
            if left == right {
                return lhs.lastPathComponent < rhs.lastPathComponent
            }
            return left < right
        }
    }

    private func modificationDate(_ url: URL) -> Date {
        (try? url.resourceValues(forKeys: [.contentModificationDateKey])
            .contentModificationDate) ?? .distantPast
    }

    private func uniqueURL() -> URL {
        let base = Int64(Date().timeIntervalSince1970 * 1000)
        var candidate = directory.appendingPathComponent("\(filePrefix)\(base).jpg")
        var suffix = 1
        while FileManager.default.fileExists(atPath: candidate.path) {
            candidate = directory.appendingPathComponent("\(filePrefix)\(base)_\(suffix).jpg")
            suffix += 1
        }
        return candidate
    }
}

enum AvatarImageDecoder {
    static func decode(_ data: Data, maxPixelSize: Int = 2048) -> CGImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else {
            return nil
        }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
            kCGImageSourceShouldCacheImmediately: true,
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }
}
