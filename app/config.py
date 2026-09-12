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
    ctx_memory_budget: int = field(default_factory=lambda: _env_int("CTX_MEMORY_BUDGET", 4000))
    ctx_window_turns: int = field(default_factory=lambda: _env_int("CTX_WINDOW_TURNS", 20))
    default_duration_fallback_min: int = field(
        default_factory=lambda: _env_int("DEFAULT_DURATION_FALLBACK_MIN", 10)
    )
    candidate_ttl_days: int = field(default_factory=lambda: _env_int("CANDIDATE_TTL_DAYS", 7))
    audit_enabled: bool = field(default_factory=lambda: _env_bool("AUDIT_ENABLED", True))
    qc_enabled: bool = field(default_factory=lambda: _env_bool("QC_ENABLED", True))
    reasoning_effort: str = field(
        default_factory=lambda: _env("REASONING_EFFORT", "").lower()
    )  # ""=不传(自动) | low | medium | high

    content_root: Path = field(default_factory=lambda: Path(_env("CONTENT_ROOT", "content")))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))

    def resolved_model(self, worker: str) -> str:
        override = getattr(self, f"model_{worker}", None)
        if override:
            return override
        if worker in {"qc", "audit", "classify"}:
            return self.model_cheap
        return self.model_main

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
# 注意含 llm_api_key：settings.json 必须在 .gitignore 里。
SETTINGS_OVERRIDE_FIELDS = (
    "llm_base_url",
    "llm_api_key",
    "model_main",
    "model_cheap",
    "reasoning_effort",
    "qc_enabled",
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