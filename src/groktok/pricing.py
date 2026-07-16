"""xAI API token prices and cost analysis for local Build usage.

Prices from https://docs.x.ai/docs/models (Text API Pricing).
Subscription pool usage is *not* billed this way — this is an
**API-equivalent** cost so you can compare compute intensity in dollars.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .local_tokens import LocalTokenReport, TokenBucket

# USD per 1_000_000 tokens. Standard tier = prompts under long-context threshold.
# Long-context tier applies when a request's prompt reaches the threshold
# (typically 200k) — then *all* tokens on that request use the higher rate.


@dataclass(frozen=True)
class ModelRate:
    model: str
    input_per_mtok: float
    cached_input_per_mtok: float
    output_per_mtok: float
    long_input_per_mtok: Optional[float] = None
    long_cached_input_per_mtok: Optional[float] = None
    long_output_per_mtok: Optional[float] = None
    long_context_threshold: Optional[int] = 200_000
    notes: str = ""

    def rates(self, *, long_context: bool = False) -> tuple[float, float, float]:
        if long_context and self.long_input_per_mtok is not None:
            return (
                self.long_input_per_mtok,
                self.long_cached_input_per_mtok
                if self.long_cached_input_per_mtok is not None
                else self.cached_input_per_mtok,
                self.long_output_per_mtok
                if self.long_output_per_mtok is not None
                else self.output_per_mtok,
            )
        return (
            self.input_per_mtok,
            self.cached_input_per_mtok,
            self.output_per_mtok,
        )


# Snapshot of docs.x.ai text pricing (update when xAI changes rates).
PRICING_SOURCE = "https://docs.x.ai/docs/models"
PRICING_AS_OF = "2026-07"

_MODEL_RATES: list[ModelRate] = [
    ModelRate(
        model="grok-4.5",
        input_per_mtok=2.00,
        cached_input_per_mtok=0.50,
        output_per_mtok=6.00,
        long_input_per_mtok=4.00,
        long_cached_input_per_mtok=1.00,
        long_output_per_mtok=12.00,
        notes="Flagship; default for Grok Build",
    ),
    ModelRate(
        model="grok-4.3",
        input_per_mtok=1.25,
        cached_input_per_mtok=0.20,
        output_per_mtok=2.50,
        long_input_per_mtok=2.50,
        long_cached_input_per_mtok=0.40,
        long_output_per_mtok=5.00,
    ),
    ModelRate(
        model="grok-4.20-0309-reasoning",
        input_per_mtok=1.25,
        cached_input_per_mtok=0.20,
        output_per_mtok=2.50,
        long_input_per_mtok=2.50,
        long_cached_input_per_mtok=0.40,
        long_output_per_mtok=5.00,
    ),
    ModelRate(
        model="grok-4.20-0309-non-reasoning",
        input_per_mtok=1.25,
        cached_input_per_mtok=0.20,
        output_per_mtok=2.50,
        long_input_per_mtok=2.50,
        long_cached_input_per_mtok=0.40,
        long_output_per_mtok=5.00,
    ),
    ModelRate(
        model="grok-4.20-multi-agent-0309",
        input_per_mtok=1.25,
        cached_input_per_mtok=0.20,
        output_per_mtok=2.50,
        long_input_per_mtok=2.50,
        long_cached_input_per_mtok=0.40,
        long_output_per_mtok=5.00,
    ),
    ModelRate(
        model="grok-build-0.1",
        input_per_mtok=1.00,
        cached_input_per_mtok=0.20,
        output_per_mtok=2.00,
        long_input_per_mtok=2.00,
        long_cached_input_per_mtok=0.40,
        long_output_per_mtok=4.00,
    ),
    # Older / common aliases seen in the wild
    ModelRate(
        model="grok-4",
        input_per_mtok=3.00,
        cached_input_per_mtok=0.75,
        output_per_mtok=15.00,
        long_input_per_mtok=None,
        notes="Legacy flagship rates (approx)",
    ),
    ModelRate(
        model="grok-code-fast-1",
        input_per_mtok=0.20,
        cached_input_per_mtok=0.05,
        output_per_mtok=1.50,
    ),
    ModelRate(
        model="grok-4-1-fast",
        input_per_mtok=0.20,
        cached_input_per_mtok=0.05,
        output_per_mtok=0.50,
    ),
    ModelRate(
        model="grok-4-fast",
        input_per_mtok=0.20,
        cached_input_per_mtok=0.05,
        output_per_mtok=0.50,
    ),
]

DEFAULT_MODEL = "grok-4.5"


def _index() -> dict[str, ModelRate]:
    return {m.model.lower(): m for m in _MODEL_RATES}


def lookup_rate(model: str) -> ModelRate:
    key = (model or "").lower().strip()
    table = _index()
    if key in table:
        return table[key]
    # prefix / alias fuzzy match
    for name, rate in table.items():
        if key.startswith(name) or name.startswith(key):
            return rate
    # family shortcuts
    if "4.5" in key or key in ("grok-build", "grok-build-plan"):
        return table["grok-4.5"]
    if "4.3" in key:
        return table["grok-4.3"]
    if "build" in key:
        return table.get("grok-build-0.1") or table["grok-4.5"]
    # fallback: flagship rates with a note
    base = table[DEFAULT_MODEL]
    return ModelRate(
        model=model or "unknown",
        input_per_mtok=base.input_per_mtok,
        cached_input_per_mtok=base.cached_input_per_mtok,
        output_per_mtok=base.output_per_mtok,
        long_input_per_mtok=base.long_input_per_mtok,
        long_cached_input_per_mtok=base.long_cached_input_per_mtok,
        long_output_per_mtok=base.long_output_per_mtok,
        notes=f"Unknown model {model!r}; using {DEFAULT_MODEL} rates",
    )


def _usd(tokens: int, rate_per_mtok: float) -> float:
    return (tokens / 1_000_000.0) * rate_per_mtok


@dataclass
class CostBreakdown:
    model: str
    long_context: bool
    rate_input: float
    rate_cached: float
    rate_output: float

    uncached_input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int  # informational; assumed included in output for billing

    cost_uncached_input: float
    cost_cached_input: float
    cost_output: float
    cost_total: float

    rate_notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "long_context": self.long_context,
            "rates_usd_per_mtok": {
                "input": self.rate_input,
                "cached_input": self.rate_cached,
                "output": self.rate_output,
            },
            "tokens": {
                "uncached_input": self.uncached_input_tokens,
                "cached_input": self.cached_input_tokens,
                "output": self.output_tokens,
                "reasoning_included_in_output": self.reasoning_tokens,
            },
            "cost_usd": {
                "uncached_input": round(self.cost_uncached_input, 6),
                "cached_input": round(self.cost_cached_input, 6),
                "output": round(self.cost_output, 6),
                "total": round(self.cost_total, 6),
            },
            "notes": self.rate_notes,
        }


@dataclass
class CostReport:
    """API-equivalent cost for a LocalTokenReport window."""

    pricing_source: str
    pricing_as_of: str
    long_context: bool
    auto_long_context: bool
    by_model: list[CostBreakdown]
    total_usd: float
    # Extrapolations when pool % known
    estimated_full_week_usd: Optional[float] = None
    estimated_remaining_usd: Optional[float] = None
    pool_percent_used: Optional[float] = None
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pricing_source": self.pricing_source,
            "pricing_as_of": self.pricing_as_of,
            "long_context": self.long_context,
            "auto_long_context": self.auto_long_context,
            "by_model": [b.as_dict() for b in self.by_model],
            "total_usd": round(self.total_usd, 6),
            "estimated_full_week_usd": (
                round(self.estimated_full_week_usd, 6)
                if self.estimated_full_week_usd is not None
                else None
            ),
            "estimated_remaining_usd": (
                round(self.estimated_remaining_usd, 6)
                if self.estimated_remaining_usd is not None
                else None
            ),
            "pool_percent_used": self.pool_percent_used,
            "caveats": list(self.caveats),
        }


def _avg_input_per_call(bucket: TokenBucket) -> Optional[float]:
    if bucket.model_calls <= 0:
        return None
    return bucket.input_tokens / float(bucket.model_calls)


def cost_for_bucket(
    model: str,
    bucket: TokenBucket,
    *,
    long_context: bool = False,
) -> CostBreakdown:
    rate = lookup_rate(model)
    rin, rcached, rout = rate.rates(long_context=long_context)
    uncached = bucket.uncached_input_tokens
    cached = bucket.cached_read_tokens
    # Clamp: if cached > input due to partial data, don't go negative
    if uncached < 0:
        uncached = 0
    if cached > bucket.input_tokens:
        cached = bucket.input_tokens
        uncached = 0

    c_in = _usd(uncached, rin)
    c_cached = _usd(cached, rcached)
    # Reasoning tokens are reported inside/alongside output; bill output once.
    c_out = _usd(bucket.output_tokens, rout)
    notes = rate.notes
    if rate.model.lower() != (model or "").lower():
        notes = (notes + "; " if notes else "") + f"matched rates for {rate.model}"

    return CostBreakdown(
        model=model,
        long_context=long_context,
        rate_input=rin,
        rate_cached=rcached,
        rate_output=rout,
        uncached_input_tokens=uncached,
        cached_input_tokens=cached,
        output_tokens=bucket.output_tokens,
        reasoning_tokens=bucket.reasoning_tokens,
        cost_uncached_input=c_in,
        cost_cached_input=c_cached,
        cost_output=c_out,
        cost_total=c_in + c_cached + c_out,
        rate_notes=notes,
    )


def analyze_cost(
    local: LocalTokenReport,
    *,
    long_context: Optional[bool] = None,
    pool_percent_used: Optional[float] = None,
    default_model: str = DEFAULT_MODEL,
) -> CostReport:
    """
    Compute API-equivalent USD cost for local token usage.

    ``long_context``:
      - True/False force tier
      - None: auto — use long rates if avg input tokens/call ≥ 200k for a model
    """
    caveats = [
        "API-equivalent cost only — SuperGrok weekly pool is not billed at these rates.",
        "Reasoning tokens are assumed included in output token counts (not double-billed).",
        f"Rates from {PRICING_SOURCE} (as of ~{PRICING_AS_OF}).",
    ]

    auto = long_context is None
    by_model: list[CostBreakdown] = []

    sources = local.by_model
    if not sources:
        # Attribute entire total to default model
        sources = {default_model: local.total}

    any_long = False
    for model, bucket in sorted(sources.items(), key=lambda kv: -kv[1].total_tokens):
        use_long = bool(long_context) if long_context is not None else False
        if auto:
            avg = _avg_input_per_call(bucket)
            rate = lookup_rate(model)
            thr = rate.long_context_threshold or 200_000
            use_long = avg is not None and avg >= thr
            if use_long:
                caveats.append(
                    f"{model}: avg ~{avg:,.0f} input tokens/call ≥ {thr:,} → long-context rates."
                )
        any_long = any_long or use_long
        by_model.append(cost_for_bucket(model, bucket, long_context=use_long))

    total = sum(b.cost_total for b in by_model)

    full_week = None
    remaining = None
    if pool_percent_used is not None and pool_percent_used > 0 and total > 0:
        full_week = total / (pool_percent_used / 100.0)
        remaining = full_week * max(0.0, (100.0 - pool_percent_used) / 100.0)
        caveats.append(
            "Full-week $ estimate = window cost ÷ (pool % used), same invert as tokens."
        )

    return CostReport(
        pricing_source=PRICING_SOURCE,
        pricing_as_of=PRICING_AS_OF,
        long_context=bool(long_context) if long_context is not None else any_long,
        auto_long_context=auto,
        by_model=by_model,
        total_usd=total,
        estimated_full_week_usd=full_week,
        estimated_remaining_usd=remaining,
        pool_percent_used=pool_percent_used,
        caveats=caveats,
    )


def format_usd(amount: float) -> str:
    if amount >= 100:
        return f"${amount:,.2f}"
    if amount >= 1:
        return f"${amount:,.2f}"
    if amount >= 0.01:
        return f"${amount:,.4f}"
    return f"${amount:,.6f}"
