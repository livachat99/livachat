package dev.livachat.app.data

import dev.livachat.app.data.local.resolveServerAddress
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 地址读取口径（docs/setting_server_address.md §3.1，2026-10-13 二改）：
 * 合法用原文；缺失/非法一律空串（未配置态）——不做旧 `server_id` 映射、不做默认回退。
 */
class ResolveServerAddressTest {

    @Test
    fun `valid stored address is used as-is`() {
        assertEquals("example.com:9000", resolveServerAddress("example.com:9000"))
        assertEquals("203.0.113.10:8080", resolveServerAddress("203.0.113.10:8080"))
    }

    @Test
    fun `missing or invalid address resolves to empty (unconfigured)`() {
        assertEquals("", resolveServerAddress(null))
        assertEquals("", resolveServerAddress(""))
        assertEquals("", resolveServerAddress("bad address"))
        assertEquals("", resolveServerAddress("wss://a:1"))
        // 旧 server_id 值（"lan"）不是合法地址：不做旧键映射后等同未配置
        assertEquals("", resolveServerAddress("lan"))
    }
}
