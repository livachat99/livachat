package dev.livachat.app.ui.navigation

import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.navArgument
import dev.livachat.app.ui.screens.conversation.ConversationScreen
import dev.livachat.app.ui.screens.history.HistoryDetailScreen
import dev.livachat.app.ui.screens.history.HistoryScreen
import dev.livachat.app.ui.screens.mylife.MylifeHomeScreen
import dev.livachat.app.ui.screens.mylife.audiogallery.AudioGalleryScreen
import dev.livachat.app.ui.screens.mylife.gallery.FrameGalleryScreen
import dev.livachat.app.ui.screens.mylife.hotslot.EventListViewerScreen
import dev.livachat.app.ui.screens.mylife.hotslot.HotSlotScreen
import dev.livachat.app.ui.screens.permission.PermissionScreen
import dev.livachat.app.ui.screens.settings.SettingsScreen
import dev.livachat.app.ui.screens.splash.SplashScreen
import dev.livachat.app.ui.screens.webview.WebViewScreen

@Composable
fun NavGraph(
    navController: NavHostController,
) {
    NavHost(
        navController = navController,
        startDestination = Routes.SPLASH,
    ) {
        composable(Routes.SPLASH) {
            SplashScreen(
                onNavigateToPermission = {
                    navController.navigate(Routes.PERMISSION) {
                        popUpTo(Routes.SPLASH) { inclusive = true }
                    }
                },
                onNavigateToConversation = {
                    navController.navigate(Routes.CONVERSATION) {
                        popUpTo(Routes.SPLASH) { inclusive = true }
                    }
                },
            )
        }

        composable(Routes.PERMISSION) {
            PermissionScreen(
                onComplete = {
                    navController.navigate(Routes.CONVERSATION) {
                        popUpTo(Routes.PERMISSION) { inclusive = true }
                    }
                },
            )
        }

        composable(Routes.CONVERSATION) {
            ConversationScreen(
                onNavigateToHistory = {
                    navController.navigate(Routes.HISTORY)
                },
                onNavigateToSettings = {
                    navController.navigate(Routes.SETTINGS)
                },
                onOpenLink = { url -> navController.navigate(Routes.webview(url)) },
                onNavigateToTimeMachine = { navController.navigate(Routes.mylife()) },
            )
        }

        composable(Routes.HISTORY) {
            HistoryScreen(
                onNavigateBack = { navController.popBackStack() },
                onNavigateToDetail = { sessionId ->
                    navController.navigate(Routes.historyDetail(sessionId))
                },
            )
        }

        composable(
            route = Routes.HISTORY_DETAIL,
            arguments = listOf(navArgument("sessionId") { type = NavType.StringType }),
        ) { backStackEntry ->
            val sessionId = backStackEntry.arguments?.getString("sessionId") ?: ""
            HistoryDetailScreen(
                sessionId = sessionId,
                onNavigateBack = { navController.popBackStack() },
                onOpenLink = { url -> navController.navigate(Routes.webview(url)) },
            )
        }

        composable(Routes.SETTINGS) {
            SettingsScreen(
                onNavigateBack = { navController.popBackStack() },
            )
        }

        composable(
            route = Routes.WEBVIEW,
            arguments = listOf(navArgument("url") { type = NavType.StringType }),
        ) { backStackEntry ->
            val url = backStackEntry.arguments?.getString("url") ?: ""
            WebViewScreen(
                url = url,
                onNavigateBack = { navController.popBackStack() },
            )
        }

        // MyLife 宿主（单视图，无参；2026-09-22 §14 R1：tab/date 深链随本批删除，设计文档 §6.3）
        composable(route = Routes.MYLIFE) {
            MylifeHomeScreen(
                onNavigateToGallery = { from, to, label, focusFrameId, pending ->
                    navController.navigate(
                        Routes.mylifeGallery(from, to, label, focusFrameId, pending),
                    )
                },
                onNavigateToHotSlot = { startMs, endMs, pending ->
                    navController.navigate(Routes.mylifeHotSlot(startMs, endMs, pending))
                },
                onNavigateToEventList = { tsMs, pending ->
                    navController.navigate(Routes.mylifeEventList(tsMs, pending))
                },
                onNavigateToAudio = { date, label, count, pending ->
                    navController.navigate(Routes.mylifeAudio(date, label, count, pending))
                },
                onNavigateBack = { navController.popBackStack() },
            )
        }

        // Gallery 帧网格（独立全屏路由，push 在宿主之上；范围 + focus 帧 + pending）
        composable(
            route = Routes.MYLIFE_GALLERY,
            arguments = listOf(
                navArgument("from") { type = NavType.StringType; nullable = true; defaultValue = null },
                navArgument("to") { type = NavType.StringType; nullable = true; defaultValue = null },
                navArgument("label") { type = NavType.StringType; nullable = true; defaultValue = null },
                navArgument("focus") { type = NavType.StringType; nullable = true; defaultValue = null },
                navArgument("pending") { type = NavType.StringType; nullable = true; defaultValue = null },
            ),
        ) { backStackEntry ->
            val args = backStackEntry.arguments
            FrameGalleryScreen(
                from = args?.getString("from").orEmpty(),
                to = args?.getString("to").orEmpty(),
                label = args?.getString("label").orEmpty(),
                // builder 恒带参：focus 允许空串（E7），pending 宽松解析
                focusFrameId = args?.getString("focus")?.takeIf { it.isNotEmpty() },
                pending = args?.getString("pending")?.toBooleanStrictOrNull() ?: false,
                onNavigateBack = { navController.popBackStack() },
            )
        }

        // hotSlotView（独立全屏路由，push 在宿主之上；取数窗口 + pending 顶栏，§4.3）
        composable(
            route = Routes.MYLIFE_HOT_SLOT,
            arguments = listOf(
                navArgument("start") { type = NavType.LongType },
                navArgument("end") { type = NavType.LongType },
                navArgument("pending") { type = NavType.StringType; nullable = true; defaultValue = null },
            ),
        ) { backStackEntry ->
            val args = backStackEntry.arguments
            HotSlotScreen(
                startMs = args?.getLong("start") ?: 0L,
                endMs = args?.getLong("end") ?: 0L,
                pending = args?.getString("pending")?.toBooleanStrictOrNull() ?: false,
                onNavigateBack = { navController.popBackStack() },
                // agent_job 正文 Markdown 链接（docs/android_timeline_hotslot.md §12）：对齐会话页写法
                onOpenLink = { url -> navController.navigate(Routes.webview(url)) },
            )
        }

        // AudioGallery（独立全屏路由，push 在宿主之上；日卡日期 + label + 段数快照 + pending 顶栏，
        // docs/android_timeline_audio_gallery.md §4.1）
        composable(
            route = Routes.MYLIFE_AUDIO,
            arguments = listOf(
                navArgument("date") { type = NavType.StringType; nullable = true; defaultValue = null },
                navArgument("label") { type = NavType.StringType; nullable = true; defaultValue = null },
                navArgument("count") { type = NavType.StringType; nullable = true; defaultValue = null },
                navArgument("pending") { type = NavType.StringType; nullable = true; defaultValue = null },
            ),
        ) { backStackEntry ->
            val args = backStackEntry.arguments
            AudioGalleryScreen(
                date = args?.getString("date").orEmpty(),
                label = args?.getString("label").orEmpty(),
                count = args?.getString("count")?.toIntOrNull() ?: 0,
                pending = args?.getString("pending")?.toBooleanStrictOrNull() ?: false,
                // 时间头 → eventListViewer（复用既有路由，docs/android_timeline_audio_gallery.md §9）
                onNavigateToEventList = { tsMs, pending ->
                    navController.navigate(Routes.mylifeEventList(tsMs, pending))
                },
                onNavigateBack = { navController.popBackStack() },
            )
        }

        // eventListViewer（独立全屏路由，push 在宿主之上；被点事件时刻 + pending 顶栏，docs/android_timeline_topics.md §3.3.2）
        composable(
            route = Routes.MYLIFE_EVENT_LIST,
            arguments = listOf(
                navArgument("ts") { type = NavType.LongType },
                navArgument("pending") { type = NavType.StringType; nullable = true; defaultValue = null },
            ),
        ) { backStackEntry ->
            val args = backStackEntry.arguments
            EventListViewerScreen(
                tsMs = args?.getLong("ts") ?: 0L,
                pending = args?.getString("pending")?.toBooleanStrictOrNull() ?: false,
                onNavigateBack = { navController.popBackStack() },
                onOpenLink = { url -> navController.navigate(Routes.webview(url)) },
            )
        }

    }
}
