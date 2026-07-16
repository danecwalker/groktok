"""Command-line entrypoint for groktok."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Optional, Sequence, Tuple

from . import __version__
from .auth import AuthError, load_credentials
from .billing import BillingError, UsageReport, fetch_usage
from .config import clear_config, load_config
from .display import render_local_tokens, render_text, report_to_dict
from .estimate import estimate_weekly_tokens
from .interactive import run_interactive
from .local_tokens import (
    parse_since_arg,
    report_to_dict as local_report_to_dict,
    resolve_period,
    scan_local_tokens,
)
from .pricing import analyze_cost


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="groktok",
        description=(
            "Show your Grok subscription weekly usage pool (with local Build "
            "token counts), monthly allotment, and session tokens.\n\n"
            "If the API week is stale (e.g. pool reset this morning):\n"
            "  groktok -i\n"
            "  groktok --since morning --pool-percent 0"
        ),
        epilog=(
            "Auth: ~/.grok/auth.json from `grok login`, or GROKTOK_TOKEN.\n"
            "Local tokens: ~/.grok/sessions turn_completed usage.\n"
            "Saved overrides: ~/.grok/groktok.json (from -i).\n"
            "Usage tab: https://grok.com/?_s=usage"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Interactively set week start / pool %% (for mid-week resets)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON")
    parser.add_argument(
        "--history", action="store_true", help="Include monthly usage history"
    )
    parser.add_argument(
        "--period",
        default="week",
        metavar="PERIOD",
        help="Token window: week, 7d, today/morning, month, all (default: week)",
    )
    parser.add_argument(
        "--since",
        metavar="WHEN",
        help="Override token start: morning, today, 2026-07-16, -6h, …",
    )
    parser.add_argument(
        "--until",
        metavar="WHEN",
        help="Override token end (default now). Same formats as --since",
    )
    parser.add_argument(
        "--pool-percent",
        type=float,
        metavar="PCT",
        help="Override weekly pool %% used (e.g. 0 after a reset)",
    )
    parser.add_argument(
        "--ignore-saved",
        action="store_true",
        help="Ignore overrides in ~/.grok/groktok.json",
    )
    parser.add_argument(
        "--clear-overrides",
        action="store_true",
        help="Delete saved overrides and exit",
    )
    parser.add_argument(
        "--top", type=int, default=5, metavar="N", help="Top sessions to list"
    )
    parser.add_argument(
        "--no-local", action="store_true", help="Skip local session scan"
    )
    parser.add_argument(
        "--no-cost",
        action="store_true",
        help="Hide API-equivalent cost analysis",
    )
    parser.add_argument(
        "--long-context",
        action="store_true",
        help="Force long-context API rates (≥200k prompt tier)",
    )
    parser.add_argument(
        "--standard-rates",
        action="store_true",
        help="Force standard (short-context) API rates",
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--weekly",
        action="store_true",
        help="Weekly pool + local Build tokens",
    )
    scope.add_argument(
        "--monthly", action="store_true", help="Monthly usage only (remote)"
    )
    scope.add_argument(
        "--tokens",
        action="store_true",
        help="Local Build tokens only (offline)",
    )
    return parser


def _resolve_window(
    args: argparse.Namespace,
    report: Optional[UsageReport],
) -> Tuple[Optional[datetime], Optional[datetime], str, Optional[float], str]:
    """Resolve (since, until, label, pool_percent_override, source)."""
    cfg = None if args.ignore_saved else load_config()
    pool_pct: Optional[float] = args.pool_percent

    # 1) Explicit --since wins
    if args.since:
        since = parse_since_arg(args.since)
        until = parse_since_arg(args.until) if args.until else None
        label = f"since {since.astimezone().strftime('%b %d %H:%M %Z')}"
        if until:
            label = (
                f"{since.astimezone().strftime('%b %d %H:%M')} → "
                f"{until.astimezone().strftime('%b %d %H:%M %Z')}"
            )
        if pool_pct is None and cfg and cfg.pool_percent_override is not None:
            pool_pct = cfg.pool_percent_override
        return since, until, label, pool_pct, "flag"

    # 2) Saved week-start override
    if cfg and cfg.week_start_override:
        since = cfg.week_start_dt()
        until = cfg.week_end_dt()
        label = cfg.note or "saved override"
        if pool_pct is None:
            pool_pct = cfg.pool_percent_override
        return since, until, label, pool_pct, "saved"

    # 3) Period presets / API week
    period = "week" if args.weekly else args.period
    weekly_start = report.weekly.period.start if report else None
    weekly_end = report.weekly.period.end if report else None
    since, until, label = resolve_period(
        period, weekly_start=weekly_start, weekly_end=weekly_end
    )
    if pool_pct is None and cfg and cfg.pool_percent_override is not None:
        pool_pct = cfg.pool_percent_override
        return since, until, label, pool_pct, "saved-percent"
    return since, until, label, pool_pct, "default"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.clear_overrides:
        if clear_config():
            print("Cleared saved overrides (~/.grok/groktok.json)")
        else:
            print("No saved overrides to clear")
        return 0

    want_remote = not args.tokens or args.interactive
    want_local = not args.no_local and (
        args.tokens or args.weekly or args.interactive or not args.monthly
    )
    if args.monthly and not args.interactive:
        want_local = False
    if args.no_local:
        want_local = False

    report: Optional[UsageReport] = None
    if want_remote:
        try:
            creds = load_credentials()
            report = fetch_usage(creds)
        except AuthError as exc:
            if want_local and not args.weekly and not args.monthly:
                print(f"warning: {exc}", file=sys.stderr)
                want_remote = False
            else:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        except BillingError as exc:
            if want_local and not args.weekly and not args.monthly:
                print(f"warning: {exc}", file=sys.stderr)
                want_remote = False
                report = None
            else:
                print(f"error: {exc}", file=sys.stderr)
                return 1

    pool_percent_override: Optional[float] = None
    period_label = ""
    local = None
    show_cost = not args.no_cost
    long_context: Optional[bool]
    if args.long_context:
        long_context = True
    elif args.standard_rates:
        long_context = False
    else:
        long_context = None  # auto

    if args.interactive:
        try:
            ov = run_interactive(report, top_n=max(0, args.top))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            print("\nAborted.", file=sys.stderr)
            return 130
        period_label = ov.period_label
        pool_percent_override = ov.pool_percent
        local = scan_local_tokens(
            since=ov.since, until=ov.until, top_n=max(0, args.top)
        )
    elif want_local:
        try:
            since, until, period_label, pool_percent_override, _src = _resolve_window(
                args, report
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        local = scan_local_tokens(since=since, until=until, top_n=max(0, args.top))

    # --- JSON ---
    if args.json:
        payload: dict = {}
        if report is not None and want_remote:
            payload.update(report_to_dict(report))
            if args.weekly:
                payload.pop("monthly", None)
            elif args.monthly:
                payload.pop("weekly", None)
            if not args.history and isinstance(payload.get("monthly"), dict):
                payload["monthly"].pop("history", None)
        if local is not None:
            local_dict = local_report_to_dict(local)
            local_dict["period_label"] = period_label
            if pool_percent_override is not None:
                local_dict["pool_percent_override"] = pool_percent_override
            # Effective pool % for cost invert
            eff_pct = pool_percent_override
            if eff_pct is None and report is not None:
                eff_pct = report.weekly.credit_usage_percent
            if show_cost:
                cost = analyze_cost(
                    local,
                    long_context=long_context,
                    pool_percent_used=eff_pct if eff_pct and eff_pct > 0 else None,
                )
                local_dict["api_cost"] = cost.as_dict()
            payload["local_tokens"] = local_dict
            if isinstance(payload.get("weekly"), dict) and report is not None:
                payload["weekly"]["build_tokens"] = local_dict
                est = estimate_weekly_tokens(
                    report.weekly,
                    local,
                    usage_percent_override=pool_percent_override,
                )
                payload["weekly"]["token_pool_estimate"] = est.as_dict()
                if pool_percent_override is not None:
                    payload["weekly"]["pool_percent_override"] = pool_percent_override
                if show_cost and "api_cost" in local_dict:
                    payload["weekly"]["api_cost"] = local_dict["api_cost"]
        if args.tokens and not args.interactive:
            payload = {
                "local_tokens": local_report_to_dict(local) if local else {},
                "period_label": period_label,
            }
            if local is not None and show_cost:
                payload["local_tokens"]["api_cost"] = analyze_cost(
                    local, long_context=long_context
                ).as_dict()
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    # --- text: tokens only ---
    if args.tokens and not args.interactive:
        assert local is not None
        render_local_tokens(
            local,
            period_label=period_label,
            show_top=args.top > 0,
            show_cost=show_cost,
            long_context=long_context,
            pool_percent=pool_percent_override,
        )
        return 0

    # --- text: weekly / interactive ---
    if args.weekly or args.interactive:
        if report is None:
            if local is not None:
                render_local_tokens(
                    local,
                    period_label=period_label,
                    show_top=args.top > 0,
                    show_cost=show_cost,
                    long_context=long_context,
                    pool_percent=pool_percent_override,
                )
                return 0
            print("error: no data to show", file=sys.stderr)
            return 1
        render_text(
            report,
            local=local,
            local_period_label=period_label,
            pool_percent_override=pool_percent_override,
            show_cost=show_cost,
            long_context=long_context,
            show_top_sessions=args.top > 0,
            sections=("weekly",),
        )
        return 0

    if args.monthly:
        assert report is not None
        render_text(report, show_history=args.history, sections=("monthly",))
        return 0

    # --- text: default full ---
    sections: list[str] = []
    if report is not None and want_remote:
        sections.extend(["weekly", "monthly"])
    if local is not None and report is None:
        sections.append("local")
    elif (
        local is not None
        and args.period not in ("week", "weekly", "subscription")
        and not args.since
        and not (load_config().week_start_override if not args.ignore_saved else None)
    ):
        sections.append("local")

    render_text(
        report,
        local=local,
        local_period_label=period_label,
        pool_percent_override=pool_percent_override,
        show_cost=show_cost,
        long_context=long_context,
        show_history=args.history,
        show_top_sessions=args.top > 0,
        sections=tuple(sections) if sections else ("weekly", "monthly"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
