"""Persist optional groktok overrides, pool calibration, and plan price."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .auth import grok_home


CONFIG_NAME = "groktok.json"


@dataclass
class PoolCalibration:
    """
    Anchor for local-first weekly pool estimates.

    Capacity is in Build-token-equivalent units:

        capacity ≈ local_build_tokens / (invert_percent / 100)

    Later, without the billing API:

        build_pool_% ≈ 100 × tokens_since_week_start / capacity
    """

    week_start: str  # ISO-8601
    capacity_total: int
    tokens_per_percent: float
    invert_percent: float
    invert_basis: str  # build_product | overall_pool | user_override
    overall_percent_at_cal: float
    build_tokens_at_cal: int
    source: str  # api | manual | interactive
    calibrated_at: str  # ISO-8601
    confidence: str = "medium"
    week_end: Optional[str] = None
    build_share_percent: Optional[float] = None
    other_products_percent: float = 0.0
    uncached_capacity: Optional[int] = None

    def week_start_dt(self) -> Optional[datetime]:
        return _parse_iso(self.week_start)

    def week_end_dt(self) -> Optional[datetime]:
        return _parse_iso(self.week_end)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["PoolCalibration"]:
        try:
            capacity = int(data["capacity_total"])
            if capacity <= 0:
                return None
            return cls(
                week_start=str(data["week_start"]),
                capacity_total=capacity,
                tokens_per_percent=float(
                    data.get("tokens_per_percent") or (capacity / 100.0)
                ),
                invert_percent=float(data.get("invert_percent") or 0.0),
                invert_basis=str(data.get("invert_basis") or "overall_pool"),
                overall_percent_at_cal=float(
                    data.get("overall_percent_at_cal") or 0.0
                ),
                build_tokens_at_cal=int(data.get("build_tokens_at_cal") or 0),
                source=str(data.get("source") or "api"),
                calibrated_at=str(
                    data.get("calibrated_at")
                    or datetime.now(timezone.utc).isoformat()
                ),
                confidence=str(data.get("confidence") or "medium"),
                week_end=data.get("week_end"),
                build_share_percent=(
                    float(data["build_share_percent"])
                    if data.get("build_share_percent") is not None
                    else None
                ),
                other_products_percent=float(
                    data.get("other_products_percent") or 0.0
                ),
                uncached_capacity=(
                    int(data["uncached_capacity"])
                    if data.get("uncached_capacity") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class GroktokConfig:
    """User overrides and calibration that survive between runs."""

    week_start_override: Optional[str] = None  # ISO-8601
    week_end_override: Optional[str] = None
    pool_percent_override: Optional[float] = None
    note: Optional[str] = None
    updated_at: Optional[str] = None
    # Monthly SuperGrok / plan fee (USD) for amortized $/token — user-supplied.
    plan_price_usd: Optional[float] = None
    calibration: Optional[PoolCalibration] = None

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

    cal = None
    raw_cal = data.get("calibration")
    if isinstance(raw_cal, dict):
        cal = PoolCalibration.from_dict(raw_cal)

    plan = data.get("plan_price_usd")
    try:
        plan_f = float(plan) if plan is not None else None
    except (TypeError, ValueError):
        plan_f = None

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
        plan_price_usd=plan_f,
        calibration=cal,
    )


def save_config(cfg: GroktokConfig) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg.updated_at = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {}
    for k, v in asdict(cfg).items():
        if v is None:
            continue
        if k == "calibration" and isinstance(v, dict):
            # asdict already nested; drop nulls inside
            payload[k] = {ck: cv for ck, cv in v.items() if cv is not None}
        else:
            payload[k] = v
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def update_config(**fields: Any) -> GroktokConfig:
    """Merge fields into saved config and write."""
    cfg = load_config()
    for key, value in fields.items():
        if not hasattr(cfg, key):
            raise AttributeError(f"Unknown config field: {key}")
        setattr(cfg, key, value)
    save_config(cfg)
    return cfg


def clear_config() -> bool:
    path = config_path()
    if path.is_file():
        path.unlink()
        return True
    return False


def clear_calibration() -> bool:
    """Drop calibration only; keep overrides / plan price."""
    cfg = load_config()
    if cfg.calibration is None:
        return False
    cfg.calibration = None
    save_config(cfg)
    return True
