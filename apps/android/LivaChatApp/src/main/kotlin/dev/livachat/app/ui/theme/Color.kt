package dev.livachat.app.ui.theme

import androidx.compose.ui.graphics.Color

// Brand colors (§3.2)
val BrandIndigo = Color(0xFF6366F1)
val BrandViolet = Color(0xFF8B5CF6)

// Semantic colors
val Success = Color(0xFF10B981)
val Warning = Color(0xFFF59E0B)
val Error = Color(0xFFEF4444)
val Info = Color(0xFF3B82F6)

// Generic colors
val Red = Color(0xFFEF4444)   // 通用红：录制指示、屏幕抓取失败环等状态强调；与 Error 同值但语义无关
val Green = Color(0xFF10B981) // 通用绿：屏幕抓取"已发送"环等状态强调；与 Success 同值但语义无关
val Gold = Color(0xFFFFD700)  // 通用金：Agent 任务弹窗边框等视觉强调；非语义状态色
// 屏幕抓取浮窗（docs/android_screen_recording.md §4.2）：高透明黑底 + 白波纹，
// 尽量减少对屏幕内容的遮挡；提示胶囊用更实的暗底保证白字可读
val TranslucentBlack = Color(0x59000000)
val PillBlack = Color(0xD9000000)

// Surface colors (Dark)
val Background = Color(0xFF0F172A)
val Surface = Color(0xFF1E293B)
val SurfaceVariant = Color(0xFF334155)

// Text colors
val TextPrimary = Color(0xFFF8FAFC)
val TextSecondary = Color(0xFF94A3B8)
val TextTertiary = Color(0xFF64748B)

// Avatar gradient
val AvatarGradientStart = Color(0xFF6366F1)
val AvatarGradientEnd = Color(0xFF8B5CF6)
