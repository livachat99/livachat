package dev.livachat.app

import android.content.Context
import io.mockk.every
import io.mockk.mockk
import java.util.Locale

/**
 * JVM 单测用 Context 桩（i18n 后，docs/android_multilingual.md §3.9）：
 * getString 回显 "res:<资源 id>"——断言侧以同一形式比对，验证「用了哪个资源」而非具体文案，
 * 避免把中文/英文写死进断言（locale 无关）。
 * resources.configuration.locales[0] 供 VM 读取 UI locale（mylife 日期标签），缺省 zh-CN。
 */
fun testStringContext(locale: Locale = Locale.forLanguageTag("zh-CN")): Context = mockk(relaxed = true) {
    every { getString(any()) } answers { "res:${firstArg<Int>()}" }
    every { getString(any(), *anyVararg()) } answers { "res:${firstArg<Int>()}" }
    every { resources.configuration.locales[0] } returns locale
    every { resources.getQuantityString(any(), any(), *anyVararg()) } answers { "resq:${firstArg<Int>()}" }
}
