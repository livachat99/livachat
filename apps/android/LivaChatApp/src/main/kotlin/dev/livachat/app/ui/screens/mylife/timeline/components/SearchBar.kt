package dev.livachat.app.ui.screens.mylife.timeline.components

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.platform.LocalConfiguration
import dev.livachat.app.R
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.theme.Spacing

/** 常驻搜索框（§3.1/§3.4）：🔍 leading、✕ 清除；IME action=search 立即触发；输入防抖在 VM。 */
@Composable
fun SearchBar(
    query: String,
    onQueryChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    onSearchImmediate: () -> Unit = {},
) {
    OutlinedTextField(
        value = query,
        onValueChange = onQueryChange,
        modifier = modifier.fillMaxWidth().padding(horizontal = Spacing.md),
        placeholder = {
            Text(stringResource(R.string.mylife_search_placeholder), color = MylifeColors.Dust)
        },
        leadingIcon = {
            Icon(Icons.Filled.Search, contentDescription = null, tint = MylifeColors.Dust)
        },
        trailingIcon = {
            if (query.isNotEmpty()) {
                IconButton(onClick = { onQueryChange("") }) {
                    Icon(Icons.Filled.Clear, contentDescription = stringResource(R.string.common_clear), tint = MylifeColors.Dust)
                }
            }
        },
        singleLine = true,
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
        keyboardActions = KeyboardActions(onSearch = { onSearchImmediate() }),
        textStyle = MaterialTheme.typography.bodyMedium.copy(color = MylifeColors.Star),
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = MylifeColors.Line,
            unfocusedBorderColor = MylifeColors.Line,
            focusedContainerColor = MylifeColors.Panel.copy(alpha = 0.6f),
            unfocusedContainerColor = MylifeColors.Panel.copy(alpha = 0.4f),
            cursorColor = MylifeColors.Gold,
        ),
    )
}
