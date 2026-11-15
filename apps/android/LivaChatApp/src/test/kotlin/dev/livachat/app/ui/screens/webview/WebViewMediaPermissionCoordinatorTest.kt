package dev.livachat.app.ui.screens.webview

import android.Manifest
import android.net.Uri
import android.webkit.PermissionRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class WebViewMediaPermissionCoordinatorTest {

    private val serverBase = "https://server.test:8080"
    private val pageUrl = "$serverBase/meeting/12345678"
    private val videoCapture = PermissionRequest.RESOURCE_VIDEO_CAPTURE
    private val audioCapture = PermissionRequest.RESOURCE_AUDIO_CAPTURE

    private class FakePermissionRequest(
        private val origin: Uri,
        private val resources: Array<String>,
    ) : PermissionRequest() {
        var grantedResources: Array<String>? = null
        var denied: Boolean = false

        override fun deny() {
            denied = true
        }

        override fun getOrigin(): Uri = origin

        override fun getResources(): Array<String> = resources

        override fun grant(resources: Array<String>) {
            grantedResources = resources
        }
    }

    private fun request(
        vararg resources: String,
        origin: String = serverBase,
    ): FakePermissionRequest = FakePermissionRequest(
        origin = Uri.parse(origin),
        resources = arrayOf(*resources),
    )

    private fun coordinator(
        pageUrlProvider: () -> String? = { pageUrl },
        granted: MutableSet<String> = mutableSetOf(),
        resumed: () -> Boolean = { true },
        alive: Boolean = true,
        onLaunch: (Set<String>) -> Unit = {},
    ): WebViewMediaPermissionCoordinator = WebViewMediaPermissionCoordinator(
        pageUrlProvider = pageUrlProvider,
        serverBaseUrl = serverBase,
        allowDebugLoopback = false,
        isRuntimePermissionGranted = { it in granted },
        isHostResumed = resumed,
        isWebViewAlive = { alive },
        requestRuntimePermissions = onLaunch,
        log = {},
    )

    @Test
    fun `granted permissions are passed through immediately`() {
        val granted = mutableSetOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)
        val media = request(videoCapture, audioCapture)
        var launched: Set<String>? = null
        val subject = coordinator(granted = granted) { launched = it }

        subject.onPermissionRequest(media)

        assertEquals(setOf(videoCapture, audioCapture), media.grantedResources?.toSet())
        assertFalse(media.denied)
        assertNull(launched)
    }

    @Test
    fun `missing permissions are requested and partial grant stays safe`() {
        val granted = mutableSetOf<String>()
        val launched = mutableListOf<Set<String>>()
        val media = request(videoCapture, audioCapture)
        val subject = coordinator(granted = granted) { launched += it }

        subject.onPermissionRequest(media)
        assertEquals(setOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO), launched.single())
        assertNull(media.grantedResources)

        granted += Manifest.permission.CAMERA
        subject.onRuntimePermissionResult()

        assertEquals(setOf(videoCapture), media.grantedResources?.toSet())
        assertFalse(media.denied)
    }

    @Test
    fun `denied runtime permission results in deny not ungranted grant`() {
        val launched = mutableListOf<Set<String>>()
        val media = request(videoCapture)
        val subject = coordinator { launched += it }

        subject.onPermissionRequest(media)
        subject.onRuntimePermissionResult()

        assertEquals(setOf(Manifest.permission.CAMERA), launched.single())
        assertNull(media.grantedResources)
        assertTrue(media.denied)
    }

    @Test
    fun `request arriving during permission dialog waits and gets a follow-up prompt`() {
        val granted = mutableSetOf<String>()
        val launched = mutableListOf<Set<String>>()
        val camera = request(videoCapture)
        val microphone = request(audioCapture)
        val subject = coordinator(granted = granted) { launched += it }

        subject.onPermissionRequest(camera)
        subject.onPermissionRequest(microphone)

        assertEquals(listOf(setOf(Manifest.permission.CAMERA)), launched)
        assertNull(microphone.grantedResources)

        granted += Manifest.permission.CAMERA
        subject.onRuntimePermissionResult()

        assertEquals(
            listOf(
                setOf(Manifest.permission.CAMERA),
                setOf(Manifest.permission.RECORD_AUDIO),
            ),
            launched,
        )
        assertNull(microphone.grantedResources)

        granted += Manifest.permission.RECORD_AUDIO
        subject.onRuntimePermissionResult()

        assertEquals(setOf(videoCapture), camera.grantedResources?.toSet())
        assertEquals(setOf(audioCapture), microphone.grantedResources?.toSet())
    }

    @Test
    fun `combined request does not grant before all of its permissions are resolved`() {
        val granted = mutableSetOf<String>()
        val launched = mutableListOf<Set<String>>()
        val camera = request(videoCapture)
        val both = request(videoCapture, audioCapture)
        val subject = coordinator(granted = granted) { launched += it }

        subject.onPermissionRequest(camera)
        subject.onPermissionRequest(both)

        granted += Manifest.permission.CAMERA
        subject.onRuntimePermissionResult()

        assertEquals(
            listOf(
                setOf(Manifest.permission.CAMERA),
                setOf(Manifest.permission.RECORD_AUDIO),
            ),
            launched,
        )
        assertNull(both.grantedResources)

        granted += Manifest.permission.RECORD_AUDIO
        subject.onRuntimePermissionResult()

        assertEquals(setOf(videoCapture, audioCapture), both.grantedResources?.toSet())
    }

    @Test
    fun `canceled request is not granted when permission result arrives`() {
        val granted = mutableSetOf<String>()
        val media = request(videoCapture)
        val subject = coordinator(granted = granted)

        subject.onPermissionRequest(media)
        subject.onPermissionRequestCanceled(media)
        granted += Manifest.permission.CAMERA
        subject.onRuntimePermissionResult()

        assertNull(media.grantedResources)
        assertFalse(media.denied)
    }

    @Test
    fun `dispose denies pending requests and rejects later requests`() {
        val media = request(videoCapture)
        val later = request(videoCapture)
        val subject = coordinator()

        subject.onPermissionRequest(media)
        subject.dispose()
        subject.onPermissionRequest(later)

        assertTrue(media.denied)
        assertTrue(later.denied)
    }

    @Test
    fun `page navigation to an untrusted origin denies before grant`() {
        var currentPage = pageUrl
        val granted = mutableSetOf<String>()
        val media = request(videoCapture)
        val subject = coordinator(pageUrlProvider = { currentPage }, granted = granted)

        subject.onPermissionRequest(media)
        currentPage = "https://evil.test/meeting/12345678"
        granted += Manifest.permission.CAMERA
        subject.onRuntimePermissionResult()

        assertTrue(media.denied)
        assertNull(media.grantedResources)
    }

    @Test
    fun `untrusted page and origin are denied without launching permissions`() {
        val launched = mutableListOf<Set<String>>()
        val untrustedPage = request(videoCapture)
        val untrustedOrigin = request(videoCapture, origin = "https://evil.test")
        val subject = coordinator(pageUrlProvider = { "https://evil.test/meeting/123" }) { launched += it }

        subject.onPermissionRequest(untrustedPage)
        assertTrue(untrustedPage.denied)

        val trustedPageSubject = coordinator { launched += it }
        trustedPageSubject.onPermissionRequest(untrustedOrigin)
        assertTrue(untrustedOrigin.denied)
        assertTrue(launched.isEmpty())
    }

    @Test
    fun `null page url and background request without permission flow are denied`() {
        val nullPage = request(videoCapture)
        val background = request(videoCapture)
        val launched = mutableListOf<Set<String>>()
        val subject = coordinator(resumed = { false }) { launched += it }

        coordinator(pageUrlProvider = { null }).onPermissionRequest(nullPage)
        subject.onPermissionRequest(background)

        assertTrue(nullPage.denied)
        assertTrue(background.denied)
        assertNull(background.grantedResources)
        assertTrue(launched.isEmpty())
    }

    @Test
    fun `permission result delivered while host is paused is still processed`() {
        var resumed = true
        val granted = mutableSetOf<String>()
        val media = request(videoCapture)
        val subject = coordinator(granted = granted, resumed = { resumed })

        subject.onPermissionRequest(media)
        resumed = false
        granted += Manifest.permission.CAMERA
        subject.onRuntimePermissionResult()

        assertEquals(setOf(videoCapture), media.grantedResources?.toSet())
        assertFalse(media.denied)
    }

    @Test
    fun `already granted second request is granted while paused after first dialog`() {
        var resumed = true
        val granted = mutableSetOf(Manifest.permission.RECORD_AUDIO)
        val camera = request(videoCapture)
        val microphone = request(audioCapture)
        val subject = coordinator(granted = granted, resumed = { resumed })

        subject.onPermissionRequest(camera)
        resumed = false
        granted += Manifest.permission.CAMERA
        subject.onRuntimePermissionResult()
        assertEquals(setOf(videoCapture), camera.grantedResources?.toSet())

        // LiveKit 可能在摄像头授权后紧接着申请已授权的麦克风；此时 Activity 可能仍在 pause。
        subject.onPermissionRequest(microphone)

        assertEquals(setOf(audioCapture), microphone.grantedResources?.toSet())
        assertFalse(microphone.denied)
    }

    @Test
    fun `follow-up waits for resume after permission dialog pauses host`() {
        var resumed = true
        val granted = mutableSetOf<String>()
        val launched = mutableListOf<Set<String>>()
        val camera = request(videoCapture)
        val microphone = request(audioCapture)
        val subject = coordinator(granted = granted, resumed = { resumed }) { launched += it }

        subject.onPermissionRequest(camera)
        resumed = false
        // 权限对话框 pause 期间，同一轮授权流程中的第二个请求仍应被接收。
        subject.onPermissionRequest(microphone)
        granted += Manifest.permission.CAMERA
        subject.onRuntimePermissionResult()

        assertEquals(listOf(setOf(Manifest.permission.CAMERA)), launched)
        assertEquals(setOf(videoCapture), camera.grantedResources?.toSet())
        assertNull(microphone.grantedResources)

        resumed = true
        subject.onHostResumed()

        assertEquals(
            listOf(
                setOf(Manifest.permission.CAMERA),
                setOf(Manifest.permission.RECORD_AUDIO),
            ),
            launched,
        )

        granted += Manifest.permission.RECORD_AUDIO
        subject.onRuntimePermissionResult()
        assertEquals(setOf(audioCapture), microphone.grantedResources?.toSet())
    }

    @Test
    fun `onHostResumed forces pending follow-up even before state query catches up`() {
        var resumed = false
        val granted = mutableSetOf<String>()
        val launched = mutableListOf<Set<String>>()
        val camera = request(videoCapture)
        val microphone = request(audioCapture)
        val subject = coordinator(granted = granted, resumed = { resumed }) { launched += it }

        resumed = true
        subject.onPermissionRequest(camera)
        resumed = false
        subject.onPermissionRequest(microphone)
        granted += Manifest.permission.CAMERA
        subject.onRuntimePermissionResult()
        assertEquals(listOf(setOf(Manifest.permission.CAMERA)), launched)

        subject.onHostResumed()

        assertEquals(
            listOf(
                setOf(Manifest.permission.CAMERA),
                setOf(Manifest.permission.RECORD_AUDIO),
            ),
            launched,
        )
    }

    @Test
    fun `background request with granted permission is denied without permission flow`() {
        val media = request(videoCapture)
        val subject = coordinator(
            granted = mutableSetOf(Manifest.permission.CAMERA),
            resumed = { false },
        )

        subject.onPermissionRequest(media)

        assertTrue(media.denied)
        assertNull(media.grantedResources)
    }

    @Test
    fun `dead webview denies without launching`() {
        val launched = mutableListOf<Set<String>>()
        val media = request(videoCapture)
        coordinator(alive = false) { launched += it }.onPermissionRequest(media)

        assertTrue(media.denied)
        assertTrue(launched.isEmpty())
    }

    @Test
    fun `permission launcher failure denies pending request`() {
        val media = request(videoCapture)
        val subject = coordinator(onLaunch = { error("no launcher") })

        subject.onPermissionRequest(media)

        assertTrue(media.denied)
    }
}
