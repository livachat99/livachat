from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Leaf config models (BaseModel — no env binding at this level)
# ---------------------------------------------------------------------------


class ServerSslConfig(BaseModel):
    enabled: bool = False
    certfile: str = ""
    keyfile: str = ""
    keyfile_password: str | None = None
    cafile: str | None = None
    cert_reqs: Literal["none", "optional", "required"] = "none"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 1
    max_frame_bytes: int = 2_097_152  # 2MB
    log_level: str = "INFO"
    ssl: ServerSslConfig = ServerSslConfig()


class AuthConfig(BaseModel):
    mode: str = "dev"  # dev | jwt
    dev_token: str = ""
    jwt_algorithm: str = "HS256"
    jwt_secret: str = ""


class SessionConfig(BaseModel):
    resume_ttl_s: int = 30
    heartbeat_interval_s: int = 15
    heartbeat_timeout_s: int = 45
    working_memory_turns: int = 10
    max_video_frames: int = 1


class MediaAudioConfig(BaseModel):
    default_sample_rate: int = 16000
    opus_frame_ms: int = 20
    opus_bitrate: int = 32000
    channels: int = 1


class MediaVideoConfig(BaseModel):
    max_queue_frames: int = 3
    decode: str = "pillow"  # pillow | turbojpeg


class MediaConfig(BaseModel):
    audio: MediaAudioConfig = MediaAudioConfig()
    video: MediaVideoConfig = MediaVideoConfig()


# ---------------------------------------------------------------------------
# Provider configs
# ---------------------------------------------------------------------------


class AsrProviderConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    params: dict[str, Any] | None = None
    workspace_id: str | None = None


class AsrConfig(BaseModel):
    active: str = ""
    fallback: list[str] = []
    providers: dict[str, AsrProviderConfig] = {}


class LlmProviderConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    vision_model: str | None = None
    supports_vision: bool = False
    extra_body: dict[str, object] | None = None
    workspace_id: str | None = None


class LlmGenerationConfig(BaseModel):
    temperature: float = 0.8
    max_tokens: int = 1024
    top_p: float = 0.9


class LlmConfig(BaseModel):
    active: str = ""
    fallback: list[str] = []
    system_prompt_path: str = "configs/dev/prompts/companion.txt"
    translate_prompt_path: str = "configs/dev/prompts/translator.txt"
    generation: LlmGenerationConfig = LlmGenerationConfig()
    providers: dict[str, LlmProviderConfig] = {}


class TtsProviderConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    voice: str | None = None
    output_format: dict[str, Any] | str | None = None
    version: str | None = None
    workspace_id: str | None = None


class TtsConfig(BaseModel):
    active: str = ""
    fallback: list[str] = []
    providers: dict[str, TtsProviderConfig] = {}


class PostgresConfig(BaseModel):
    dsn: str = ""
    pool_min: int = 2
    pool_max: int = 20


class RedisConfig(BaseModel):
    """DEPRECATED（chatserver_no_redis.md §6.4）：Redis 已去接线，字段仅保留
    yaml 兼容（存量 server.yaml 校验不报错）；生产路径不再读取。"""

    url: str = ""
    key_prefix: str = "livachat"


class SqliteConfig(BaseModel):
    """顶层 SQLite 配置（lvdata 底座；agent_jobs 直写，lvdata_mcp_service_v2.md §6.2.0）。

    chat_server_db_path：chat server 唯一 DB（devices/messages/recording_sessions
    三表，chatserver_sqlite_store.md §3.1）；空 = 启动 fail-fast 报错退出。
    """

    enabled: bool = True        # 总开关；lvdata_db_path 未配时自动降级（不创建 writer）
    # lvdata SQLite 路径（通常 <lvdata_root>/lvdata_inventory.db）；空 = 不启用
    lvdata_db_path: str = ""
    # chat server SQLite 路径（必填，fail-fast）；空 = 启动报错退出
    chat_server_db_path: str = ""
    # JobLvdataWriter 队列上限（D4 背压；gt=0 必须：Queue(maxsize<=0) 为无界队列）
    max_queue_size: int = Field(default=1024, gt=0)


class OmniProviderConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    voice: str | None = None
    supports_vision: bool = False
    modalities: list[str] = ["text", "audio"]
    extra_body: dict[str, object] | None = None
    # 仅 alibaba_omni（DashScope）使用：workspace_id 只驱动 X-DashScope-WorkSpace 头，
    # 与 workspace 专属域名两值配套、互不派生（与 asr/tts/llm/translate 同一约定）
    workspace_id: str | None = None


class OmniConfig(BaseModel):
    enabled: bool = False
    active: str = ""
    fallback: list[str] = []
    system_prompt_path: str = "configs/dev/prompts/companion.txt"
    generation: LlmGenerationConfig = LlmGenerationConfig()
    providers: dict[str, OmniProviderConfig] = {}


class AvDetectorProviderConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    # 仅 alibaba_avdetector（DashScope realtime 族）使用：workspace_id 只驱动
    # X-DashScope-WorkSpace 头（与 asr/tts/llm/omni 同一约定）
    workspace_id: str | None = None


class AvDetectorConfig(BaseModel):
    """音视频主动监测（avdetector，docs/fd_pipeline_avdetector.md §4 D4）。

    enabled=false → 服务不创建、代码路径全不激活（与现状完全一致）。
    节流/冷却等默认值为该文档 [R3]/[R13]/[R16]/[R17] 拍板值。
    """

    enabled: bool = False
    active: str = ""
    fallback: list[str] = []
    system_prompt_path: str = "configs/dev/prompts/avdetector.txt"
    # [R6] 拍板：低温度 + 短回复（检测回复极短，约束成本与随机性）
    generation: LlmGenerationConfig = LlmGenerationConfig(temperature=0.2, max_tokens=128)
    check_timeout_s: float = 8.0          # 单次检测查询超时
    audio_commit_interval_s: float = 15.0 # 静默 commit 间隔（窗口纪律，D5 ★）
    check_min_interval_s: float = 10.0    # 全局最小检测间隔（[R13] 通道调度）
    video_check_interval_s: float = 10.0  # 视频检测节流（[R17] 拍板 30s→10s）
    image_disable_cooldown_s: float = 300.0  # 拒图停图时限（[P6.14] 拍板：到期恢复）
    proactive_cooldown_s: float = 30.0    # 主动发言冷却（[R3]；危险事件豁免 [R15]）
    visual_turn_suppress_s: float = 5.0   # cam 首帧回合结束后的普通画面抑制窗（D8；危险豁免）
    reopen_interval_s: float = 600.0      # 检测流周期重建（历史限额兜底）
    providers: dict[str, AvDetectorProviderConfig] = {}


class LogFileConfig(BaseModel):
    """日志文件轮转（docs/server_logging.md）：进程内 RotatingFileHandler。"""

    enabled: bool = True
    dir: str = ""                                             # 空 = <project_root>/logs（resolve_path 语义）
    max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)    # 单文件上限 50MB
    backup_count: int = Field(default=3, ge=1)                # 保留 .1~.N


class ObservabilityConfig(BaseModel):
    metrics_enabled: bool = True
    tracing_enabled: bool = False
    json_logs: bool = True
    log_file: LogFileConfig = LogFileConfig()


# ---------------------------------------------------------------------------
# Translate config (pipeline lecture mode)
# ---------------------------------------------------------------------------


class TranslateConfig(BaseModel):
    """Pipeline-mode live translate engine (alibaba_livetranslate) config.

    Top-level config section, peer to asr / llm / tts / omni.
    Currently only supports lecture usecase; extensible to dialogue providers later.
    """
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    voice: str | None = None
    workspace_id: str | None = None
    # Source/target language defaults; overridden at runtime by
    # session.change's my_language / local_language.
    source_language: str = "en"
    target_language: str = "en"
    corpus_phrases: dict[str, str] | None = None
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    enable_voice_clone: bool = False
    voice_clone_frequency: str | None = None


# ---------------------------------------------------------------------------
# Recording config (Phase 1 — side-car recording to local FS)
# ---------------------------------------------------------------------------


class RecordingConfig(BaseModel):
    enabled: bool = False
    base_dir: str = "/opt/lvdata"
    audio_segment_s: int = 60
    queue_maxsize: int = 2048
    chat_flush_interval_s: float = 5.0
    chat_flush_lines: int = 20
    fsync_interval_s: float = 5.0
    meta_flush_interval_s: float = 60.0  # ticker 周期（跨天 finalize/idle TTL）；session.json 已静态化
    idle_session_ttl_s: int = 3600


class BffConfig(BaseModel):
    """MyLife BFF query layer (read-only lvdata facade)."""

    enabled: bool = True
    data_root: str = ""                      # 默认回退 recording.base_dir（/opt/lvdata）
    lvdata_db_path: str = ""                 # 默认 <data_root>/lvdata_inventory.db
    media_cache_dir: str = ""                # 音频转码缓存，默认 <data_root>/_cache
    device_uuids: list[str] = []             # 数据范围白名单（服务端 scope）；空 = 全部
    timezone: str = "Asia/Shanghai"          # 本地日分组默认时区
    # remove 执行面（docs/bff_service_remove.md）：BFF → lvdata-mcp REST；
    # url 空 = 端点禁用（503 removal_disabled，安全默认）
    lvdata_api_url: str = ""
    lvdata_api_token: str = ""
    lvdata_api_timeout_s: float = 30.0


class WebrootConfig(BaseModel):
    """Agent 产出 Web 页面的静态托管（docs/webroot_feature.md §3.1）。

    enabled 默认关：dev yaml 显式开启；生产部署需显式开启（默认态功能整体不激活）。
    root 空串回退 <recording.base_dir>/webroot（/opt/lvdata/webroot）。"""

    enabled: bool = False
    root: str = ""


# ---------------------------------------------------------------------------
# Root config (BaseSettings — reads env vars)
# ---------------------------------------------------------------------------


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_prefix="LIVACHAT__",
        env_ignore_empty=True,
    )

    server: ServerConfig = ServerConfig()
    auth: AuthConfig = AuthConfig()
    session: SessionConfig = SessionConfig()
    media: MediaConfig = MediaConfig()
    asr: AsrConfig = AsrConfig()
    llm: LlmConfig = LlmConfig()
    tts: TtsConfig = TtsConfig()
    omni: OmniConfig = OmniConfig()
    avdetector: AvDetectorConfig = AvDetectorConfig()
    translate: TranslateConfig = TranslateConfig()
    postgres: PostgresConfig = PostgresConfig()
    redis: RedisConfig = RedisConfig()
    sqlite: SqliteConfig = SqliteConfig()
    observability: ObservabilityConfig = ObservabilityConfig()
    recording: RecordingConfig = RecordingConfig()
    bff: BffConfig = BffConfig()
    webroot: WebrootConfig = WebrootConfig()
    agent_relay: dict | None = None
