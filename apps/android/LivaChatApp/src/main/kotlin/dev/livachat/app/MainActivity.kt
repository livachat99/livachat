package dev.livachat.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.CompositionLocalProvider
import androidx.navigation.compose.rememberNavController
import dagger.hilt.android.AndroidEntryPoint
import dev.livachat.app.ui.navigation.LocalAllowSelfSigned
import dev.livachat.app.ui.navigation.LocalServerBaseUrl
import dev.livachat.app.ui.navigation.NavGraph
import dev.livachat.app.ui.theme.LivaChatTheme
import javax.inject.Inject
import javax.inject.Named

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject
    @Named("serverBaseUrl")
    lateinit var serverBaseUrl: String

    @Inject
    @Named("allowSelfSigned")
    @JvmField
    var allowSelfSigned: Boolean = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 应用固定暗色主题：强制系统栏使用浅色图标，避免系统处于浅色模式时图标不可见。
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )
        setContent {
            // 渲染期链接转换的服务端基址（docs/webroot_feature.md §3.4 D4'）：MarkdownContent 经
            // LocalServerBaseUrl 读取；默认 ""（未提供）时降级为不转换。
            // LocalAllowSelfSigned 供 WebView 的 SSL 错误处理判断是否放行已配置服务器的自签证书。
            CompositionLocalProvider(
                LocalServerBaseUrl provides serverBaseUrl,
                LocalAllowSelfSigned provides allowSelfSigned,
            ) {
                LivaChatTheme {
                    val navController = rememberNavController()
                    NavGraph(navController = navController)
                }
            }
        }
    }
}
