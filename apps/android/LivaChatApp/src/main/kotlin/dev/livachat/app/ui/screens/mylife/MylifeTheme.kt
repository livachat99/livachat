package dev.livachat.app.ui.screens.mylife

import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import coil.compose.LocalImageLoader
import dev.livachat.app.di.MylifeModule
import dev.livachat.app.ui.theme.LivaChatTheme

/**
 * Web mylife theme.css 色板逐值移植（设计文档 §6.2）。
 * 与 App 全局 BrandIndigo 体系并存：MyLife 是独立"人生世界"子空间。
 *
 * ⚠️ Android 无消费方（仅 Web 对齐参考，**勿再引用**）：`Void`/`Pink`/`Blue`/`Purple`/`Cyan`/`Green`
 * ——星图与回放整栈删除后闲置（docs/android_timeline_hotslot.md §14.3 / §11 P2）；如需彻底清理另开批次。
 */
object MylifeColors {
    val Abyss = Color(0xFF0A0E1A)
    val Void = Color(0xFF0D1220)
    val Panel = Color(0xFF141A2E)
    val Line = Color(0xFF232C47)
    val Star = Color(0xFFE8ECFF)
    val Dust = Color(0xFF8B93B8)
    val Gold = Color(0xFFF5C86E)
    val Pink = Color(0xFFF28BB0)
    val Blue = Color(0xFF7DB4F5)
    val Purple = Color(0xFFB58CF5)
    val Cyan = Color(0xFF6FD6D0)
    val Red = Color(0xFFF56E6E)
    val Green = Color(0xFF6EF5A8)
}

/**
 * MyLife 子空间：内嵌全局 LivaChatTheme（Spacing/Typography），
 * 并注入 D4 共享 OkHttpClient 的 Coil ImageLoader——自签 TLS 下 AsyncImage
 * 必须走同一 trust-all client，否则帧图握手失败（设计文档 §5.4/D4）。
 */
@Composable
fun MylifeTheme(content: @Composable () -> Unit) {
    val context = LocalContext.current
    val imageLoader = remember { MylifeModule.mylifeImageLoader(context) }
    CompositionLocalProvider(LocalImageLoader provides imageLoader) {
        LivaChatTheme(content = content)
    }
}
