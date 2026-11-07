package dev.livachat.sdk.model

/** A running background job reported by a job_status snapshot. */
data class AgentJob(
    /** Job identifier (relay job_key, uuid hex). Result ai_text carries
     *  message_id "msg_agent_job_{jobId}". */
    val jobId: String,
    /** Agent that owns the job. Currently always "hermes". */
    val agentName: String,
    /** One-line task summary (same text as the job-started ai_text). */
    val summary: String,
    /** When the job entered running, converted to device-local epoch ms
     *  via the session clock offset. App may compute live running time as
     *  `System.currentTimeMillis() - startedAtMs`. */
    val startedAtMs: Long,
    /** Server-computed elapsed running time at frame send time (ms).
     *  Fallback when clock offset is unavailable. */
    val runningTimeMs: Long,
)
