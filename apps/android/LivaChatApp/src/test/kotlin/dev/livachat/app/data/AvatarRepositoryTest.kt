package dev.livachat.app.data

import android.content.Context
import android.net.Uri
import dev.livachat.app.data.repository.AvatarRepository
import java.io.File
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment

@RunWith(RobolectricTestRunner::class)
class AvatarRepositoryTest {

    private val context: Context get() = RuntimeEnvironment.getApplication()
    private val repository = AvatarRepository(context)

    @Test
    fun `save uses a unique name and removes the previous avatar`() = runTest {
        val source = File(context.cacheDir, "src.jpg").apply { writeBytes(byteArrayOf(1, 2, 3)) }
        val legacy = File(context.filesDir, "avatar/custom_avatar.jpg")
        legacy.parentFile!!.mkdirs()
        legacy.writeBytes(byteArrayOf(9))

        val saved = repository.saveAvatarFromUri(Uri.fromFile(source))

        val files = File(context.filesDir, "avatar").listFiles()!!
        assertEquals(1, files.size)
        assertTrue(files[0].name.startsWith("custom_avatar_"))
        assertEquals(Uri.fromFile(files[0]), saved)
        assertFalse(legacy.exists())
        assertEquals(saved, repository.getAvatarUri())
    }

    @Test
    fun `save failure keeps the previous avatar and leaves no partial file`() = runTest {
        val legacy = File(context.filesDir, "avatar/custom_avatar_old.jpg")
        legacy.parentFile!!.mkdirs()
        legacy.writeBytes(byteArrayOf(9))

        var threw = false
        try {
            repository.saveAvatarFromUri(Uri.fromFile(File(context.cacheDir, "missing.jpg")))
        } catch (_: Exception) {
            threw = true
        }

        assertTrue(threw)
        assertTrue(legacy.exists())
        assertEquals(1, File(context.filesDir, "avatar").listFiles()!!.size)
    }

    @Test
    fun `deleteCameraSourceIfOwned removes a FileProvider content uri source`() = runTest {
        val photoDir = File(context.cacheDir, "avatar_photos").apply { mkdirs() }
        val photo = File(photoDir, "camera_123.jpg").apply { writeBytes(byteArrayOf(1)) }
        val uri = Uri.parse("content://${context.packageName}.fileprovider/avatar_photos/camera_123.jpg")

        repository.deleteCameraSourceIfOwned(uri)

        assertFalse(photo.exists())
    }

    @Test
    fun `deleteCameraSourceIfOwned ignores foreign uris`() = runTest {
        val outside = File(context.filesDir, "keep.jpg").apply { writeBytes(byteArrayOf(1)) }

        repository.deleteCameraSourceIfOwned(Uri.parse("content://media/external/images/media/42"))
        repository.deleteCameraSourceIfOwned(Uri.fromFile(outside))

        assertTrue(outside.exists())
    }
}
