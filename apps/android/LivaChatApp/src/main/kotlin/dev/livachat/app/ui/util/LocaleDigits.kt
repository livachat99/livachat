package dev.livachat.app.ui.util

import java.util.Locale

/**
 * 数字本地化助手（docs/android_multilingual.md §20.2 D4/§20.1 缺口⑤）：ar → ASCII 0-9 映射为
 * 阿拉伯-印度数字（U+0660-0669，与资源侧 `%d` 天然阿数对齐，防 JDK 格式化侧混排）；其余语言恒等。
 * 幂等：已含阿数的文本无 ASCII 数字可映射，再经本函数不变。
 */
internal fun localizeDigits(text: String, locale: Locale): String {
    if (locale.language != "ar") return text
    return buildString(text.length) {
        for (ch in text) {
            append(if (ch in '0'..'9') '\u0660' + (ch - '0') else ch)
        }
    }
}
