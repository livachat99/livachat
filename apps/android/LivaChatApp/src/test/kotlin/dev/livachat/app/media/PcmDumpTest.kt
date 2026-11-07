package dev.livachat.app.media

import java.io.File
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * docs/android_audio_capture.md §7.1 用例 11-14：
 * 逐样本落盘、达上限停写、目录不可用静默降级、close 幂等/关闭后写入不再落盘。
 *
 * 一实例=一文件（跨代不再共享队列/写线程）——本文件用多实例并存覆盖该不变式。
 */
class PcmDumpTest {

    private fun tempDir(): File = Files.createTempDirectory("pcmdump").toFile()

    private fun frames(n: Int): List<ShortArray> = (0 until n).map { i ->
        ShortArray(320) { j -> ((i * 320 + j) * 37 % 65536 - 32768).toShort() }
    }

    private fun onlyRaw(dir: File): File =
        dir.listFiles()!!.single { it.name.startsWith("pcm_") && it.name.endsWith(".raw") }

    private fun expectedBytes(frames: List<ShortArray>): ByteArray {
        val samples = frames.sumOf { it.size }
        val b = ByteArray(samples * 2)
        var i = 0
        for (frame in frames) {
            for (s in frame) {
                val v = s.toInt()
                b[i * 2] = (v and 0xFF).toByte()
                b[i * 2 + 1] = ((v shr 8) and 0xFF).toByte()
                i++
            }
        }
        return b
    }

    // 11. 写入 N 帧 → 文件字节数 = N×320×2 且逐字节等于 s16le 编码
    @Test
    fun writesFramesVerbatimAsS16le() {
        val dir = tempDir()
        val data = frames(3)
        val dump = PcmDump(dir, "s1_r16000")

        data.forEach { dump.write(it, it.size) }
        assertEquals(0, dump.close())

        val file = onlyRaw(dir)
        assertTrue(file.name.startsWith("pcm_s1_r16000_"))
        assertEquals(3 * 320 * 2, file.length())
        assertEquals(3L * 320 * 2, dump.bytesWritten)
        assertTrue(file.readBytes().contentEquals(expectedBytes(data)))
    }

    // 12/13. 达单文件上限停写：文件不超上限，后续写入不再增长
    @Test
    fun stopsAtSizeCap() {
        val dir = tempDir()
        val dump = PcmDump(dir, "cap", maxBytes = 1280, queueFrames = 64) // 2 帧

        val data = frames(10)
        data.forEach { dump.write(it, it.size) }

        val deadline = System.currentTimeMillis() + 2_000
        while (!dump.isCapped && System.currentTimeMillis() < deadline) Thread.sleep(10)
        assertTrue("writer must stop at cap", dump.isCapped)

        val sizeAtCap = onlyRaw(dir).length()
        assertEquals(1280, sizeAtCap)

        data.forEach { dump.write(it, it.size) } // 超限后写入应被忽略
        Thread.sleep(50)
        dump.close()
        assertEquals(sizeAtCap, onlyRaw(dir).length())
    }

    // 14a. 目录不可用（路径已是文件）→ 静默降级：file=null、写入不抛、close 返回 0
    @Test
    fun unusableDir_degradesSilently() {
        val notADir = File(tempDir(), "not-a-dir").apply { createNewFile() }
        val dump = PcmDump(notADir, "t")

        assertNull(dump.file)
        dump.write(ShortArray(320) { 7 }, 320)
        assertEquals(0, dump.close())
        assertEquals(0L, dump.bytesWritten)
        assertEquals(1, notADir.parentFile!!.listFiles()!!.size) // 未产出任何文件
    }

    // 14b. close 幂等；关闭后写入不再落盘
    @Test
    fun closeIsIdempotentAndWritesStop() {
        val dir = tempDir()
        val dump = PcmDump(dir, "t")

        dump.write(ShortArray(320) { 7 }, 320)
        assertEquals(0, dump.close())
        assertEquals(0, dump.close()) // 幂等
        assertEquals(640, onlyRaw(dir).length())

        dump.write(ShortArray(320) { 9 }, 320) // close 后写入被忽略
        Thread.sleep(50)
        assertEquals(640, onlyRaw(dir).length())
    }

    // 一实例=一文件：两个实例并存互不影响（旧实例晚退不污染新实例）
    @Test
    fun instancesAreIsolated() {
        val dir = tempDir()
        val older = PcmDump(dir, "s1_r16000")
        older.write(frames(1)[0], 320)

        val newer = PcmDump(dir, "s5_r16000")
        newer.write(frames(2)[0], 320)
        newer.write(frames(2)[1], 320)

        older.close() // 旧实例先关（模拟源切换后异步收敛）
        newer.close()

        val files = dir.listFiles()!!.filter { it.name.endsWith(".raw") }
        assertEquals(2, files.size)
        assertEquals(640, files.single { it.name.contains("s1_r16000") }.length())
        assertEquals(1280, files.single { it.name.contains("s5_r16000") }.length())
    }
}
