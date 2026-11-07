import Combine
import Foundation
import LivaChatSDK

@MainActor
final class HistoryService: ObservableObject, ConversationHistoryRecording {
    /// Oversized images are skipped when replaying history; the text message
    /// is still shown. This bounds each image, not the aggregate: replay still
    /// loads every historical image eagerly until lazy loading lands.
    private static let maxHistoryImageBytes = 2 * 1024 * 1024

    @Published private(set) var revision = 0
    private(set) var isSuppressingMessages = false

    var sessionSwitched: AnyPublisher<HistorySeed, Never> {
        sessionSwitchedSubject.eraseToAnyPublisher()
    }

    private let baseDirectory: URL
    private let now: () -> Date
    private let calendar: Calendar
    private let sessionSwitchedSubject = PassthroughSubject<HistorySeed, Never>()

    private var storage: Storage?
    private var storageFailed = false
    private var current: SessionContext?
    private var tail: Task<Void, Never>?
    private var clearInFlight = false

    private struct Storage {
        let database: HistoryDatabase
        let images: HistoryImageStore
    }

    private struct SessionContext {
        let sessionID: String
        var nextSeq: Int
    }

    private struct PendingMessage {
        let id: String
        let role: String
        let text: String
        let timestamp: Date
        let image: AiImage?
        let source: AITextSource?
        let mode: ChatMessageMode?
    }

    init(
        baseDirectory: URL,
        now: @escaping () -> Date = Date.init,
        calendar: Calendar = .current
    ) {
        self.baseDirectory = baseDirectory
        self.now = now
        self.calendar = calendar
    }

    // MARK: - Lifecycle

    func beginSession() async -> HistorySeed? {
        await withCheckedContinuation { continuation in
            enqueue { [weak self] in
                guard let self else {
                    continuation.resume(returning: nil)
                    return
                }
                await self.processBegin(continuation)
            }
        }
    }

    func finishSession() {
        enqueue { [weak self] in
            guard let self, let context = self.current else { return }
            self.current = nil
            await self.processFinish(sessionID: context.sessionID)
        }
    }

    // MARK: - Reads

    func loadSessions(search: String) async -> [HistorySession] {
        guard let storage = ensureStorage() else { return [] }
        do {
            return try await storage.database.sessions(search: search)
        } catch {
            AppLog.error("History", "load sessions failed: \(error.localizedDescription)")
            return []
        }
    }

    func loadDetail(sessionID: String) async -> HistoryDetail {
        guard let storage = ensureStorage() else {
            return HistoryDetail(session: nil, messages: [])
        }
        do {
            let session = try await storage.database.session(id: sessionID)
            let records = try await storage.database.messages(sessionID: sessionID)
            let messages = await chatMessages(from: records, images: storage.images)
            return HistoryDetail(session: session, messages: messages)
        } catch {
            AppLog.error("History", "load detail failed: \(error.localizedDescription)")
            return HistoryDetail(session: nil, messages: [])
        }
    }

    // MARK: - Maintenance

    func deleteSession(id: String) async {
        await withCheckedContinuation { continuation in
            enqueue { [weak self] in
                guard let self else {
                    continuation.resume()
                    return
                }
                await self.processDelete(sessionID: id, continuation: continuation)
            }
        }
    }

    func clearAll() async {
        guard !clearInFlight else { return }
        clearInFlight = true
        isSuppressingMessages = true
        await withCheckedContinuation { continuation in
            enqueue { [weak self] in
                guard let self else {
                    continuation.resume()
                    return
                }
                await self.processClear(continuation: continuation)
            }
        }
        isSuppressingMessages = false
        clearInFlight = false
    }

    // MARK: - ConversationHistoryRecording

    func recordUserMessage(
        id: String,
        text: String,
        timestamp: Date,
        mode: ChatMessageMode?
    ) {
        guard !isSuppressingMessages,
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        enqueueAppend(
            PendingMessage(
                id: id,
                role: HistoryMessageRecord.roleUser,
                text: text,
                timestamp: timestamp,
                image: nil,
                source: nil,
                mode: mode
            )
        )
    }

    func recordAssistantMessage(
        id: String,
        text: String,
        timestamp: Date,
        image: AiImage?,
        source: AITextSource?,
        mode: ChatMessageMode?
    ) {
        // Image-only assistant messages are valid (omni mode / camera
        // snapshots). Persist them too, otherwise they disappear on relaunch.
        guard !isSuppressingMessages,
              !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || image != nil
        else { return }
        enqueueAppend(
            PendingMessage(
                id: id,
                role: HistoryMessageRecord.roleAI,
                text: text,
                timestamp: timestamp,
                image: image,
                source: source,
                mode: mode
            )
        )
    }

    func waitForPendingWrites() async {
        await tail?.value
    }

    // MARK: - Queue

    private func enqueue(_ operation: @escaping @MainActor () async -> Void) {
        let previous = tail
        tail = Task { @MainActor in
            await previous?.value
            await operation()
        }
    }

    private func enqueueAppend(_ message: PendingMessage) {
        enqueue { [weak self] in
            await self?.processAppend(message)
        }
    }

    private func processBegin(_ continuation: CheckedContinuation<HistorySeed?, Never>) async {
        guard let storage = ensureStorage() else {
            continuation.resume(returning: nil)
            return
        }
        let date = dateKey(now())
        let currentDate = now()
        do {
            let session = try await storage.database.createOrGetTodaySession(date: date, now: currentDate)
            let maxSeq = try await storage.database.maxSeq(sessionID: session.id)
            current = SessionContext(sessionID: session.id, nextSeq: maxSeq + 1)
            let records = try await storage.database.messages(sessionID: session.id)
            let messages = await chatMessages(from: records, images: storage.images)
            continuation.resume(returning: HistorySeed(sessionID: session.id, messages: messages))
        } catch {
            AppLog.error("History", "begin session failed: \(error.localizedDescription)")
            continuation.resume(returning: nil)
        }
    }

    private func processFinish(sessionID: String) async {
        guard let storage = ensureStorage() else { return }
        do {
            let records = try await storage.database.messages(sessionID: sessionID)
            let summary = HistorySummary.make(from: records)
            try await storage.database.updateSessionEnd(
                id: sessionID,
                endTime: now(),
                messageCount: records.count,
                firstUserMessage: summary.firstUserMessage,
                summary: summary.summary
            )
            revision += 1
        } catch {
            AppLog.error("History", "finish session failed: \(error.localizedDescription)")
        }
    }

    private func processAppend(_ message: PendingMessage) async {
        guard var context = current, let storage = ensureStorage() else { return }
        let seq = context.nextSeq
        context.nextSeq += 1
        current = context

        var record = HistoryMessageRecord(
            id: message.id,
            sessionID: context.sessionID,
            role: message.role,
            text: message.text,
            isFinal: true,
            seq: seq,
            timestamp: message.timestamp,
            imagePath: nil,
            imageMime: message.image?.mime,
            imageWidth: message.image?.width,
            imageHeight: message.image?.height,
            imageOrientation: message.image?.orientation.rawValue,
            imageCameraFacing: message.image?.cameraFacing.rawValue,
            source: message.source?.rawValue,
            mode: message.mode?.rawValue
        )

        if let image = message.image {
            do {
                record.imagePath = try await storage.images.write(image.data, messageID: message.id)
            } catch {
                AppLog.error("History", "image persist failed: \(error.localizedDescription)")
            }
        }

        do {
            try await storage.database.append(record)
            revision += 1
        } catch {
            AppLog.error("History", "append message failed: \(error.localizedDescription)")
            if let filename = record.imagePath {
                await storage.images.delete(filenames: [filename])
            }
        }
    }

    private func processDelete(
        sessionID: String,
        continuation: CheckedContinuation<Void, Never>
    ) async {
        guard let storage = ensureStorage() else {
            continuation.resume()
            return
        }
        let wasCurrent = current?.sessionID == sessionID
        var switched: HistorySession?
        if wasCurrent {
            let date = dateKey(now())
            let currentDate = now()
            do {
                let newSession = try await storage.database.forceNewTodaySession(
                    date: date,
                    now: currentDate
                )
                current = SessionContext(sessionID: newSession.id, nextSeq: 1)
                switched = newSession
            } catch {
                AppLog.error("History", "replace current session failed: \(error.localizedDescription)")
                current = nil
            }
        }

        do {
            let images = try await storage.database.deleteSession(id: sessionID)
            await storage.images.delete(filenames: images)
            if let switched {
                sessionSwitchedSubject.send(HistorySeed(sessionID: switched.id, messages: []))
            }
            revision += 1
        } catch {
            AppLog.error("History", "delete session failed: \(error.localizedDescription)")
        }
        continuation.resume()
    }

    private func processClear(continuation: CheckedContinuation<Void, Never>) async {
        guard let storage = ensureStorage() else {
            continuation.resume()
            return
        }
        do {
            _ = try await storage.database.deleteAll()
            await storage.images.deleteAll()
            let date = dateKey(now())
            let currentDate = now()
            let newSession = try await storage.database.forceNewTodaySession(
                date: date,
                now: currentDate
            )
            current = SessionContext(sessionID: newSession.id, nextSeq: 1)
            sessionSwitchedSubject.send(HistorySeed(sessionID: newSession.id, messages: []))
            revision += 1
        } catch {
            AppLog.error("History", "clear history failed: \(error.localizedDescription)")
            current = nil
        }
        continuation.resume()
    }

    // MARK: - Storage

    private func ensureStorage() -> Storage? {
        if let storage { return storage }
        if storageFailed { return nil }
        do {
            try FileManager.default.createDirectory(
                at: baseDirectory,
                withIntermediateDirectories: true
            )
            var resourceURL = baseDirectory
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try? resourceURL.setResourceValues(values)

            let database = try HistoryDatabase(
                fileURL: baseDirectory.appendingPathComponent("livachat.db", isDirectory: false)
            )
            let images = try HistoryImageStore(
                directory: baseDirectory.appendingPathComponent("chat_images", isDirectory: true)
            )
            let newStorage = Storage(database: database, images: images)
            storage = newStorage
            return newStorage
        } catch {
            storageFailed = true
            AppLog.error("History", "storage init failed: \(error.localizedDescription)")
            return nil
        }
    }

    private func chatMessages(
        from records: [HistoryMessageRecord],
        images: HistoryImageStore
    ) async -> [ChatMessage] {
        var result: [ChatMessage] = []
        result.reserveCapacity(records.count)
        for record in records {
            var image: AiImage?
            if let filename = record.imagePath,
               let data = try? await images.read(
                   filename: filename,
                   maxBytes: Self.maxHistoryImageBytes
               ) {
                image = AiImage(
                    data: data,
                    mime: record.imageMime ?? "image/jpeg",
                    width: record.imageWidth ?? 0,
                    height: record.imageHeight ?? 0,
                    orientation: Orientation(rawValue: record.imageOrientation ?? 0) ?? .portrait0,
                    cameraFacing: CameraFacing(rawValue: record.imageCameraFacing ?? "") ?? .front
                )
            }
            result.append(
                ChatMessage(
                    id: record.id,
                    role: record.role == HistoryMessageRecord.roleUser ? .user : .assistant,
                    text: record.text,
                    isFinal: true,
                    timestamp: record.timestamp,
                    image: image,
                    source: record.source.flatMap(AITextSource.init(rawValue:)),
                    mode: record.mode.flatMap(ChatMessageMode.init(rawValue:))
                )
            )
        }
        return result
    }

    #if DEBUG
    func seedUITestData(imageData: Data?) async {
        guard let storage = ensureStorage() else { return }
        let today = now()
        let yesterday = today.addingTimeInterval(-86_400)
        do {
            let first = try await storage.database.forceNewTodaySession(
                date: dateKey(yesterday),
                now: yesterday
            )
            let markdown = """
            # Kyoto guide

            Plan:
            - Visit Fushimi Inari
            - Walk Gion

            | Day | Plan |
            | --- | --- |
            | 1 | Temples |
            | 2 | Food tour |

            [Details](https://example.com/kyoto)
            """
            try await storage.database.append(
                seedRecord(
                    id: "user-ui-kyoto",
                    sessionID: first.id,
                    role: .user,
                    text: "Plan a trip to Kyoto",
                    seq: 0,
                    timestamp: yesterday
                )
            )
            try await storage.database.append(
                seedRecord(
                    id: "ai-ui-markdown",
                    sessionID: first.id,
                    role: .assistant,
                    text: markdown,
                    seq: 1,
                    timestamp: yesterday.addingTimeInterval(10)
                )
            )
            var imageRecord = seedRecord(
                id: "ai-ui-image",
                sessionID: first.id,
                role: .assistant,
                text: "Here is a photo.",
                seq: 2,
                timestamp: yesterday.addingTimeInterval(20)
            )
            if let imageData {
                do {
                    imageRecord.imagePath = try await storage.images.write(
                        imageData,
                        messageID: imageRecord.id
                    )
                } catch {
                    AppLog.error("History", "seed image failed: \(error.localizedDescription)")
                }
            }
            try await storage.database.append(imageRecord)
            try await storage.database.append(
                seedRecord(
                    id: "ai-msg_agent_job_ui_history",
                    sessionID: first.id,
                    role: .assistant,
                    text: "**Done.** Inventory checked.\n\n[Open report](https://example.com/report)",
                    seq: 3,
                    timestamp: yesterday.addingTimeInterval(30),
                    source: "agent"
                )
            )
            try await storage.database.updateSessionEnd(
                id: first.id,
                endTime: yesterday.addingTimeInterval(600),
                messageCount: 4,
                firstUserMessage: "Plan a trip to Kyoto",
                summary: "Plan a trip to Kyoto"
            )

            let second = try await storage.database.forceNewTodaySession(
                date: dateKey(today),
                now: today
            )
            try await storage.database.append(
                seedRecord(
                    id: "user-ui-dinner",
                    sessionID: second.id,
                    role: .user,
                    text: "Dinner ideas",
                    seq: 0,
                    timestamp: today
                )
            )
            try await storage.database.append(
                seedRecord(
                    id: "ai-ui-dinner",
                    sessionID: second.id,
                    role: .assistant,
                    text: "How about pasta?",
                    seq: 1,
                    timestamp: today.addingTimeInterval(10)
                )
            )
            revision += 1
        } catch {
            AppLog.error("History", "seed UI test data failed: \(error.localizedDescription)")
        }
    }

    private func seedRecord(
        id: String,
        sessionID: String,
        role: ChatRole,
        text: String,
        seq: Int,
        timestamp: Date,
        source: String? = nil
    ) -> HistoryMessageRecord {
        HistoryMessageRecord(
            id: id,
            sessionID: sessionID,
            role: role == .user ? HistoryMessageRecord.roleUser : HistoryMessageRecord.roleAI,
            text: text,
            isFinal: true,
            seq: seq,
            timestamp: timestamp,
            imagePath: nil,
            imageMime: imageDataMime(for: id),
            imageWidth: imageDataMime(for: id) == nil ? nil : 8,
            imageHeight: imageDataMime(for: id) == nil ? nil : 8,
            imageOrientation: imageDataMime(for: id) == nil ? nil : 0,
            imageCameraFacing: imageDataMime(for: id) == nil ? nil : "gallery",
            source: source,
            mode: nil
        )
    }

    private func imageDataMime(for id: String) -> String? {
        id == "ai-ui-image" ? "image/jpeg" : nil
    }
    #endif

    private func dateKey(_ date: Date) -> String {
        var gregorian = Calendar(identifier: .gregorian)
        gregorian.timeZone = calendar.timeZone
        let components = gregorian.dateComponents([.year, .month, .day], from: date)
        let year = components.year ?? 0
        let month = components.month ?? 0
        let day = components.day ?? 0
        return PosixFormat.string("%04d-%02d-%02d", year, month, day)
    }
}

@MainActor
final class NoopConversationHistory: ConversationHistoryRecording {
    let isSuppressingMessages = false
    let sessionSwitched = Empty<HistorySeed, Never>().eraseToAnyPublisher()

    func recordUserMessage(
        id: String,
        text: String,
        timestamp: Date,
        mode: ChatMessageMode?
    ) {}
    func recordAssistantMessage(
        id: String,
        text: String,
        timestamp: Date,
        image: AiImage?,
        source: AITextSource?,
        mode: ChatMessageMode?
    ) {}
    func finishSession() {}
}
