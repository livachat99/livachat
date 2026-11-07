import CoreGraphics
import UIKit
import XCTest
@testable import LivaChatApp

final class AvatarStoreTests: XCTestCase {
    func testSaveLoadLatestAndRemove() async throws {
        let directory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = AvatarStore(directory: directory)
        let first = try makeJPEG(color: .red)
        let second = try makeJPEG(color: .blue)

        _ = try await store.save(first)
        let loadedFirst = await store.loadLatestData()
        let hasFirst = await store.hasStoredAvatar()
        XCTAssertEqual(loadedFirst, first)
        XCTAssertTrue(hasFirst)

        _ = try await store.save(second)
        let loadedSecond = await store.loadLatestData()
        XCTAssertEqual(loadedSecond, second)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: directory.path).count, 1)

        try await store.removeAll()
        let removed = await store.loadLatestData()
        let hasAfterRemove = await store.hasStoredAvatar()
        XCTAssertNil(removed)
        XCTAssertFalse(hasAfterRemove)
    }

    func testSaveInApplicationSupportKeepsNewFile() async throws {
        let base = try XCTUnwrap(
            FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        )
        let directory = base.appendingPathComponent(
            "avatar-store-path-test-\(UUID().uuidString)",
            isDirectory: true
        )
        defer { try? FileManager.default.removeItem(at: directory) }

        let store = AvatarStore(directory: directory)
        let data = try makeJPEG(color: .red)
        _ = try await store.save(data)

        let names = try FileManager.default.contentsOfDirectory(atPath: directory.path)
        XCTAssertEqual(names.filter { $0.hasPrefix("custom_avatar_") }.count, 1)
        let loaded = await store.loadLatestData()
        XCTAssertEqual(loaded, data)
    }

    func testSaveFailureKeepsOldAvatar() async throws {
        let root = makeDirectory()
        defer { try? FileManager.default.removeItem(at: root) }
        let oldDirectory = root.appendingPathComponent("avatar", isDirectory: true)
        let store = AvatarStore(directory: oldDirectory)
        let old = try makeJPEG(color: .green)
        _ = try await store.save(old)

        let fileBlockingDirectory = root.appendingPathComponent("blocked")
        try Data("not a directory".utf8).write(to: fileBlockingDirectory)
        let failing = AvatarStore(directory: fileBlockingDirectory)
        let new = try makeJPEG(color: .yellow)
        await XCTAssertThrowsErrorAsync {
            try await failing.save(new)
        }

        let loaded = await store.loadLatestData()
        XCTAssertEqual(loaded, old)
    }

    func testMissingDirectoryAndCorruptData() async {
        let directory = makeDirectory().appendingPathComponent("missing")
        let store = AvatarStore(directory: directory)
        let missing = await store.loadLatestData()
        let hasMissing = await store.hasStoredAvatar()
        XCTAssertNil(missing)
        XCTAssertFalse(hasMissing)

        let corruptDirectory = makeDirectory()
        defer { try? FileManager.default.removeItem(at: corruptDirectory) }
        let corruptStore = AvatarStore(directory: corruptDirectory)
        _ = try? await corruptStore.save(Data("not an image".utf8))
        let corrupt = await corruptStore.loadLatestData()
        XCTAssertNotNil(corrupt)
        XCTAssertNil(corrupt.flatMap { AvatarImageDecoder.decode($0) })
    }

    func testDecoderReturnsImageForJPEG() throws {
        let data = try makeJPEG(color: .purple)
        XCTAssertNotNil(AvatarImageDecoder.decode(data))
    }

    private func makeDirectory() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("avatar-store-\(UUID().uuidString)", isDirectory: true)
    }

    private func makeJPEG(color: UIColor) throws -> Data {
        let width = 8
        let height = 8
        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            throw NSError(domain: "AvatarStoreTests", code: 1)
        }
        context.setFillColor(color.cgColor)
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        guard let image = context.makeImage(),
              let data = CameraImageEncoder.jpegData(from: image, quality: 0.9) else {
            throw NSError(domain: "AvatarStoreTests", code: 2)
        }
        return data
    }
}

private func XCTAssertThrowsErrorAsync(
    _ operation: @escaping () async throws -> Void,
    file: StaticString = #filePath,
    line: UInt = #line
) async {
    do {
        try await operation()
        XCTFail("expected error", file: file, line: line)
    } catch {}
}
