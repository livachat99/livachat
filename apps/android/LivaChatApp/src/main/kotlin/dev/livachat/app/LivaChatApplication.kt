package dev.livachat.app

import android.app.Application
import android.util.Log
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import dagger.hilt.android.HiltAndroidApp
import dev.livachat.app.media.AppForegroundState
import javax.inject.Inject

@HiltAndroidApp
class LivaChatApplication : Application() {

    @Inject lateinit var appForegroundState: AppForegroundState

    override fun onCreate() {
        super.onCreate()
        // 进程级前后台感知（docs/android_background_support.md §2.2）：发布进程事实，驱动麦克风
        // 后台释放/前台重获取门控（docs/android_audio_capture.md §3.6）；onStop 有
        // ~700ms debounce（库内部防 Activity 间切换误报），时间戳比实际退后台晚 ~0.7s。
        ProcessLifecycleOwner.get().lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onStart(owner: LifecycleOwner) {
                Log.i(TAG, "app entered foreground")
                appForegroundState.onEnterForeground()
            }

            override fun onStop(owner: LifecycleOwner) {
                Log.i(TAG, "app entered background")
                appForegroundState.onEnterBackground()
            }
        })
    }

    private companion object {
        const val TAG = "LivaChatForeground"
    }
}
