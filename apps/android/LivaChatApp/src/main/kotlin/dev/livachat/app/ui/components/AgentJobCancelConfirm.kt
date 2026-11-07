package dev.livachat.app.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.Elevation
import dev.livachat.app.ui.theme.Gold
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.Surface
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary
import dev.livachat.sdk.model.AgentJob

/**
 * 行内取消确认状态（docs/android_agent_job_status.md §5.2/§6.4）。
 * 定义在组件包，避免 ui/components 反向依赖 conversation 屏幕包。
 */
data class AgentJobCancelUiState(
    val job: AgentJob,
    val inFlight: Boolean = false,
    val error: String? = null,   // 本地化后的用户文案
)

/**
 * 行内确认卡片：覆盖在目标 job 行上（调用方负责遮罩）。
 * 问题 +「否」「是」；inFlight 时按钮被进度替代；失败在第二行提示并允许重试。
 */
@Composable
fun AgentJobCancelConfirm(
    state: AgentJobCancelUiState,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(12.dp),
        color = Surface,
        border = BorderStroke(1.dp, Gold),
        shadowElevation = Elevation.Level2,
    ) {
        Column(
            modifier = Modifier.padding(horizontal = Spacing.sm, vertical = Spacing.xs),
            verticalArrangement = Arrangement.spacedBy(Spacing.xs),
        ) {
            // 问题独占整行：长本地化文案最多两行，不会挤压操作按钮；居中
            Text(
                text = stringResource(R.string.conv_agent_job_cancel_question),
                style = MaterialTheme.typography.bodyMedium,
                color = TextPrimary,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth(),
            )
            state.error?.let { error ->
                Text(
                    text = error,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (state.inFlight) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(14.dp),
                        strokeWidth = 2.dp,
                        color = BrandIndigo,
                    )
                    Spacer(modifier = Modifier.width(Spacing.xs))
                    Text(
                        text = stringResource(R.string.conv_agent_job_cancel_in_progress),
                        style = MaterialTheme.typography.labelMedium,
                        color = TextSecondary,
                    )
                } else {
                    val compact = PaddingValues(horizontal = Spacing.sm, vertical = 0.dp)
                    TextButton(
                        onClick = onDismiss,
                        contentPadding = compact,
                    ) {
                        Text(stringResource(R.string.conv_agent_job_cancel_no))
                    }
                    TextButton(
                        onClick = onConfirm,
                        contentPadding = compact,
                    ) {
                        Text(stringResource(R.string.conv_agent_job_cancel_yes))
                    }
                }
            }
        }
    }
}
