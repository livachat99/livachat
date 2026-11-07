from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Adapter configs
# ---------------------------------------------------------------------------


class HermesAdapterConfig(BaseModel):
    base_url: str = "http://localhost:8080"
    api_key: str = ""
    timeout_s: int = Field(default=30, gt=0)
    max_retries: int = 2

    # Agent 昵称：① hermes prompts 自称（mission/background 注入）② LivaChat 认知声明里的称呼
    # （docs/agent_relay_prompts.md §8；多 proactive adapter 时按启用集合进入声明）
    # ③ chatlog 行 agent_id provenance（recording_data_process §2.5）；
    # 空 = 未配置（prompts 段落 / LivaChat 声明整条不注入）
    nickname: str = ""

    # -- v2 proactive mode ---------------------------------------------------
    mode: Literal["reactive", "proactive", "both"] = "proactive"
    nudge_timeout_s: int = 60                   # main-agent lightweight turn timeout
    # chatlog watcher 旋钮已上移 ProactiveConfig（D11）；以下 5 键为 deprecated
    # alias，仅在显式设置且 proactive 未显式设置时迁移（_migrate_watcher_params）
    nudge_coalesce_s: float = 2.0               # deprecated → proactive.nudge_coalesce_s
    nudge_min_interval_s: float = 5.0           # deprecated → proactive.nudge_min_interval_s
    nudge_max_lines: int = 20                   # deprecated → proactive.nudge_max_lines
    nudge_max_bytes: int = 4096                 # deprecated → proactive.nudge_max_bytes
    chatlog_poll_interval_s: float = 3.0        # deprecated → proactive.chatlog_poll_interval_s
    # §5.8: per-device in-flight hard cap（v2 语义）；旧键为 deprecated alias
    max_concurrent_jobs_per_device: int = 10
    max_concurrent_jobs_per_session: int = 10   # deprecated alias
    max_in_flight_jobs: int = 20                # global in-flight hard cap
    # v2: running 超时与 JobStore 48h 结果窗口对齐（R2）
    background_timeout_s: int = 172800          # watchdog: running timeout (48h)
    watchdog_interval_s: int = 60               # watchdog scan interval
    job_requested_timeout_s: int = 60           # requested→running reconciliation
    notify_job_started: bool = True             # immediately tell user a job started
    monitor_prompt_path: str = ""               # empty = built-in default template
    reactive_max_wait_s: int = 30               # optional (P3): reactive→background

    # -- v3 job session carrier (hermes_adaptor_design_v3.md) ------------------
    # prompt.background carrier removed: every Hermes job runs in its own
    # dedicated session.
    job_cancel_kill_processes: bool = True      # cancel 时清理 process registry 跟踪的后台进程
    job_cancel_grace_s: float = 5.0             # interrupt 后等 interrupted complete 的宽限
    job_recovery_enabled: bool = True           # 启动/重连时恢复 job 会话
    # D17：resume working 后一次性复查延迟。每次重连都会重新 arm；10s 只需覆盖
    # resume 快照与 transport 重绑定之间的毫秒级缝隙，勿按 600s 周期轮询理解。
    job_resume_recheck_s: float = 10.0

    # -- v2 device connection & recovery (R1, D8/D9/D10/D11) ------------------
    startup_recovery_enabled: bool = True       # R1: startup scan JobStore, resume device conns
    recovery_concurrency: int = 10              # startup recovery bounded concurrency
    session_resume_retries: int = 3             # resume retry count (session_lost after)
    session_resume_backoff_s: float = 10.0      # resume retry base interval
    reconnect_backoff_initial_s: float = 1.0    # per-device reconnect backoff base (D11)
    reconnect_backoff_max_s: float = 30.0       # per-device reconnect backoff cap
    reconnect_jitter: float = 0.25              # reconnect jitter ratio (±25%)
    device_conn_idle_reaper_s: float = 60.0     # idle connection reaper scan period (D8)
    max_device_connections: int = 1000          # connection cap; idle LRU eviction beyond (D8)

    # -- §5.8 连接保活：应用层活性探测 (hermes_adaptor_design_v2.md §5.8) -------
    liveness_probe_interval_s: float = Field(default=30.0, ge=0)  # 保留连接空闲探测周期；0 = 关闭探测
    liveness_probe_timeout_s: float = Field(default=5.0, gt=0)    # 单次探测超时（tools.list）
    liveness_probe_failures: int = Field(default=2, ge=1)  # 连续超时阈值 → 拆连重连

    # -- v2 result storage & redelivery (R2, D5/D7) ---------------------------
    max_stored_result_bytes: int = 16384        # result_text 落库截断（对齐 max_reply_bytes）
    deliver_pending_on_reconnect: bool = True   # session.started 补投开关（D7）

    # -- v2 session/connection retention (D3) ---------------------------------
    session_retention: Literal["keep", "close_on_idle"] = "keep"

    @model_validator(mode="after")
    def _apply_legacy_quota_alias(self) -> HermesAdapterConfig:
        """§5.8: 旧键 max_concurrent_jobs_per_session 为 deprecated alias ——
        仅当旧键被显式设置而新键未设置时迁移，新键优先。"""
        if (
            "max_concurrent_jobs_per_session" in self.model_fields_set
            and "max_concurrent_jobs_per_device" not in self.model_fields_set
        ):
            self.max_concurrent_jobs_per_device = self.max_concurrent_jobs_per_session
        return self


class OpenClawAdapterConfig(BaseModel):
    """OpenClaw Gateway adapter（docs/openclaw_adaptor_design.md，B 方案）。

    认证走 loopback 后端路径：`gateway-client`/`backend` + 共享 token
    （协议文档 §2.2 实测）；单条 WS 服务全部 device（D3）。
    """

    ws_url: str = "ws://127.0.0.1:18789"           # 根路径 WS（协议文档 §1.1）
    token: str = ""                                # gateway.auth.token（共享密钥）
    agent_id: str = "main"                         # 会话键中的 agentId
    mode: Literal["reactive", "proactive", "both"] = "proactive"
    # agent_id(nickname) 透传；多 proactive adapter 时进入 LivaChat 声明（§8）；空 = 不注入称呼段
    nickname: str = ""

    # -- timeouts / reconnect -------------------------------------------------
    request_timeout_s: int = Field(default=30, gt=0)    # 单条 RPC 超时
    ask_timeout_s: int = Field(default=60, gt=0)        # reactive turn 超时
    nudge_timeout_s: int = Field(default=60, gt=0)      # 监听 turn 超时（hermes 对齐）
    connect_timeout_s: int = Field(default=10, gt=0)    # 握手（challenge→hello）超时
    silence_timeout_s: float = Field(default=75.0, gt=0)  # 入站静默判死（2×tick(30s)+余量）
    reconnect_backoff_initial_s: float = 1.0
    reconnect_backoff_max_s: float = 30.0
    reconnect_jitter: float = 0.25

    # -- jobs（语义与 hermes 同名键对齐）-------------------------------------
    max_concurrent_jobs_per_device: int = 10
    max_in_flight_jobs: int = 20
    background_timeout_s: int = 172800              # 48h，与 JobStore TTL 对齐（R2）
    watchdog_interval_s: int = 60
    job_requested_timeout_s: int = 60
    notify_job_started: bool = True
    max_stored_result_bytes: int = 16384            # 落库截断（对齐 max_reply_bytes）
    startup_recovery_enabled: bool = True           # 启动对账（§4.4.5）
    recovery_concurrency: int = 10
    job_parent_link: bool = True                    # job 会话挂 parentSessionKey/spawnDepth 谱系
    job_cancel_grace_s: float = 5.0                 # abort 后等 aborted 终态事件的宽限
    monitor_prompt_path: str = ""                   # 契约块模板覆盖（空 = 内置）
    task_prompt_path: str = ""                      # 任务书模板覆盖

    # -- 审批兜底（§4.5）；False = 不应答 ------------------------------------
    approvals_autorespond: bool = True
    question_autorespond: bool = True


class AdaptersConfig(BaseModel):
    hermes: HermesAdapterConfig = HermesAdapterConfig()
    openclaw: OpenClawAdapterConfig = OpenClawAdapterConfig()


# ---------------------------------------------------------------------------
# Proactive config
# ---------------------------------------------------------------------------


class ProactiveConfig(BaseModel):
    enabled: bool = True
    min_segment_duration_s: int = 10
    debounce_s: int = 30
    # chatlog watcher 旋钮（D11：watcher 是 adapter 无关组件，参数唯一化；
    # 原 HermesAdapterConfig 同名键为 deprecated alias，见 AgentRelayConfig
    # 的 _migrate_watcher_params）
    chatlog_poll_interval_s: float = 3.0
    nudge_coalesce_s: float = 2.0
    nudge_min_interval_s: float = 5.0
    nudge_max_lines: int = 20
    nudge_max_bytes: int = 4096


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------


class AgentRelayConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_prefix="LIVACHAT__AGENT_RELAY__",
        env_ignore_empty=True,
    )

    enabled: bool = False
    # DEPRECATED（D3）：进程内总线/直接投递后失去意义；字段保留避免存量
    # yaml 校验报错，忽略不读
    events_channel: str = "livachat:agent:events"
    push_channel_prefix: str = "livachat:push"
    max_reply_bytes: int = Field(default=16384, gt=0)
    default_adapters: list[str] = ["hermes"]
    # DEPRECATED：语义已移除（relay 不再维护对话窗口）；字段保留避免存量
    # yaml 校验报错，忽略不读
    session_history_turns: int = 3
    adapter_call_timeout_s: int = Field(default=120, gt=0)

    # -- job_status 通知（fd_job_status.md §4.6）-------------------------------
    job_status_enabled: bool = True         # 总开关；False 时不创建 notifier、不挂钩子
    job_status_interval_s: float = 15.0     # 周期推送间隔（R1）
    job_status_scan_interval_s: float = 1.0  # 扫描 tick 间隔（兜底/计时粒度）

    adapters: AdaptersConfig = AdaptersConfig()
    proactive: ProactiveConfig = ProactiveConfig()

    @model_validator(mode="after")
    def _migrate_watcher_params(self) -> AgentRelayConfig:
        """D11：watcher 旋钮上移 ProactiveConfig 后，hermes 旧键为 deprecated
        alias——仅当旧键被显式设置而新键未设置时迁移，新键优先（对齐
        HermesAdapterConfig._apply_legacy_quota_alias 的先例）。"""
        hermes = self.adapters.hermes
        for key in (
            "chatlog_poll_interval_s",
            "nudge_coalesce_s",
            "nudge_min_interval_s",
            "nudge_max_lines",
            "nudge_max_bytes",
        ):
            if (
                key in hermes.model_fields_set
                and key not in self.proactive.model_fields_set
            ):
                setattr(self.proactive, key, getattr(hermes, key))
        return self
