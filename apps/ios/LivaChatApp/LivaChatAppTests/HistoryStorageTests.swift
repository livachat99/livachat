import LivaChatSDK
import XCTest
@testable import LivaChatApp

@MainActor
final class HistoryStorageTests: XCTestCase {
    private func makeRoot() -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("history-tests-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func cleanup(_ url: URL) {
        try? FileManager.default.removeItem(at: url)
    }

    private var testCalendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        return calendar
    }

    // MARK: - Database

    func testSessionAndMessagePersistenceSearchAndOrdering() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let db = try HistoryDatabase(fileURL: root.appendingPathComponent("livachat.db"))
        let day1 = Date(timeIntervalSince1970: 1_700_000_000)
        let day2 = day1.addingTimeInterval(86_400)
        let first = try await db.createOrGetTodaySession(date: "2026-10-20", now: day1)
        let second = try await db.createOrGetTodaySession(date: "2026-10-21", now: day2)

        try await db.append(
            record(sessionID: first.id, role: .user, text: "weather in Paris", seq: 0)
        )
        try await db.append(
            record(sessionID: first.id, role: .assistant, text: "It is sunny in Paris", seq: 1)
        )
        try await db.append(
            record(sessionID: second.id, role: .user, text: "dinner ideas", seq: 0)
        )

        let all = try await db.sessions(search: "")
        XCTAssertEqual(all.map(\.id), [second.id, first.id])
        XCTAssertEqual(all.first(where: { $0.id == first.id })?.messageCount, 2)
        XCTAssertEqual(all.first(where: { $0.id == first.id })?.firstUserMessage, "weather in Paris")

        let byMessage = try await db.sessions(search: "sunny")
        XCTAssertEqual(byMessage.map(\.id), [first.id])
        let byFirstUser = try await db.sessions(search: "dinner")
        XCTAssertEqual(byFirstUser.map(\.id), [second.id])
        let none = try await db.sessions(search: "missing")
        XCTAssertTrue(none.isEmpty)

        let messages = try await db.messages(sessionID: first.id)
        XCTAssertEqual(messages.map(\.seq), [0, 1])
        XCTAssertEqual(messages.map(\.text), ["weather in Paris", "It is sunny in Paris"])
        let maxSeq = try await db.maxSeq(sessionID: first.id)
        XCTAssertEqual(maxSeq, 1)

        await db.close()
    }

    func testDailySessionReuseAndForceNew() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let db = try HistoryDatabase(fileURL: root.appendingPathComponent("livachat.db"))
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let first = try await db.createOrGetTodaySession(date: "2026-10-20", now: now)
        let reused = try await db.createOrGetTodaySession(date: "2026-10-20", now: now.addingTimeInterval(60))
        XCTAssertEqual(first.id, reused.id)

        let forced = try await db.forceNewTodaySession(date: "2026-10-20", now: now.addingTimeInterval(120))
        XCTAssertNotEqual(first.id, forced.id)
        let reusedForced = try await db.createOrGetTodaySession(date: "2026-10-20", now: now.addingTimeInterval(180))
        XCTAssertEqual(forced.id, reusedForced.id)

        let sessions = try await db.sessions(search: "")
        XCTAssertEqual(sessions.count, 2)
        await db.close()
    }

    func testDeleteSessionCascadesAndReturnsImagePaths() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let db = try HistoryDatabase(fileURL: root.appendingPathComponent("livachat.db"))
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let session = try await db.createOrGetTodaySession(date: "2026-10-20", now: now)
        var message = record(sessionID: session.id, role: .assistant, text: "image", seq: 0)
        message.imagePath = "abc.jpg"
        try await db.append(message)

        let images = try await db.deleteSession(id: session.id)
        XCTAssertEqual(images, ["abc.jpg"])
        let remainingMessages = try await db.messages(sessionID: session.id)
        XCTAssertTrue(remainingMessages.isEmpty)
        let deletedSession = try await db.session(id: session.id)
        XCTAssertNil(deletedSession)
        await db.close()
    }

    func testDatabasePersistsAcrossReopen() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let url = root.appendingPathComponent("livachat.db")
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let db1 = try HistoryDatabase(fileURL: url)
        let session = try await db1.createOrGetTodaySession(date: "2026-10-20", now: now)
        try await db1.append(record(sessionID: session.id, role: .user, text: "hello", seq: 0))
        await db1.close()

        let db2 = try HistoryDatabase(fileURL: url)
        let sessions = try await db2.sessions(search: "")
        XCTAssertEqual(sessions.count, 1)
        let messages = try await db2.messages(sessionID: session.id)
        XCTAssertEqual(messages.first?.text, "hello")
        await db2.close()
    }

    // MARK: - Summary & images

    func testSummaryUsesFirstThreeUserMessagesAndTruncates() {
        let root = makeRoot()
        defer { cleanup(root) }
        let long = String(repeating: "x", count: 120)
        let records = [
            record(sessionID: "s", role: .user, text: "one", seq: 0),
            record(sessionID: "s", role: .assistant, text: "ignored", seq: 1),
            record(sessionID: "s", role: .user, text: "two", seq: 2),
            record(sessionID: "s", role: .user, text: long, seq: 3),
            record(sessionID: "s", role: .user, text: "four", seq: 4)
        ]
        let summary = HistorySummary.make(from: records)
        XCTAssertEqual(summary.firstUserMessage, "one")
        XCTAssertEqual(summary.summary, String(("one two " + long).prefix(100)) + "...")
    }

    func testImageStoreUsesSafeHashedFilename() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let store = try HistoryImageStore(directory: root.appendingPathComponent("chat_images"))
        let data = Data("jpeg".utf8)
        let filename = try await store.write(data, messageID: "../../evil")
        XCTAssertEqual(filename.count, 68)
        XCTAssertTrue(filename.hasSuffix(".jpg"))
        XCTAssertFalse(filename.contains("/"))
        let readBack = try await store.read(filename: filename)
        XCTAssertEqual(readBack, data)
        do {
            _ = try await store.read(filename: "../evil.jpg")
            XCTFail("path traversal must be rejected")
        } catch {
            XCTAssertTrue(error is HistoryImageStoreError)
        }
    }

    // MARK: - Service

    func testImageStoreSkipsFilesAboveReadLimit() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let store = try HistoryImageStore(directory: root.appendingPathComponent("chat_images"))
        let data = Data(repeating: 0xAB, count: 32)
        let filename = try await store.write(data, messageID: "oversized")

        let skipped = try await store.read(filename: filename, maxBytes: 16)
        let readBack = try await store.read(filename: filename, maxBytes: 32)

        XCTAssertNil(skipped)
        XCTAssertEqual(readBack, data)
    }

    func testServicePersistsImageOnlyAssistantMessage() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let service = HistoryService(baseDirectory: root, now: { now }, calendar: testCalendar)
        let seedValue = await service.beginSession()
        let seed = try XCTUnwrap(seedValue)

        let image = AiImage(
            data: Data([0xFF, 0xD8, 0xFF, 0xD9]),
            mime: "image/jpeg",
            width: 1,
            height: 1,
            orientation: .portrait0,
            cameraFacing: .gallery
        )
        service.recordAssistantMessage(
            id: "ai-image-only",
            text: "",
            timestamp: now,
            image: image,
            source: nil,
            mode: nil
        )
        await service.waitForPendingWrites()

        let detail = await service.loadDetail(sessionID: seed.sessionID)
        let message = try XCTUnwrap(detail.messages.first(where: { $0.id == "ai-image-only" }))
        XCTAssertEqual(message.text, "")
        XCTAssertEqual(message.image?.data, image.data)
    }

    func testServiceSkipsOversizedHistoryImages() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let service = HistoryService(baseDirectory: root, now: { now }, calendar: testCalendar)
        let seedValue = await service.beginSession()
        let seed = try XCTUnwrap(seedValue)

        let oversized = AiImage(
            data: Data(repeating: 0xAB, count: 2 * 1024 * 1024 + 1),
            mime: "image/jpeg",
            width: 10,
            height: 20,
            orientation: .portrait0,
            cameraFacing: .back
        )
        service.recordAssistantMessage(
            id: "ai-large",
            text: "large image",
            timestamp: now,
            image: oversized,
            source: .llm,
            mode: nil
        )
        await service.waitForPendingWrites()

        let detail = await service.loadDetail(sessionID: seed.sessionID)
        let message = try XCTUnwrap(detail.messages.first(where: { $0.id == "ai-large" }))
        XCTAssertNil(message.image)
        XCTAssertEqual(message.text, "large image")
    }

    func testServiceLifecycleSeedFinishAndRestart() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let service = HistoryService(
            baseDirectory: root,
            now: { now },
            calendar: testCalendar
        )
        let seedValue = await service.beginSession()
        let seed = try XCTUnwrap(seedValue)
        XCTAssertTrue(seed.messages.isEmpty)

        let image = AiImage(
            data: Data("image".utf8),
            mime: "image/jpeg",
            width: 10,
            height: 20,
            orientation: .landscape90,
            cameraFacing: .back
        )
        service.recordUserMessage(id: "user-1", text: "hello", timestamp: now, mode: .translate)
        service.recordAssistantMessage(
            id: "ai-1",
            text: "hi there",
            timestamp: now.addingTimeInterval(1),
            image: image,
            source: .llm,
            mode: .translate
        )
        await service.waitForPendingWrites()

        var detail = await service.loadDetail(sessionID: seed.sessionID)
        XCTAssertEqual(detail.messages.map(\.text), ["hello", "hi there"])
        XCTAssertEqual(detail.messages.last?.image?.data, image.data)
        XCTAssertEqual(detail.messages.last?.source, .llm)
        XCTAssertEqual(detail.messages.first?.mode, .translate)
        XCTAssertEqual(detail.messages.last?.mode, .translate)

        service.finishSession()
        await service.waitForPendingWrites()
        let sessions = await service.loadSessions(search: "")
        let finished = try XCTUnwrap(sessions.first)
        XCTAssertEqual(finished.messageCount, 2)
        XCTAssertEqual(finished.firstUserMessage, "hello")
        XCTAssertEqual(finished.summary, "hello")
        XCTAssertNotNil(finished.endTime)

        // 同日重启：复用同一 session，并 seed 已落库消息。
        let reopened = HistoryService(
            baseDirectory: root,
            now: { now.addingTimeInterval(300) },
            calendar: testCalendar
        )
        let reseedValue = await reopened.beginSession()
        let reseed = try XCTUnwrap(reseedValue)
        XCTAssertEqual(reseed.sessionID, seed.sessionID)
        XCTAssertEqual(reseed.messages.map(\.text), ["hello", "hi there"])

        // 清空：删旧数据与图片，创建新的空 session。
        await reopened.clearAll()
        let afterClear = await reopened.loadSessions(search: "")
        XCTAssertEqual(afterClear.count, 1)
        XCTAssertNotEqual(afterClear.first?.id, seed.sessionID)
        XCTAssertEqual(afterClear.first?.messageCount, 0)
        detail = await reopened.loadDetail(sessionID: seed.sessionID)
        XCTAssertTrue(detail.messages.isEmpty)
        let imagesDir = root.appendingPathComponent("chat_images")
        let remaining = (try? FileManager.default.contentsOfDirectory(atPath: imagesDir.path)) ?? []
        XCTAssertTrue(remaining.isEmpty)
    }

    func testFinishSessionFlushesQueuedAppends() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let service = HistoryService(baseDirectory: root, now: { now }, calendar: testCalendar)
        let seedValue = await service.beginSession()
        let seed = try XCTUnwrap(seedValue)

        service.recordUserMessage(id: "user-1", text: "hello", timestamp: now, mode: .translate)
        service.finishSession()
        await service.waitForPendingWrites()

        let detail = await service.loadDetail(sessionID: seed.sessionID)
        XCTAssertEqual(detail.messages.map(\.text), ["hello"])
        let sessions = await service.loadSessions(search: "")
        XCTAssertEqual(sessions.first?.messageCount, 1)
        XCTAssertEqual(sessions.first?.summary, "hello")
        XCTAssertNotNil(sessions.first?.endTime)
    }

    func testClearAllRemovesOrphanImages() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let service = HistoryService(baseDirectory: root, now: { now }, calendar: testCalendar)
        _ = await service.beginSession()

        let images = root.appendingPathComponent("chat_images", isDirectory: true)
        try FileManager.default.createDirectory(at: images, withIntermediateDirectories: true)
        try Data("orphan".utf8).write(to: images.appendingPathComponent("orphan.jpg"))

        await service.clearAll()

        let remaining = (try? FileManager.default.contentsOfDirectory(atPath: images.path)) ?? []
        XCTAssertTrue(remaining.isEmpty)
    }

    func testDeleteCurrentSessionCreatesNewSessionAndRemovesOld() async throws {
        let root = makeRoot()
        defer { cleanup(root) }
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let service = HistoryService(baseDirectory: root, now: { now }, calendar: testCalendar)
        let seedValue = await service.beginSession()
        let seed = try XCTUnwrap(seedValue)
        service.recordUserMessage(id: "user-1", text: "hello", timestamp: now, mode: .translate)
        await service.waitForPendingWrites()

        await service.deleteSession(id: seed.sessionID)
        let sessions = await service.loadSessions(search: "")
        XCTAssertEqual(sessions.count, 1)
        XCTAssertNotEqual(sessions.first?.id, seed.sessionID)
        XCTAssertEqual(sessions.first?.messageCount, 0)
        let old = await service.loadDetail(sessionID: seed.sessionID)
        XCTAssertNil(old.session)
        XCTAssertTrue(old.messages.isEmpty)
    }

    private func record(
        sessionID: String,
        role: ChatRole,
        text: String,
        seq: Int
    ) -> HistoryMessageRecord {
        HistoryMessageRecord(
            id: "\(sessionID)-\(seq)",
            sessionID: sessionID,
            role: role == .user ? HistoryMessageRecord.roleUser : HistoryMessageRecord.roleAI,
            text: text,
            isFinal: true,
            seq: seq,
            timestamp: Date(timeIntervalSince1970: 1_700_000_000 + Double(seq)),
            imagePath: nil,
            imageMime: nil,
            imageWidth: nil,
            imageHeight: nil,
            imageOrientation: nil,
            imageCameraFacing: nil,
            source: nil,
            mode: nil
        )
    }
}
