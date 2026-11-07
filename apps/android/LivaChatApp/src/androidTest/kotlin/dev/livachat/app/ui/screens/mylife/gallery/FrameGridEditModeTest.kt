package dev.livachat.app.ui.screens.mylife.gallery

import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.click
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.FrameDto
import dev.livachat.app.ui.screens.mylife.gallery.components.FrameGrid
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 编辑模式网格（docs/android_timeline_image_gallery.md §9.4.1 / §9.6）：
 * 选择框自消费 tap → onToggleSelect 且**不**触发 onFrameClick（开查看器）；
 * 图片其余区域仍 onFrameClick（需求④）；非编辑模式无选择框。
 */
@RunWith(AndroidJUnit4::class)
class FrameGridEditModeTest {

    @get:Rule
    val composeRule = createComposeRule()

    /** 选择框 contentDescription 取资源（设备语言 en/zh 均可），勿硬编码中文。 */
    private val selectPhotoDesc = InstrumentationRegistry.getInstrumentation()
        .targetContext.getString(R.string.mylife_select_photo)

    private fun frame(i: Int) = FrameDto(
        tsMs = 1_000L * i,
        mediaUrl = "http://127.0.0.1:1/f$i.jpg", // 不可达：图片加载失败不影响点击逻辑
        frameId = "f$i",
        eventRef = "s:video:f$i",
    )

    @Test
    fun checkboxTogglesAndDoesNotOpenViewer() {
        var toggles = 0
        var opens = 0
        composeRule.setContent {
            FrameGrid(
                frames = listOf(frame(1), frame(2)),
                loadingMore = false,
                gridState = rememberLazyGridState(),
                onFrameClick = { opens++ },
                onLoadMore = {},
                editMode = true,
                onToggleSelect = { toggles++ },
            )
        }

        composeRule.onAllNodesWithContentDescription(selectPhotoDesc)[0].performClick()
        composeRule.runOnIdle {
            assertEquals(1, toggles)
            assertEquals("选择框 tap 不得触发图片点击", 0, opens)
        }
    }

    @Test
    fun imageAreaClickOpensViewerWithoutToggling() {
        var toggles = 0
        var opens = 0
        composeRule.setContent {
            FrameGrid(
                frames = listOf(frame(1), frame(2)),
                loadingMore = false,
                gridState = rememberLazyGridState(),
                onFrameClick = { opens++ },
                onLoadMore = {},
                editMode = true,
                onToggleSelect = { toggles++ },
            )
        }

        val checkbox = composeRule.onAllNodesWithContentDescription(selectPhotoDesc)[0]
            .getUnclippedBoundsInRoot()
        composeRule.onRoot().performTouchInput {
            // 选择框命中区左上 20dp 处 = 同格图片区域（在选择框命中区之外）
            click(Offset((checkbox.left - 20.dp).toPx(), (checkbox.top - 20.dp).toPx()))
        }
        composeRule.runOnIdle {
            assertEquals(1, opens)
            assertEquals(0, toggles)
        }
    }

    @Test
    fun noCheckboxOutsideEditMode() {
        composeRule.setContent {
            FrameGrid(
                frames = listOf(frame(1)),
                loadingMore = false,
                gridState = rememberLazyGridState(),
                onFrameClick = {},
                onLoadMore = {},
                editMode = false,
            )
        }
        composeRule.onAllNodesWithContentDescription(selectPhotoDesc).assertCountEquals(0)
    }
}
