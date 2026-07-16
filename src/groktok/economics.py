"""SuperGrok economics: allotment / plan / API-equivalent cost per token."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .billing import MonthlyUsage, WeeklyUsage
from .calibration import LocalPoolEstimate
from .config import PoolCalibration
from .local_tokens import LocalTokenReport
from .pricing import CostReport, analyze_cost


def _per_mtok(usd: float, tokens: int) -> Optional[float]:
    if tokens <= 0 or usd < 0:
        return None
    return (usd / tokens) * 1_000_000.0


def _build_share_fraction(
    weekly: Optional[WeeklyUsage],
    cal: Optional[PoolCalibration],
    local_est: Optional[LocalPoolEstimate],
) -> tuple[Optional[float], str]:
    """
    Fraction of multi-product usage attributed to Grok Build (0–1).

    Prefer live API product breakdown, then calibration, else assume 1.0.
    """
    if weekly and weekly.product_usage:
        build = 0.0
        total = 0.0
        for p in weekly.product_usage:
            pct = float(p.usage_percent or 0.0)
            total += pct
            name = (p.product or "").lower().replace(" ", "")
            if "build" in name or name in ("grokbuild", "code", "cli"):
                build += pct
        if total > 0 and build > 0:
            # Product rows are already % of the pool, not fractions of each other.
            # Build's share of *accounted* product usage:
            return build / total, "api_product_mix"
        if build > 0 and weekly.credit_usage_percent > 0:
            return min(1.0, build / weekly.credit_usage_percent), "api_build_over_overall"

    if local_est and local_est.build_share_percent and local_est.estimated_overall_percent:
        if local_est.estimated_overall_percent > 0:
            return (
                min(
                    1.0,
                    local_est.build_share_percent
                    / max(local_est.estimated_overall_percent, 0.01),
                ),
                "calibration_mix",
            )

    if cal and cal.build_share_percent is not None and cal.overall_percent_at_cal > 0:
        return (
            min(1.0, cal.build_share_percent / cal.overall_percent_at_cal),
            "calibration_mix",
        )

    if cal and cal.invert_basis == "build_product" and cal.invert_percent > 0:
        # invert_percent was build's pool points; overall may be 100
        if cal.overall_percent_at_cal > 0:
            return (
                min(1.0, cal.invert_percent / cal.overall_percent_at_cal),
                "calibration_invert",
            )

    return 1.0, "assume_all_build"


@dataclass
class SuperGrokEconomics:
    """Derived SuperGrok-side economics for a token window."""

    build_tokens: int
    usage_percent: Optional[float]
    usage_source: str  # override | local_calibration | api | none

    # Capacity / local-first
    capacity_total: Optional[int] = None
    build_pool_percent: Optional[float] = None
    estimated_overall_percent: Optional[float] = None
    api_overall_percent: Optional[float] = None
    remaining_tokens: Optional[int] = None
    calibration: Optional[dict[str, Any]] = None

    # Dollar rates (USD per 1M tokens)
    allotment_usd_per_mtok: Optional[float] = None
    plan_usd_per_mtok: Optional[float] = None
    api_equiv_usd_per_mtok: Optional[float] = None

    # Supporting figures
    monthly_used_usd: Optional[float] = None
    monthly_limit_usd: Optional[float] = None
    build_share_fraction: Optional[float] = None
    build_share_source: Optional[str] = None
    attributed_allotment_usd: Optional[float] = None
    plan_price_usd: Optional[float] = None
    api_equiv_total_usd: Optional[float] = None
    api_equiv_by_model: list[dict[str, Any]] = field(default_factory=list)

    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "build_tokens": self.build_tokens,
            "usage_percent": self.usage_percent,
            "usage_source": self.usage_source,
            "capacity_total": self.capacity_total,
            "build_pool_percent": (
                round(self.build_pool_percent, 3)
                if self.build_pool_percent is not None
                else None
            ),
            "estimated_overall_percent": (
                round(self.estimated_overall_percent, 3)
                if self.estimated_overall_percent is not None
                else None
            ),
            "api_overall_percent": self.api_overall_percent,
            "remaining_tokens": self.remaining_tokens,
            "calibration": self.calibration,
            "rates_usd_per_mtok": {
                "supergrok_allotment": (
                    round(self.allotment_usd_per_mtok, 6)
                    if self.allotment_usd_per_mtok is not None
                    else None
                ),
                "plan_amortized": (
                    round(self.plan_usd_per_mtok, 6)
                    if self.plan_usd_per_mtok is not None
                    else None
                ),
                "api_list_equivalent": (
                    round(self.api_equiv_usd_per_mtok, 6)
                    if self.api_equiv_usd_per_mtok is not None
                    else None
                ),
            },
            "monthly_used_usd": self.monthly_used_usd,
            "monthly_limit_usd": self.monthly_limit_usd,
            "build_share_fraction": (
                round(self.build_share_fraction, 4)
                if self.build_share_fraction is not None
                else None
            ),
            "build_share_source": self.build_share_source,
            "attributed_allotment_usd": (
                round(self.attributed_allotment_usd, 4)
                if self.attributed_allotment_usd is not None
                else None
            ),
            "plan_price_usd": self.plan_price_usd,
            "api_equiv_total_usd": (
                round(self.api_equiv_total_usd, 6)
                if self.api_equiv_total_usd is not None
                else None
            ),
            "api_equiv_by_model": self.api_equiv_by_model,
            "notes": list(self.notes),
        }


def analyze_economics(
    local: LocalTokenReport,
    *,
    weekly: Optional[WeeklyUsage] = None,
    monthly: Optional[MonthlyUsage] = None,
    calibration: Optional[PoolCalibration] = None,
    local_estimate: Optional[LocalPoolEstimate] = None,
    usage_percent: Optional[float] = None,
    usage_source: str = "none",
    plan_price_usd: Optional[float] = None,
    cost: Optional[CostReport] = None,
    long_context: Optional[bool] = None,
    show_api_cost: bool = True,
) -> SuperGrokEconomics:
    """
    Derive SuperGrok-side $/token alongside API list-equivalent cost.

    SuperGrok allotment rate:
        attributed_$ = monthly_used_$ × build_share_fraction
        allotment_$/MTok = attributed_$ / build_tokens × 1e6

    Plan amortized rate (optional user plan price):
        plan_$/MTok = plan_price_usd / build_tokens × 1e6
        (uses tokens in the provided local window — typically month or week)
    """
    notes: list[str] = []
    build_tokens = local.total.total_tokens

    share, share_src = _build_share_fraction(weekly, calibration, local_estimate)
    notes.append(
        f"Build share fraction {share:.2%} from {share_src.replace('_', ' ')}."
    )

    monthly_used = monthly.used_usd if monthly else None
    monthly_limit = monthly.monthly_limit_usd if monthly else None
    attributed = None
    allotment_rate = None
    if monthly_used is not None and share is not None and build_tokens > 0:
        attributed = monthly_used * share
        allotment_rate = _per_mtok(attributed, build_tokens)
        notes.append(
            "Allotment $/MTok = (monthly used $ × build share) / local Build tokens. "
            "Monthly $ is the included compute budget, not your card charge."
        )
        # Window mismatch warning
        if local.since and monthly and monthly.period_start:
            if local.since > monthly.period_start:
                notes.append(
                    "Token window may be shorter than the monthly allotment period "
                    "— allotment rate can be overstated."
                )

    plan_rate = None
    if plan_price_usd is not None and plan_price_usd > 0 and build_tokens > 0:
        plan_rate = _per_mtok(float(plan_price_usd), build_tokens)
        notes.append(
            "Plan $/MTok amortizes plan_price_usd over Build tokens in this window."
        )
    elif plan_price_usd is None:
        notes.append(
            "Set plan price with --plan-price USD to see amortized subscription $/MTok."
        )

    if cost is None and show_api_cost:
        cost = analyze_cost(
            local,
            long_context=long_context,
            pool_percent_used=usage_percent if usage_percent and usage_percent > 0 else None,
        )

    api_total = cost.total_usd if cost else None
    api_rate = _per_mtok(api_total, build_tokens) if api_total is not None else None
    by_model = [b.as_dict() for b in cost.by_model] if cost else []

    cap = None
    build_pool_pct = None
    est_overall = None
    remaining = None
    cal_dict = None
    if local_estimate is not None:
        cap = local_estimate.capacity_total
        build_pool_pct = local_estimate.build_pool_percent
        est_overall = local_estimate.estimated_overall_percent
        remaining = local_estimate.remaining_overall_tokens
    elif calibration is not None:
        cap = calibration.capacity_total
        if cap > 0 and build_tokens >= 0:
            build_pool_pct = min(150.0, 100.0 * build_tokens / cap)
            est_overall = build_pool_pct
            remaining = max(0, cap - build_tokens)
    if calibration is not None:
        cal_dict = calibration.as_dict()

    api_pct = weekly.credit_usage_percent if weekly else None

    return SuperGrokEconomics(
        build_tokens=build_tokens,
        usage_percent=usage_percent,
        usage_source=usage_source,
        capacity_total=cap,
        build_pool_percent=build_pool_pct,
        estimated_overall_percent=est_overall,
        api_overall_percent=api_pct,
        remaining_tokens=remaining,
        calibration=cal_dict,
        allotment_usd_per_mtok=allotment_rate,
        plan_usd_per_mtok=plan_rate,
        api_equiv_usd_per_mtok=api_rate,
        monthly_used_usd=monthly_used,
        monthly_limit_usd=monthly_limit,
        build_share_fraction=share,
        build_share_source=share_src,
        attributed_allotment_usd=attributed,
        plan_price_usd=plan_price_usd,
        api_equiv_total_usd=api_total,
        api_equiv_by_model=by_model,
        notes=notes,
    )
