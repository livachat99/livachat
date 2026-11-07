package dev.livachat.app.ui.screens.splash

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.data.local.PreferencesDataSource
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import javax.inject.Inject

sealed class SplashDestination {
    data object Permission : SplashDestination()
    data object Conversation : SplashDestination()
}

@HiltViewModel
class SplashViewModel @Inject constructor(
    private val preferencesDataSource: PreferencesDataSource,
    @ApplicationContext private val context: Context,
) : ViewModel() {

    private val _destination = MutableStateFlow<SplashDestination?>(null)
    val destination: StateFlow<SplashDestination?> = _destination.asStateFlow()

    fun bootstrap() {
        viewModelScope.launch {
            val startTime = System.currentTimeMillis()

            val isFirstLaunch = preferencesDataSource.isFirstLaunch.first()

            // 运行时权限门禁（不只首启）：老用户在系统设置撤销 mic 权限后冷启动，
            // 直进会话会触发 AudioRecord 无权限构建崩溃（UnsupportedOperationException）；
            // 与授权页规则一致——mic 未授权禁止进入会话。
            // 用原生 context.checkSelfPermission（minSdk 26）：ContextCompat 内部走
            // TextUtils.equals，JVM 单测环境（android.jar 未 mock）会抛 not-mocked
            val micGranted = context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED

            val elapsed = System.currentTimeMillis() - startTime
            val remaining = 1000L - elapsed
            if (remaining > 0) {
                delay(remaining)
            }

            _destination.value = if (isFirstLaunch || !micGranted) {
                SplashDestination.Permission
            } else {
                SplashDestination.Conversation
            }
        }
    }
}
