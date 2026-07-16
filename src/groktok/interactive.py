"""Interactive prompts for week-start / pool-% overrides."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, TextIO

from .billing import UsageReport
from .config import GroktokConfig, clear_config, config_path, load_config, save_config
from .display import Style, _fmt_dt, _fmt_tokens, _use_color
from .estimate import estimate_weekly_tokens
from .local_tokens import scan_local_tokens


@dataclass
class SessionOverrides:
    """Effective window + % after interactive / CLI resolution."""

    since: Optional[datetime]
    until: Optional[datetime]
    period_label: str
    pool_percent: Optional[float]  # None = use API value
    source: str  # api | saved | interactive | flag
    saved: bool = False


def _prompt(msg: str, default: str = "", stream_in: TextIO = None, stream_out: TextIO = None) -> str:
    stream_in = stream_in or sys.stdin
    stream_out = stream_out or sys.stdout
    suffix = f" [{default}]" if default else ""
    stream_out.write(f"{msg}{suffix}: ")
    stream_out.flush()
    try:
        line = stream_in.readline()
    except KeyboardInterrupt:
        stream_out.write("\n")
        raise
    if not line:
        return default
    text = line.strip()
    return text if text else default


def _parse_user_datetime(raw: str, *, now: Optional[datetime] = None) -> datetime:
    """
    Accept flexible inputs:
      today, yesterday, morning, this morning,
      2026-07-16, 2026-07-16 09:30, 2026-07-16T09:30:00,
      9am, 9:30am, now, -6h, -1d
    Local timezone assumed when omitted.
    """
    now = now or datetime.now().astimezone()
    text = raw.strip().lower()

    if text in ("now",):
        return now.astimezone(timezone.utc)

    if text in ("today", "midnight", "this morning", "morning"):
        local = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return local.astimezone(timezone.utc)

    if text in (
        "yesterday",
        "yesterday morning",
        "yesterday midnight",
        "last night",
    ):
        local = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return local.astimezone(timezone.utc)

    m = re.fullmatch(r"-?(\d+)\s*h(ours?)?", text)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).astimezone(timezone.utc)

    m = re.fullmatch(r"-?(\d+)\s*d(ays?)?", text)
    if m:
        return (now - timedelta(days=int(m.group(1)))).astimezone(timezone.utc)

    m = re.fullmatch(r"-?(\d+)\s*m(in(ute)?s?)?", text)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).astimezone(timezone.utc)

    # 9am / 9:30am / 14:00 today
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        local = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return local.astimezone(timezone.utc)

    # ISO-ish / common date formats
    candidates = [raw.strip()]
    if "T" not in raw and " " in raw.strip():
        candidates.append(raw.strip().replace(" ", "T"))
    for c in candidates:
        try:
            if c.endswith("Z"):
                c = c[:-1] + "+00:00"
            dt = datetime.fromisoformat(c)
            if dt.tzinfo is None:
                # interpret as local
                dt = dt.replace(tzinfo=now.tzinfo or timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    # date only YYYY-MM-DD
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw.strip())
    if m:
        local = now.replace(
            year=int(m.group(1)),
            month=int(m.group(2)),
            day=int(m.group(3)),
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return local.astimezone(timezone.utc)

    raise ValueError(
        f"Could not parse date/time {raw!r}. Try: today, morning, yesterday, "
        f"2026-07-16, 2026-07-16 09:30, 9am, -6h"
    )


def run_interactive(
    report: Optional[UsageReport],
    *,
    top_n: int = 5,
) -> SessionOverrides:
    """Prompt the user to confirm/fix the week window and pool %."""
    style = Style(_use_color(sys.stdout))
    cfg = load_config()
    api_start = report.weekly.period.start if report else None
    api_end = report.weekly.period.end if report else None
    api_pct = report.weekly.credit_usage_percent if report else None

    print(style.bold("groktok — interactive week setup"))
    print()
    if report:
        print(style.dim("From Grok billing API:"))
        print(f"  Week start   {_fmt_dt(api_start)}")
        print(f"  Week end     {_fmt_dt(api_end)}")
        print(f"  Pool used    {api_pct:.1f}%")
        print()
    else:
        print(style.dim("No remote billing data — set the window manually."))
        print()

    if cfg.week_start_override or cfg.pool_percent_override is not None:
        print(style.dim(f"Saved overrides in {config_path()}:"))
        if cfg.week_start_override:
            print(f"  week start   {_fmt_dt(cfg.week_start_dt())}")
        if cfg.pool_percent_override is not None:
            print(f"  pool %       {cfg.pool_percent_override:.1f}%")
        print()

    print("When did this weekly pool period start / last reset?")
    print(
        "  [1] API week start"
        + (f"  ({_fmt_dt(api_start)})" if api_start else "  (unavailable)")
    )
    print("  [2] This morning  (today at local midnight)")
    print("  [3] Yesterday morning  (yesterday at local midnight)")
    print(
        "  [4] Saved override"
        + (
            f"  ({_fmt_dt(cfg.week_start_dt())})"
            if cfg.week_start_override
            else "  (none)"
        )
    )
    print(
        "  [5] Type a relative time  "
        "(morning, yesterday, -6h, … — no calendar date needed)"
    )
    print("  [6] Clear saved overrides and use API")
    # Default to this morning when API still shows high usage — common reset lag.
    default_choice = "2" if (api_pct is not None and api_pct >= 5) else (
        "1" if api_start else "2"
    )
    choice = _prompt("Choice", default_choice)

    since: Optional[datetime] = None
    until: Optional[datetime] = api_end
    period_label = "subscription week"
    source = "interactive"

    if choice == "6":
        clear_config()
        cfg = GroktokConfig()
        print(style.dim("  Cleared saved overrides."))
        choice = "1" if api_start else "2"

    if choice == "1":
        since = api_start
        until = api_end
        period_label = "API subscription week"
        source = "api"
    elif choice == "2":
        since = _parse_user_datetime("morning")
        until = None
        period_label = "since local midnight (this morning)"
    elif choice == "3":
        since = _parse_user_datetime("yesterday")
        until = None
        period_label = "since yesterday morning"
    elif choice == "4":
        if not cfg.week_start_override:
            print("  No saved week start — pick a relative time.")
            choice = "5"
        else:
            since = cfg.week_start_dt()
            until = cfg.week_end_dt()
            period_label = "saved override"
            source = "saved"
    if choice == "5":
        raw = _prompt(
            "Week start (morning, yesterday, yesterday morning, -12h, …)",
            "yesterday morning",
        )
        since = _parse_user_datetime(raw)
        period_label = f"since {_fmt_dt(since)}"
        end_raw = _prompt("Week end (blank = now)", "")
        if end_raw:
            until = _parse_user_datetime(end_raw)
            period_label = f"{_fmt_dt(since)} → {_fmt_dt(until)}"
        else:
            until = None

    if since is None:
        since = api_start or _parse_user_datetime("morning")
        period_label = period_label or "fallback"

    print()
    print("Current weekly pool % used?")
    api_label = f"{api_pct:.1f}%" if api_pct is not None else "n/a"
    print(f"  [1] Use API value  ({api_label})")
    print("  [2] Enter manually  (use 0 if it just reset)")
    if cfg.pool_percent_override is not None:
        print(f"  [3] Saved override  ({cfg.pool_percent_override:.1f}%)")
    pct_choice = _prompt("Choice", "1" if api_pct is not None else "2")
    pool_percent: Optional[float] = None
    if pct_choice == "3" and cfg.pool_percent_override is not None:
        pool_percent = cfg.pool_percent_override
        source = "saved"
    elif pct_choice == "2":
        raw_pct = _prompt("Pool percent used (0–100)", "0")
        try:
            pool_percent = float(raw_pct)
        except ValueError as exc:
            raise ValueError(f"Invalid percent: {raw_pct}") from exc
        pool_percent = max(0.0, min(100.0, pool_percent))
    else:
        pool_percent = None  # use API

    print()
    # Preview scan
    print(style.dim("Scanning local sessions…"))
    local = scan_local_tokens(since=since, until=until, top_n=top_n)
    print(
        f"  Tokens since {_fmt_dt(since)}: "
        f"{_fmt_tokens(local.total.total_tokens)}  "
        f"({local.total.total_tokens:,}) across {local.sessions_with_usage} sessions"
    )

    if report is not None:
        # Apply percent override for preview estimate
        weekly = report.weekly
        effective_pct = (
            pool_percent if pool_percent is not None else weekly.credit_usage_percent
        )
        # Temporarily monkey via estimate override param
        est = estimate_weekly_tokens(
            weekly,
            local,
            usage_percent_override=pool_percent,
        )
        if est.estimated_capacity_total is not None:
            print(
                f"  Estimated full week ≈ {_fmt_tokens(est.estimated_capacity_total)}  "
                f"(from {effective_pct:.1f}% used)"
            )
            print(
                f"  Estimated remaining ≈ {_fmt_tokens(est.estimated_remaining_total or 0)}"
            )
        elif effective_pct is not None and effective_pct <= 0:
            print(
                style.dim(
                    "  Pool at 0% — capacity estimate needs some usage first "
                    "(or enter a known prior %)."
                )
            )
    print()

    save = _prompt("Save these overrides for next time? [y/N]", "n").lower()
    saved = False
    if save in ("y", "yes"):
        # Preserve plan price + any existing calibration; main() may refresh cal.
        prev = load_config()
        new_cfg = GroktokConfig(
            week_start_override=since.isoformat() if since else None,
            week_end_override=until.isoformat() if until else None,
            pool_percent_override=pool_percent,
            note=period_label,
            plan_price_usd=prev.plan_price_usd,
            calibration=prev.calibration,
        )
        path = save_config(new_cfg)
        print(style.dim(f"  Saved to {path}"))
        saved = True

    print()
    return SessionOverrides(
        since=since,
        until=until,
        period_label=period_label,
        pool_percent=pool_percent,
        source=source,
        saved=saved,
    )
