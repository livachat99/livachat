package dev.livachat.app.domain.model

import java.time.Instant

data class SessionSummary(
    val id: String,
    val startTime: Instant,
    val endTime: Instant?,
    val messageCount: Int,
    val summary: String?,
    val firstUserMessage: String? = null,
)
