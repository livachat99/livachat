package dev.livachat.app.mock.ui

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp

@Composable
fun PermissionScreen(
    onPermissionsGranted: () -> Unit,
    onOpenSettings: () -> Unit = {},
) {
    val permissions = arrayOf(
        Manifest.permission.RECORD_AUDIO,
        Manifest.permission.CAMERA,
    )

    val launcher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        val allGranted = permissions.all { results[it] == true }
        if (allGranted) onPermissionsGranted()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = "LivaChat",
            style = MaterialTheme.typography.headlineLarge,
        )

        Spacer(Modifier.height(24.dp))

        Text(
            text = "Microphone and camera permissions are required for voice and video chat. " +
                   "If you previously denied permissions, open Settings to enable them.",
            style = MaterialTheme.typography.bodyLarge,
            textAlign = TextAlign.Center,
        )

        Spacer(Modifier.height(24.dp))

        Button(onClick = { launcher.launch(permissions) }) {
            Text("Grant Permissions")
        }

        Spacer(Modifier.height(12.dp))

        OutlinedButton(onClick = onOpenSettings) {
            Text("Open Settings")
        }
    }
}
