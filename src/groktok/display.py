"""Human-readable and JSON rendering for usage reports."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Optional, TextIO

from .billing import MonthlyUsage, UsageReport, WeeklyUsage
from .calibration import LocalPoolEstimate
from .economics import SuperGrokEconomics
from .estimate import estimate_weekly_tokens
from .local_tokens import LocalTokenReport
from .pricing import CostReport, analyze_cost, format_usd


# ANSI colors (disabled when not a TTY or NO_COLOR is set)
def _use_color(stream: TextIO) -> bool:
    import os

    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


class Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def level(self, pct: float, text: str) -> str:
        if pct >= 90:
            return self.red(text)
        if pct >= 70:
            return self.yellow(text)
        return self.green(text)


def _bar(percent: float, width: int = 28) -> str:
    pct = max(0.0, min(100.0, percent))
    filled = int(round(width * pct / 100.0))
    filled = min(width, max(0, filled))
    return "█" * filled + "░" * (width - filled)


def _fmt_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    local = dt.astimezone()
    return local.strftime("%b %d, %Y %H:%M %Z")


def _fmt_money_cents(cents: Optional[int]) -> str:
    if cents is None:
        return "—"
    return f"${cents / 100.0:,.2f}"


def _fmt_tokens(n: int) -> str:
    """Human-friendly token counts: 1.23M, 45.3k, or with commas."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:,}"


def _remaining(end: Optional[datetime]) -> str:
    if not end:
        return "—"
    now = datetime.now(timezone.utc)
    delta = end - now
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "reset overdue / pending"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{mins}m")
    return "in " + " ".join(parts)


def _period_type_label(raw: Optional[str]) -> str:
    if not raw:
        return "Period"
    # USAGE_PERIOD_TYPE_WEEKLY -> Weekly
    name = raw.replace("USAGE_PERIOD_TYPE_", "").replace("_", " ").title()
    return name


def report_to_dict(report: UsageReport) -> dict[str, Any]:
    weekly = report.weekly
    monthly = report.monthly

    def iso(dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None

    return {
        "account": {
            "email": report.email,
            "user_id": report.user_id,
            "team_id": report.team_id,
            "auth_source": report.credentials_source,
        },
        "weekly": {
            "period_type": weekly.period.type,
            "usage_percent": weekly.credit_usage_percent,
            "start": iso(weekly.period.start),
            "end": iso(weekly.period.end),
            "resets_in_seconds": (
                int((weekly.period.end - datetime.now(timezone.utc)).total_seconds())
                if weekly.period.end
                else None
            ),
            "product_usage": [
                {"product": p.product, "usage_percent": p.usage_percent}
                for p in weekly.product_usage
            ],
            "extra_usage_credits_usd": (
                weekly.prepaid_balance_cents / 100.0
                if weekly.prepaid_balance_cents is not None
                else None
            ),
            "on_demand_cap_usd": (
                weekly.on_demand_cap_cents / 100.0
                if weekly.on_demand_cap_cents is not None
                else None
            ),
            "on_demand_used_usd": (
                weekly.on_demand_used_cents / 100.0
                if weekly.on_demand_used_cents is not None
                else None
            ),
            "is_unified_billing_user": weekly.is_unified_billing_user,
            "top_up_method": weekly.top_up_method,
        },
        "monthly": {
            # API `format=tokens` returns values in USD cents (same unit as Grok /usage).
            "used_usd": monthly.used_usd,
            "limit_usd": monthly.monthly_limit_usd,
            "used_cents": monthly.used_cents,
            "limit_cents": monthly.monthly_limit_cents,
            "usage_percent": round(monthly.usage_percent, 2),
            "period_start": iso(monthly.period_start),
            "period_end": iso(monthly.period_end),
            "on_demand_cap_usd": (
                monthly.on_demand_cap_cents / 100.0
                if monthly.on_demand_cap_cents is not None
                else None
            ),
            "history": [
                {
                    "year": h.year,
                    "month": h.month,
                    "included_used_usd": h.included_used_cents / 100.0,
                    "on_demand_used_usd": h.on_demand_used_cents / 100.0,
                    "total_used_usd": h.total_used_cents / 100.0,
                }
                for h in monthly.history
            ],
        },
    }


def _render_token_stats(
    local: LocalTokenReport,
    style: Style,
    line,
    *,
    indent: str = "  ",
    show_top: bool = True,
    top_n: int = 5,
) -> None:
    """Shared token lines used by weekly embed + standalone local section."""
    t = local.total
    line(
        f"{indent}Total tokens         "
        f"{style.cyan(_fmt_tokens(t.total_tokens))}  ({t.total_tokens:,})"
    )
    line(
        f"{indent}Input                "
        f"{_fmt_tokens(t.input_tokens)}  ({t.input_tokens:,})"
    )
    line(
        f"{indent}  cached             "
        f"{_fmt_tokens(t.cached_read_tokens)}  ({t.cached_read_tokens:,})"
    )
    line(
        f"{indent}  uncached           "
        f"{_fmt_tokens(t.uncached_input_tokens)}  ({t.uncached_input_tokens:,})"
    )
    line(
        f"{indent}Output               "
        f"{_fmt_tokens(t.output_tokens)}  ({t.output_tokens:,})"
    )
    line(
        f"{indent}Reasoning            "
        f"{_fmt_tokens(t.reasoning_tokens)}  ({t.reasoning_tokens:,})"
    )
    line(f"{indent}Model calls / turns  {t.model_calls:,} / {t.turns:,}")

    if local.by_model:
        line()
        line(style.dim(f"{indent}By model"))
        for model, bucket in sorted(
            local.by_model.items(), key=lambda kv: -kv[1].total_tokens
        ):
            line(
                f"{indent}  {model:<16} {_fmt_tokens(bucket.total_tokens):>10} total  "
                f"({bucket.turns} turns, {bucket.model_calls} calls)"
            )

    if show_top and local.top_sessions:
        line()
        line(style.dim(f"{indent}Top sessions"))
        for s in local.top_sessions[:top_n]:
            label = s.title or s.session_id[:12]
            if len(label) > 48:
                label = label[:45] + "…"
            line(f"{indent}  {_fmt_tokens(s.usage.total_tokens):>8}  {label}")


def render_cost_analysis(
    cost: CostReport,
    *,
    style: Optional[Style] = None,
    indent: str = "  ",
    stream: Optional[TextIO] = None,
) -> None:
    stream = stream or sys.stdout
    style = style or Style(_use_color(stream))

    def line(text: str = "") -> None:
        print(text, file=stream)

    line(style.bold(f"{indent}API-equivalent cost"))
    line(
        style.dim(
            f"{indent}  rates: docs.x.ai text pricing (~{cost.pricing_as_of}) · "
            "not your SuperGrok bill"
        )
    )
    for b in cost.by_model:
        tier = "long-context" if b.long_context else "standard"
        line(
            f"{indent}  {b.model}  ({tier}: "
            f"${b.rate_input:.2f}/${b.rate_cached:.2f}/${b.rate_output:.2f} per 1M "
            f"in/cache/out)"
        )
        line(
            f"{indent}    uncached in  {_fmt_tokens(b.uncached_input_tokens):>8}  →  "
            f"{format_usd(b.cost_uncached_input)}"
        )
        line(
            f"{indent}    cached in    {_fmt_tokens(b.cached_input_tokens):>8}  →  "
            f"{format_usd(b.cost_cached_input)}"
        )
        line(
            f"{indent}    output       {_fmt_tokens(b.output_tokens):>8}  →  "
            f"{format_usd(b.cost_output)}"
        )
        line(
            f"{indent}    subtotal                  {style.cyan(format_usd(b.cost_total))}"
        )

    line(
        f"{indent}  Window total               "
        f"{style.cyan(format_usd(cost.total_usd))}"
    )
    if cost.estimated_full_week_usd is not None:
        line(
            f"{indent}  Full week ≈                "
            f"{style.cyan(format_usd(cost.estimated_full_week_usd))}  "
            f"(at {cost.pool_percent_used:.1f}% used)"
        )
        line(
            f"{indent}  Remaining ≈                "
            f"{format_usd(cost.estimated_remaining_usd or 0)}"
        )
    # Counterfactual: all input billed at full (uncached) rate
    no_cache = 0.0
    for b in cost.by_model:
        all_in = b.uncached_input_tokens + b.cached_input_tokens
        no_cache += (all_in / 1_000_000.0) * b.rate_input
        no_cache += (b.output_tokens / 1_000_000.0) * b.rate_output
    if no_cache > cost.total_usd + 0.005:
        saved = no_cache - cost.total_usd
        line(
            f"{indent}  If no prompt cache ≈       {format_usd(no_cache)}  "
            f"(cache saved {style.green(format_usd(saved))})"
        )


def render_economics(
    eco: SuperGrokEconomics,
    *,
    style: Optional[Style] = None,
    indent: str = "  ",
    stream: Optional[TextIO] = None,
) -> None:
    """SuperGrok allotment / plan / API-equivalent rates."""
    stream = stream or sys.stdout
    style = style or Style(_use_color(stream))

    def line(text: str = "") -> None:
        print(text, file=stream)

    line(style.bold(f"{indent}SuperGrok economics"))
    line(
        style.dim(
            f"{indent}  (derived; allotment $ ≠ card charge; API $ = list price)"
        )
    )
    if eco.capacity_total is not None:
        line(
            f"{indent}  Capacity (calibrated)  "
            f"{style.cyan(_fmt_tokens(eco.capacity_total))}  "
            f"({eco.capacity_total:,}) tokens"
        )
    if eco.build_pool_percent is not None:
        line(
            f"{indent}  Build pool used       "
            f"{style.level(eco.build_pool_percent, f'{eco.build_pool_percent:.1f}%')}  "
            f"(local tokens ÷ capacity)"
        )
    if eco.usage_percent is not None:
        src = eco.usage_source.replace("_", " ")
        line(
            f"{indent}  Overall pool used     "
            f"{style.level(eco.usage_percent, f'{eco.usage_percent:.1f}%')}  "
            f"({src})"
        )
    if (
        eco.api_overall_percent is not None
        and eco.usage_source != "api"
        and abs((eco.usage_percent or 0) - eco.api_overall_percent) > 0.5
    ):
        line(
            style.dim(
                f"{indent}  Billing API says      {eco.api_overall_percent:.1f}%  "
                f"(may be stale)"
            )
        )
    if eco.remaining_tokens is not None:
        line(
            f"{indent}  Remaining ≈           "
            f"{_fmt_tokens(eco.remaining_tokens)} tokens"
        )

    line()
    line(style.dim(f"{indent}  USD per 1M tokens (this window)"))
    if eco.allotment_usd_per_mtok is not None:
        attr = (
            f"  from {format_usd(eco.attributed_allotment_usd or 0)} "
            f"allotment × build share"
            if eco.attributed_allotment_usd is not None
            else ""
        )
        line(
            f"{indent}    SuperGrok allotment "
            f"{style.cyan(f'${eco.allotment_usd_per_mtok:.4f}/MTok')}{attr}"
        )
    else:
        line(
            style.dim(
                f"{indent}    SuperGrok allotment —  "
                f"(need monthly usage + local tokens)"
            )
        )
    if eco.plan_usd_per_mtok is not None:
        line(
            f"{indent}    Plan amortized      "
            f"{style.cyan(f'${eco.plan_usd_per_mtok:.4f}/MTok')}  "
            f"(plan {format_usd(eco.plan_price_usd or 0)})"
        )
    else:
        line(
            style.dim(
                f"{indent}    Plan amortized      —  "
                f"(set with --plan-price USD)"
            )
        )
    if eco.api_equiv_usd_per_mtok is not None:
        line(
            f"{indent}    API list-equivalent "
            f"${eco.api_equiv_usd_per_mtok:.4f}/MTok  "
            f"({format_usd(eco.api_equiv_total_usd or 0)} total)"
        )
    if eco.build_share_fraction is not None:
        line(
            style.dim(
                f"{indent}  Build share {eco.build_share_fraction:.0%} "
                f"({(eco.build_share_source or '').replace('_', ' ')})"
            )
        )


def render_local_tokens(
    local: LocalTokenReport,
    *,
    period_label: str = "",
    show_top: bool = True,
    show_cost: bool = True,
    long_context: Optional[bool] = None,
    pool_percent: Optional[float] = None,
    economics: Optional[SuperGrokEconomics] = None,
    stream: Optional[TextIO] = None,
) -> None:
    stream = stream or sys.stdout
    style = Style(_use_color(stream))

    def line(text: str = "") -> None:
        print(text, file=stream)

    title = "Local Build tokens"
    if period_label:
        title = f"{title}  ({period_label})"
    line(style.bold(title))
    line(
        style.dim(
            f"  from {local.sessions_with_usage} sessions "
            f"({local.sessions_scanned} scanned) · Grok Build on this machine"
        )
    )
    line()
    _render_token_stats(local, style, line, show_top=show_top)
    if economics is not None:
        line()
        render_economics(economics, style=style, indent="  ", stream=stream)
    if show_cost:
        cost = analyze_cost(
            local, long_context=long_context, pool_percent_used=pool_percent
        )
        line()
        render_cost_analysis(cost, style=style, indent="  ", stream=stream)
    line()
    line(style.dim(f"  {local.source_note}"))


def render_text(
    report: Optional[UsageReport] = None,
    *,
    local: Optional[LocalTokenReport] = None,
    local_period_label: str = "",
    pool_percent_override: Optional[float] = None,
    effective_pool_percent: Optional[float] = None,
    usage_source: str = "api",
    local_estimate: Optional[LocalPoolEstimate] = None,
    economics: Optional[SuperGrokEconomics] = None,
    show_cost: bool = True,
    long_context: Optional[bool] = None,
    show_history: bool = False,
    show_top_sessions: bool = True,
    sections: tuple[str, ...] = ("weekly", "monthly", "local"),
    stream: Optional[TextIO] = None,
) -> None:
    stream = stream or sys.stdout
    style = Style(_use_color(stream))
    cols = shutil.get_terminal_size((80, 20)).columns
    bar_width = max(16, min(36, cols - 24))

    def line(text: str = "") -> None:
        print(text, file=stream)

    show_weekly = "weekly" in sections and report is not None
    show_monthly = "monthly" in sections and report is not None
    show_local = "local" in sections and local is not None

    line(style.bold("Grok usage"))
    if report and report.email:
        line(style.dim(f"  account  {report.email}"))
    line()

    if show_weekly and report is not None:
        weekly = report.weekly
        api_pct = weekly.credit_usage_percent
        if effective_pool_percent is not None:
            w_pct = float(effective_pool_percent)
        elif pool_percent_override is not None:
            w_pct = float(pool_percent_override)
        else:
            w_pct = api_pct
        period_label = _period_type_label(weekly.period.type)
        src_label = {
            "local_calibration": "local tokens",
            "override": "override",
            "api": "billing API",
            "none": "unknown",
        }.get(usage_source, usage_source)

        line(style.bold(f"Weekly pool  ({period_label})"))
        bar = style.level(w_pct, _bar(w_pct, bar_width))
        pct_txt = style.level(w_pct, f"{w_pct:.1f}% used")
        if usage_source == "local_calibration":
            pct_txt = style.level(
                w_pct, f"{w_pct:.1f}% used  (local-first · {src_label})"
            )
        elif usage_source == "override" and abs(w_pct - api_pct) > 0.05:
            pct_txt = style.level(
                w_pct,
                f"{w_pct:.1f}% used  (override; API says {api_pct:.1f}%)",
            )
        line(f"  {bar}  {pct_txt}")
        if (
            usage_source == "local_calibration"
            and abs(w_pct - api_pct) > 0.5
        ):
            line(
                style.dim(
                    f"  Billing API          {api_pct:.1f}% used  (may be delayed)"
                )
            )
        line(
            f"  API window   {_fmt_dt(weekly.period.start)}  →  "
            f"{_fmt_dt(weekly.period.end)}"
        )
        if local is not None and local.since is not None:
            until_label = _fmt_dt(local.until) if local.until else "now"
            line(
                f"  Token window {_fmt_dt(local.since)}  →  {until_label}"
            )
            if weekly.period.start and local.since != weekly.period.start:
                line(
                    style.dim(
                        "  (token window differs from API — using your override)"
                    )
                )
        line(f"  Resets {_remaining(weekly.period.end)}")

        if weekly.product_usage:
            line()
            line(style.dim("  By product  (billing API)"))
            for p in sorted(weekly.product_usage, key=lambda x: -x.usage_percent):
                pbar = style.level(p.usage_percent, _bar(p.usage_percent, 16))
                line(f"    {p.product:<14} {pbar}  {p.usage_percent:.1f}%")

        if weekly.prepaid_balance_cents is not None:
            line()
            line(
                f"  Extra usage credits  "
                f"{style.cyan(_fmt_money_cents(weekly.prepaid_balance_cents))} remaining"
            )

        if weekly.on_demand_cap_cents:
            line(
                f"  On-demand            "
                f"{_fmt_money_cents(weekly.on_demand_used_cents)} / "
                f"{_fmt_money_cents(weekly.on_demand_cap_cents)}"
            )

        # Embed local Build tokens for this same weekly window + pool estimate.
        if local is not None:
            t = local.total
            est = estimate_weekly_tokens(
                weekly,
                local,
                usage_percent_override=(
                    pool_percent_override
                    if pool_percent_override is not None
                    else (w_pct if usage_source != "api" else None)
                ),
            )
            line()
            line(style.dim("  Build tokens this week  (local, since pool start)"))
            line(
                style.dim(
                    f"    {local.sessions_with_usage} sessions · "
                    f"{local_period_label or 'subscription week'}"
                )
            )
            line(
                f"    Total used         "
                f"{style.cyan(_fmt_tokens(t.total_tokens))}  ({t.total_tokens:,})"
            )
            line(
                f"    Input / uncached   "
                f"{_fmt_tokens(t.input_tokens)} / "
                f"{_fmt_tokens(t.uncached_input_tokens)}"
            )
            line(
                f"    Output / reasoning "
                f"{_fmt_tokens(t.output_tokens)} / "
                f"{_fmt_tokens(t.reasoning_tokens)}"
            )
            line(f"    Calls / turns      {t.model_calls:,} / {t.turns:,}")

            line()
            line(style.bold("  Estimated full-week token pool"))
            if local_estimate is not None:
                cap = local_estimate.capacity_total
                # Remaining must match the % we actually display (not a stale
                # API/override figure mixed with local remaining).
                rem = max(
                    0,
                    int(round(cap * max(0.0, (100.0 - w_pct) / 100.0))),
                )
                line(
                    style.dim(
                        f"    method: calibrated capacity  ·  "
                        f"confidence: {local_estimate.confidence}  ·  "
                        f"live from local tokens"
                    )
                )
                line(
                    f"    Full week ≈        "
                    f"{style.cyan(_fmt_tokens(cap))}  ({cap:,}) tokens"
                )
                line(
                    f"    Build footprint    "
                    f"{local_estimate.build_pool_percent:.1f}% of capacity  "
                    f"({_fmt_tokens(t.total_tokens)} tokens)"
                )
                if abs(local_estimate.estimated_overall_percent - w_pct) > 1.0:
                    line(
                        style.dim(
                            f"    Local estimate     "
                            f"{local_estimate.estimated_overall_percent:.1f}% overall "
                            f"({local_estimate.build_pool_percent:.1f}% build)  ·  "
                            f"display uses {usage_source.replace('_', ' ')}"
                        )
                    )
                line(
                    f"    Overall used ≈     "
                    f"{w_pct:.1f}%  →  remaining ≈ "
                    f"{style.level(w_pct, _fmt_tokens(rem))}  "
                    f"({max(0.0, 100.0 - w_pct):.1f}% left)"
                )
                line(
                    f"    {_bar(w_pct, bar_width)}  "
                    f"{style.level(w_pct, f'{w_pct:.0f}% of ~{_fmt_tokens(cap)}')}"
                )
            elif est.estimated_capacity_total is not None:
                cap = est.estimated_capacity_total
                rem = est.estimated_remaining_total or 0
                used_equiv = est.estimated_used_via_percent
                line(
                    style.dim(
                        f"    method: local tokens ÷ {est.invert_percent:.1f}% "
                        f"({est.invert_basis.replace('_', ' ')})  ·  "
                        f"confidence: {est.confidence}"
                    )
                )
                line(
                    f"    Full week ≈        "
                    f"{style.cyan(_fmt_tokens(cap))}  ({cap:,}) tokens"
                )
                line(
                    f"    Used ≈             "
                    f"{_fmt_tokens(used_equiv or t.total_tokens)}  "
                    f"({w_pct:.1f}% of pool)"
                )
                line(
                    f"    Remaining ≈        "
                    f"{style.level(w_pct, _fmt_tokens(rem))}  "
                    f"({max(0.0, 100.0 - w_pct):.1f}% left)"
                )
                line(
                    f"    {_bar(w_pct, bar_width)}  "
                    f"{style.level(w_pct, f'{w_pct:.0f}% of ~{_fmt_tokens(cap)}')}"
                )
            else:
                line(style.dim("    (not enough data to invert pool size yet)"))

            if est.estimated_capacity_uncached is not None and local_estimate is None:
                line(
                    style.dim(
                        f"    Uncached-input basis: full week ≈ "
                        f"{_fmt_tokens(est.estimated_capacity_uncached)}, "
                        f"remaining ≈ "
                        f"{_fmt_tokens(est.estimated_remaining_uncached or 0)}"
                    )
                )

            if local_estimate and local_estimate.notes:
                line(style.dim(f"    note: {local_estimate.notes[0]}"))
            elif est.notes:
                line(style.dim(f"    note: {est.notes[0]}"))

            if economics is not None:
                line()
                render_economics(economics, style=style, indent="  ", stream=stream)

            if show_cost:
                cost = analyze_cost(
                    local,
                    long_context=long_context,
                    pool_percent_used=w_pct if w_pct > 0 else None,
                )
                line()
                render_cost_analysis(cost, style=style, indent="  ", stream=stream)

            if show_top_sessions and local.top_sessions:
                line(style.dim("    Top sessions"))
                for s in local.top_sessions[:3]:
                    label = s.title or s.session_id[:12]
                    if len(label) > 44:
                        label = label[:41] + "…"
                    line(f"      {_fmt_tokens(s.usage.total_tokens):>8}  {label}")

        if show_monthly:
            line()

    if show_monthly and report is not None:
        monthly = report.monthly
        m_pct = monthly.usage_percent
        # API format=tokens returns USD cents (matches Grok Build /usage labels).
        line(style.bold("Monthly usage  (included allotment)"))
        mbar = style.level(m_pct, _bar(m_pct, bar_width))
        used = _fmt_money_cents(monthly.used_cents)
        limit = _fmt_money_cents(monthly.monthly_limit_cents)
        line(
            f"  {mbar}  "
            f"{style.level(m_pct, f'{used} / {limit}')}  "
            f"({m_pct:.1f}%)"
        )
        line(f"  {_fmt_dt(monthly.period_start)}  →  {_fmt_dt(monthly.period_end)}")
        remaining_cents = max(0, monthly.monthly_limit_cents - monthly.used_cents)
        line(f"  Remaining            {style.cyan(_fmt_money_cents(remaining_cents))}")

        if show_history and monthly.history:
            line()
            line(style.dim("  History"))
            for h in monthly.history:
                line(
                    f"    {h.year}-{h.month:02d}  "
                    f"included {_fmt_money_cents(h.included_used_cents)}  "
                    f"on-demand {_fmt_money_cents(h.on_demand_used_cents)}  "
                    f"total {_fmt_money_cents(h.total_used_cents)}"
                )

        if show_local:
            line()

    # Detailed local section when weekly didn't already embed it, or when
    # the user asked for a non-week period / tokens-only style detail.
    # Default full view: weekly embeds a summary; skip the duplicate block
    # unless period is not the subscription week (handled by caller sections).
    if show_local and local is not None and not show_weekly:
        title = "Local Build tokens"
        if local_period_label:
            title = f"{title}  ({local_period_label})"
        line(style.bold(title))
        line(
            style.dim(
                f"  from {local.sessions_with_usage} sessions "
                f"({local.sessions_scanned} scanned) · this machine"
            )
        )
        line()
        _render_token_stats(
            local,
            style,
            line,
            show_top=show_top_sessions,
            top_n=5,
        )

    line()
    line(style.dim("Tip: open https://grok.com/?_s=usage for the full Usage tab."))


def render_json(report: UsageReport, stream: Optional[TextIO] = None) -> None:
    stream = stream or sys.stdout
    json.dump(report_to_dict(report), stream, indent=2)
    stream.write("\n")
