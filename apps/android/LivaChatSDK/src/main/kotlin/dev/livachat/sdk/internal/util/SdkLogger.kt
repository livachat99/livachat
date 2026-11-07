package dev.livachat.sdk.internal.util

import android.util.Log
import java.io.File
import java.io.FileWriter
import java.io.IOException
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

/**
 * SDK-internal logging utility.
 *
 * Writes to both logcat (via [android.util.Log]) and an optional log file.
 * Timestamps use microsecond precision and are placed at the front of every line.
 *
 * Microsecond precision is achieved by combining [System.currentTimeMillis] (wall clock)
 * with [System.nanoTime] (monotonic sub-ms offset). The anchor is periodically refreshed
 * to correct for clock drift.
 *
 * Thread-safe for file writes via [synchronized].
 */
internal object SdkLogger {

    private const val TAG_PREFIX = "LivaChat-"
    private const val REANCHOR_INTERVAL_MS = 60_000L

    private val tsFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSSSSS'Z'")
        .withZone(ZoneOffset.UTC)

    @Volatile private var writer: FileWriter? = null

    // Wall-clock ↔ monotonic anchor for microsecond timestamps.
    @Volatile private var anchorMillis: Long = 0L
    @Volatile private var anchorNanos: Long = 0L
    @Volatile private var lastAnchorRefresh: Long = 0L

    // ── Initialization ──────────────────────────────────────────────

    fun init(logFilePath: String?) {
        refreshAnchor()
        writer?.close()
        writer = if (logFilePath != null) {
            try {
                val file = File(logFilePath)
                file.parentFile?.mkdirs()
                FileWriter(file, true)
            } catch (e: IOException) {
                Log.e("${TAG_PREFIX}Logger", "Failed to open log file: $logFilePath", e)
                null
            }
        } else {
            null
        }
    }

    // ── Public API ──────────────────────────────────────────────────

    fun d(tag: String, msg: String) = log(Log.DEBUG, tag, msg, null)
    fun i(tag: String, msg: String) = log(Log.INFO, tag, msg, null)
    fun w(tag: String, msg: String, tr: Throwable? = null) = log(Log.WARN, tag, msg, tr)
    fun e(tag: String, msg: String, tr: Throwable? = null) = log(Log.ERROR, tag, msg, tr)

    // ── Internal ────────────────────────────────────────────────────

    private fun log(priority: Int, tag: String, msg: String, tr: Throwable?) {
        val ts = formatTimestamp()
        val fullTag = TAG_PREFIX + tag
        val line = "$ts ${priorityChar(priority)}/$fullTag: $msg"

        // logcat
        if (tr != null) {
            Log.println(priority, fullTag, "$line\n${Log.getStackTraceString(tr)}")
        } else {
            Log.println(priority, fullTag, line)
        }

        // file
        val w = writer ?: return
        synchronized(w) {
            try {
                w.append(line).append('\n')
                if (tr != null) {
                    w.append(Log.getStackTraceString(tr)).append('\n')
                }
                w.flush()
            } catch (_: IOException) {
                // file write failed — don't cascade
            }
        }
    }

    private fun formatTimestamp(): String {
        maybeRefreshAnchor()
        val elapsedUs = (System.nanoTime() - anchorNanos) / 1_000L
        val totalUs = anchorMillis * 1_000L + elapsedUs
        val seconds = totalUs / 1_000_000L
        val micros = (totalUs % 1_000_000L).toInt()
        return tsFormatter.format(Instant.ofEpochSecond(seconds, micros * 1_000L))
    }

    private fun maybeRefreshAnchor() {
        val now = System.currentTimeMillis()
        if (now - lastAnchorRefresh > REANCHOR_INTERVAL_MS) {
            refreshAnchor()
        }
    }

    private fun refreshAnchor() {
        anchorNanos = System.nanoTime()
        anchorMillis = System.currentTimeMillis()
        lastAnchorRefresh = anchorMillis
    }

    private fun priorityChar(priority: Int): Char = when (priority) {
        Log.DEBUG -> 'D'
        Log.INFO -> 'I'
        Log.WARN -> 'W'
        Log.ERROR -> 'E'
        else -> '?'
    }
}
