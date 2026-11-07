package dev.livachat.app.ui.screens.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface as MaterialSurface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties
import androidx.hilt.navigation.compose.hiltViewModel
import dev.livachat.app.R
import dev.livachat.app.ui.legal.CustomTabsUrlOpener
import dev.livachat.app.ui.legal.LegalLinks
import dev.livachat.app.ui.legal.UrlOpener
import dev.livachat.app.ui.theme.Background
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.Error
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.Success
import dev.livachat.app.ui.theme.Surface
import dev.livachat.app.ui.theme.SurfaceVariant
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary
import dev.livachat.app.ui.theme.TextTertiary

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onNavigateBack: () -> Unit,
    urlOpener: UrlOpener? = null,
    viewModel: SettingsViewModel = hiltViewModel(),
) {
    val uiState by viewModel.uiState.collectAsState()
    val context = LocalContext.current
    val opener = urlOpener ?: remember(context) { CustomTabsUrlOpener(context) }
    var showLicenses by remember { mutableStateOf(false) }

    Column(modifier = Modifier.fillMaxSize().background(Background).navigationBarsPadding()) {
        TopAppBar(
            title = { Text(stringResource(R.string.settings_title), color = TextPrimary) },
            navigationIcon = {
                IconButton(onClick = onNavigateBack) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.common_back), tint = TextPrimary)
                }
            },
            colors = TopAppBarDefaults.topAppBarColors(containerColor = Background),
        )

        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(Spacing.md),
            verticalArrangement = Arrangement.spacedBy(Spacing.lg),
        ) {
            // Service section（docs/setting_server_address.md §3.4）
            SettingsSection(stringResource(R.string.settings_section_service)) {
                ServerAddressSetting(
                    value = uiState.serverAddressInput,
                    onValueChange = viewModel::onServerAddressChange,
                )
                AuthTokenSetting(
                    value = uiState.authTokenInput,
                    onValueChange = viewModel::onAuthTokenChange,
                )
                SwitchSetting(
                    title = stringResource(R.string.settings_tls_allow_self_signed),
                    subtitle = stringResource(R.string.settings_tls_allow_self_signed_desc),
                    checked = uiState.tlsAllowSelfSigned,
                    onCheckedChange = viewModel::setTlsAllowSelfSigned,
                    testTag = "settings_tls_allow_self_signed",
                )
                ServiceCheckSetting(
                    status = uiState.serviceStatus,
                    enabled = uiState.canProbe,
                    onCheck = { viewModel.checkService() },
                )
                val inlineValidation = uiState.validationMessage ?: if (
                    uiState.dirty && !uiState.canProbe
                ) {
                    stringResource(R.string.settings_save_failed_message)
                } else {
                    null
                }
                inlineValidation?.let { message ->
                    Text(
                        text = message,
                        style = MaterialTheme.typography.bodySmall,
                        color = Error,
                        modifier = Modifier.padding(top = Spacing.xs),
                    )
                }
                if (uiState.dirty) {
                    // 直接保存并进入重启提示；非法输入由 inline 文案提示 / Save 置灰
                    Button(
                        onClick = viewModel::saveServerConfig,
                        enabled = uiState.canProbe,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = Spacing.md)
                            .height(48.dp)
                            .testTag(SAVE_SERVER_CONFIG_TAG),
                        shape = RoundedCornerShape(16.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = BrandIndigo,
                            contentColor = TextPrimary,
                            disabledContainerColor = SurfaceVariant,
                            disabledContentColor = TextTertiary,
                        ),
                    ) {
                        Text(stringResource(R.string.settings_save))
                    }
                }
            }

            SettingsSection(
                title = stringResource(R.string.settings_section_video),
                contentSpacing = Spacing.sm,
            ) {
                SwitchSetting(
                    title = stringResource(R.string.settings_default_front_camera),
                    subtitle = stringResource(R.string.settings_default_front_camera_desc),
                    checked = uiState.defaultFrontCamera,
                    onCheckedChange = { viewModel.setDefaultFrontCamera(it) },
                )
                SwitchSetting(
                    title = stringResource(R.string.settings_auto_camera_on_join),
                    subtitle = stringResource(R.string.settings_auto_camera_on_join_desc),
                    checked = uiState.autoCameraOnJoin,
                    onCheckedChange = { viewModel.setAutoCameraOnJoin(it) },
                )
            }

            SettingsSection(
                title = stringResource(R.string.settings_section_general),
                contentSpacing = Spacing.sm,
            ) {
                SwitchSetting(
                    title = stringResource(R.string.settings_haptic),
                    subtitle = stringResource(R.string.settings_haptic_desc),
                    checked = uiState.hapticEnabled,
                    onCheckedChange = { viewModel.setHapticEnabled(it) },
                )
                SwitchSetting(
                    title = stringResource(R.string.settings_ai_proactive),
                    subtitle = stringResource(R.string.settings_ai_proactive_desc),
                    checked = uiState.avdetectorEnabled,
                    onCheckedChange = { viewModel.setAvdetectorEnabled(it) },
                    testTag = "settings_ai_proactive",
                )
            }

            SettingsSection(
                title = stringResource(R.string.settings_section_about),
                contentSpacing = 0.dp,
            ) {
                SettingRow("LivaChat", uiState.appVersion)
                HorizontalDivider(color = SurfaceVariant)
                LegalRow(stringResource(R.string.settings_privacy_policy), "settings_privacy") {
                    opener.open(LegalLinks.PRIVACY)
                }
                HorizontalDivider(color = SurfaceVariant)
                LegalRow(stringResource(R.string.settings_eula), "settings_eula") {
                    opener.open(LegalLinks.EULA)
                }
                HorizontalDivider(color = SurfaceVariant)
                LegalRow(stringResource(R.string.settings_support), "settings_support") {
                    opener.open(LegalLinks.SUPPORT)
                }
                HorizontalDivider(color = SurfaceVariant)
                LegalRow(stringResource(R.string.settings_open_source_licenses), "settings_licenses") {
                    showLicenses = true
                }
            }

            Spacer(modifier = Modifier.height(Spacing.xxl))
        }
    }

    // Restart-to-apply confirmation after saving server config
    if (uiState.showRestartDialog) {
        ServerSwitchDialog(onExit = viewModel::quitToApply)
    }

    if (showLicenses) {
        LicensesDialog(onDismiss = { showLicenses = false })
    }
}

/**
 * 保存服务器配置后的"必须重启"弹窗（文案泛化 Q2；
 * 2026-10-13 再简：正文仅「重新打开 App 后生效。」）：标题 `titleMedium` 居中；单按钮
 * 「退出」居中；禁外点/返回关闭（改完必须退出）。
 * internal：仪器测试直接渲染本组件（androidTest 与 main 同包友元）。
 */
@Composable
internal fun ServerSwitchDialog(
    onExit: () -> Unit,
) {
    AlertDialog(
        // 必须退出：外点/返回均不可绕过（弹窗出现前配置已落盘）
        onDismissRequest = { },
        properties = DialogProperties(
            dismissOnBackPress = false,
            dismissOnClickOutside = false,
        ),
        title = {
            Text(
                text = stringResource(R.string.settings_server_updated),
                style = MaterialTheme.typography.titleMedium,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        text = {
            Text(
                text = stringResource(R.string.settings_restart_to_apply),
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = {
            Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                TextButton(onClick = onExit) {
                    Text(stringResource(R.string.settings_exit))
                }
            }
        },
    )
}

@Composable
private fun SectionHeader(title: String) {
    Text(
        text = title,
        style = MaterialTheme.typography.labelLarge,
        color = BrandIndigo,
        modifier = Modifier.padding(vertical = Spacing.sm),
    )
}

/** iOS 同款分区：标题 + Surface 卡片（16dp 圆角、16dp 内边距）。 */
@Composable
private fun SettingsSection(
    title: String,
    contentSpacing: Dp = Spacing.md,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.sm)) {
        SectionHeader(title)
        MaterialSurface(
            color = Surface,
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(Spacing.md),
                verticalArrangement = Arrangement.spacedBy(contentSpacing),
                content = content,
            )
        }
    }
}

/** 地址输入框 testTag（仪器测试直取）。 */
internal const val SERVER_ADDRESS_FIELD_TAG = "serverAddressField"

/** 认证密钥输入框 testTag（仪器测试直取）。 */
internal const val AUTH_TOKEN_FIELD_TAG = "authTokenField"

/** 「保存」按钮 testTag（仪器测试直取）。 */
internal const val SAVE_SERVER_CONFIG_TAG = "saveServerConfig"

/** 服务器地址输入；空态占位「ip:port 或 域名:port」（§10.3）。 */
@Composable
private fun ServerAddressSetting(
    value: String,
    onValueChange: (String) -> Unit,
) {
    Text(
        text = stringResource(R.string.settings_server_label),
        style = MaterialTheme.typography.bodyLarge,
        color = TextPrimary,
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = Spacing.sm, bottom = Spacing.xs),
    )
    CompactField(
        value = value,
        onValueChange = onValueChange,
        placeholder = stringResource(R.string.settings_server_placeholder),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done),
        modifier = Modifier.fillMaxWidth().testTag(SERVER_ADDRESS_FIELD_TAG),
        trailing = {},
    )
}

/** 认证密钥输入（掩码 + 眼睛切换）；空态占位「查看 server 配置文件获取」（§10.3）。 */
@Composable
private fun AuthTokenSetting(
    value: String,
    onValueChange: (String) -> Unit,
) {
    var tokenVisible by remember { mutableStateOf(false) }
    Text(
        text = stringResource(R.string.settings_auth_token),
        style = MaterialTheme.typography.bodyLarge,
        color = TextPrimary,
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = Spacing.md, bottom = Spacing.xs),
    )
    CompactField(
        value = value,
        onValueChange = onValueChange,
        placeholder = stringResource(R.string.settings_auth_token_placeholder),
        visualTransformation = if (tokenVisible) {
            VisualTransformation.None
        } else {
            PasswordVisualTransformation()
        },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Ascii, imeAction = ImeAction.Done),
        modifier = Modifier.fillMaxWidth().testTag(AUTH_TOKEN_FIELD_TAG),
        trailing = {
            IconButton(onClick = { tokenVisible = !tokenVisible }) {
                Icon(
                    imageVector = if (tokenVisible) Icons.Filled.VisibilityOff else Icons.Filled.Visibility,
                    contentDescription = if (tokenVisible) {
                        stringResource(R.string.settings_hide_token)
                    } else {
                        stringResource(R.string.settings_show_token)
                    },
                    tint = TextSecondary,
                )
            }
        },
    )
}

/**
 * 紧凑输入框（服务器地址 / 认证密钥共用）：44dp 高度、文字垂直居中、尾内图标槽、
 * 空值占位（§10.3；占位画在文本层下方，有值即隐）。不用 M3 `OutlinedTextField`——其内部
 * 按 56dp 最小高度布局，强制压低会把文字底部裁掉（真机实测）。internal：仪器测试直渲。
 */
@Composable
internal fun CompactField(
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    keyboardOptions: KeyboardOptions,
    trailing: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    visualTransformation: VisualTransformation = VisualTransformation.None,
) {
    val interactionSource = remember { MutableInteractionSource() }
    val focused by interactionSource.collectIsFocusedAsState()
    val borderColor = if (focused) BrandIndigo else SurfaceVariant
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        singleLine = true,
        textStyle = MaterialTheme.typography.bodyLarge.copy(color = TextPrimary),
        cursorBrush = SolidColor(BrandIndigo),
        visualTransformation = visualTransformation,
        keyboardOptions = keyboardOptions,
        interactionSource = interactionSource,
        modifier = modifier,
        decorationBox = { innerTextField ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(44.dp)
                    .background(Background, RoundedCornerShape(12.dp))
                    .border(1.dp, borderColor, RoundedCornerShape(12.dp))
                    .padding(start = Spacing.md),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(modifier = Modifier.weight(1f)) {
                    // 占位在下层：空值显示；输入后由 innerTextField 文本覆盖即隐（光标可叠其上）
                    if (value.isEmpty()) {
                        Text(
                            text = placeholder,
                            style = MaterialTheme.typography.bodyLarge,
                            color = TextTertiary,
                            maxLines = 1,
                        )
                    }
                    innerTextField()
                }
                trailing()
            }
        },
    )
}

@Composable
private fun SwitchSetting(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    testTag: String? = null,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = Spacing.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = TextPrimary)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = TextSecondary)
        }
        Spacer(modifier = Modifier.width(Spacing.md))
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            modifier = if (testTag != null) Modifier.testTag(testTag) else Modifier,
            colors = SwitchDefaults.colors(
                checkedThumbColor = TextPrimary,
                checkedTrackColor = BrandIndigo,
                uncheckedThumbColor = TextSecondary,
                uncheckedTrackColor = SurfaceVariant,
            ),
        )
    }
}

@Composable
private fun SettingRow(
    title: String,
    value: String,
    onClick: (() -> Unit)? = null,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .then(if (onClick != null) Modifier.clickable { onClick() } else Modifier)
            .heightIn(min = 52.dp)
            .padding(vertical = Spacing.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, style = MaterialTheme.typography.bodyLarge, color = TextPrimary, modifier = Modifier.weight(1f))
        Text(value, style = MaterialTheme.typography.bodySmall, color = TextSecondary)
    }
}

/** Legal 行：标题 + chevron，44dp 最小高度（对齐 iOS `legalRow`）。 */
@Composable
internal fun LegalRow(
    title: String,
    tag: String,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 44.dp)
            .clickable(onClick = onClick)
            .testTag(tag)
            .padding(vertical = Spacing.xs),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            title,
            style = MaterialTheme.typography.bodyLarge,
            color = TextPrimary,
            modifier = Modifier.weight(1f),
        )
        Icon(
            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
            contentDescription = null,
            tint = TextTertiary,
        )
    }
}

/** 检测服务行：REST 预检（token 校验）；`enabled=false`（地址/密钥非法）时禁用并置灰。 */
@Composable
private fun ServiceCheckSetting(
    status: ServiceStatus,
    enabled: Boolean,
    onCheck: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled && status != ServiceStatus.Checking) { onCheck() }
            .padding(vertical = Spacing.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(stringResource(R.string.settings_check_service), style = MaterialTheme.typography.bodyLarge, color = TextPrimary)
            Text(
                stringResource(R.string.settings_check_service_desc),
                style = MaterialTheme.typography.bodySmall,
                color = TextSecondary,
            )
        }

        when (status) {
            is ServiceStatus.Idle -> {
                Text(
                    stringResource(R.string.settings_check_action),
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (enabled) BrandIndigo else TextTertiary,
                )
            }
            is ServiceStatus.Checking -> {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    strokeWidth = 2.dp,
                    color = BrandIndigo,
                )
            }
            is ServiceStatus.Available -> {
                Text(
                    stringResource(R.string.settings_status_available),
                    style = MaterialTheme.typography.bodyMedium,
                    color = Success,
                )
            }
            is ServiceStatus.AuthFailed -> {
                Text(
                    stringResource(R.string.settings_status_auth_failed),
                    style = MaterialTheme.typography.bodyMedium,
                    color = Error,
                )
            }
            is ServiceStatus.Unavailable -> {
                Text(
                    stringResource(R.string.settings_status_unavailable),
                    style = MaterialTheme.typography.bodyMedium,
                    color = Error,
                )
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun SettingsScreenPreview() {
    LivaChatTheme {
        SettingsScreen(onNavigateBack = {})
    }
}
