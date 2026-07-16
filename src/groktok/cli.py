"""Command-line entrypoint for groktok."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional, Sequence, Tuple

from . import __version__
from .auth import AuthError, load_credentials
from .billing import BillingError, UsageReport, fetch_usage
from .calibration import (
    calibrate_from_weekly,
    estimate_from_calibration,
    maybe_update_week_start,
    resolve_effective_pool_percent,
    should_recalibrate,
)
from .config import (
    clear_calibration,
    clear_config,
    load_config,
    save_config,
    update_config,
)
from .display import render_local_tokens, render_text, report_to_dict
from .economics import analyze_economics
from .estimate import estimate_weekly_tokens
from .interactive import run_interactive
from .local_tokens import (
    parse_since_arg,
    report_to_dict as local_report_to_dict,
    resolve_period,
    scan_local_tokens,
)
from .pricing import analyze_cost

# Bump when the machine-readable JSON shape changes incompatibly.
JSON_SCHEMA_VERSION = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="groktok",
        description=(
            "Show your Grok subscription weekly usage pool (with local Build "
            "token counts), monthly allotment, and SuperGrok economics.\n\n"
            "Local-first usage: calibrates pool capacity from billing once, then\n"
            "tracks %% from local Build tokens (billing API can lag after resets).\n\n"
            "If the week reset but the API is stale:\n"
            "  groktok -i\n"
            "  groktok --since morning --pool-percent 0 --recalibrate-window\n\n"
            "Tools / scripts: use --json (or --format json) for a stable schema."
        ),
        epilog=(
            "Auth: ~/.grok/auth.json from `grok login`, or GROKTOK_TOKEN.\n"
            "Local tokens: ~/.grok/sessions turn_completed usage.\n"
            "Saved state: ~/.grok/groktok.json (overrides + calibration).\n"
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
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON on stdout (alias for --format json)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default=None,
        dest="output_format",
        help="Output format: text (default) or json (for tools/scripts)",
    )
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
        "--plan-price",
        type=float,
        metavar="USD",
        help="Monthly plan fee USD (saved; used for amortized $/MTok)",
    )
    parser.add_argument(
        "--usage-source",
        choices=("auto", "local", "api"),
        default="auto",
        help="Pool %% source: auto (local-first), local, or api (default: auto)",
    )
    parser.add_argument(
        "--recalibrate",
        action="store_true",
        help="Force re-anchor pool capacity from current API/%% + local tokens",
    )
    parser.add_argument(
        "--recalibrate-window",
        action="store_true",
        help="Keep capacity; move calibration week start to --since (early reset)",
    )
    parser.add_argument(
        "--clear-calibration",
        action="store_true",
        help="Drop saved pool capacity calibration and exit",
    )
    parser.add_argument(
        "--ignore-saved",
        action="store_true",
        help="Ignore overrides/calibration in ~/.grok/groktok.json",
    )
    parser.add_argument(
        "--clear-overrides",
        action="store_true",
        help="Delete saved overrides + calibration and exit",
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
        "--no-economics",
        action="store_true",
        help="Hide SuperGrok economics block",
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


def _want_json(args: argparse.Namespace) -> bool:
    if args.json:
        return True
    return args.output_format == "json"


def _json_envelope(**fields: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "schema_version": JSON_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }


def _emit_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _fail(
    *,
    as_json: bool,
    code: str,
    message: str,
    exit_code: int,
) -> int:
    if as_json:
        _emit_json(
            {
                "ok": False,
                "version": __version__,
                "schema_version": JSON_SCHEMA_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "error": {"code": code, "message": str(message)},
            }
        )
    else:
        print(f"error: {message}", file=sys.stderr)
    return exit_code


def _resolve_window(
    args: argparse.Namespace,
    report: Optional[UsageReport],
    cfg_week_start: Optional[datetime] = None,
    cfg_week_end: Optional[datetime] = None,
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

    # 2b) Calibration week start (local-first window without saved override)
    if cfg_week_start is not None and (
        args.usage_source in ("auto", "local") or args.weekly
    ):
        # Prefer calibration window when it differs from a lagging API start
        api_start = report.weekly.period.start if report else None
        use_cal_window = api_start is None or abs(
            (cfg_week_start - api_start).total_seconds()
        ) > 3600
        # Also use cal window if user prefers local usage source
        if use_cal_window or args.usage_source == "local":
            since = cfg_week_start
            until = cfg_week_end
            label = "calibrated week"
            return since, until, label, pool_pct, "calibration"

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


def _manage_calibration(
    args: argparse.Namespace,
    *,
    report: Optional[UsageReport],
    local: Any,
    pool_percent_override: Optional[float],
    since: Optional[datetime],
    until: Optional[datetime],
) -> tuple[Any, Any]:
    """
    Load/update calibration; return (calibration, local_estimate).
    """
    if args.ignore_saved:
        cfg = load_config()  # still allow plan price from env? no — ignore all
        # Fresh empty for calibration decisions but keep in-memory only
        cal = None
        plan_only = None
    else:
        cfg = load_config()
        cal = cfg.calibration
        plan_only = cfg.plan_price_usd

    local_est = None
    if local is None or local.total.total_tokens <= 0:
        if cal is not None and not args.ignore_saved:
            local_est = None
        return cal, local_est

    # Early-reset path: keep capacity, move window
    if args.recalibrate_window and cal is not None and since is not None:
        cal = maybe_update_week_start(
            cal, week_start=since, week_end=until, source="manual"
        )
        if not args.ignore_saved:
            update_config(calibration=cal)
        local_est = estimate_from_calibration(cal, local)
        return cal, local_est

    # Build candidate from API when possible
    candidate = None
    api_pct = None
    if report is not None:
        api_pct = report.weekly.credit_usage_percent
        # Only invert when we have a positive anchor
        anchor = (
            pool_percent_override
            if pool_percent_override is not None
            else api_pct
        )
        if anchor is not None and anchor > 0:
            candidate = calibrate_from_weekly(
                report.weekly,
                local,
                usage_percent_override=pool_percent_override,
                source=(
                    "manual" if pool_percent_override is not None else "api"
                ),
                week_start=since or local.since or report.weekly.period.start,
                week_end=until or local.until or report.weekly.period.end,
            )

    force = bool(args.recalibrate)
    if should_recalibrate(
        cal if not args.ignore_saved else None,
        candidate,
        force=force,
        api_overall=api_pct,
    ):
        cal = candidate
        if cal is not None and not args.ignore_saved:
            update_config(calibration=cal)
            if not _want_json(args):
                print(
                    f"calibrated pool capacity ≈ {cal.capacity_total:,} tokens "
                    f"({cal.confidence} confidence, source={cal.source})",
                    file=sys.stderr,
                )

    # Apply local estimate from saved/current calibration
    if cal is not None:
        local_est = estimate_from_calibration(cal, local)

    return cal, local_est


def _build_json_payload(
    args: argparse.Namespace,
    *,
    report: Optional[UsageReport],
    want_remote: bool,
    local: Any,
    period_label: str,
    pool_percent_override: Optional[float],
    show_cost: bool,
    long_context: Optional[bool],
    calibration: Any,
    local_estimate: Any,
    economics: Any,
    effective_pct: Optional[float],
    usage_source: str,
    plan_price: Optional[float],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}

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
        if show_cost:
            cost = analyze_cost(
                local,
                long_context=long_context,
                pool_percent_used=(
                    effective_pct if effective_pct and effective_pct > 0 else None
                ),
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

    if calibration is not None:
        payload["calibration"] = calibration.as_dict()
    if local_estimate is not None:
        payload["local_pool_estimate"] = local_estimate.as_dict()
    if economics is not None:
        payload["supergrok_economics"] = economics.as_dict()
    if effective_pct is not None:
        payload["effective_pool_percent"] = effective_pct
        payload["usage_source"] = usage_source
    if plan_price is not None:
        payload["plan_price_usd"] = plan_price

    if args.tokens and not args.interactive:
        local_only: dict[str, Any] = local_report_to_dict(local) if local else {}
        if local is not None:
            local_only["period_label"] = period_label
            if show_cost:
                local_only["api_cost"] = analyze_cost(
                    local, long_context=long_context
                ).as_dict()
        payload_tokens: dict[str, Any] = {
            "local_tokens": local_only,
            "period_label": period_label,
        }
        if calibration is not None:
            payload_tokens["calibration"] = calibration.as_dict()
        if local_estimate is not None:
            payload_tokens["local_pool_estimate"] = local_estimate.as_dict()
        if economics is not None:
            payload_tokens["supergrok_economics"] = economics.as_dict()
        if effective_pct is not None:
            payload_tokens["effective_pool_percent"] = effective_pct
            payload_tokens["usage_source"] = usage_source
        if plan_price is not None:
            payload_tokens["plan_price_usd"] = plan_price
        payload = payload_tokens

    return _json_envelope(**payload)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    as_json = _want_json(args)

    if args.clear_overrides:
        cleared = clear_config()
        if as_json:
            _emit_json(
                _json_envelope(
                    cleared=cleared,
                    path="~/.grok/groktok.json",
                )
            )
        elif cleared:
            print("Cleared saved overrides + calibration (~/.grok/groktok.json)")
        else:
            print("No saved overrides to clear")
        return 0

    if args.clear_calibration:
        cleared = clear_calibration()
        if as_json:
            _emit_json(_json_envelope(cleared_calibration=cleared))
        elif cleared:
            print("Cleared pool calibration")
        else:
            print("No calibration to clear")
        return 0

    if args.plan_price is not None:
        if args.plan_price < 0:
            return _fail(
                as_json=as_json,
                code="usage",
                message="--plan-price must be >= 0",
                exit_code=2,
            )
        if not args.ignore_saved:
            update_config(plan_price_usd=float(args.plan_price))

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
                return _fail(
                    as_json=as_json,
                    code="auth",
                    message=str(exc),
                    exit_code=2,
                )
        except BillingError as exc:
            if want_local and not args.weekly and not args.monthly:
                print(f"warning: {exc}", file=sys.stderr)
                want_remote = False
                report = None
            else:
                return _fail(
                    as_json=as_json,
                    code="billing",
                    message=str(exc),
                    exit_code=1,
                )

    pool_percent_override: Optional[float] = None
    period_label = ""
    local = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    show_cost = not args.no_cost
    long_context: Optional[bool]
    if args.long_context:
        long_context = True
    elif args.standard_rates:
        long_context = False
    else:
        long_context = None

    cfg_preview = None if args.ignore_saved else load_config()
    cal_week_start = (
        cfg_preview.calibration.week_start_dt()
        if cfg_preview and cfg_preview.calibration
        else None
    )
    cal_week_end = (
        cfg_preview.calibration.week_end_dt()
        if cfg_preview and cfg_preview.calibration
        else None
    )
    plan_price = None
    if args.plan_price is not None:
        plan_price = float(args.plan_price)
    elif cfg_preview is not None:
        plan_price = cfg_preview.plan_price_usd

    if args.interactive:
        if as_json:
            return _fail(
                as_json=True,
                code="usage",
                message="--interactive cannot be combined with --json/--format json",
                exit_code=2,
            )
        try:
            ov = run_interactive(report, top_n=max(0, args.top))
        except ValueError as exc:
            return _fail(
                as_json=False, code="usage", message=str(exc), exit_code=2
            )
        except KeyboardInterrupt:
            print("\nAborted.", file=sys.stderr)
            return 130
        period_label = ov.period_label
        pool_percent_override = ov.pool_percent
        since, until = ov.since, ov.until
        local = scan_local_tokens(
            since=ov.since, until=ov.until, top_n=max(0, args.top)
        )
        # Calibrate from interactive anchors when possible
        if report is not None and local is not None and not args.ignore_saved:
            if pool_percent_override is not None and pool_percent_override > 0:
                cand = calibrate_from_weekly(
                    report.weekly,
                    local,
                    usage_percent_override=pool_percent_override,
                    source="interactive",
                    week_start=since,
                    week_end=until,
                )
                if cand is not None:
                    update_config(calibration=cand)
            elif (
                pool_percent_override is not None
                and pool_percent_override <= 0
                and cfg_preview
                and cfg_preview.calibration
                and since is not None
            ):
                update_config(
                    calibration=maybe_update_week_start(
                        cfg_preview.calibration,
                        week_start=since,
                        week_end=until,
                        source="interactive",
                    )
                )
    elif want_local:
        try:
            since, until, period_label, pool_percent_override, _src = _resolve_window(
                args,
                report,
                cfg_week_start=cal_week_start,
                cfg_week_end=cal_week_end,
            )
        except ValueError as exc:
            return _fail(
                as_json=as_json, code="usage", message=str(exc), exit_code=2
            )
        local = scan_local_tokens(since=since, until=until, top_n=max(0, args.top))

    # Calibration + local-first estimate (capacity is a weekly-pool concept)
    calibration, local_estimate = _manage_calibration(
        args,
        report=report,
        local=local,
        pool_percent_override=pool_percent_override,
        since=since,
        until=until,
    )

    # Only treat local_estimate as live pool % when the token window is the
    # subscription / calibrated week — not for arbitrary --period month/today/etc.
    week_like = (
        args.weekly
        or args.interactive
        or (
            not args.tokens
            and args.period in ("week", "weekly", "subscription")
            and not args.since
        )
        or (
            args.tokens
            and args.period in ("week", "weekly", "subscription")
        )
        or (since is not None and calibration is not None)
    )
    # If user asked for a non-week period explicitly, don't map tokens→pool %.
    if args.tokens and args.period not in ("week", "weekly", "subscription"):
        week_like = False
    if args.period not in ("week", "weekly", "subscription") and not args.weekly and not args.interactive:
        if args.since is None:
            week_like = False

    prefer_local = args.usage_source in ("auto", "local")
    if args.usage_source == "api":
        prefer_local = False
    if args.usage_source == "local":
        prefer_local = True

    api_pct = report.weekly.credit_usage_percent if report else None
    use_local_est = (
        local_estimate
        if week_like and (prefer_local or args.usage_source == "local")
        else None
    )
    effective_pct, usage_source = resolve_effective_pool_percent(
        api_percent=api_pct,
        override_percent=pool_percent_override,
        local_estimate=use_local_est,
        prefer_local=prefer_local and week_like,
    )
    if args.usage_source == "api" and api_pct is not None:
        effective_pct, usage_source = api_pct, "api"
    elif (
        args.usage_source == "local"
        and local_estimate is not None
        and week_like
    ):
        effective_pct = local_estimate.estimated_overall_percent
        usage_source = "local_calibration"

    economics = None
    if local is not None and not args.no_economics:
        economics = analyze_economics(
            local,
            weekly=report.weekly if report else None,
            monthly=report.monthly if report else None,
            calibration=calibration,
            local_estimate=local_estimate,
            usage_percent=effective_pct,
            usage_source=usage_source,
            plan_price_usd=plan_price,
            long_context=long_context,
            # Always include list-equiv $/MTok in economics; --no-cost only
            # hides the detailed cost breakdown block.
            show_api_cost=True,
        )

    # --- JSON ---
    if as_json:
        payload = _build_json_payload(
            args,
            report=report,
            want_remote=want_remote,
            local=local,
            period_label=period_label,
            pool_percent_override=pool_percent_override,
            show_cost=show_cost,
            long_context=long_context,
            calibration=calibration,
            local_estimate=local_estimate,
            economics=economics,
            effective_pct=effective_pct,
            usage_source=usage_source,
            plan_price=plan_price,
        )
        _emit_json(payload)
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
            pool_percent=effective_pct,
            economics=economics,
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
                    pool_percent=effective_pct,
                    economics=economics,
                )
                return 0
            return _fail(
                as_json=False,
                code="billing",
                message="no data to show",
                exit_code=1,
            )
        render_text(
            report,
            local=local,
            local_period_label=period_label,
            pool_percent_override=pool_percent_override,
            effective_pool_percent=effective_pct,
            usage_source=usage_source,
            local_estimate=local_estimate,
            economics=economics,
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
        and not (
            load_config().week_start_override if not args.ignore_saved else None
        )
    ):
        sections.append("local")

    render_text(
        report,
        local=local,
        local_period_label=period_label,
        pool_percent_override=pool_percent_override,
        effective_pool_percent=effective_pct,
        usage_source=usage_source,
        local_estimate=local_estimate,
        economics=economics,
        show_cost=show_cost,
        long_context=long_context,
        show_history=args.history,
        show_top_sessions=args.top > 0,
        sections=tuple(sections) if sections else ("weekly", "monthly"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
