package dev.livachat.app.ui.navigation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * 回归：导航入口不得把占位符按字面量送入导航（NavGraph.kt 曾用 Routes.MYLIFE
 * 直接导航，导致 Timeline 进入定位模式且 BFF 404）。
 * 2026-09-22（§14 R1/R3=C）：mylife 收敛为无参路由（tab/date 退役、Replay 路由删除），
 * 保留"无占位符/无 query/helper 与常量等价"三条断言防回归。
 * gallery 的 label 走 android.net.Uri.encode，仍需 Robolectric 影子实现。
 */
@RunWith(RobolectricTestRunner::class)
class RoutesTest {

    @Test
    fun `mylife 路由无占位符字面量`() {
        assertFalse("占位符不得以字面形式进入导航", Routes.mylife().contains("{"))
        assertEquals("mylife", Routes.mylife())
    }

    @Test
    fun `mylife 路由无 query 键集（tab 与 date 已退役）`() {
        assertFalse("无参路由不得携带 query", Routes.mylife().contains("?"))
    }

    @Test
    fun `mylife helper 与 MYLIFE 常量等价`() {
        assertEquals(Routes.MYLIFE, Routes.mylife())
    }

    @Test
    fun `mylifeGallery helper 编码 label 且 focus 缺省为空串`() {
        val route = Routes.mylifeGallery(
            "2026-08-21", "2026-08-21", "8月21日 · 周五", focusFrameId = null, pending = true,
        )
        assertTrue(route.startsWith("mylife/gallery?from=2026-08-21&to=2026-08-21&label="))
        assertFalse("label 必须已编码（无原始空格/中文）", route.contains(" ") || route.contains("周五"))
        assertTrue(route.endsWith("&focus=&pending=true"))
    }

    @Test
    fun `mylifeGallery helper 传 focus 帧`() {
        val route = Routes.mylifeGallery("2026-08-21", "2026-08-21", "l", focusFrameId = "f-1")
        assertTrue(route.endsWith("&focus=f-1&pending=false"))
    }
}
