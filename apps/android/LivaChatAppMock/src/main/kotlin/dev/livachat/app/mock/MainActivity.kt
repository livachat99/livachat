package dev.livachat.app.mock

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import dev.livachat.app.mock.ui.ConversationScreen
import dev.livachat.app.mock.ui.PermissionScreen
import dev.livachat.app.mock.viewmodel.ConversationViewModel

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val requiredPermissions = arrayOf(
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.CAMERA,
        )

        setContent {
            val grantedOnStart = requiredPermissions.all {
                ContextCompat.checkSelfPermission(
                    this@MainActivity, it
                ) == PackageManager.PERMISSION_GRANTED
            }

            var permissionsGranted by remember { mutableStateOf(grantedOnStart) }
            val viewModel: ConversationViewModel = viewModel()

            if (permissionsGranted) {
                viewModel.init(this@MainActivity)
                if (!viewModel.cameraInitialized) {
                    viewModel.initCamera(application, this@MainActivity)
                }
                ConversationScreen(viewModel = viewModel)
            } else {
                PermissionScreen(
                    onPermissionsGranted = { permissionsGranted = true },
                    onOpenSettings = {
                        val intent = Intent(
                            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                            Uri.parse("package:$packageName"),
                        )
                        startActivity(intent)
                    },
                )
            }
        }
    }
}
