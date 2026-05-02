"""Shared environment helpers for local smoke-check scripts."""

from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
LOCAL_DEFAULTS = {
    "DEBUG": "1",
    "SECRET_KEY": "local-smoke-not-secret",
    "FERNET_KEYS": "local-smoke-fernet-key",
    "DATABASE_ENGINE": "postgres",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "perfumex_local",
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "",
    "ALLOWED_HOSTS": "127.0.0.1,localhost",
    "CSRF_TRUSTED_ORIGINS": "https://127.0.0.1,https://localhost",
    "ASSISTANT_USE_OPENAI": "false",
}
LOCAL_DJANGO_DEFAULTS = {
    **LOCAL_DEFAULTS,
    "DJANGO_SETTINGS_MODULE": "perfumex.settings",
}
LOCAL_APPS = ("prices", "assistant_core", "assistant_linking", "catalog")


def local_env_file_values() -> dict[str, str]:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip("'\"")
    return values


def apply_defaults(defaults: dict[str, str] | None = None) -> None:
    for key, value in local_env_file_values().items():
        os.environ.setdefault(key, value)
    for key, value in (defaults or LOCAL_DEFAULTS).items():
        os.environ.setdefault(key, value)


def local_env() -> dict[str, str]:
    env = os.environ.copy()
    for key, value in local_env_file_values().items():
        env.setdefault(key, value)
    for key, value in LOCAL_DEFAULTS.items():
        env.setdefault(key, value)
    return env
