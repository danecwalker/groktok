"""Minimal persisted state for token-based weekly usage."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .auth import grok_home

CONFIG_NAME = "groktok.json"


@dataclass
class TokenMeterState:
    """Saved capacity for local-token weekly usage (with mid-week zeros)."""

    week_start: str  # ISO — billing week this capacity applies to
    capacity_tokens: int
    zeros: int
    model_filter: Optional[str] = None
    updated_at: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["TokenMeterState"]:
        try:
            cap = int(data["capacity_tokens"])
            zeros = int(data.get("zeros") or 0)
            if cap <= 0 or zeros < 0:
                return None
            return cls(
                week_start=str(data["week_start"]),
                capacity_tokens=cap,
                zeros=zeros,
                model_filter=data.get("model_filter"),
                updated_at=data.get("updated_at"),
            )
        except (KeyError, TypeError, ValueError):
            return None


def config_path() -> Path:
    return grok_home() / CONFIG_NAME


def load_meter_state() -> Optional[TokenMeterState]:
    path = config_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("token_meter")
    if not isinstance(raw, dict):
        # Back-compat: allow flat file
        raw = data
    return TokenMeterState.from_dict(raw)


def save_meter_state(state: TokenMeterState) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = datetime.now(timezone.utc).isoformat()
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing["token_meter"] = state.as_dict()
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return path
