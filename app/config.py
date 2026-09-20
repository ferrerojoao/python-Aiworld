from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_DOTENV_CACHE: dict[str, str] | None = None


def _load_dotenv() -> dict[str, str]:
    global _DOTENV_CACHE
    if _DOTENV_CACHE is None:
        data: dict[str, str] = {}
        path = Path(__file__).resolve().parent.parent / ".env"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip().strip('"').strip("'")
        _DOTENV_CACHE = data
    return _DOTENV_CACHE


def _env(name: str, default: str = "") -> str:
    value = os.getenv(f"AIWORLD_{name}")
    if value is not None:
        return value
    return _load_dotenv().get(f"AIWORLD_{name}", default)


def _env_int(name: str, default: int) -> int:
    raw = _env(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class InjectionLimits:
    """注入上限（2026-09-16 从世界配置搬到系统设置，三处共用同一份）。

    口径与直觉一致：**0 = 不限制（给全部）**。唯一的例外是 ``event_log_full``
    ——"最近几条给正文原文"，最少 1；0 条原文等于让编剧无从续写。

    为什么搬到系统设置：这些是**玩家的阅读与成本偏好**，不是世界设定。写在
    world.json 里意味着换个世界就得重配一遍，而"嫌 NPC 记性太差"是跨世界的
    诉求（2026-09-16 用户实测反馈：缓存命中后 token 没那么疼，条数才是瓶颈）。
    """

    event_log_limit: int = 10  # 事件日志取最近几条（0 = 全部）
    event_log_full: int = 3  # 其中最近几条给正文原文（>= 1）
    known_set_limit: int = 5  # 每个 NPC 已知集条数（0 = 全部）


# 装配层的兜底默认值：探针与单测直接调装配函数时用；生产路径一律传
# ``Settings.limits()``，保证"设置改了就真生效"。
DEFAULT_LIMITS = InjectionLimits()


# 用**辅助模型**出口的角色（2026-09-20）。这是"谁是辅助"的唯一事实源：
# ``resolved_model`` 与 ``endpoint_for`` 都读它，别在别处再写一份名单——
# 两处名单一旦漂了，就会出现"模型名取自辅助、出口却走主"这种半配置。
# 落卡草稿（landing）也算辅助活：从已采纳正文里抽字段，不需要主模型的笔力。
AUX_WORKERS = frozenset({"qc", "audit", "classify", "landing"})


@dataclass
class Settings:
    """Runtime configuration from AIWORLD_* environment variables."""

    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8765))
    auth_token: str = field(default_factory=lambda: _env("AUTH_TOKEN", ""))
    require_auth_for_non_local: bool = field(
        default_factory=lambda: _env_bool("REQUIRE_AUTH_FOR_NON_LOCAL", True)
    )

    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "http://127.0.0.1:11434/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", "ollama"))
    # 辅助模型的**独立出口**（2026-09-20）：留空 = 跟随上面那一套。
    # 两侧各有一套 base_url / key 才谈得上"两个模型分开配"——常见配法是
    # 主模型走付费 API、辅助模型走本地 ollama（或反过来：便宜的本地模型写正文、
    # 强模型只做质检）。留空即回落，是为了不配也能照旧跑（单出口零变化）。
    cheap_base_url: str = field(default_factory=lambda: _env("CHEAP_BASE_URL", ""))
    cheap_api_key: str = field(default_factory=lambda: _env("CHEAP_API_KEY", ""))
    llm_timeout_seconds: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_SECONDS", "90") or 90))
    model_main: str = field(default_factory=lambda: _env("MODEL_MAIN", "qwen2.5:7b"))
    model_cheap: str = field(default_factory=lambda: _env("MODEL_CHEAP", "qwen2.5:7b"))

    model_director: str | None = field(default_factory=lambda: _env("MODEL_DIRECTOR") or None)
    model_actor: str | None = field(default_factory=lambda: _env("MODEL_ACTOR") or None)
    model_story: str | None = field(default_factory=lambda: _env("MODEL_STORY") or None)
    model_qc: str | None = field(default_factory=lambda: _env("MODEL_QC") or None)
    model_audit: str | None = field(default_factory=lambda: _env("MODEL_AUDIT") or None)
    model_classify: str | None = field(default_factory=lambda: _env("MODEL_CLASSIFY") or None)

    temp_qc: float = field(default_factory=lambda: float(_env("TEMP_QC", "0.2") or 0.2))
    # 编剧（writer）采样温度：创作要发散，默认 1.0（2026-09-12 用户定）。
    temp_writer: float = field(default_factory=lambda: float(_env("TEMP_WRITER", "1.0") or 1.0))
    ctx_memory_budget: int = field(default_factory=lambda: _env_int("CTX_MEMORY_BUDGET", 4000))
    ctx_window_turns: int = field(default_factory=lambda: _env_int("CTX_WINDOW_TURNS", 20))
    default_duration_fallback_min: int = field(
        default_factory=lambda: _env_int("DEFAULT_DURATION_FALLBACK_MIN", 10)
    )
    candidate_ttl_days: int = field(default_factory=lambda: _env_int("CANDIDATE_TTL_DAYS", 7))
    audit_enabled: bool = field(default_factory=lambda: _env_bool("AUDIT_ENABLED", True))
    qc_enabled: bool = field(default_factory=lambda: _env_bool("QC_ENABLED", True))

    # 注入上限（系统设置，2026-09-16）：编剧 / QC / Actor 三处共用。0 = 给全部。
    # 这里只存原始值，clamp 统一在 limits() 里做（单一事实源）。
    event_log_limit: int = field(
        default_factory=lambda: _env_int("EVENT_LOG_LIMIT", DEFAULT_LIMITS.event_log_limit)
    )
    event_log_full: int = field(
        default_factory=lambda: _env_int("EVENT_LOG_FULL", DEFAULT_LIMITS.event_log_full)
    )
    known_set_limit: int = field(
        default_factory=lambda: _env_int("KNOWN_SET_LIMIT", DEFAULT_LIMITS.known_set_limit)
    )

    reasoning_effort: str = field(
        default_factory=lambda: _env("REASONING_EFFORT", "").lower()
    )  # ""=不传(自动) | low | medium | high

    content_root: Path = field(default_factory=lambda: Path(_env("CONTENT_ROOT", "content")))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))

    def resolved_model(self, worker: str) -> str:
        override = getattr(self, f"model_{worker}", None)
        if override:
            return override
        if worker in AUX_WORKERS:
            return self.model_cheap
        return self.model_main

    def uses_aux_endpoint(self, worker: str) -> bool:
        """该角色是否走**辅助出口**。辅助侧没配 base_url 时一律走主出口。"""
        return worker in AUX_WORKERS and bool(self.cheap_base_url)

    def endpoint_for(self, worker: str) -> tuple[str, str]:
        """worker → (base_url, api_key)。一处判定"该敲哪个门"。

        base_url 与 api_key **成对**回落，不做各自回落：只填 base_url 不填 key
        的用法不存在，分开回落只会造出"新网关 + 旧 key"这种半配置，而且它
        静默生效、报错还长得像服务端鉴权问题。
        """
        if self.uses_aux_endpoint(worker):
            return self.cheap_base_url, self.cheap_api_key
        return self.llm_base_url, self.llm_api_key

    def endpoint_label(self, worker: str) -> str:
        """给调试卡片用的出口标签，形如 ``辅 · 127.0.0.1:11434``。

        只露主机（**绝不含 key**）：调试面板会把这条显示出来，整条 base_url
        带过去也还行，但 key 一旦被拼进去就是明面上的泄露。
        """
        base_url, _ = self.endpoint_for(worker)
        kind = "辅" if self.uses_aux_endpoint(worker) else "主"
        host = base_url.split("://")[-1].split("/")[0] or base_url
        return f"{kind} · {host}"

    def limits(self) -> InjectionLimits:
        """注入上限快照。clamp 只在这一处收口：负数一律当 0（不限制），
        正文条数最少 1——UI 与 env 都可能塞进非法值，装配层不该自己防御。"""
        return InjectionLimits(
            event_log_limit=max(0, self.event_log_limit),
            event_log_full=max(1, self.event_log_full),
            known_set_limit=max(0, self.known_set_limit),
        )

    def check_host_security(self) -> None:
        """Refuse to start on a non-loopback address without a token."""
        if self.require_auth_for_non_local and not self.auth_token:
            host = self.host.lower()
            local = host in {"127.0.0.1", "localhost", "::1"}
            if not local:
                raise RuntimeError(
                    "AIWORLD_HOST is not loopback and AIWORLD_AUTH_TOKEN is empty. "
                    "Set a token or keep the server on 127.0.0.1."
                )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.check_host_security()
    return settings


# UI 可改且需要跨重启保留的设置项（PUT /api/settings 落盘，启动时覆盖 env 值）。
# 注意含 llm_api_key / cheap_api_key：settings.json 必须在 .gitignore 里。
SETTINGS_OVERRIDE_FIELDS = (
    "llm_base_url",
    "llm_api_key",
    "cheap_base_url",
    "cheap_api_key",
    "model_main",
    "model_cheap",
    "reasoning_effort",
    "qc_enabled",
    # 注入上限（2026-09-16 从世界配置搬来）
    "event_log_limit",
    "event_log_full",
    "known_set_limit",
)


def load_settings_overrides(path: str | Path) -> dict:
    """Read UI-persisted settings overrides. Missing/corrupt file = no overrides."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in SETTINGS_OVERRIDE_FIELDS if k in raw}


def apply_settings_overrides(settings: Settings, overrides: dict) -> None:
    """Apply persisted overrides in place (env values stay as fallback)."""
    for key, value in overrides.items():
        setattr(settings, key, value)


def save_settings_overrides(path: str | Path, settings: Settings) -> None:
    """Persist the UI-editable settings snapshot (PUT /settings writes here)."""
    from app.core.store import write_json_atomic

    data = {k: getattr(settings, k) for k in SETTINGS_OVERRIDE_FIELDS}
    write_json_atomic(Path(path), data)