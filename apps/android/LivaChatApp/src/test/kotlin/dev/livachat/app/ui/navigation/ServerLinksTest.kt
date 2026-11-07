package dev.livachat.app.ui.navigation

import org.junit.Assert.assertEquals
import org.junit.Test

/** docs/webroot_feature.md §3.4/§6：渲染期转换与服务器基址推导（纯 JVM，不依赖 Android）。 */
class ServerLinksTest {

    private val uuid = "01a0f54b-4fe4-7c4f-80a2-88e711556d69"
    private val uuid2 = "01a0f55c-1f2b-7e8a-8000-112233445566"
    private val base = "https://192.168.1.10:8080"

    // -- serverBaseUrl 推导（IP→域名映射暂注释停用，恢复见 main/ServerLinks.kt；不波及 checkService）

    // 映射停用期间跳过（恢复 PUBLIC_DOMAIN_BY_HOST 后取消注释）：
    // @Test
    // fun `wss explicit port to https base`() {
    //     assertEquals(
    //         "https://example.com:8080",
    //         serverBaseUrl("wss://203.0.113.10:8080/v1/ws/stream"),
    //     )
    // }

    @Test
    fun `lan server base`() {
        assertEquals(
            "https://192.168.1.10:8080",
            serverBaseUrl("wss://192.168.1.10:8080/v1/ws/stream"),
        )
    }

    @Test
    fun `wss defaults to 443`() {
        assertEquals("https://example.com:443", serverBaseUrl("wss://example.com/ws"))
    }

    @Test
    fun `ws to http with default 80`() {
        assertEquals("http://example.com:80", serverBaseUrl("ws://example.com/ws"))
    }

    @Test
    fun `malformed url to empty`() {
        assertEquals("", serverBaseUrl("not a url"))
    }

    // -- 组合：映射后的渲染基址进入 livachat_url 转换（映射停用期间跳过）

    // @Test
    // fun `mapped base feeds conversion`() {
    //     assertEquals(
    //         "https://example.com:8080/webroot/$uuid/",
    //         convertLivachatUrls(
    //             "livachat_url://webroot/$uuid/",
    //             serverBaseUrl("wss://203.0.113.10:8080/v1/ws/stream"),
    //         ),
    //     )
    // }

    // -- convertLivachatUrls：合法替换

    @Test
    fun `converts markdown link context`() {
        assertEquals(
            "[页面]($base/webroot/$uuid/)",
            convertLivachatUrls("[页面](livachat_url://webroot/$uuid/)", base),
        )
    }

    @Test
    fun `converts without trailing slash`() {
        assertEquals(
            "$base/webroot/$uuid",
            convertLivachatUrls("livachat_url://webroot/$uuid", base),
        )
    }

    @Test
    fun `converts deep static path`() {
        assertEquals(
            "$base/webroot/$uuid/static/app.js",
            convertLivachatUrls("livachat_url://webroot/$uuid/static/app.js", base),
        )
    }

    @Test
    fun `converts multiple occurrences`() {
        assertEquals(
            "$base/webroot/$uuid/ 与 $base/webroot/$uuid2/",
            convertLivachatUrls("livachat_url://webroot/$uuid/ 与 livachat_url://webroot/$uuid2/", base),
        )
    }

    @Test
    fun `scheme match is case-insensitive`() {
        assertEquals(
            "$base/webroot/$uuid/",
            convertLivachatUrls("Livachat_Url://WebRoot/$uuid/", base),
        )
    }

    @Test
    fun `uppercase uuid passes and rest preserved`() {
        assertEquals(
            "$base/webroot/${uuid.uppercase()}/",
            convertLivachatUrls("livachat_url://webroot/${uuid.uppercase()}/", base),
        )
    }

    @Test
    fun `query passes through`() {
        assertEquals(
            "$base/webroot/$uuid/?a=1",
            convertLivachatUrls("livachat_url://webroot/$uuid/?a=1", base),
        )
    }

    // -- convertLivachatUrls：token 截断界（剥离字符留原位）

    @Test
    fun `trailing chinese period stays outside`() {
        assertEquals(
            "见 $base/webroot/$uuid/。",
            convertLivachatUrls("见 livachat_url://webroot/$uuid/。", base),
        )
    }

    @Test
    fun `trailing ascii comma stripped and stays outside`() {
        assertEquals(
            "$base/webroot/$uuid/,",
            convertLivachatUrls("livachat_url://webroot/$uuid/,", base),
        )
    }

    @Test
    fun `double quote is a boundary`() {
        assertEquals(
            "\"$base/webroot/$uuid/\"",
            convertLivachatUrls("\"livachat_url://webroot/$uuid/\"", base),
        )
    }

    @Test
    fun `angle bracket autolink is a boundary`() {
        assertEquals(
            "<$base/webroot/$uuid/>",
            convertLivachatUrls("<livachat_url://webroot/$uuid/>", base),
        )
    }

    // -- convertLivachatUrls：非法片段原样保留

    @Test
    fun `non uuid first segment kept as-is`() {
        val text = "livachat_url://webroot/not-a-uuid/x"
        assertEquals(text, convertLivachatUrls(text, base))
    }

    @Test
    fun `uuid v4 kept as-is`() {
        val text = "livachat_url://webroot/01a0f54b-4fe4-4c4f-80a2-88e711556d69/"
        assertEquals(text, convertLivachatUrls(text, base))
    }

    @Test
    fun `dotdot kept as-is`() {
        val text = "livachat_url://webroot/$uuid/../x"
        assertEquals(text, convertLivachatUrls(text, base))
    }

    @Test
    fun `empty rest kept as-is`() {
        val text = "livachat_url://webroot/"
        assertEquals(text, convertLivachatUrls(text, base))
    }

    @Test
    fun `non webroot host untouched`() {
        val text = "livachat_url://audio/$uuid/"
        assertEquals(text, convertLivachatUrls(text, base))
    }

    @Test
    fun `invalid occurrence kept while following valid one converts`() {
        assertEquals(
            "livachat_url://webroot/bad/x 与 $base/webroot/$uuid/",
            convertLivachatUrls("livachat_url://webroot/bad/x 与 livachat_url://webroot/$uuid/", base),
        )
    }

    @Test
    fun `invalid occurrence with trailing context preserved exactly`() {
        val text = "x livachat_url://webroot/bad/y, z"
        assertEquals(text, convertLivachatUrls(text, base))
    }

    @Test
    fun `missing slash after webroot untouched`() {
        val text = "livachat_url://webroot"
        assertEquals(text, convertLivachatUrls(text, base))
    }

    @Test
    fun `text without occurrence unchanged`() {
        val text = "普通文本 https://example.com/x"
        assertEquals(text, convertLivachatUrls(text, base))
    }

    // -- convertLivachatUrls：降级与幂等

    @Test
    fun `empty base returns input unchanged`() {
        val text = "livachat_url://webroot/$uuid/"
        assertEquals(text, convertLivachatUrls(text, ""))
    }

    @Test
    fun `conversion is idempotent`() {
        val once = convertLivachatUrls("[t](livachat_url://webroot/$uuid/)", base)
        assertEquals(once, convertLivachatUrls(once, base))
    }
}
