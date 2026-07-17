"""Human-readable and JSON rendering for billing usage reports."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from typing import Any, Optional, TextIO

from .billing import UsageReport
from .local_tokens import LocalTokenReport


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
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:,}"


def _remaining(end: Optional[datetime]) -> str:
    if not end:
        return "—"
    now = datetime.now(timezone.utc)
    secs = int((end - now).total_seconds())
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
    return raw.replace("USAGE_PERIOD_TYPE_", "").replace("_", " ").title()


def report_to_dict(
    report: UsageReport,
    *,
    history: bool = False,
    local: Optional[LocalTokenReport] = None,
) -> dict[str, Any]:
    weekly = report.weekly
    monthly = report.monthly

    def iso(dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None

    weekly_dict: dict[str, Any] = {
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
    }
    if local is not None:
        weekly_dict["local_build_tokens"] = local.as_dict()

    payload: dict[str, Any] = {
        "account": {
            "email": report.email,
            "user_id": report.user_id,
            "team_id": report.team_id,
            "auth_source": report.credentials_source,
        },
        "weekly": weekly_dict,
        "monthly": {
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
        },
    }
    if history:
        payload["monthly"]["history"] = [
            {
                "year": h.year,
                "month": h.month,
                "included_used_usd": h.included_used_cents / 100.0,
                "on_demand_used_usd": h.on_demand_used_cents / 100.0,
                "total_used_usd": h.total_used_cents / 100.0,
            }
            for h in monthly.history
        ]
    return payload


def render_text(
    report: UsageReport,
    *,
    local: Optional[LocalTokenReport] = None,
    show_history: bool = False,
    sections: tuple[str, ...] = ("weekly", "monthly"),
    stream: Optional[TextIO] = None,
) -> None:
    stream = stream or sys.stdout
    style = Style(_use_color(stream))
    cols = shutil.get_terminal_size((80, 20)).columns
    bar_width = max(16, min(36, cols - 24))

    def line(text: str = "") -> None:
        print(text, file=stream)

    line(style.bold("Grok usage"))
    if report.email:
        line(style.dim(f"  account  {report.email}"))
    line()

    if "weekly" in sections:
        weekly = report.weekly
        w_pct = weekly.credit_usage_percent
        period_label = _period_type_label(weekly.period.type)
        line(style.bold(f"Weekly pool  ({period_label})"))
        bar = style.level(w_pct, _bar(w_pct, bar_width))
        line(f"  {bar}  {style.level(w_pct, f'{w_pct:.1f}% used')}")
        line(
            f"  {_fmt_dt(weekly.period.start)}  →  {_fmt_dt(weekly.period.end)}"
        )
        line(f"  Resets {_remaining(weekly.period.end)}")

        if weekly.product_usage:
            line()
            line(style.dim("  By product"))
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

        if local is not None:
            t = local.total
            line()
            title = "  Local Build tokens  (this machine, weekly window)"
            if local.matched_models:
                if len(local.matched_models) == 1:
                    title = (
                        f"  Local Build tokens  "
                        f"({local.matched_models[0]}, weekly window)"
                    )
                else:
                    title = (
                        f"  Local Build tokens  "
                        f"(models: {', '.join(local.matched_models)}, weekly window)"
                    )
            line(style.dim(title))
            line(
                style.dim(
                    f"    {local.sessions_with_usage} sessions with usage · "
                    f"{local.sessions_scanned} scanned"
                )
            )
            if local.model_filter:
                line(
                    style.dim(
                        f"    filter             --model {local.model_filter!r} "
                        f"→ {', '.join(local.matched_models)}"
                    )
                )
            line(
                f"    Total              "
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
            if local.by_model and (
                not local.matched_models or len(local.by_model) > 1
            ):
                line(style.dim("    By model"))
                for model, bucket in sorted(
                    local.by_model.items(), key=lambda kv: -kv[1].total_tokens
                ):
                    line(
                        f"      {model:<16} "
                        f"{_fmt_tokens(bucket.total_tokens):>10}  "
                        f"({bucket.turns} turns)"
                    )
            line(style.dim(f"    {local.source_note}"))

        if "monthly" in sections:
            line()

    if "monthly" in sections:
        monthly = report.monthly
        m_pct = monthly.usage_percent
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

    line()
    line(style.dim("Tip: open https://grok.com/?_s=usage for the full Usage tab."))
