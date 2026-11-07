package dev.livachat.app.ui.screens.history

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import dev.livachat.app.R
import dev.livachat.app.domain.model.SessionSummary
import dev.livachat.app.ui.components.EmptyState
import dev.livachat.app.ui.components.LoadingSkeleton
import dev.livachat.app.ui.theme.Background
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.SurfaceVariant
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary
import dev.livachat.app.ui.theme.TextTertiary
import dev.livachat.app.ui.util.localizeDigits
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.time.format.TextStyle

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryScreen(
    onNavigateBack: () -> Unit,
    onNavigateToDetail: (String) -> Unit,
    viewModel: HistoryViewModel = hiltViewModel(),
) {
    val uiState by viewModel.uiState.collectAsState()

    Column(modifier = Modifier.fillMaxSize().background(Background).navigationBarsPadding()) {
        TopAppBar(
            title = { Text(stringResource(R.string.history_title), color = TextPrimary) },
            navigationIcon = {
                IconButton(onClick = onNavigateBack) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.common_back), tint = TextPrimary)
                }
            },
            actions = {
                TextButton(
                    onClick = { viewModel.showClearAllConfirm(true) },
                    // sessions 是搜索过滤后的子集:库里有记录但被搜索词过滤为空时仍须可清空
                    enabled = !uiState.isLoading &&
                        (uiState.sessions.isNotEmpty() || uiState.searchQuery.isNotEmpty()),
                ) {
                    Text(stringResource(R.string.history_clear), color = dev.livachat.app.ui.theme.Error)
                }
            },
            colors = TopAppBarDefaults.topAppBarColors(containerColor = Background),
        )

        // Search bar
        OutlinedTextField(
            value = uiState.searchQuery,
            onValueChange = { viewModel.onSearchQueryChanged(it) },
            modifier = Modifier.fillMaxWidth().padding(horizontal = Spacing.md),
            placeholder = { Text(stringResource(R.string.history_search_placeholder), color = TextTertiary) },
            leadingIcon = { Icon(Icons.Filled.Search, null, tint = TextSecondary) },
            singleLine = true,
            shape = RoundedCornerShape(12.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedTextColor = TextPrimary,
                unfocusedTextColor = TextPrimary,
                focusedBorderColor = SurfaceVariant,
                unfocusedBorderColor = SurfaceVariant,
                cursorColor = TextPrimary,
            ),
        )

        Spacer(modifier = Modifier.height(Spacing.sm))

        // Session list
        when {
            uiState.isLoading -> {
                LoadingSkeleton(lineCount = 5)
            }
            uiState.sessions.isEmpty() -> {
                EmptyState(
                    icon = Icons.Filled.Search,
                    title = stringResource(R.string.history_empty_title),
                    subtitle = stringResource(R.string.history_empty_subtitle),
                )
            }
            else -> {
                LazyColumn(
                    modifier = Modifier.fillMaxSize().padding(horizontal = Spacing.md),
                ) {
                    items(uiState.sessions, key = { it.id }) { session ->
                        SessionCard(
                            session = session,
                            onClick = { onNavigateToDetail(session.id) },
                            onDelete = { viewModel.showDeleteConfirm(session.id) },
                        )
                        Spacer(modifier = Modifier.height(Spacing.sm))
                    }
                }
            }
        }
    }

    // Delete confirmation（标题居中缩小字号；正文与按钮并排居中——M3 无按钮居中参数，
    // 以单 confirmButton slot 内全宽居中 Row 承载两按钮，同对话页各弹窗惯例）
    uiState.showDeleteConfirm?.let { sessionId ->
        AlertDialog(
            onDismissRequest = { viewModel.showDeleteConfirm(null) },
            title = {
                Text(
                    stringResource(R.string.history_delete_title),
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center,
                    style = MaterialTheme.typography.titleMedium,
                )
            },
            text = {
                Text(
                    stringResource(R.string.history_delete_message),
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center,
                )
            },
            confirmButton = {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.Center,
                ) {
                    TextButton(onClick = { viewModel.showDeleteConfirm(null) }) {
                        Text(stringResource(R.string.common_cancel))
                    }
                    TextButton(onClick = {
                        viewModel.onDeleteSession(sessionId)
                        viewModel.showDeleteConfirm(null)
                    }) {
                        Text(stringResource(R.string.common_delete), color = dev.livachat.app.ui.theme.Error)
                    }
                }
            },
        )
    }

    // Clear all confirmation
    if (uiState.showClearAllConfirm) {
        AlertDialog(
            onDismissRequest = { viewModel.showClearAllConfirm(false) },
            title = {
                Text(
                    text = stringResource(R.string.history_clear_all_title),
                    style = MaterialTheme.typography.titleMedium,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth(),
                )
            },
            text = {
                Text(
                    text = stringResource(R.string.history_clear_all_message),
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth(),
                )
            },
            confirmButton = {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.Center,
                ) {
                    TextButton(onClick = { viewModel.showClearAllConfirm(false) }) {
                        Text(stringResource(R.string.common_cancel))
                    }
                    TextButton(onClick = { viewModel.onClearAll() }) {
                        Text(stringResource(R.string.history_clear), color = dev.livachat.app.ui.theme.Error)
                    }
                }
            },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SessionCard(
    session: SessionSummary,
    onClick: () -> Unit,
    onDelete: () -> Unit,
) {
    val dismissState = rememberSwipeToDismissBoxState(
        confirmValueChange = { value ->
            if (value == SwipeToDismissBoxValue.EndToStart) {
                onDelete()
                false
            } else true
        },
    )

    SwipeToDismissBox(
        state = dismissState,
        backgroundContent = {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(horizontal = Spacing.md)
                    .background(dev.livachat.app.ui.theme.Error, RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.CenterEnd,
            ) {
                Icon(
                    Icons.Filled.Delete, stringResource(R.string.common_delete),
                    tint = TextPrimary,
                    modifier = Modifier.padding(end = Spacing.lg),
                )
            }
        },
        enableDismissFromStartToEnd = false,
    ) {
        Card(
            onClick = onClick,
            colors = CardDefaults.cardColors(containerColor = dev.livachat.app.ui.theme.Surface),
            shape = RoundedCornerShape(12.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(Spacing.md),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = session.firstUserMessage
                            ?: session.summary
                            ?: stringResource(R.string.history_untitled),
                        style = MaterialTheme.typography.bodyLarge,
                        color = TextPrimary,
                        maxLines = 1,
                    )
                    Spacer(modifier = Modifier.height(Spacing.xs))
                    Text(
                        text = formatSessionSubtitle(session),
                        style = MaterialTheme.typography.bodySmall,
                        color = TextTertiary,
                    )
                }
                Icon(
                    Icons.Filled.Delete, stringResource(R.string.common_delete),
                    tint = TextTertiary,
                    modifier = Modifier.clickable { onDelete() },
                )
            }
        }
    }
}

/**
 * 会话条目副标题（§3.5）：日期/星期按当前 UI locale 本地化；zh 输出与旧 `yyyy年M月d日` 逐字一致。
 * 日期数字经 [localizeDigits]（ar 阿数，docs/android_multilingual.md §20 缺口⑤——ofLocalizedDate 亦为
 * DecimalStyle.STANDARD，JDK/Android 两侧均拉丁数字，须同助手本地化；其余语言恒等）。
 */
@Composable
private fun formatSessionSubtitle(session: SessionSummary): String {
    val locale = LocalConfiguration.current.locales[0]
    val zoned = session.startTime.atZone(ZoneId.systemDefault())
    val dateStr = localizeDigits(
        DateTimeFormatter.ofLocalizedDate(FormatStyle.LONG)
            .withLocale(locale)
            .withZone(ZoneId.systemDefault())
            .format(session.startTime),
        locale,
    )
    val dayOfWeek = zoned.dayOfWeek.getDisplayName(TextStyle.FULL, locale)
    return pluralStringResource(R.plurals.history_item_meta, session.messageCount, dateStr, dayOfWeek, session.messageCount)
}

@Preview(showBackground = true)
@Composable
private fun HistoryScreenPreview() {
    LivaChatTheme {
        HistoryScreen(
            onNavigateBack = {},
            onNavigateToDetail = {},
        )
    }
}
