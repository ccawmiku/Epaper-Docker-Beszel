from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name))
    except ValueError:
        return default


def _csv_env(name: str) -> list[str]:
    return [part.strip() for part in _env(name).split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    beszel_base_url: str = _env("BESZEL_BASE_URL", "http://192.168.1.20:8090")
    beszel_email: str = _env("BESZEL_EMAIL")
    beszel_password: str = _env("BESZEL_PASSWORD")
    beszel_history_minutes: int = _env_int("BESZEL_HISTORY_MINUTES", 30)
    beszel_record_type: str = _env("BESZEL_RECORD_TYPE", "1m")
    display_chart_minutes: int = _env_int("DISPLAY_CHART_MINUTES", 1440)
    app_host: str = _env("APP_HOST", "0.0.0.0")
    app_port: int = _env_int("APP_PORT", 15001)

    def __post_init__(self) -> None:
        object.__setattr__(self, "beszel_base_url", self.beszel_base_url.rstrip("/"))
        object.__setattr__(self, "beszel_system_names", _csv_env("BESZEL_SYSTEM_NAMES"))
        object.__setattr__(self, "beszel_system_ids", _csv_env("BESZEL_SYSTEM_IDS"))


settings = Settings()
