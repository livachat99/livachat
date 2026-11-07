package dev.livachat.app.ui.screens.settings

import android.content.Context
import androidx.core.content.pm.PackageInfoCompat
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.R
import dev.livachat.app.data.remote.ProbeResult
import dev.livachat.app.data.remote.ServiceProbe
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.isValidAuthToken
import dev.livachat.app.domain.model.parseServerAddress
import dev.livachat.app.domain.usecase.SettingsUseCase
import javax.inject.Inject
import javax.inject.Named
import kotlin.system.exitProcess
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val settingsUseCase: SettingsUseCase,
    private val serviceProbe: ServiceProbe,
    private val conversationCoordinator: ConversationCoordinator,
    @ApplicationContext private val context: Context,
    @Named("deviceId") private val deviceId: String,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SettingsUiState(
        appVersion = appVersionLabel(),
    ))
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    private var serverConfigInitialized = false
    private var tlsInitialized = false
    private var tlsTouched = false

    init {
        viewModelScope.launch {
            settingsUseCase.observePreferences().collect { prefs ->
                val firstTls = !tlsInitialized
                tlsInitialized = true
                _uiState.update {
                    it.copy(
                        defaultFrontCamera = prefs.defaultFrontCamera,
                        autoCameraOnJoin = prefs.autoCameraOnJoin,
                        hapticEnabled = prefs.hapticEnabled,
                        avdetectorEnabled = prefs.avdetectorEnabled,
                        // 首发射时若用户已手动切换，保留用户草稿，避免被持久化旧值覆盖
                        tlsAllowSelfSigned = if (firstTls && !tlsTouched) {
                            prefs.tlsAllowSelfSigned
                        } else {
                            it.tlsAllowSelfSigned
                        },
                        savedTlsAllowSelfSigned = prefs.tlsAllowSelfSigned,
                    )
                }
            }
        }
        // 已持久化的服务器配置：首次发射填草稿 + 基线；此后只更新基线（会话内写入仅随保存发生）
        viewModelScope.launch {
            settingsUseCase.observeServerConfig().collect { config ->
                _uiState.update { cur ->
                    if (serverConfigInitialized) {
                        cur.copy(savedAddress = config.address, savedToken = config.token)
                    } else {
                        serverConfigInitialized = true
                        cur.copy(
                            serverAddressInput = config.address,
                            authTokenInput = config.token,
                            savedAddress = config.address,
                            savedToken = config.token,
                        )
                    }
                }
            }
        }
    }

    fun setDefaultFrontCamera(front: Boolean) {
        viewModelScope.launch { settingsUseCase.setDefaultFrontCamera(front) }
    }

    fun setAutoCameraOnJoin(enabled: Boolean) {
        viewModelScope.launch { settingsUseCase.setAutoCameraOnJoin(enabled) }
    }

    fun setHapticEnabled(enabled: Boolean) {
        viewModelScope.launch { settingsUseCase.setHapticEnabled(enabled) }
    }

    /**
     * 保存“允许 AI 主动搭话”并立即通知当前会话。
     * 落盘 + SDK 调用用 NonCancellable 包住：用户切完立即返回时不能半途取消。
     */
    fun setAvdetectorEnabled(enabled: Boolean) {
        viewModelScope.launch {
            withContext(NonCancellable) {
                settingsUseCase.setAvdetectorEnabled(enabled)
                conversationCoordinator.setAvdetectorEnabled(enabled)
            }
        }
    }

    /** TLS 草稿：只改 UI，点「保存」才落盘（与 iOS tlsAllowSelfSigned 的 apply 语义一致）。 */
    fun setTlsAllowSelfSigned(enabled: Boolean) {
        tlsTouched = true
        _uiState.update {
            it.copy(tlsAllowSelfSigned = enabled, serviceStatus = ServiceStatus.Idle)
        }
    }

    /** 地址草稿编辑：编辑后旧检测结果不再对应当前输入，状态回落 Idle。 */
    fun onServerAddressChange(value: String) {
        _uiState.update {
            it.copy(
                serverAddressInput = value,
                serviceStatus = ServiceStatus.Idle,
                validationMessage = validateDraft(value, it.authTokenInput),
            )
        }
    }

    fun onAuthTokenChange(value: String) {
        _uiState.update {
            it.copy(
                authTokenInput = value,
                serviceStatus = ServiceStatus.Idle,
                validationMessage = validateDraft(it.serverAddressInput, value),
            )
        }
    }

    private fun validateDraft(address: String, token: String): String? {
        val addressInvalid = address.isNotEmpty() && parseServerAddress(address) == null
        val tokenInvalid = token.isNotEmpty() && !isValidAuthToken(token)
        return if (addressInvalid || tokenInvalid) {
            context.getString(R.string.settings_save_failed_message)
        } else {
            null
        }
    }

    /** 保存：无改动早退；合格后直接落盘并进入重启提示（不再弹中间确认框）。 */
    fun saveServerConfig() {
        if (!_uiState.value.dirty) return
        persistServerConfig()
    }

    private fun persistServerConfig() {
        val cur = _uiState.value
        if (!cur.canProbe) {
            // UI 入口在 canProbe=false 时已置灰；这里兜底显示 inline 文案
            _uiState.update {
                it.copy(validationMessage = context.getString(R.string.settings_save_failed_message))
            }
            return
        }
        val address = cur.serverAddressInput
        val token = cur.authTokenInput
        val allowSelfSigned = cur.tlsAllowSelfSigned
        viewModelScope.launch {
            // 用户点保存后若立刻返回，onCleared 取消 scope 也要保证落盘完成
            withContext(NonCancellable) {
                settingsUseCase.setServerConfig(address, token, allowSelfSigned)
                _uiState.update {
                    it.copy(
                        savedAddress = address,
                        savedToken = token,
                        savedTlsAllowSelfSigned = allowSelfSigned,
                        showRestartDialog = true,
                    )
                }
            }
        }
    }

    /** 退出应用；服务器配置在下次启动时生效（后台自动重启被系统限制，见 SettingsScreen 弹窗文案）。 */
    fun quitToApply() {
        _uiState.update { it.copy(showRestartDialog = false) }
        exitProcess(0)
    }

    /** 「1.0.0.100」= versionName + versionCode，供设置页一眼核对设备装的是哪次构建。 */
    private fun appVersionLabel(): String {
        val info = context.packageManager.getPackageInfo(context.packageName, 0)
        return "${info.versionName ?: ""}.${PackageInfoCompat.getLongVersionCode(info)}"
    }

    /** 检测服务：REST 预检 `/healthz_token`（token 校验与 WS 握手一致，docs/setting_server_address.md §3.3）。 */
    fun checkService() {
        val cur = _uiState.value
        if (cur.serviceStatus == ServiceStatus.Checking) return
        if (!cur.canProbe) return
        // 按发起时刻的草稿探测（先测后存）；结果仅在草稿未再变化时落地，
        // 避免旧探测结果覆盖新状态（快照守卫）
        val address = cur.serverAddressInput
        val token = cur.authTokenInput
        val allowSelfSigned = cur.tlsAllowSelfSigned
        _uiState.update { it.copy(serviceStatus = ServiceStatus.Checking) }
        viewModelScope.launch {
            val result = serviceProbe.probe(address, token, deviceId, allowSelfSigned)
            val status = when (result) {
                ProbeResult.AVAILABLE -> ServiceStatus.Available
                ProbeResult.AUTH_FAILED -> ServiceStatus.AuthFailed
                ProbeResult.UNREACHABLE -> ServiceStatus.Unavailable
            }
            _uiState.update { state ->
                if (state.serverAddressInput == address &&
                    state.authTokenInput == token &&
                    state.tlsAllowSelfSigned == allowSelfSigned
                ) {
                    state.copy(serviceStatus = status)
                } else {
                    state
                }
            }
        }
    }
}
