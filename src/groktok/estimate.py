"""Estimate weekly pool size in tokens from local usage + remote % used."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .billing import WeeklyUsage
from .local_tokens import LocalTokenReport, TokenBucket


@dataclass
class WeeklyTokenEstimate:
    """
    Invert local Build tokens over the week against the pool percentage to
    recover an implied full-week token capacity:

        capacity ≈ tokens_since_week_start / (percent_of_pool / 100)

    When the product breakdown lists GrokBuild, we invert against Build's
    share of the pool (not the overall %), then report remaining pool as
    (100 − overall%) of that capacity.
    """

    usage_percent: float
    invert_percent: float
    invert_basis: str  # "build_product" | "overall_pool"

    used_total_tokens: int
    used_uncached_input: int
    used_output_tokens: int

    estimated_capacity_total: Optional[int]
    estimated_remaining_total: Optional[int]
    estimated_used_via_percent: Optional[int]  # capacity * overall%/100

    estimated_capacity_uncached: Optional[int]
    estimated_remaining_uncached: Optional[int]

    build_pool_percent: Optional[float]
    other_products_percent: float
    confidence: str  # high | medium | low | none
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "usage_percent": self.usage_percent,
            "invert_percent": self.invert_percent,
            "invert_basis": self.invert_basis,
            "used_total_tokens": self.used_total_tokens,
            "used_uncached_input": self.used_uncached_input,
            "used_output_tokens": self.used_output_tokens,
            "estimated_capacity_total": self.estimated_capacity_total,
            "estimated_remaining_total": self.estimated_remaining_total,
            "estimated_used_via_percent": self.estimated_used_via_percent,
            "estimated_capacity_uncached": self.estimated_capacity_uncached,
            "estimated_remaining_uncached": self.estimated_remaining_uncached,
            "build_pool_percent": self.build_pool_percent,
            "other_products_percent": self.other_products_percent,
            "confidence": self.confidence,
            "notes": list(self.notes),
            "method": (
                "local_tokens_since_week_start / (invert_percent / 100); "
                "remaining = capacity * (1 - overall_usage_percent/100)"
            ),
        }


def _invert(used: int, percent: float) -> Optional[int]:
    if used <= 0 or percent <= 0:
        return None
    pct = min(float(percent), 100.0)
    return int(round(used / (pct / 100.0)))


def _build_share(weekly: WeeklyUsage) -> tuple[Optional[float], float]:
    """Return (build_percent, other_products_sum_percent)."""
    build = 0.0
    other = 0.0
    saw_build = False
    for p in weekly.product_usage:
        name = (p.product or "").lower().replace(" ", "")
        if "build" in name or name in ("grokbuild", "code", "cli"):
            build += float(p.usage_percent)
            saw_build = True
        else:
            other += float(p.usage_percent)
    if not weekly.product_usage:
        return None, 0.0
    if not saw_build:
        return 0.0, other
    return build, other


def estimate_weekly_tokens(
    weekly: WeeklyUsage,
    local: LocalTokenReport,
    *,
    bucket: Optional[TokenBucket] = None,
    usage_percent_override: Optional[float] = None,
    invert_percent_override: Optional[float] = None,
) -> WeeklyTokenEstimate:
    t = bucket or local.total
    overall_pct = (
        float(usage_percent_override)
        if usage_percent_override is not None
        else float(weekly.credit_usage_percent or 0.0)
    )
    build_pct, other_pct = _build_share(weekly)

    # Prefer Build product % when present and > 0 — local tokens map to that slice.
    # If the user overrode the overall pool %, also use that for invert (product
    # breakdown may still reflect the old period).
    if invert_percent_override is not None:
        invert_pct = float(invert_percent_override)
        invert_basis = "user_override"
    elif usage_percent_override is not None:
        invert_pct = overall_pct
        invert_basis = "user_override"
    elif build_pct is not None and build_pct > 0:
        invert_pct = build_pct
        invert_basis = "build_product"
    else:
        invert_pct = overall_pct
        invert_basis = "overall_pool"

    cap_total = _invert(t.total_tokens, invert_pct)
    cap_uncached = _invert(t.uncached_input_tokens, invert_pct)

    used_via_pct = (
        int(round(cap_total * (overall_pct / 100.0)))
        if cap_total is not None and overall_pct > 0
        else None
    )
    rem_total = (
        int(round(cap_total * max(0.0, (100.0 - overall_pct) / 100.0)))
        if cap_total is not None
        else None
    )
    rem_uncached = (
        int(round(cap_uncached * max(0.0, (100.0 - overall_pct) / 100.0)))
        if cap_uncached is not None
        else None
    )

    notes: list[str] = []
    confidence = "none"

    if overall_pct <= 0 and invert_pct <= 0:
        notes.append("Week pool shows 0% used — cannot invert to a capacity yet.")
    elif t.total_tokens <= 0:
        notes.append("No local Build tokens in this week window to invert from.")
    else:
        notes.append(
            f"Full-week size ≈ local tokens ÷ {invert_pct:.1f}% "
            f"({invert_basis.replace('_', ' ')})."
        )
        if invert_basis == "build_product" and other_pct <= 0.5:
            confidence = "high"
            notes.append(
                "Pool usage is essentially all GrokBuild on the account — "
                "local Build tokens are a strong proxy."
            )
        elif invert_basis == "build_product" and other_pct > 0.5:
            confidence = "medium"
            notes.append(
                f"Other products used ~{other_pct:.1f}% of the pool. "
                "Capacity is extrapolated from the Build slice; remaining "
                "includes headroom for those products too."
            )
        elif invert_basis == "overall_pool" and other_pct > 0:
            confidence = "low"
            notes.append(
                "Inverting against overall pool % but non-Build products "
                "also consumed quota — estimate may be off."
            )
        else:
            confidence = "medium"
            notes.append(
                "No product breakdown (or Build share unknown) — "
                "assumes local tokens ≈ all pool usage."
            )

        notes.append(
            "Local logs only cover Grok Build on this machine. "
            "Chat/Imagine/Voice/other devices are not in the token count."
        )

    return WeeklyTokenEstimate(
        usage_percent=overall_pct,
        invert_percent=invert_pct,
        invert_basis=invert_basis,
        used_total_tokens=t.total_tokens,
        used_uncached_input=t.uncached_input_tokens,
        used_output_tokens=t.output_tokens,
        estimated_capacity_total=cap_total,
        estimated_remaining_total=rem_total,
        estimated_used_via_percent=used_via_pct,
        estimated_capacity_uncached=cap_uncached,
        estimated_remaining_uncached=rem_uncached,
        build_pool_percent=build_pct,
        other_products_percent=other_pct,
        confidence=confidence,
        notes=notes,
    )
