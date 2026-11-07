package dev.livachat.app.ui.components

import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.semantics.SemanticsActions
import kotlin.math.abs
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.R
import dev.livachat.app.ui.theme.LivaChatTheme
import kotlinx.coroutines.flow.MutableStateFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class BottomToolbarTest {

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun str(id: Int) = context.getString(id)
    private fun micOn() = str(R.string.conv_toolbar_mic_on)
    private fun micOff() = str(R.string.conv_toolbar_mic_off)
    private fun speakerOn() = str(R.string.conv_toolbar_speaker_on)
    private fun speakerOff() = str(R.string.conv_toolbar_speaker_off)
    private fun cameraOn() = str(R.string.conv_toolbar_camera_on)
    private fun cameraOff() = str(R.string.conv_toolbar_camera_off)
    private fun selectMic() = str(R.string.conv_select_mic_device)
    private fun selectSpeaker() = str(R.string.conv_select_speaker_device)
    private fun galleryPicker() = str(R.string.conv_avatar_from_gallery)
    // 工具栏短标签 = caption 首词（BottomToolbar.kt 的 split(" ") 渲染）
    private fun micLabel() = micOn().split(" ").first()
    private fun speakerLabel() = speakerOn().split(" ").first()
    private fun cameraLabel() = cameraOn().split(" ").first()

    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun three_buttons_are_displayed_with_correct_content_descriptions() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                )
            }
        }

        composeTestRule
            .onNodeWithContentDescription(micOn())
            .assertIsDisplayed()
        composeTestRule
            .onNodeWithContentDescription(speakerOn())
            .assertIsDisplayed()
        composeTestRule
            .onNodeWithContentDescription(cameraOn())
            .assertIsDisplayed()
    }

    @Test
    fun toggleMic_callback_is_invoked_on_mic_click() {
        var toggled = false
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = { toggled = true },
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                )
            }
        }

        composeTestRule
            .onNodeWithContentDescription(micOn())
            .performClick()
        assertTrue(toggled)
    }

    @Test
    fun all_off_state_shows_correct_captions() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = false,
                    isSpeakerOn = false,
                    isCameraOn = false,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                )
            }
        }

        composeTestRule
            .onNodeWithContentDescription(micOff())
            .assertIsDisplayed()
        composeTestRule
            .onNodeWithContentDescription(speakerOff())
            .assertIsDisplayed()
        composeTestRule
            .onNodeWithContentDescription(cameraOff())
            .assertIsDisplayed()
    }

    @Test
    fun micVolume_glyph_exposes_semantic_level() {
        val micLevel = MutableStateFlow(3)
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    micLevel = micLevel,
                )
            }
        }

        // v2：meter 融入 mic 图标——开启时话筒头即音量表（tag=micVolumeGlyph）。
        // glyph 语义被合并进 IconButton，tag 只在 unmerged 树可寻
        val glyph = composeTestRule.onNodeWithTag("micVolumeGlyph", useUnmergedTree = true)
        glyph.assertExists()
        assertEquals(3, glyph.fetchSemanticsNode().config[LevelKey])
    }

    @Test
    fun micVolume_glyph_updates_are_collected() {
        val micLevel = MutableStateFlow(3)
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    micLevel = micLevel,
                )
            }
        }

        composeTestRule.runOnIdle { micLevel.value = 4 }
        assertEquals(
            4,
            composeTestRule.onNodeWithTag("micVolumeGlyph", useUnmergedTree = true)
                .fetchSemanticsNode().config[LevelKey],
        )
    }

    @Test
    fun micVolume_glyph_hidden_when_mic_off() {
        // v2：mic 关闭渲染标准斜杠图标，无电平表节点
        val micLevel = MutableStateFlow(3)
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = false,
                    isSpeakerOn = false,
                    isCameraOn = false,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    micLevel = micLevel,
                )
            }
        }

        composeTestRule.onAllNodesWithTag("micVolumeGlyph").assertCountEquals(0)
        composeTestRule
            .onNodeWithContentDescription(micOff())
            .assertIsDisplayed()
    }

    @Test
    fun micLevel_null_falls_back_to_standard_icon_and_keeps_captions_aligned() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    micLevel = null,
                )
            }
        }

        // 无 flow 时退回标准 Mic 图标，不渲染电平表；按钮/说明布局恒定
        composeTestRule.onAllNodesWithTag("micVolumeGlyph").assertCountEquals(0)
        composeTestRule
            .onNodeWithContentDescription(micOn())
            .assertIsDisplayed()

        val micTop = composeTestRule.onNodeWithText(micLabel()).getUnclippedBoundsInRoot().top.value
        val speakerTop = composeTestRule.onNodeWithText(speakerLabel()).getUnclippedBoundsInRoot().top.value
        val cameraTop = composeTestRule.onNodeWithText(cameraLabel()).getUnclippedBoundsInRoot().top.value
        assertTrue("mic/speaker caption misaligned", abs(micTop - speakerTop) <= 0.5f)
        assertTrue("speaker/camera caption misaligned", abs(speakerTop - cameraTop) <= 0.5f)
    }

    // ── 麦克风设备选择器（docs/android_audio_input_devices.md §7.2）────────────

    @Test
    fun micDeviceSelector_chevron_hidden_when_single_device() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    showMicDeviceSelector = false,
                    onOpenMicDevicePicker = {},
                )
            }
        }

        composeTestRule
            .onAllNodesWithContentDescription(selectMic())
            .assertCountEquals(0)
    }

    @Test
    fun micDeviceSelector_chevron_shown_and_sits_before_caption() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    showMicDeviceSelector = true,
                    onOpenMicDevicePicker = {},
                )
            }
        }

        composeTestRule
            .onNodeWithContentDescription(selectMic())
            .assertIsDisplayed()
        // ^ 在「麦克风」文字之前（需求 2 原文）：unmerged 树取图标与文本各自 bounds 比对
        val chevron = composeTestRule
            .onNodeWithContentDescription(selectMic(), useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        val caption = composeTestRule
            .onNodeWithText(micLabel(), useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        assertTrue(
            "chevron must sit before mic caption: right=${chevron.right} left=${caption.left}",
            chevron.right.value <= caption.left.value + 0.5f,
        )
    }

    @Test
    fun micDeviceSelector_click_invokes_open_callback_not_mute_toggle() {
        var opened = false
        var micToggled = false
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = { micToggled = true },
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    showMicDeviceSelector = true,
                    onOpenMicDevicePicker = { opened = true },
                )
            }
        }

        // 点 ^（caption 行）：打开选择器回调触发；正上方 48dp 静音按钮不被误触
        composeTestRule
            .onNodeWithContentDescription(selectMic())
            .performClick()
        assertTrue(opened)
        assertFalse(micToggled)
    }

    @Test
    fun micDeviceSelector_caption_text_centered_under_mic_icon() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    showMicDeviceSelector = true,
                    onOpenMicDevicePicker = {},
                )
            }
        }

        // 「麦克风」文字中心 = 上方麦克风图标（48dp 按钮）中心；^ 悬挂左侧、不参与居中
        val iconBounds = composeTestRule
            .onNodeWithContentDescription(micOn())
            .getUnclippedBoundsInRoot()
        val textBounds = composeTestRule
            .onNodeWithText(micLabel(), useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        val iconCenterX = (iconBounds.left.value + iconBounds.right.value) / 2f
        val textCenterX = (textBounds.left.value + textBounds.right.value) / 2f
        assertTrue(
            "mic caption not centered under icon: icon=$iconCenterX text=$textCenterX",
            abs(iconCenterX - textCenterX) <= 0.5f,
        )
    }

    @Test
    fun micDeviceSelector_caption_touch_target_covers_padding() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    showMicDeviceSelector = true,
                    onOpenMicDevicePicker = {},
                )
            }
        }

        // 触区高度回归锁（docs/android_audio_input_devices.md §5.2）：clickable 须含行底 padding（≈24dp）；置于 padding 内层时仅 ≈16dp
        val captionBounds = composeTestRule
            .onNodeWithContentDescription(selectMic())
            .getUnclippedBoundsInRoot()
        val captionHeight = captionBounds.bottom.value - captionBounds.top.value
        assertTrue(
            "caption touch height too small: ${captionHeight}dp (expect >= 20dp)",
            captionHeight >= 20f,
        )
    }

    @Test
    fun micDeviceSelector_caption_sits_close_under_mic_icon() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    showMicDeviceSelector = true,
                    onOpenMicDevicePicker = {},
                )
            }
        }

        // 图标↔文字间距回归锁（docs/android_audio_input_devices.md §5.2）：图标盒底 → 文字盒顶 ≤14dp
        // （12dp 死区 + 1dp 文字下移 = 13dp；上界留 1dp 余量防边界脆性；
        //  改动前为 12+4+4 = 20dp）
        val glyphBounds = composeTestRule
            .onNodeWithContentDescription(micOn(), useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        val textBounds = composeTestRule
            .onNodeWithText(micLabel(), useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        val gapDp = textBounds.top.value - glyphBounds.bottom.value
        // 420dpi（2.625x）下 1px ≈ 0.381dp，实际 14.095dp 属像素取整；上界放宽到 14.2dp。
        assertTrue("caption too far from icon: gap=${gapDp}dp (expect <= 14.2dp)", gapDp <= 14.2f)
    }

    // ── 扬声器设备选择器（docs/android_audio_output_devices.md §8.2；与麦克风列同型锁）────

    @Test
    fun outputDeviceSelector_chevron_hidden_when_single_device() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    showOutputDeviceSelector = false,
                    onOpenOutputDevicePicker = {},
                )
            }
        }

        composeTestRule
            .onAllNodesWithContentDescription(selectSpeaker())
            .assertCountEquals(0)
    }

    @Test
    fun outputDeviceSelector_chevron_shown_and_sits_before_caption() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    showOutputDeviceSelector = true,
                    onOpenOutputDevicePicker = {},
                )
            }
        }

        composeTestRule
            .onNodeWithContentDescription(selectSpeaker())
            .assertIsDisplayed()
        // ^ 在「扬声器」文字之前（unmerged 树取各自 bounds）
        val chevron = composeTestRule
            .onNodeWithContentDescription(selectSpeaker(), useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        val caption = composeTestRule
            .onNodeWithText(speakerLabel(), useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        assertTrue(
            "chevron must sit before speaker caption: right=${chevron.right} left=${caption.left}",
            chevron.right.value <= caption.left.value + 0.5f,
        )
    }

    @Test
    fun outputDeviceSelector_click_invokes_open_callback_not_mute_toggle() {
        var opened = false
        var speakerToggled = false
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = { speakerToggled = true },
                    onToggleCamera = {},
                    onPickGallery = {},
                    showOutputDeviceSelector = true,
                    onOpenOutputDevicePicker = { opened = true },
                )
            }
        }

        composeTestRule
            .onNodeWithContentDescription(selectSpeaker())
            .performClick()
        assertTrue(opened)
        assertFalse(speakerToggled)
    }

    @Test
    fun outputDeviceSelector_caption_touch_target_covers_padding() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = true,
                    isSpeakerOn = true,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                    showOutputDeviceSelector = true,
                    onOpenOutputDevicePicker = {},
                )
            }
        }

        // 触区高度回归锁（同麦克风列）：clickable 须含行底 padding（≈24dp）
        val bounds = composeTestRule
            .onNodeWithContentDescription(selectSpeaker())
            .getUnclippedBoundsInRoot()
        val height = bounds.bottom.value - bounds.top.value
        assertTrue("speaker caption touch height too small: ${height}dp (expect >= 20dp)", height >= 20f)
    }

    // ── 图库「+」（docs/camera_enhance.md §9）────────────────────────

    @Test
    fun galleryPlus_shown_before_caption_and_click_opens_gallery_not_camera() {
        var opened = false
        var cameraToggled = false
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = false,
                    isSpeakerOn = false,
                    isCameraOn = false,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = { cameraToggled = true },
                    onPickGallery = { opened = true },
                )
            }
        }

        composeTestRule.onNodeWithContentDescription(galleryPicker()).assertIsDisplayed()
        val plus = composeTestRule
            .onNodeWithContentDescription(galleryPicker(), useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        val caption = composeTestRule
            .onNodeWithText(cameraLabel(), useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        assertTrue(
            "plus must sit before camera caption: plus.right=${plus.right} caption.left=${caption.left}",
            plus.right.value <= caption.left.value + 0.5f,
        )

        composeTestRule.onNodeWithContentDescription(galleryPicker()).performClick()
        assertTrue(opened)
        assertFalse(cameraToggled)
    }

    @Test
    fun galleryPlus_caption_centered_under_camera_icon() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = false,
                    isSpeakerOn = false,
                    isCameraOn = false,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                )
            }
        }

        val iconBounds = composeTestRule
            .onNodeWithContentDescription(cameraOff())
            .getUnclippedBoundsInRoot()
        val textBounds = composeTestRule
            .onNodeWithText(cameraLabel(), useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        val iconCenterX = (iconBounds.left.value + iconBounds.right.value) / 2f
        val textCenterX = (textBounds.left.value + textBounds.right.value) / 2f
        assertTrue(
            "camera caption not centered under icon: icon=$iconCenterX text=$textCenterX",
            abs(iconCenterX - textCenterX) <= 0.5f,
        )
    }

    @Test
    fun galleryPlus_caption_touch_target_covers_padding() {
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = false,
                    isSpeakerOn = false,
                    isCameraOn = false,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = {},
                )
            }
        }

        val bounds = composeTestRule
            .onNodeWithContentDescription(galleryPicker())
            .getUnclippedBoundsInRoot()
        val height = bounds.bottom.value - bounds.top.value
        assertTrue("gallery caption touch height too small: ${height}dp (expect >= 20dp)", height >= 20f)
    }

    @Test
    fun galleryPlus_hidden_and_caption_not_clickable_when_camera_on() {
        var opened = false
        composeTestRule.setContent {
            LivaChatTheme {
                BottomToolbar(
                    isMicOn = false,
                    isSpeakerOn = false,
                    isCameraOn = true,
                    onToggleMic = {},
                    onToggleSpeaker = {},
                    onToggleCamera = {},
                    onPickGallery = { opened = true },
                )
            }
        }

        composeTestRule.onAllNodesWithContentDescription(galleryPicker()).assertCountEquals(0)
        val caption = composeTestRule.onNodeWithText(cameraLabel(), useUnmergedTree = true)
        assertFalse(
            "camera caption must not be clickable when camera is on",
            caption.fetchSemanticsNode().config.contains(SemanticsActions.OnClick),
        )
        assertFalse(opened)
    }
}
