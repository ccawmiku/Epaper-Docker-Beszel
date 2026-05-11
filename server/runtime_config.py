from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parent / "runtime_config.json"
_lock = Lock()


@dataclass
class RuntimeConfig:
    display_interval_seconds: int = 60
    chart_minutes: int = 1440
    force_refresh_seq: int = 0
    updated_at: str = ""


def load_runtime_config() -> RuntimeConfig:
    with _lock:
        return _load_unlocked()


def update_runtime_config(patch: dict[str, Any]) -> RuntimeConfig:
    with _lock:
        config = _load_unlocked()
        if "display_interval_seconds" in patch:
            config.display_interval_seconds = _bounded_int(patch["display_interval_seconds"], 30, 3600, 60)
        if "chart_minutes" in patch:
            config.chart_minutes = _bounded_int(patch["chart_minutes"], 60, 1440, 1440)
        config.updated_at = datetime.now(timezone.utc).isoformat()
        _save_unlocked(config)
        return config


def bump_force_refresh() -> RuntimeConfig:
    with _lock:
        config = _load_unlocked()
        config.force_refresh_seq += 1
        config.updated_at = datetime.now(timezone.utc).isoformat()
        _save_unlocked(config)
        return config


def _load_unlocked() -> RuntimeConfig:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    else:
        data = {}
    return RuntimeConfig(
        display_interval_seconds=_bounded_int(data.get("display_interval_seconds"), 30, 3600, 60),
        chart_minutes=_bounded_int(data.get("chart_minutes"), 60, 1440, 1440),
        force_refresh_seq=_bounded_int(data.get("force_refresh_seq"), 0, 1000000000, 0),
        updated_at=str(data.get("updated_at") or ""),
    )


def _save_unlocked(config: RuntimeConfig) -> None:
    CONFIG_PATH.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")


def _bounded_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))
