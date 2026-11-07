import Combine
import Foundation
import LivaChatSDK

protocol AgentJobCanceling: Sendable {
    func cancel(jobID: String) async throws
}

struct AgentJobCancelError: Error, Equatable, Sendable {
    let statusCode: Int
    let code: String
    let message: String
}

struct AgentJobCancelState: Equatable, Sendable {
    let job: AgentJob
    var inFlight = false
    var failed = false
}

@MainActor
final class AgentJobStatusModel: ObservableObject {
    @Published private(set) var jobs: [AgentJob] = []
    @Published private(set) var isPanelPresented = false
    @Published private(set) var cancelState: AgentJobCancelState?

    private var canceler: AgentJobCanceling?

    init(canceler: AgentJobCanceling? = nil) {
        self.canceler = canceler
    }

    func setCanceler(_ canceler: AgentJobCanceling?) {
        self.canceler = canceler
    }

    func replaceJobs(_ jobs: [AgentJob]) {
        self.jobs = jobs
        if jobs.isEmpty {
            isPanelPresented = false
        }
        if let pending = cancelState,
           !jobs.contains(where: { $0.jobID == pending.job.jobID }) {
            cancelState = nil
        }
    }

    func clear() {
        jobs = []
        isPanelPresented = false
        cancelState = nil
    }

    func togglePanel() {
        if isPanelPresented {
            dismissPanel()
        } else if !jobs.isEmpty {
            isPanelPresented = true
        }
    }

    func dismissPanel() {
        isPanelPresented = false
        if cancelState?.inFlight != true {
            cancelState = nil
        }
    }

    func requestCancel(_ job: AgentJob) {
        guard cancelState?.inFlight != true else { return }
        cancelState = AgentJobCancelState(job: job)
    }

    func dismissCancel() {
        guard cancelState?.inFlight != true else { return }
        cancelState = nil
    }

    func confirmCancel() {
        guard let target = cancelState, !target.inFlight else { return }
        guard let canceler else {
            cancelState = AgentJobCancelState(job: target.job, failed: true)
            return
        }

        let jobID = target.job.jobID
        cancelState = AgentJobCancelState(job: target.job, inFlight: true)
        Task { @MainActor [weak self] in
            do {
                try await canceler.cancel(jobID: jobID)
                self?.completeCancel(jobID: jobID, failed: false)
            } catch is CancellationError {
                return
            } catch let error as AgentJobCancelError {
                let silent = (error.statusCode == 404 && error.code == "not_found")
                    || (error.statusCode == 409 && error.code == "conflict")
                self?.completeCancel(jobID: jobID, failed: !silent)
            } catch {
                self?.completeCancel(jobID: jobID, failed: true)
            }
        }
    }

    private func completeCancel(jobID: String, failed: Bool) {
        guard let current = cancelState, current.job.jobID == jobID else { return }
        if failed {
            cancelState = AgentJobCancelState(job: current.job, failed: true)
        } else {
            cancelState = nil
        }
    }
}
