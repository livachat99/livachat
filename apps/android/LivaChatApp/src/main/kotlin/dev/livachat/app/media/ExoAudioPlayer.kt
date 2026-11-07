package dev.livachat.app.media

import android.content.Context
import android.util.Log
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import javax.inject.Inject
import javax.inject.Named
import dev.livachat.app.R
import javax.inject.Singleton

/**
 * 语音段播放器实现（docs/android_timeline_audio_gallery.md §4.6）：懒建的单个 ExoPlayer，
 * 播放 Ogg Opus 语音段（BFF 媒体端点支持 Range ⇒ seek 直接生效）。
 *
 * - 媒体通道：USAGE_MEDIA + CONTENT_TYPE_SPEECH、ExoPlayer 自管音频焦点（与实时会话的
 *   USAGE_VOICE_COMMUNICATION + MODE_IN_COMMUNICATION 区分）；
 * - 与实时会话互斥（Q8 单向）：观察 [AudioPlaybackController.isPlaying]，升为 true 时暂停自己；
 *   **不观察回落沿、不自动恢复**——反向（AI 开口打断画廊）的暂停/恢复交给系统焦点（§7 E3 定案）；
 * - 所有 player 访问都在主线程（scope = Main.immediate，ExoPlayer 单线程约束）。
 */
@OptIn(UnstableApi::class)
@androidx.annotation.OptIn(UnstableApi::class)
@Singleton
class ExoAudioPlayer @Inject constructor(
    @ApplicationContext private val context: Context,
    @Named("mylife") private val client: OkHttpClient,
    private val playbackController: AudioPlaybackController,
) : AudioPlayer {

    private val _state = MutableStateFlow(AudioPlayer.State())
    override val state: StateFlow<AudioPlayer.State> = _state.asStateFlow()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val renderersFactory = GalleryRenderersFactory(context)

    private var player: ExoPlayer? = null
    private var ticker: Job? = null

    private val listener = object : Player.Listener {
        override fun onPlaybackStateChanged(playbackState: Int) {
            when (playbackState) {
                Player.STATE_BUFFERING -> _state.update { it.copy(isBuffering = true) }
                Player.STATE_READY -> _state.update {
                    it.copy(isBuffering = false, durationMs = positiveOr(player?.duration, it.durationMs))
                }
                Player.STATE_ENDED -> {
                    // 播完 → 复位停止态（位置归零）；key 保留（卡片回到「▶ + 总长」）。
                    // 必须先 pause()：到达 ENDED 时 playWhenReady 仍为 true，直接 seekTo(0)
                    // 会立刻自动重播（无限循环）
                    player?.pause()
                    player?.seekTo(0)
                    stopTicker()
                    _state.update { it.copy(isPlaying = false, isBuffering = false, positionMs = 0L) }
                }
                else -> Unit
            }
        }

        override fun onIsPlayingChanged(isPlaying: Boolean) {
            if (isPlaying) {
                startTicker()
            } else {
                stopTicker()
                _state.update {
                    it.copy(positionMs = positiveOr(player?.currentPosition, it.positionMs))
                }
            }
            _state.update { it.copy(isPlaying = isPlaying) }
        }

        override fun onPlayerError(error: PlaybackException) {
            Log.w(TAG, "playback error: ${error.errorCodeName}", error)
            stopTicker()
            // 保留 key：错误归属到具体卡片（§4.6）
            _state.update { it.copy(isPlaying = false, isBuffering = false, error = context.getString(R.string.player_play_failed)) }
        }
    }

    init {
        scope.launch {
            playbackController.isPlaying.collect { speaking ->
                if (speaking && _state.value.isPlaying) {
                    Log.d(TAG, "realtime session speaking → pause gallery playback")
                    player?.pause()
                }
            }
        }
        scope.launch {
            // 实时会话切换输出设备时，Gallery 同步跟随（docs/android_audio_output_devices.md §6）
            playbackController.preferredOutputDeviceId.collect {
                if (player != null) applyPreferredDevice()
            }
        }
    }

    override fun toggle(key: String, url: String) {
        val p = ensurePlayer()
        when (val action = decideToggle(_state.value, key, url)) {
            is ToggleAction.Start -> {
                if (_state.value.key != action.key) {
                    p.setMediaItem(MediaItem.fromUri(action.url))
                } else {
                    p.seekTo(0)   // 同 key（error 重试）：清错误 + 回到起点
                }
                applyPreferredDevice()
                // 必须 prepare()：onPlayerError 后播放器停在 STATE_IDLE，只调 play() 不会重播
                //（已 prepare 时为 no-op，见 §4.6 重试语义）
                p.prepare()
                p.play()
                // durationMs 从 0 起（ExoPlayer 就绪后回填）；UI 侧以段 duration_s 兜底显示
                _state.value = AudioPlayer.State(key = action.key, isBuffering = true)
            }
            ToggleAction.Pause -> p.pause()
            ToggleAction.Resume -> p.play()
        }
    }

    override fun seekTo(ms: Long) {
        val target = ms.coerceAtLeast(0L)
        player?.seekTo(target)
        _state.update { it.copy(positionMs = target) }
    }

    override fun stop() {
        stopTicker()
        player?.let {
            it.stop()
            it.release()
        }
        player = null
        _state.value = AudioPlayer.State()
    }

    // ── 内部 ──────────────────────────────────────────────────────────

    /** 把实时会话当前设备钉到 Gallery AudioTrack；null = 系统默认。 */
    private fun applyPreferredDevice() {
        val sink = renderersFactory.audioSink
        if (sink == null) {
            Log.w(TAG, "gallery audio sink not ready; skip preferred device")
            return
        }
        runCatching { sink.setPreferredDevice(playbackController.currentOutputDevice()) }
            .onFailure { Log.w(TAG, "setPreferredDevice failed", it) }
    }

    private fun ensurePlayer(): ExoPlayer = player ?: ExoPlayer.Builder(context)
        .setRenderersFactory(renderersFactory)
        .setMediaSourceFactory(DefaultMediaSourceFactory(OkHttpDataSource.Factory(client)))
        .build()
        .apply {
            setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_SPEECH)
                    .build(),
                /* handleAudioFocus = */ true,
            )
            addListener(listener)
        }
        .also { player = it }

    /** 播放中每 200ms 回写位置/时长（media3 无位置回调）。 */
    private fun startTicker() {
        if (ticker?.isActive == true) return
        ticker = scope.launch {
            while (true) {
                val p = player ?: return@launch
                _state.update {
                    it.copy(
                        positionMs = p.currentPosition,
                        durationMs = positiveOr(p.duration, it.durationMs),
                    )
                }
                delay(POSITION_TICK_MS)
            }
        }
    }

    private fun stopTicker() {
        ticker?.cancel()
        ticker = null
    }

    /** ExoPlayer 未就绪时 duration = C.TIME_UNSET（负值）/ 位置可为 0 → 回落已知值。 */
    private fun positiveOr(value: Long?, fallback: Long): Long =
        if (value != null && value > 0) value else fallback

    @androidx.annotation.OptIn(UnstableApi::class)
    private class GalleryRenderersFactory(context: Context) : DefaultRenderersFactory(context) {
        var audioSink: DefaultAudioSink? = null
            private set

        override fun buildAudioSink(
            context: Context,
            enableFloatOutput: Boolean,
            enableAudioTrackPlaybackParams: Boolean,
        ): AudioSink? = super
            .buildAudioSink(context, enableFloatOutput, enableAudioTrackPlaybackParams)
            ?.also { audioSink = it as? DefaultAudioSink }
    }

    private companion object {
        const val TAG = "LivaChat-AudioPlayer"
        const val POSITION_TICK_MS = 200L
    }
}
