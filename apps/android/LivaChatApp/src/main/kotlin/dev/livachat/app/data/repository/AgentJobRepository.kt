package dev.livachat.app.data.repository

import dev.livachat.app.data.remote.mylife.MylifeApi
import javax.inject.Inject
import javax.inject.Singleton

/** Agent job 取消的薄封装（docs/android_agent_job_status.md §7.1）；VM 不直接依赖 remote API。 */
@Singleton
class AgentJobRepository @Inject constructor(
    private val api: MylifeApi,
) {
    suspend fun cancel(jobId: String) {
        api.cancelAgentJob(jobId)
    }
}
