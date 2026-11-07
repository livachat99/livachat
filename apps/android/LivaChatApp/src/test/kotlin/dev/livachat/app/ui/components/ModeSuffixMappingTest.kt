package dev.livachat.app.ui.components

import dev.livachat.app.R
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * D4=B 展示侧映射（docs/android_multilingual.md §3.4）：mode+role → 后缀资源。
 * 会话实时气泡与 history 详情共用 MessageBubble → 同源（§2.3.3）。
 */
class ModeSuffixMappingTest {

    @Test
    fun transcribeUser_mapsToTranscriptSuffix() {
        assertEquals(
            R.string.conv_msg_suffix_transcript,
            modeSuffixResFor(ChatMessage.MODE_TRANSCRIBE, MessageRole.USER),
        )
    }

    @Test
    fun translateUser_mapsToOriginalSuffix() {
        assertEquals(
            R.string.conv_msg_suffix_original,
            modeSuffixResFor(ChatMessage.MODE_TRANSLATE, MessageRole.USER),
        )
    }

    @Test
    fun translateAi_mapsToTranslationSuffix() {
        assertEquals(
            R.string.conv_msg_suffix_translation,
            modeSuffixResFor(ChatMessage.MODE_TRANSLATE, MessageRole.AI),
        )
    }

    @Test
    fun nullMode_mapsToNoSuffix() {
        assertNull(modeSuffixResFor(null, MessageRole.USER))
        assertNull(modeSuffixResFor(null, MessageRole.AI))
    }
}
