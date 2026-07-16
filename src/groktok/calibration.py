"""Pool capacity calibration and local-first usage estimates."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .billing import WeeklyUsage
from .config import PoolCalibration
from .estimate import estimate_weekly_tokens
from .local_tokens import LocalTokenReport


@dataclass
class LocalPoolEstimate:
    """Live pool usage derived from local Build tokens + saved capacity."""

    build_tokens: int
    capacity_total: int
    tokens_per_percent: float
    # % of full capacity consumed by local Build tokens (primary live signal)
    build_pool_percent: float
    # Overall pool % under constant product-mix assumption
    estimated_overall_percent: float
    remaining_build_tokens: int
    remaining_overall_tokens: int
    week_start: Optional[str]
    source: str  # calibration
    confidence: str
    invert_basis: str
    build_share_percent: Optional[float]
    other_products_percent: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "build_tokens": self.build_tokens,
            "capacity_total": self.capacity_total,
            "tokens_per_percent": round(self.tokens_per_percent, 2),
            "build_pool_percent": round(self.build_pool_percent, 3),
            "estimated_overall_percent": round(self.estimated_overall_percent, 3),
            "remaining_build_tokens": self.remaining_build_tokens,
            "remaining_overall_tokens": self.remaining_overall_tokens,
            "week_start": self.week_start,
            "source": self.source,
            "confidence": self.confidence,
            "invert_basis": self.invert_basis,
            "build_share_percent": self.build_share_percent,
            "other_products_percent": self.other_products_percent,
            "notes": list(self.notes),
        }


def calibrate_from_estimate(
    *,
    capacity_total: int,
    invert_percent: float,
    invert_basis: str,
    overall_percent: float,
    build_tokens: int,
    confidence: str,
    source: str,
    week_start: Optional[datetime],
    week_end: Optional[datetime] = None,
    build_share_percent: Optional[float] = None,
    other_products_percent: float = 0.0,
    uncached_capacity: Optional[int] = None,
) -> Optional[PoolCalibration]:
    if capacity_total <= 0 or build_tokens <= 0:
        return None
    if invert_percent is not None and invert_percent <= 0 and overall_percent <= 0:
        return None
    now = datetime.now(timezone.utc).isoformat()
    start_iso = (
        week_start.astimezone(timezone.utc).isoformat() if week_start else now
    )
    end_iso = (
        week_end.astimezone(timezone.utc).isoformat() if week_end else None
    )
    return PoolCalibration(
        week_start=start_iso,
        week_end=end_iso,
        capacity_total=int(capacity_total),
        tokens_per_percent=float(capacity_total) / 100.0,
        invert_percent=float(invert_percent),
        invert_basis=invert_basis,
        overall_percent_at_cal=float(overall_percent),
        build_tokens_at_cal=int(build_tokens),
        source=source,
        calibrated_at=now,
        confidence=confidence,
        build_share_percent=build_share_percent,
        other_products_percent=float(other_products_percent or 0.0),
        uncached_capacity=uncached_capacity,
    )


def calibrate_from_weekly(
    weekly: WeeklyUsage,
    local: LocalTokenReport,
    *,
    usage_percent_override: Optional[float] = None,
    source: str = "api",
    week_start: Optional[datetime] = None,
    week_end: Optional[datetime] = None,
) -> Optional[PoolCalibration]:
    """Build a calibration from API weekly usage + local tokens."""
    est = estimate_weekly_tokens(
        weekly,
        local,
        usage_percent_override=usage_percent_override,
    )
    if est.estimated_capacity_total is None or est.estimated_capacity_total <= 0:
        return None
    if est.used_total_tokens <= 0:
        return None
    # Need a positive invert anchor
    if est.invert_percent <= 0 and est.usage_percent <= 0:
        return None

    start = week_start or local.since or weekly.period.start
    end = week_end or local.until or weekly.period.end
    return calibrate_from_estimate(
        capacity_total=est.estimated_capacity_total,
        invert_percent=est.invert_percent,
        invert_basis=est.invert_basis,
        overall_percent=est.usage_percent,
        build_tokens=est.used_total_tokens,
        confidence=est.confidence,
        source=source,
        week_start=start,
        week_end=end,
        build_share_percent=est.build_pool_percent,
        other_products_percent=est.other_products_percent,
        uncached_capacity=est.estimated_capacity_uncached,
    )


def estimate_from_calibration(
    cal: PoolCalibration,
    local: LocalTokenReport,
) -> LocalPoolEstimate:
    """Live usage rates from local tokens and a saved capacity anchor."""
    t = local.total.total_tokens
    cap = max(1, int(cal.capacity_total))
    build_pct = 100.0 * float(t) / float(cap)
    build_pct = max(0.0, build_pct)

    notes: list[str] = [
        "Local-first estimate: build_pool_% = 100 × local_tokens / capacity.",
        "Does not include Chat/Imagine/Voice or other machines.",
    ]

    # Constant product-mix assumption for overall %:
    #   overall ≈ build_pool_% × (overall_at_cal / invert_percent)
    # When invert was build share B and overall was P: overall ≈ build_% × P/B
    inv = float(cal.invert_percent) if cal.invert_percent > 0 else 0.0
    overall_at = float(cal.overall_percent_at_cal or 0.0)
    # After an early reset we may have zeroed overall_at_cal while keeping
    # invert_percent as Build's pool share (e.g. 72). Still scale overall.
    mix_overall = overall_at if overall_at > 0 else (
        100.0 if inv > 0 and cal.invert_basis == "build_product" else 0.0
    )
    if inv > 0 and mix_overall > 0:
        overall_pct = build_pct * (mix_overall / inv)
        notes.append(
            f"Overall % assumes constant mix "
            f"(× {mix_overall:.1f}/{inv:.1f} from calibration)."
        )
    else:
        # Capacity was inverted on overall pool — build tokens ≈ whole pool
        overall_pct = build_pct
        notes.append(
            "Overall % ≈ build pool % (calibration used overall / no mix)."
        )

    overall_pct = max(0.0, min(100.0, overall_pct))
    # build_pool can exceed 100 if capacity was underestimated or multi-device
    build_pct_clamped = min(build_pct, 150.0)

    rem_build = max(0, int(round(cap - t)))
    rem_overall = max(
        0, int(round(cap * max(0.0, (100.0 - overall_pct) / 100.0)))
    )

    return LocalPoolEstimate(
        build_tokens=t,
        capacity_total=cap,
        tokens_per_percent=float(cal.tokens_per_percent),
        build_pool_percent=build_pct_clamped,
        estimated_overall_percent=overall_pct,
        remaining_build_tokens=rem_build,
        remaining_overall_tokens=rem_overall,
        week_start=cal.week_start,
        source="calibration",
        confidence=cal.confidence,
        invert_basis=cal.invert_basis,
        build_share_percent=cal.build_share_percent,
        other_products_percent=cal.other_products_percent,
        notes=notes,
    )


def should_recalibrate(
    existing: Optional[PoolCalibration],
    candidate: Optional[PoolCalibration],
    *,
    force: bool = False,
    api_overall: Optional[float] = None,
    api_week_start: Optional[datetime] = None,
) -> bool:
    """Decide whether to replace the saved calibration."""
    if candidate is None:
        return False
    if force:
        return True
    if existing is None:
        return True

    # User-pinned anchors (early reset / -i / --recalibrate-window) stay put
    # until they explicitly --recalibrate.
    if existing.source in ("manual", "interactive"):
        return False

    old_start = existing.week_start_dt()
    new_start = candidate.week_start_dt()

    # If our saved week start is intentionally offset from the billing API
    # period (common after early resets), do not auto-adopt API inversions.
    if old_start and api_week_start:
        if abs((old_start - api_week_start).total_seconds()) > 3600:
            return False

    # New subscription week window from API (period start moved by > 1h)
    if old_start and new_start and api_week_start:
        # Only treat as new week when the candidate tracks the API start
        if abs((new_start - api_week_start).total_seconds()) < 3600:
            if abs((new_start - old_start).total_seconds()) > 3600:
                return True

    # Capacity shifted a lot while API still shows meaningful usage
    if existing.capacity_total > 0 and candidate.capacity_total > 0:
        ratio = candidate.capacity_total / float(existing.capacity_total)
        if ratio < 0.5 or ratio > 2.0:
            if api_overall is not None and api_overall >= 5.0:
                return True

    # Prefer higher confidence when existing is low/none
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    if rank.get(candidate.confidence, 0) > rank.get(existing.confidence, 0) + 1:
        return True

    # Refresh same-window calibration when tokens have grown and API % rose
    if (
        api_overall is not None
        and existing.overall_percent_at_cal > 0
        and api_overall > existing.overall_percent_at_cal + 5
        and candidate.build_tokens_at_cal > existing.build_tokens_at_cal * 1.1
        and abs(candidate.capacity_total - existing.capacity_total)
        < 0.25 * existing.capacity_total
    ):
        return True

    return False


def maybe_update_week_start(
    cal: PoolCalibration,
    *,
    week_start: Optional[datetime],
    week_end: Optional[datetime] = None,
    source: str = "manual",
) -> PoolCalibration:
    """
    Keep capacity after an early reset; only move the window.
    Used when pool % is 0 and we cannot re-invert.
    """
    if week_start is None:
        return cal
    now = datetime.now(timezone.utc).isoformat()
    return PoolCalibration(
        week_start=week_start.astimezone(timezone.utc).isoformat(),
        week_end=(
            week_end.astimezone(timezone.utc).isoformat() if week_end else cal.week_end
        ),
        capacity_total=cal.capacity_total,
        tokens_per_percent=cal.tokens_per_percent,
        invert_percent=cal.invert_percent,
        invert_basis=cal.invert_basis,
        overall_percent_at_cal=0.0,
        build_tokens_at_cal=0,
        source=source,
        calibrated_at=now,
        confidence=cal.confidence,
        build_share_percent=cal.build_share_percent,
        other_products_percent=cal.other_products_percent,
        uncached_capacity=cal.uncached_capacity,
    )


def resolve_effective_pool_percent(
    *,
    api_percent: Optional[float],
    override_percent: Optional[float],
    local_estimate: Optional[LocalPoolEstimate],
    prefer_local: bool = True,
) -> tuple[Optional[float], str]:
    """
    Pick the weekly pool % used for display / economics.

    Priority:
      1. Explicit override (--pool-percent / saved override used as override)
      2. Local calibration estimate (if prefer_local)
      3. Billing API percent
    """
    if override_percent is not None:
        return round(float(override_percent), 3), "override"
    if prefer_local and local_estimate is not None:
        return round(float(local_estimate.estimated_overall_percent), 3), "local_calibration"
    if api_percent is not None:
        return round(float(api_percent), 3), "api"
    if local_estimate is not None:
        return round(float(local_estimate.estimated_overall_percent), 3), "local_calibration"
    return None, "none"
