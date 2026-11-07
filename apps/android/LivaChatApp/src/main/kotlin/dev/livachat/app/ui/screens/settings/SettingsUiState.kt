package dev.livachat.app.ui.screens.settings

import dev.livachat.app.domain.model.isServerConfigComplete

sealed class ServiceStatus {
    data object Idle : ServiceStatus()
    data object Checking : ServiceStatus()
    data object Available : ServiceStatus()
    data object AuthFailed : ServiceStatus()
    data object Unavailable : ServiceStatus()
}

data class SettingsUiState(
    val defaultFrontCamera: Boolean = false,
    val autoCameraOnJoin: Boolean = false,
    val hapticEnabled: Boolean = true,
    val avdetectorEnabled: Boolean = true,
    val tlsAllowSelfSigned: Boolean = true,
    val savedTlsAllowSelfSigned: Boolean = true,
    val appVersion: String = "",
    /** 输入框草稿（未保存即未生效）；未配置（升级/首装）为空串，待用户输入。 */
    val serverAddressInput: String = "",
    val authTokenInput: String = "",
    /** 已持久化基线（重启后实际生效值；dirty 判定基准）。 */
    val savedAddress: String = "",
    val savedToken: String = "",
    val showRestartDialog: Boolean = false,
    /** inline 校验文案（地址/密钥非法时显示）。 */
    val validationMessage: String? = null,
    val serviceStatus: ServiceStatus = ServiceStatus.Idle,
) {
    /** 检测可用性 / 保存校验门控：地址与密钥均齐备（空串非法）。 */
    val canProbe: Boolean get() = isServerConfigComplete(serverAddressInput, authTokenInput)

    /** 草稿与已存基线有无差异（「保存」按钮出现条件）。 */
    val dirty: Boolean
        get() = serverAddressInput != savedAddress ||
            authTokenInput != savedToken ||
            tlsAllowSelfSigned != savedTlsAllowSelfSigned
}
