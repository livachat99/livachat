package dev.livachat.app.mock.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import dev.livachat.app.mock.ui.components.StatusIndicator
import dev.livachat.app.mock.viewmodel.ConversationViewModel
import dev.livachat.sdk.model.ConnectionState

@Composable
fun ConversationScreen(viewModel: ConversationViewModel) {
    val uiState by viewModel.uiState.collectAsState()
    val listState = rememberLazyListState()

    LaunchedEffect(uiState.messages.size) {
        if (uiState.messages.isNotEmpty()) {
            listState.animateScrollToItem(uiState.messages.size - 1)
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        // Error banner
        if (uiState.errorMessage != null) {
            Surface(
                modifier = Modifier.fillMaxWidth(),
                color = MaterialTheme.colorScheme.errorContainer,
            ) {
                Row(
                    modifier = Modifier.padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        uiState.errorMessage!!,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = { viewModel.dismissError() }) {
                        Text("Dismiss")
                    }
                }
            }
        }

        // Status bar
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            StatusIndicator(
                connectionState = uiState.connectionState,
                sessionState = uiState.sessionState,
            )

            Row {
                Button(
                    onClick = { viewModel.connect() },
                    enabled = uiState.connectionState == ConnectionState.DISCONNECTED,
                ) { Text("Connect") }
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = { viewModel.startSession() },
                    enabled = uiState.connectionState == ConnectionState.CONNECTED,
                ) { Text("Start") }
            }
        }

        // Controls
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Button(onClick = { viewModel.toggleCapture() }) {
                Text(if (uiState.isCapturing) "Stop Recording" else "Start Recording")
            }
            Spacer(Modifier.width(8.dp))
            OutlinedButton(
                onClick = { viewModel.disconnect() },
                enabled = uiState.connectionState != ConnectionState.DISCONNECTED,
            ) {
                Text("Disconnect")
            }
        }

        // Message list
        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            state = listState,
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(uiState.messages) { msg ->
                MessageBubble(text = msg.text, isUser = msg.isUser, isFinal = msg.isFinal)
            }

            if (uiState.transcription.isNotEmpty()) {
                item {
                    MessageBubble(text = uiState.transcription, isUser = true, isFinal = false)
                }
            }

            if (uiState.aiText.isNotEmpty()) {
                item {
                    MessageBubble(text = uiState.aiText, isUser = false, isFinal = false)
                }
            }
        }
    }
}

@Composable
fun MessageBubble(text: String, isUser: Boolean, isFinal: Boolean) {
    val bgColor = if (isUser) {
        MaterialTheme.colorScheme.primaryContainer
    } else {
        MaterialTheme.colorScheme.secondaryContainer
    }
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.medium,
        color = bgColor,
    ) {
        Text(
            text = text + if (!isFinal) "..." else "",
            modifier = Modifier.padding(12.dp),
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}
