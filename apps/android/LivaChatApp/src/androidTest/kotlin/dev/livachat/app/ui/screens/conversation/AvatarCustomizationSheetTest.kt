package dev.livachat.app.ui.screens.conversation

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.R
import dev.livachat.app.ui.theme.LivaChatTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * docs/fd_avator_customization.md：头像面板——item 行距 48dp 相邻（§8）、标题右侧
 * 「显示｜隐藏」胶囊（「头像隐藏到标题栏」接线：点击发射 onAvatarVisibleChange，关面板由 VM 负责）。
 * 断言模式镜像 [MicDevicePickerTest]/[OutputDevicePickerTest] 的行距用例。
 */
class AvatarCustomizationSheetTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    private var dismissed = false
    private var pickGallery = false
    private var takePhoto = false
    private var removeAvatar = false
    private var toggled = false
    private val visibilityChanges = mutableListOf<Boolean>()

    private fun setContent(avatarVisible: Boolean = true) {
        composeTestRule.setContent {
            LivaChatTheme {
                AvatarCustomizationSheet(
                    hasCustomAvatar = true,
                    onPickFromGallery = { pickGallery = true },
                    onTakePhoto = { takePhoto = true },
                    onRemoveCustomAvatar = { removeAvatar = true },
                    onDismiss = { dismissed = true },
                    onToggleConversationScene = { toggled = true },
                    avatarVisible = avatarVisible,
                    onAvatarVisibleChange = { visibilityChanges.add(it) },
                )
            }
        }
    }

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun str(id: Int) = context.getString(id)

    @Test
    fun avatar_option_rows_are_adjacent_48dp_items() {
        setContent()

        // 行距收紧（§8.1）：相邻行文本顶间距 ≈ 行高 48dp（改前 48+8+8=64dp）
        val galleryTop = composeTestRule
            .onNodeWithText(str(R.string.conv_avatar_from_gallery), useUnmergedTree = true)
            .getUnclippedBoundsInRoot().top.value
        val cameraTop = composeTestRule
            .onNodeWithText(str(R.string.conv_take_photo), useUnmergedTree = true)
            .getUnclippedBoundsInRoot().top.value
        val resetTop = composeTestRule
            .onNodeWithText(str(R.string.conv_avatar_reset), useUnmergedTree = true)
            .getUnclippedBoundsInRoot().top.value

        assertTrue(
            "row pitch too large: ${cameraTop - galleryTop}dp (expect <= 50dp)",
            cameraTop - galleryTop <= 50f,
        )
        assertTrue(
            "row pitch too large: ${resetTop - cameraTop}dp (expect <= 50dp)",
            resetTop - cameraTop <= 50f,
        )
    }

    @Test
    fun show_hide_pill_sits_right_of_header_and_emits_toggle() {
        setContent()

        val headerRight = composeTestRule
            .onNodeWithText(str(R.string.conv_avatar_picker_title), useUnmergedTree = true)
            .getUnclippedBoundsInRoot().right.value
        val normalLeft = composeTestRule
            .onNodeWithText(str(R.string.conv_avatar_scale_normal), useUnmergedTree = true)
            .getUnclippedBoundsInRoot().left.value
        composeTestRule.onNodeWithText(str(R.string.conv_avatar_scale_shrink), useUnmergedTree = true).assertIsDisplayed()

        assertTrue(
            "pill not on the right of header: normalLeft=${normalLeft}dp headerRight=${headerRight}dp",
            normalLeft > headerRight,
        )

        // 两段分别发射 true/false；切换不关面板（onDismiss 不应被触发，面板保持）
        composeTestRule.onNodeWithText(str(R.string.conv_avatar_scale_shrink), useUnmergedTree = true).performClick()
        composeTestRule.onNodeWithText(str(R.string.conv_avatar_scale_normal), useUnmergedTree = true).performClick()

        assertEquals(listOf(false, true), visibilityChanges)
        assertFalse(
            "pill click fired an unrelated callback",
            dismissed || pickGallery || takePhoto || removeAvatar || toggled,
        )
        composeTestRule.onNodeWithText(str(R.string.conv_avatar_from_gallery)).assertIsDisplayed()
    }

    @Test
    fun avatar_items_present_when_avatar_shrunk() {
        setContent(avatarVisible = false)

        // 两态均展示子项；pill 标签为「正常/缩小」
        composeTestRule.onNodeWithText(str(R.string.conv_avatar_scale_normal), useUnmergedTree = true).assertIsDisplayed()
        composeTestRule.onNodeWithText(str(R.string.conv_avatar_scale_shrink), useUnmergedTree = true).assertIsDisplayed()
        composeTestRule.onNodeWithText(str(R.string.conv_avatar_from_gallery)).assertIsDisplayed()
        composeTestRule.onNodeWithText(str(R.string.conv_take_photo)).assertIsDisplayed()
        composeTestRule.onNodeWithText(str(R.string.conv_avatar_reset)).assertIsDisplayed()
    }

    @Test
    fun translation_section_still_renders() {
        setContent()

        // SectionHeader 改造回归：同声翻译区不受影响
        composeTestRule.onNodeWithText(str(R.string.conv_welcome_chip_translate)).assertIsDisplayed()
        composeTestRule.onNodeWithText(str(R.string.conv_mode_dialogue)).assertIsDisplayed()
        composeTestRule.onNodeWithText(str(R.string.conv_mode_lecture)).assertIsDisplayed()
        composeTestRule.onNodeWithText(str(R.string.conv_mode_transcribe)).assertIsDisplayed()
        composeTestRule.onNodeWithText(str(R.string.conv_omni)).assertIsDisplayed()
    }
}
