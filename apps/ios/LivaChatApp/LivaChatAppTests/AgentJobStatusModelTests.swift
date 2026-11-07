import LivaChatSDK
import XCTest
@testable import LivaChatApp

private actor GatedCanceler: AgentJobCanceling {
    private var pending: [CheckedContinuation<Void, Error>] = []
    private var startedWaiters: [CheckedContinuation<Void, Never>] = []
    private(set) var calls: [String] = []

    func cancel(jobID: String) async throws {
        calls.append(jobID)
        let waiters = startedWaiters
        startedWaiters.removeAll()
        waiters.forEach { $0.resume() }
        try await withCheckedThrowingContinuation { continuation in
            pending.append(continuation)
        }
    }

    func waitUntilStarted() async {
        if !calls.isEmpty { return }
        await withCheckedContinuation { continuation in
            startedWaiters.append(continuation)
        }
    }

    func succeed() {
        guard !pending.isEmpty else { return }
        pending.removeFirst().resume()
    }

    func fail(_ error: Error) {
        guard !pending.isEmpty else { return }
        pending.removeFirst().resume(throwing: error)
    }
}

@MainActor
final class AgentJobStatusModelTests: XCTestCase {
    private func makeJob(
        _ id: String,
        summary: String = "Task",
        startedAtMs: Int64 = 1_000,
        runningTimeMs: Int64 = 2_000
    ) -> AgentJob {
        AgentJob(
            jobID: id,
            agentName: "hermes",
            summary: summary,
            startedAtMs: startedAtMs,
            runningTimeMs: runningTimeMs
        )
    }

    private func eventually(
        timeout: TimeInterval = 1,
        _ condition: @MainActor () -> Bool
    ) async {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("condition not met within \(timeout)s")
    }

    func testReplaceJobsDropsMissingCancelTargetAndKeepsPanel() async {
        let canceler = GatedCanceler()
        let model = AgentJobStatusModel(canceler: canceler)
        let first = makeJob("job-1")
        let second = makeJob("job-2")

        model.replaceJobs([first, second])
        model.togglePanel()
        XCTAssertTrue(model.isPanelPresented)
        model.requestCancel(first)
        XCTAssertEqual(model.cancelState?.job.jobID, "job-1")

        model.replaceJobs([second])
        XCTAssertEqual(model.jobs.map(\.jobID), ["job-2"])
        XCTAssertNil(model.cancelState)
        XCTAssertTrue(model.isPanelPresented)
    }

    func testEmptySnapshotClosesPanel() {
        let model = AgentJobStatusModel()
        model.replaceJobs([makeJob("job-1")])
        model.togglePanel()
        XCTAssertTrue(model.isPanelPresented)

        model.replaceJobs([])
        XCTAssertFalse(model.isPanelPresented)
        XCTAssertTrue(model.jobs.isEmpty)
    }

    func testToggleAndDismissKeepInFlightCancel() async {
        let canceler = GatedCanceler()
        let model = AgentJobStatusModel(canceler: canceler)
        let job = makeJob("job-1")
        model.replaceJobs([job])
        model.togglePanel()
        model.requestCancel(job)
        model.confirmCancel()
        await canceler.waitUntilStarted()
        XCTAssertTrue(model.cancelState?.inFlight == true)

        model.dismissPanel()
        XCTAssertFalse(model.isPanelPresented)
        XCTAssertTrue(model.cancelState?.inFlight == true)

        model.togglePanel()
        XCTAssertTrue(model.isPanelPresented)
        model.dismissPanel()
        XCTAssertTrue(model.cancelState?.inFlight == true)

        await canceler.succeed()
        await eventually { model.cancelState == nil }
        XCTAssertEqual(model.jobs.count, 1)
    }

    func testDismissCancelOnlyWhenNotInFlight() async {
        let canceler = GatedCanceler()
        let model = AgentJobStatusModel(canceler: canceler)
        let job = makeJob("job-1")
        model.replaceJobs([job])
        model.requestCancel(job)
        model.dismissCancel()
        XCTAssertNil(model.cancelState)

        model.requestCancel(job)
        model.confirmCancel()
        await canceler.waitUntilStarted()
        model.dismissCancel()
        XCTAssertTrue(model.cancelState?.inFlight == true)
        await canceler.succeed()
        await eventually { model.cancelState == nil }
    }

    func testRequestCancelSwitchesTargetBeforeInFlight() {
        let model = AgentJobStatusModel()
        let first = makeJob("job-1")
        let second = makeJob("job-2")
        model.replaceJobs([first, second])
        model.requestCancel(first)
        model.requestCancel(second)
        XCTAssertEqual(model.cancelState?.job.jobID, "job-2")
    }

    func testSuccessClearsStateButKeepsRowsUntilSnapshot() async {
        let canceler = GatedCanceler()
        let model = AgentJobStatusModel(canceler: canceler)
        let first = makeJob("job-1")
        let second = makeJob("job-2")
        model.replaceJobs([first, second])
        model.requestCancel(first)
        model.confirmCancel()
        await canceler.waitUntilStarted()
        XCTAssertEqual(model.jobs.count, 2)

        await canceler.succeed()
        await eventually { model.cancelState == nil }
        XCTAssertEqual(model.jobs.count, 2)

        model.replaceJobs([second])
        XCTAssertEqual(model.jobs.map(\.jobID), ["job-2"])
    }

    func testNotFoundAndConflictAreSilent() async {
        for error in [
            AgentJobCancelError(statusCode: 404, code: "not_found", message: ""),
            AgentJobCancelError(statusCode: 409, code: "conflict", message: "")
        ] {
            let canceler = GatedCanceler()
            let model = AgentJobStatusModel(canceler: canceler)
            let job = makeJob("job-1")
            model.replaceJobs([job])
            model.requestCancel(job)
            model.confirmCancel()
            await canceler.waitUntilStarted()
            await canceler.fail(error)
            await eventually { model.cancelState == nil }
            XCTAssertEqual(model.jobs.map(\.jobID), ["job-1"])
        }
    }

    func testOtherErrorsKeepTargetAndShowFailure() async {
        let canceler = GatedCanceler()
        let model = AgentJobStatusModel(canceler: canceler)
        let job = makeJob("job-1")
        model.replaceJobs([job])
        model.requestCancel(job)
        model.confirmCancel()
        await canceler.waitUntilStarted()
        await canceler.fail(
            AgentJobCancelError(
                statusCode: 503,
                code: "cancel_incomplete",
                message: "still active"
            )
        )
        await eventually { model.cancelState?.failed == true }
        XCTAssertEqual(model.cancelState?.job.jobID, "job-1")
        XCTAssertFalse(model.cancelState?.inFlight ?? true)
    }

    func testStaleResponseDoesNotOverwriteNewTarget() async {
        let canceler = GatedCanceler()
        let model = AgentJobStatusModel(canceler: canceler)
        let first = makeJob("job-1")
        let second = makeJob("job-2")
        model.replaceJobs([first, second])
        model.requestCancel(first)
        model.confirmCancel()
        await canceler.waitUntilStarted()

        model.replaceJobs([second])
        XCTAssertNil(model.cancelState)
        model.requestCancel(second)
        XCTAssertEqual(model.cancelState?.job.jobID, "job-2")

        await canceler.fail(
            AgentJobCancelError(statusCode: 500, code: "internal", message: "")
        )
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(model.cancelState?.job.jobID, "job-2")
        XCTAssertFalse(model.cancelState?.failed ?? true)
    }

    func testNilCancelerMarksFailure() {
        let model = AgentJobStatusModel()
        let job = makeJob("job-1")
        model.replaceJobs([job])
        model.requestCancel(job)
        model.confirmCancel()
        XCTAssertTrue(model.cancelState?.failed == true)
        XCTAssertFalse(model.cancelState?.inFlight ?? true)
    }

    func testClearDropsEverything() {
        let model = AgentJobStatusModel()
        let job = makeJob("job-1")
        model.replaceJobs([job])
        model.togglePanel()
        model.requestCancel(job)
        model.clear()
        XCTAssertTrue(model.jobs.isEmpty)
        XCTAssertFalse(model.isPanelPresented)
        XCTAssertNil(model.cancelState)
    }
}

final class AgentJobFormattingTests: XCTestCase {
    private let utc = TimeZone(secondsFromGMT: 0)!
    private let english = Locale(identifier: "en_US_POSIX")

    func testStartedTimeUsesLocalPattern() {
        XCTAssertEqual(
            AgentJobFormatting.startedTime(
                milliseconds: 0,
                locale: english,
                timeZone: utc
            ),
            "00:00:00"
        )
        XCTAssertEqual(
            AgentJobFormatting.startedTime(
                milliseconds: 1_700_000_000_000,
                locale: english,
                timeZone: utc
            ),
            "22:13:20"
        )
    }

    func testElapsedFormats() {
        XCTAssertEqual(AgentJobFormatting.elapsed(milliseconds: -5_000, locale: english), "0s")
        XCTAssertEqual(AgentJobFormatting.elapsed(milliseconds: 0, locale: english), "0s")
        XCTAssertEqual(AgentJobFormatting.elapsed(milliseconds: 59_000, locale: english), "59s")
        XCTAssertEqual(AgentJobFormatting.elapsed(milliseconds: 60_000, locale: english), "1m 0s")
        XCTAssertEqual(AgentJobFormatting.elapsed(milliseconds: 3_661_000, locale: english), "1h 1m")
        XCTAssertEqual(AgentJobFormatting.elapsed(milliseconds: 3_600_000, locale: english), "1h 0m")
    }

    func testRunningMillisecondsFallsBackForNegativeClock() {
        let job = AgentJob(
            jobID: "job-1",
            agentName: "hermes",
            summary: "Task",
            startedAtMs: 10_000,
            runningTimeMs: 2_000
        )
        XCTAssertEqual(
            AgentJobFormatting.runningMilliseconds(for: job, nowMs: 12_500),
            2_500
        )
        XCTAssertEqual(
            AgentJobFormatting.runningMilliseconds(for: job, nowMs: 9_000),
            2_000
        )
    }

    func testBadgeClampsAt99() {
        XCTAssertEqual(AgentJobFormatting.badgeCount(0), "0")
        XCTAssertEqual(AgentJobFormatting.badgeCount(99), "99")
        XCTAssertEqual(AgentJobFormatting.badgeCount(100), "99+")
    }

    func testArabicBadgeAndClockDigits() {
        let arabic = Locale(identifier: "ar_SA")
        XCTAssertEqual(AgentJobFormatting.badgeCount(0, locale: arabic), "٠")
        XCTAssertEqual(AgentJobFormatting.badgeCount(99, locale: arabic), "٩٩")
        XCTAssertEqual(AgentJobFormatting.badgeCount(100, locale: arabic), "٩٩+")
        XCTAssertEqual(
            AgentJobFormatting.startedTime(milliseconds: 0, locale: arabic, timeZone: utc),
            "٠٠:٠٠:٠٠"
        )
        XCTAssertEqual(
            AgentJobFormatting.startedTime(milliseconds: 1_700_000_000_000, locale: arabic, timeZone: utc),
            "٢٢:١٣:٢٠"
        )
        XCTAssertEqual(
            AgentJobFormatting.elapsed(milliseconds: 65_000, locale: arabic),
            "١m ٥s"
        )
        XCTAssertEqual(
            AgentTimeLabel.text(
                for: Date(timeIntervalSince1970: 0),
                locale: arabic,
                timeZone: utc
            ),
            "٠٠:٠٠"
        )
    }

    func testPluralKeysDoNotLeavePlaceholders() {
        for count in [1, 2] {
            XCTAssertFalse(
                L10n.plural("conv_agent_jobs_title", count).contains("%")
            )
            XCTAssertFalse(
                L10n.plural("conv_agent_jobs_running", count).contains("%")
            )
        }
    }
}
