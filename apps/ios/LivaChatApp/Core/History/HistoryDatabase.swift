import Foundation
import SQLite3

enum HistoryDatabaseError: Error, LocalizedError {
    case open(String)
    case statement(String)
    case unsupportedVersion(Int)

    var errorDescription: String? {
        switch self {
        case let .open(message): return "history db open failed: \(message)"
        case let .statement(message): return "history db statement failed: \(message)"
        case let .unsupportedVersion(version): return "unsupported history db version \(version)"
        }
    }
}

actor HistoryDatabase {
    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    private var handle: OpaquePointer?

    init(fileURL: URL) throws {
        var db: OpaquePointer?
        let flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
        let result = sqlite3_open_v2(fileURL.path, &db, flags, nil)
        guard result == SQLITE_OK, let db else {
            let message = db.map { String(cString: sqlite3_errmsg($0)) } ?? "unknown"
            if let db { sqlite3_close(db) }
            throw HistoryDatabaseError.open(message)
        }
        self.handle = db
        do {
            try Self.configure(db)
        } catch {
            sqlite3_close(db)
            self.handle = nil
            throw error
        }
    }

    func close() {
        if let handle {
            sqlite3_close(handle)
        }
        handle = nil
    }

    // MARK: - Session reads

    func createOrGetTodaySession(date: String, now: Date) throws -> HistorySession {
        if let existingID = try dailySessionID(date: date),
           let existing = try session(id: existingID) {
            return existing
        }
        return try createSession(date: date, now: now)
    }

    func forceNewTodaySession(date: String, now: Date) throws -> HistorySession {
        try execute("DELETE FROM daily_sessions WHERE date = ?", bind: { self.bind($0, 1, date) })
        return try createSession(date: date, now: now)
    }

    func session(id: String) throws -> HistorySession? {
        let stmt = try prepare(
            "SELECT id, startTime, endTime, messageCount, summary, firstUserMessage FROM sessions WHERE id = ?"
        )
        defer { sqlite3_finalize(stmt) }
        bind(stmt, 1, id)
        guard try step(stmt) == SQLITE_ROW else { return nil }
        return sessionRow(stmt)
    }

    func sessions(search: String) throws -> [HistorySession] {
        let pattern = "%\(search)%"
        let stmt = try prepare("""
            SELECT id, startTime, endTime, messageCount, summary, firstUserMessage
            FROM sessions
            WHERE ? = ''
               OR id IN (SELECT sessionId FROM messages WHERE text LIKE ?)
               OR summary LIKE ?
               OR firstUserMessage LIKE ?
            ORDER BY startTime DESC
            """)
        defer { sqlite3_finalize(stmt) }
        bind(stmt, 1, search)
        bind(stmt, 2, pattern)
        bind(stmt, 3, pattern)
        bind(stmt, 4, pattern)
        var result: [HistorySession] = []
        while try step(stmt) == SQLITE_ROW {
            result.append(sessionRow(stmt))
        }
        return result
    }

    func messages(sessionID: String) throws -> [HistoryMessageRecord] {
        let stmt = try prepare("""
            SELECT id, sessionId, role, text, isFinal, seq, timestamp,
                   imagePath, imageMime, imageWidth, imageHeight, imageOrientation,
                   imageCameraFacing, source, mode
            FROM messages
            WHERE sessionId = ?
            ORDER BY seq ASC
            """)
        defer { sqlite3_finalize(stmt) }
        bind(stmt, 1, sessionID)
        var result: [HistoryMessageRecord] = []
        while try step(stmt) == SQLITE_ROW {
            result.append(messageRow(stmt))
        }
        return result
    }

    func maxSeq(sessionID: String) throws -> Int {
        let stmt = try prepare("SELECT COALESCE(MAX(seq), -1) FROM messages WHERE sessionId = ?")
        defer { sqlite3_finalize(stmt) }
        bind(stmt, 1, sessionID)
        guard try step(stmt) == SQLITE_ROW else { return -1 }
        return Int(sqlite3_column_int64(stmt, 0))
    }

    // MARK: - Writes

    func append(_ record: HistoryMessageRecord) throws {
        try transaction {
            let stmt = try prepare("""
                INSERT OR REPLACE INTO messages
                (id, sessionId, role, text, isFinal, seq, timestamp, imagePath, imageMime,
                 imageWidth, imageHeight, imageOrientation, imageCameraFacing, source, mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """)
            defer { sqlite3_finalize(stmt) }
            bind(stmt, 1, record.id)
            bind(stmt, 2, record.sessionID)
            bind(stmt, 3, record.role)
            bind(stmt, 4, record.text)
            sqlite3_bind_int(stmt, 5, record.isFinal ? 1 : 0)
            sqlite3_bind_int64(stmt, 6, Int64(record.seq))
            sqlite3_bind_int64(stmt, 7, milliseconds(record.timestamp))
            bind(stmt, 8, record.imagePath)
            bind(stmt, 9, record.imageMime)
            bindOptionalInt(stmt, 10, record.imageWidth)
            bindOptionalInt(stmt, 11, record.imageHeight)
            bindOptionalInt(stmt, 12, record.imageOrientation)
            bind(stmt, 13, record.imageCameraFacing)
            bind(stmt, 14, record.source)
            bind(stmt, 15, record.mode)
            guard try step(stmt) == SQLITE_DONE else {
                throw error(message: "insert message")
            }

            let count = try prepare("UPDATE sessions SET messageCount = messageCount + 1 WHERE id = ?")
            defer { sqlite3_finalize(count) }
            bind(count, 1, record.sessionID)
            guard try step(count) == SQLITE_DONE else {
                throw error(message: "increment messageCount")
            }

            if record.role == HistoryMessageRecord.roleUser,
               !record.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                let first = try prepare(
                    "UPDATE sessions SET firstUserMessage = ? WHERE id = ? AND firstUserMessage IS NULL"
                )
                defer { sqlite3_finalize(first) }
                bind(first, 1, record.text)
                bind(first, 2, record.sessionID)
                guard try step(first) == SQLITE_DONE else {
                    throw error(message: "set firstUserMessage")
                }
            }
        }
    }

    func updateSessionEnd(
        id: String,
        endTime: Date,
        messageCount: Int,
        firstUserMessage: String?,
        summary: String?
    ) throws {
        let stmt = try prepare("""
            UPDATE sessions
            SET endTime = ?, messageCount = ?, firstUserMessage = ?, summary = ?
            WHERE id = ?
            """)
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_int64(stmt, 1, milliseconds(endTime))
        sqlite3_bind_int64(stmt, 2, Int64(messageCount))
        bind(stmt, 3, firstUserMessage)
        bind(stmt, 4, summary)
        bind(stmt, 5, id)
        guard try step(stmt) == SQLITE_DONE else {
            throw error(message: "end session")
        }
    }

    func deleteSession(id: String) throws -> [String] {
        try transaction {
            let stmt = try prepare(
                "SELECT imagePath FROM messages WHERE sessionId = ? AND imagePath IS NOT NULL"
            )
            let imagePaths = try collectStrings(stmt, binding: { self.bind($0, 1, id) })
            try execute("DELETE FROM sessions WHERE id = ?", bind: { self.bind($0, 1, id) })
            try execute("DELETE FROM daily_sessions WHERE sessionId = ?", bind: { self.bind($0, 1, id) })
            return imagePaths
        }
    }

    func deleteAll() throws -> [String] {
        try transaction {
            let stmt = try prepare("SELECT imagePath FROM messages WHERE imagePath IS NOT NULL")
            let imagePaths = try collectStrings(stmt)
            try execute("DELETE FROM sessions")
            try execute("DELETE FROM daily_sessions")
            return imagePaths
        }
    }

    // MARK: - Schema

    private static func configure(_ db: OpaquePointer) throws {
        for sql in [
            "PRAGMA foreign_keys = ON;",
            "PRAGMA journal_mode = WAL;",
            """
            CREATE TABLE IF NOT EXISTS sessions (
              id TEXT PRIMARY KEY NOT NULL,
              startTime INTEGER NOT NULL,
              endTime INTEGER,
              messageCount INTEGER NOT NULL,
              summary TEXT,
              firstUserMessage TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY NOT NULL,
              sessionId TEXT NOT NULL,
              role TEXT NOT NULL,
              text TEXT NOT NULL,
              isFinal INTEGER NOT NULL,
              seq INTEGER NOT NULL,
              timestamp INTEGER NOT NULL,
              imagePath TEXT,
              imageMime TEXT,
              imageWidth INTEGER,
              imageHeight INTEGER,
              imageOrientation INTEGER,
              imageCameraFacing TEXT,
              source TEXT,
              mode TEXT,
              FOREIGN KEY(sessionId) REFERENCES sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS index_messages_sessionId ON messages(sessionId);
            CREATE TABLE IF NOT EXISTS daily_sessions (
              date TEXT PRIMARY KEY NOT NULL,
              sessionId TEXT NOT NULL
            );
            """
        ] {
            var err: UnsafeMutablePointer<CChar>?
            let result = sqlite3_exec(db, sql, nil, nil, &err)
            if result != SQLITE_OK {
                let message = err.map { String(cString: $0) } ?? "unknown"
                sqlite3_free(err)
                throw HistoryDatabaseError.statement(message)
            }
        }

        let versionStatement = try prepareVersion(db)
        defer { sqlite3_finalize(versionStatement) }
        guard sqlite3_step(versionStatement) == SQLITE_ROW else {
            throw HistoryDatabaseError.statement("read user_version")
        }
        let version = Int(sqlite3_column_int(versionStatement, 0))
        if version == 0 {
            var err: UnsafeMutablePointer<CChar>?
            let result = sqlite3_exec(db, "PRAGMA user_version = 1;", nil, nil, &err)
            if result != SQLITE_OK {
                let message = err.map { String(cString: $0) } ?? "unknown"
                sqlite3_free(err)
                throw HistoryDatabaseError.statement(message)
            }
        } else if version > 1 {
            throw HistoryDatabaseError.unsupportedVersion(version)
        }
    }

    private static func prepareVersion(_ db: OpaquePointer) throws -> OpaquePointer {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, "PRAGMA user_version;", -1, &stmt, nil) == SQLITE_OK,
              let stmt else {
            throw HistoryDatabaseError.statement("prepare user_version")
        }
        return stmt
    }

    // MARK: - Row mapping

    private func sessionRow(_ stmt: OpaquePointer) -> HistorySession {
        HistorySession(
            id: text(stmt, 0) ?? "",
            startTime: date(sqlite3_column_int64(stmt, 1)),
            endTime: sqlite3_column_type(stmt, 2) == SQLITE_NULL
                ? nil
                : date(sqlite3_column_int64(stmt, 2)),
            messageCount: Int(sqlite3_column_int64(stmt, 3)),
            summary: text(stmt, 4),
            firstUserMessage: text(stmt, 5)
        )
    }

    private func messageRow(_ stmt: OpaquePointer) -> HistoryMessageRecord {
        HistoryMessageRecord(
            id: text(stmt, 0) ?? "",
            sessionID: text(stmt, 1) ?? "",
            role: text(stmt, 2) ?? HistoryMessageRecord.roleAI,
            text: text(stmt, 3) ?? "",
            isFinal: sqlite3_column_int(stmt, 4) != 0,
            seq: Int(sqlite3_column_int64(stmt, 5)),
            timestamp: date(sqlite3_column_int64(stmt, 6)),
            imagePath: text(stmt, 7),
            imageMime: text(stmt, 8),
            imageWidth: optionalInt(stmt, 9),
            imageHeight: optionalInt(stmt, 10),
            imageOrientation: optionalInt(stmt, 11),
            imageCameraFacing: text(stmt, 12),
            source: text(stmt, 13),
            mode: text(stmt, 14)
        )
    }

    // MARK: - Private helpers

    private func createSession(date: String, now: Date) throws -> HistorySession {
        let id = UUID().uuidString.lowercased()
        let startMs = milliseconds(now)
        try transaction {
            let session = try prepare(
                "INSERT INTO sessions (id, startTime, endTime, messageCount, summary, firstUserMessage) VALUES (?, ?, NULL, 0, NULL, NULL)"
            )
            defer { sqlite3_finalize(session) }
            bind(session, 1, id)
            sqlite3_bind_int64(session, 2, startMs)
            guard try step(session) == SQLITE_DONE else {
                throw error(message: "insert session")
            }

            let daily = try prepare(
                "INSERT OR REPLACE INTO daily_sessions (date, sessionId) VALUES (?, ?)"
            )
            defer { sqlite3_finalize(daily) }
            bind(daily, 1, date)
            bind(daily, 2, id)
            guard try step(daily) == SQLITE_DONE else {
                throw error(message: "insert daily session")
            }
        }
        return HistorySession(
            id: id,
            startTime: now,
            endTime: nil,
            messageCount: 0,
            summary: nil,
            firstUserMessage: nil
        )
    }

    private func dailySessionID(date: String) throws -> String? {
        let stmt = try prepare("SELECT sessionId FROM daily_sessions WHERE date = ?")
        defer { sqlite3_finalize(stmt) }
        bind(stmt, 1, date)
        guard try step(stmt) == SQLITE_ROW else { return nil }
        return text(stmt, 0)
    }

    private func transaction<T>(_ body: () throws -> T) throws -> T {
        try execute("BEGIN IMMEDIATE")
        do {
            let value = try body()
            try execute("COMMIT")
            return value
        } catch {
            try? execute("ROLLBACK")
            throw error
        }
    }

    private func execute(_ sql: String, bind: ((OpaquePointer) -> Void)? = nil) throws {
        let stmt = try prepare(sql)
        defer { sqlite3_finalize(stmt) }
        bind?(stmt)
        guard try step(stmt) == SQLITE_DONE else {
            throw error(message: sql)
        }
    }

    private func collectStrings(
        _ stmt: OpaquePointer,
        binding: ((OpaquePointer) -> Void)? = nil
    ) throws -> [String] {
        defer { sqlite3_finalize(stmt) }
        binding?(stmt)
        var values: [String] = []
        while try step(stmt) == SQLITE_ROW {
            if let value = text(stmt, 0) {
                values.append(value)
            }
        }
        return values
    }

    private func prepare(_ sql: String) throws -> OpaquePointer {
        guard let handle else {
            throw HistoryDatabaseError.statement("database is closed")
        }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &stmt, nil) == SQLITE_OK,
              let stmt else {
            throw HistoryDatabaseError.statement(String(cString: sqlite3_errmsg(handle)))
        }
        return stmt
    }

    private func step(_ stmt: OpaquePointer) throws -> Int32 {
        let result = sqlite3_step(stmt)
        guard result == SQLITE_ROW || result == SQLITE_DONE else {
            throw error(message: String(cString: sqlite3_errmsg(handle)))
        }
        return result
    }

    private func bind(_ stmt: OpaquePointer, _ index: Int32, _ value: String?) {
        if let value {
            value.withCString { pointer in
                sqlite3_bind_text(stmt, index, pointer, -1, Self.transient)
            }
        } else {
            sqlite3_bind_null(stmt, index)
        }
    }

    private func bindOptionalInt(_ stmt: OpaquePointer, _ index: Int32, _ value: Int?) {
        if let value {
            sqlite3_bind_int64(stmt, index, Int64(value))
        } else {
            sqlite3_bind_null(stmt, index)
        }
    }

    private func text(_ stmt: OpaquePointer, _ column: Int32) -> String? {
        guard let pointer = sqlite3_column_text(stmt, column) else { return nil }
        return String(cString: pointer)
    }

    private func optionalInt(_ stmt: OpaquePointer, _ column: Int32) -> Int? {
        guard sqlite3_column_type(stmt, column) != SQLITE_NULL else { return nil }
        return Int(sqlite3_column_int64(stmt, column))
    }

    private func milliseconds(_ date: Date) -> Int64 {
        Int64((date.timeIntervalSince1970 * 1000).rounded())
    }

    private func date(_ milliseconds: Int64) -> Date {
        Date(timeIntervalSince1970: Double(milliseconds) / 1000)
    }

    private func error(message: String) -> HistoryDatabaseError {
        let detail = handle.map { String(cString: sqlite3_errmsg($0)) } ?? message
        return .statement("\(message): \(detail)")
    }
}
