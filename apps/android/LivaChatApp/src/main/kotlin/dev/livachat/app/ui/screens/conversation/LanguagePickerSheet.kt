package dev.livachat.app.ui.screens.conversation

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.SurfaceVariant
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary

// 演讲者语言未知时可选「自动识别」：语义与选英语等同（下发 en，多模态模型自行识别）
const val AUTO_LANGUAGE_CODE = "auto"

/** 对方语言候选（业务功能语言清单，非 App UI 语言；显示名按当前 UI locale 自动本地化）。 */
internal val languageCodes = listOf(
    "zh", "en", "ja", "ko", "fr", "it", "de", "es", "pt", "ru", "hi", "id", "th", "vi", "ar",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LanguagePickerSheet(
    title: String? = null,
    onLanguageSelected: (String) -> Unit,
    onDismiss: () -> Unit,
    showAutoLanguage: Boolean = false,
) {
    val uiLocale = LocalConfiguration.current.locales[0]
    val languages = languageCodes.map { code ->
        code to java.util.Locale.forLanguageTag(code).getDisplayLanguage(uiLocale)
    }
    // 「自动识别」置顶；selectedCode 映射见 confirm —— auto 语义=英语
    val options = if (showAutoLanguage) {
        listOf(AUTO_LANGUAGE_CODE to stringResource(R.string.conv_language_auto)) + languages
    } else {
        languages
    }
    var selectedCode by remember { mutableStateOf("en") }
    var dropdownExpanded by remember { mutableStateOf(false) }
    val selectedName = options.firstOrNull { it.first == selectedCode }?.second ?: selectedCode

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                title ?: stringResource(R.string.conv_select_peer_language),
                modifier = Modifier.fillMaxWidth(),
                textAlign = TextAlign.Center,
                style = MaterialTheme.typography.titleMedium,
                color = TextPrimary,
            )
        },
        text = {
            Column(modifier = Modifier.fillMaxWidth()) {
                ExposedDropdownMenuBox(
                    expanded = dropdownExpanded,
                    onExpandedChange = { dropdownExpanded = it },
                ) {
                    OutlinedTextField(
                        value = selectedName,
                        onValueChange = {},
                        readOnly = true,
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = dropdownExpanded) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .menuAnchor(),
                        shape = RoundedCornerShape(12.dp),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary,
                            focusedBorderColor = SurfaceVariant,
                            unfocusedBorderColor = SurfaceVariant,
                            cursorColor = TextPrimary,
                        ),
                    )
                    ExposedDropdownMenu(
                        expanded = dropdownExpanded,
                        onDismissRequest = { dropdownExpanded = false },
                    ) {
                        options.forEach { (code, name) ->
                            DropdownMenuItem(
                                text = { Text(name, color = TextPrimary) },
                                onClick = {
                                    selectedCode = code
                                    dropdownExpanded = false
                                },
                            )
                        }
                    }
                }
            }
        },
        confirmButton = {
            // 与其它弹窗一致：按钮并排居中（单 slot 内全宽居中 Row）
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Center,
            ) {
                TextButton(onClick = onDismiss) {
                    Text(stringResource(R.string.common_cancel), color = TextSecondary)
                }
                TextButton(onClick = { onLanguageSelected(selectedCode) }) {
                    Text(stringResource(R.string.conv_picker_confirm), color = BrandIndigo)
                }
            }
        },
    )
}
