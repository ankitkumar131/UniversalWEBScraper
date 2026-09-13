"""Global configuration: config/default.yaml + environment overrides."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

_ENV_MAP = {
    # env var -> (section, key)
    "DATABASE_URL": ("storage", "database_url"),
    "REDIS_URL": ("queue", "redis_url"),
    "RESPECT_ROBOTS": ("politeness", "respect_robots"),
    "IGNORE_HTTPS_ERRORS": ("engine", "ignore_https_errors"),
    "PROXY_ENABLED": ("proxy", "enabled"),
    "PROXY_LIST_FILE": ("proxy", "list_file"),
    "PROXY_LIST": ("proxy", "list"),
    "CAPTCHA_PROVIDER": ("captcha", "provider"),
    "LLM_PROVIDER": ("llm", "provider"),
    "OPENAI_BASE_URL": ("llm", "base_url"),
    "LLM_MODEL": ("llm", "model"),
    "LOG_LEVEL": ("monitoring", "log_level"),
    "HOST": ("app", "host"),
    "PORT": ("app", "port"),
}

_BOOL = {"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False}


def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _coerce(raw: str) -> Any:
    if raw.lower() in _BOOL:
        return _BOOL[raw.lower()]
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


class Settings:
    """Typed accessor over the merged YAML/env configuration."""

    def __init__(self, data: dict[str, Any], repo_root: Path = REPO_ROOT):
        self._data = data
        self.repo_root = repo_root

    # -- generic accessors -------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def data_dir(self) -> Path:
        d = Path(self.get("app.data_dir", "data"))
        return d if d.is_absolute() else self.repo_root / d

    @property
    def artifacts_dir(self) -> Path:
        d = Path(self.get("storage.artifacts_dir", "data/artifacts"))
        return d if d.is_absolute() else self.repo_root / d

    @property
    def database_url(self) -> str:
        url = self.get("storage.database_url")
        if url:
            return url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'scraper.db').as_posix()}"

    @property
    def respect_robots(self) -> bool:
        return bool(self.get("politeness.respect_robots", True))

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


def _load_yaml_configs() -> dict:
    """default.yaml, then default-<SCRAPE_ENV>.yaml override if present."""
    data: dict = {}
    main = CONFIG_DIR / "default.yaml"
    if main.exists():
        data = yaml.safe_load(main.read_text()) or {}
    env_name = os.environ.get("SCRAPE_ENV")
    if env_name:
        override = CONFIG_DIR / f"{env_name}.yaml"
        if override.exists():
            _deep_merge(data, yaml.safe_load(override.read_text()) or {})
    return data


def load_settings() -> Settings:
    data = _load_yaml_configs()
    for env, (section, key) in _ENV_MAP.items():
        raw = os.environ.get(env)
        if raw is None or raw == "":
            continue
        value = _coerce(raw)
        if env == "PROXY_LIST":
            value = [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]
        data.setdefault(section, {})[key] = value
    return Settings(data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def load_yaml(name: str) -> dict:
    """Load a knowledge-base YAML from config/ (known_popups, user_agents, ...)."""
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}
