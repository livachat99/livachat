package dev.livachat.app.ui.screens.conversation

import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithText
import dev.livachat.app.media.AudioOutputDevice
import dev.livachat.app.ui.theme.LivaChatTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * docs/android_audio_output_devices.md §8.2（D2=B：镜像复制输入侧 [MicDevicePickerTest]）：
 * 输出设备选择弹窗——长名不遮挡右侧选中圆点、只显示产品段、行距收紧（行高 48dp 相邻）。
 */
class OutputDevicePickerTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    private val speaker = AudioOutputDevice(id = 3, type = 2, productName = "LIO-AN00", address = null)
    private val earpiece = AudioOutputDevice(id = 2, type = 1, productName = "LIO-AN00", address = null)
    private val longUsb = AudioOutputDevice(
        id = 7,
        type = 11,   // TYPE_USB_DEVICE
        productName = "USB-Audio - HUAWEI USB-C HEADSET PRO MAX ULTRA",
        address = null,
    )

    private fun setContent() {
        composeTestRule.setContent {
            LivaChatTheme {
                OutputDevicePicker(
                    devices = listOf(speaker, earpiece, longUsb),
                    selectedDeviceId = null,
                    onSelect = {},
                    onDismiss = {},
                )
            }
        }
    }

    @Test
    fun long_device_name_neither_hides_nor_pushes_out_selection_dots() {
        setContent()

        // 四行（系统默认 + 3 台设备）各一个选中圆点；tag 在 clickable Row 合并树中不可见 → unmerged
        val radios = composeTestRule.onAllNodesWithTag(OUTPUT_DEVICE_RADIO_TAG, useUnmergedTree = true)
        radios.assertCountEquals(4)
        radios[3].assertIsDisplayed()   // 长名行的圆点仍在屏内（未被推走/遮住）

        val textBounds = composeTestRule
            .onNodeWithText("HUAWEI USB-C HEADSET PRO MAX ULTRA", useUnmergedTree = true)
            .getUnclippedBoundsInRoot()
        val radioBounds = radios[3].getUnclippedBoundsInRoot()
        assertTrue(
            "long name overlaps radio: textRight=${textBounds.right} radioLeft=${radioBounds.left}",
            textBounds.right.value <= radioBounds.left.value + 0.5f,
        )
    }

    @Test
    fun device_rows_are_adjacent_48dp_items() {
        setContent()

        // 行距收紧（同输入侧）：相邻行文本顶间距 ≈ 行高 48dp。
        // 用行的选中圆点量间距，避免标签文案依赖 Build.MODEL（productName ≠ 机型时显示产品名）；
        // 且扬声器/听筒 productName 相同时跨 type 会追加类型后缀。
        val radios = composeTestRule.onAllNodesWithTag(OUTPUT_DEVICE_RADIO_TAG, useUnmergedTree = true)
        val firstTop = radios[0].getUnclippedBoundsInRoot().top.value
        val secondTop = radios[1].getUnclippedBoundsInRoot().top.value
        val pitch = secondTop - firstTop
        assertTrue("device row pitch too large: ${pitch}dp (expect <= 50dp)", pitch <= 50f)
    }
}
