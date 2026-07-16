"""Persist optional groktok overrides (week start, pool %, etc.)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .auth import grok_home


CONFIG_NAME = "groktok.json"


@dataclass
class GroktokConfig:
    """User overrides that survive between runs."""

    week_start_override: Optional[str] = None  # ISO-8601
    week_end_override: Optional[str] = None
    pool_percent_override: Optional[float] = None
    note: Optional[str] = None
    updated_at: Optional[str] = None

    def week_start_dt(self) -> Optional[datetime]:
        return _parse_iso(self.week_start_override)

    def week_end_dt(self) -> Optional[datetime]:
        return _parse_iso(self.week_end_override)


def config_path() -> Path:
    return grok_home() / CONFIG_NAME


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_config() -> GroktokConfig:
    path = config_path()
    if not path.is_file():
        return GroktokConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return GroktokConfig()
    if not isinstance(data, dict):
        return GroktokConfig()
    return GroktokConfig(
        week_start_override=data.get("week_start_override"),
        week_end_override=data.get("week_end_override"),
        pool_percent_override=(
            float(data["pool_percent_override"])
            if data.get("pool_percent_override") is not None
            else None
        ),
        note=data.get("note"),
        updated_at=data.get("updated_at"),
    )


def save_config(cfg: GroktokConfig) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg.updated_at = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {k: v for k, v in asdict(cfg).items() if v is not None}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def clear_config() -> bool:
    path = config_path()
    if path.is_file():
        path.unlink()
        return True
    return False
