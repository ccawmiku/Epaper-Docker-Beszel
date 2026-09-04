from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CONFIG_PATH = Path(__file__).resolve().parent / "runtime_config.json"
_lock = Lock()


@dataclass
class RuntimeConfig:
    display_interval_seconds: int = 1200
    chart_minutes: int = 1440
    system_modes: dict[str, str] | None = None
    font_name: str = "pixel"
    font_size: int = 0
    timezone: str = "Asia/Shanghai"
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
        if "system_modes" in patch:
            config.system_modes = _clean_modes(patch["system_modes"])
        if "font_name" in patch:
            config.font_name = _clean_font_name(patch["font_name"])
        if "font_size" in patch:
            config.font_size = _bounded_int(patch["font_size"], 0, 2, 0)
        if "timezone" in patch:
            config.timezone = _clean_timezone(patch["timezone"])
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
    raw_font_size = data.get("font_size")
    font_size_val = 0 if (raw_font_size is None or raw_font_size == -1) else _bounded_int(raw_font_size, 0, 2, 0)
    return RuntimeConfig(
        display_interval_seconds=_bounded_int(data.get("display_interval_seconds"), 30, 86400, 1200),
        chart_minutes=_bounded_int(data.get("chart_minutes"), 60, 1440, 1440),
        system_modes=_clean_modes(data.get("system_modes")),
        font_name=_clean_font_name(data.get("font_name")),
        font_size=font_size_val,
        timezone=_clean_timezone(data.get("timezone")),
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


def _clean_modes(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, mode in value.items():
        if not key:
            continue
        m = str(mode).lower()
        if m in {"disabled", "off", "none", "false"}:
            out[str(key)] = "disabled"
        elif m in {"invert", "inverted"}:
            out[str(key)] = "invert"
        else:
            out[str(key)] = "normal"
    return out


def _clean_font_name(value: Any) -> str:
    text = str(value or "pixel").lower()
    return text if text in {"pixel", "narrow", "wide", "bold"} else "pixel"


def _clean_timezone(value: Any) -> str:
    text = str(value or "Asia/Shanghai").strip()
    try:
        ZoneInfo(text)
    except ZoneInfoNotFoundError:
        return "Asia/Shanghai"
    return text
