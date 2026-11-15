package dev.livachat.app.ui.screens.webview

import android.Manifest
import android.webkit.PermissionRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class WebViewMediaPermissionTest {

    private val serverBase = "https://server.test:8080"
    private val meetingUrl = "$serverBase/meeting/12345678"

    @Test
    fun `trusted meeting paths are accepted`() {
        assertTrue(isTrustedMeetingPage("$serverBase/meeting", serverBase, false))
        assertTrue(isTrustedMeetingPage("$serverBase/meeting/", serverBase, false))
        assertTrue(isTrustedMeetingPage(meetingUrl, serverBase, false))
        assertTrue(isTrustedMeetingPage("$meetingUrl?from=push#room", serverBase, false))
    }

    @Test
    fun `non meeting paths on the trusted server are rejected`() {
        assertFalse(isTrustedMeetingPage("$serverBase/webroot/abc", serverBase, false))
        assertFalse(isTrustedMeetingPage("$serverBase/", serverBase, false))
        assertFalse(isTrustedMeetingPage("$serverBase/meetingevil/123", serverBase, false))
        assertFalse(isTrustedMeetingPage("$serverBase/meeting/../evil", serverBase, false))
        assertFalse(isTrustedMeetingPage("$serverBase/meeting/..%2Fevil", serverBase, false))
        assertFalse(isTrustedMeetingPage("$serverBase/meeting/%2e%2e/evil", serverBase, false))
    }

    @Test
    fun `meeting path on an untrusted origin is rejected`() {
        assertFalse(isTrustedMeetingPage("https://evil.test/meeting/123", serverBase, false))
        assertFalse(isTrustedMeetingPage("https://server.test.evil.test/meeting/", serverBase, false))
    }

    @Test
    fun `scheme host case and effective port follow origin rules`() {
        assertTrue(
            isTrustedMeetingPage(
                pageUrl = "https://SERVER.test/meeting/123",
                serverBaseUrl = "https://server.test:443",
                allowDebugLoopback = false,
            ),
        )
        assertFalse(
            isTrustedMeetingPage(
                pageUrl = "http://server.test/meeting/123",
                serverBaseUrl = "https://server.test",
                allowDebugLoopback = false,
            ),
        )
        assertFalse(
            isTrustedMeetingPage(
                pageUrl = "https://server.test:8443/meeting/123",
                serverBaseUrl = "https://server.test:9443",
                allowDebugLoopback = false,
            ),
        )
    }

    @Test
    fun `debug build trusts loopback and emulator hosts only when enabled`() {
        val loopback = "https://127.0.0.1:5174/meeting/123"
        val localhost = "http://localhost:5174/meeting/"
        val emulator = "https://10.0.2.2:5174/meeting/"

        assertTrue(isTrustedMeetingPage(loopback, serverBase, allowDebugLoopback = true))
        assertTrue(isTrustedMeetingPage(localhost, serverBase, allowDebugLoopback = true))
        assertTrue(isTrustedMeetingPage(emulator, serverBase, allowDebugLoopback = true))
        assertFalse(isTrustedMeetingPage(loopback, serverBase, allowDebugLoopback = false))
        assertFalse(isTrustedMeetingPage(localhost, serverBase, allowDebugLoopback = false))
    }

    @Test
    fun `media request origin must match the page origin`() {
        assertTrue(isAllowedMediaRequest(meetingUrl, "https://server.test:8080", serverBase, false))
        assertTrue(
            isAllowedMediaRequest(
                pageUrl = "https://server.test/meeting/123",
                requestOrigin = "https://server.test:443",
                serverBaseUrl = "https://server.test",
                allowDebugLoopback = false,
            ),
        )
        assertFalse(isAllowedMediaRequest(meetingUrl, "https://evil.test", serverBase, false))
        assertFalse(isAllowedMediaRequest(meetingUrl, "https://server.test", serverBase, false))
        assertFalse(isAllowedMediaRequest(meetingUrl, null, serverBase, false))
        assertFalse(isAllowedMediaRequest(meetingUrl, "null", serverBase, false))
        assertFalse(isAllowedMediaRequest(null, "https://server.test:8080", serverBase, false))
    }

    @Test
    fun `unsupported resources are filtered out of the permission plan`() {
        val plan = planMediaPermission(
            requestedResources = setOf(
                PermissionRequest.RESOURCE_VIDEO_CAPTURE,
                PermissionRequest.RESOURCE_PROTECTED_MEDIA_ID,
                PermissionRequest.RESOURCE_MIDI_SYSEX,
            ),
            isRuntimePermissionGranted = { false },
        )

        assertEquals(setOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE), plan.webResources)
        assertEquals(setOf(Manifest.permission.CAMERA), plan.runtimePermissions)
        assertEquals(setOf(Manifest.permission.CAMERA), plan.missingRuntimePermissions)
    }

    @Test
    fun `permission plan reports only missing runtime permissions`() {
        val granted = setOf(Manifest.permission.CAMERA)
        val plan = planMediaPermission(
            requestedResources = setOf(
                PermissionRequest.RESOURCE_VIDEO_CAPTURE,
                PermissionRequest.RESOURCE_AUDIO_CAPTURE,
            ),
            isRuntimePermissionGranted = { it in granted },
        )

        assertEquals(
            setOf(
                PermissionRequest.RESOURCE_VIDEO_CAPTURE,
                PermissionRequest.RESOURCE_AUDIO_CAPTURE,
            ),
            plan.webResources,
        )
        assertEquals(
            setOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO),
            plan.runtimePermissions,
        )
        assertEquals(setOf(Manifest.permission.RECORD_AUDIO), plan.missingRuntimePermissions)
    }

    @Test
    fun `unknown resources produce an empty plan`() {
        val plan = planMediaPermission(
            requestedResources = setOf("android.webkit.resource.UNKNOWN"),
            isRuntimePermissionGranted = { false },
        )

        assertTrue(plan.webResources.isEmpty())
        assertTrue(plan.runtimePermissions.isEmpty())
        assertTrue(plan.missingRuntimePermissions.isEmpty())
        assertNull(runtimePermissionFor("android.webkit.resource.UNKNOWN"))
    }

    @Test
    fun `media resource permission mapping is stable`() {
        assertEquals(
            Manifest.permission.CAMERA,
            runtimePermissionFor(PermissionRequest.RESOURCE_VIDEO_CAPTURE),
        )
        assertEquals(
            Manifest.permission.RECORD_AUDIO,
            runtimePermissionFor(PermissionRequest.RESOURCE_AUDIO_CAPTURE),
        )
    }
}
