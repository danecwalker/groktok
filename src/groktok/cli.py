"""Command-line entrypoint for groktok."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from . import __version__
from .auth import AuthError, load_credentials
from .billing import BillingError, UsageReport, fetch_usage
from .config import TokenMeterState, load_meter_state, save_meter_state
from .display import render_text, report_to_dict
from .local_tokens import (
    LocalTokenReport,
    estimate_capacity_tokens,
    filter_report_by_model,
    scan_local_tokens,
    with_zero_estimate,
)

JSON_SCHEMA_VERSION = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="groktok",
        description=(
            "Show Grok weekly usage (token meter when calibrated), monthly "
            "allotment, and local Build tokens for the weekly window."
        ),
        epilog=(
            "Auth: ~/.grok/auth.json from `grok login`, or GROKTOK_TOKEN.\n"
            "Local tokens: ~/.grok/sessions (weekly billing window).\n"
            "\n"
            "Token meter (preferred weekly usage %%):\n"
            "  1) Calibrate once:\n"
            "       groktok --recalibrate [--zeros N] [--model NAME]\n"
            "     Uses billing API %% + raw week tokens to estimate capacity,\n"
            "     then saves it to ~/.grok/groktok.json.\n"
            "  2) Later runs (no flags needed):\n"
            "       groktok\n"
            "     Usage %% = 100 × (week_tokens / capacity − zeros)\n"
            "     from local tokens only (billing API is secondary).\n"
            "\n"
            "Usage tab: https://grok.com/?_s=usage"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
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
        help="Output format: text (default) or json",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Include monthly usage history (JSON and text)",
    )
    parser.add_argument(
        "--no-local",
        action="store_true",
        help="Skip local session token scan (billing API only)",
    )
    parser.add_argument(
        "--model",
        metavar="NAME",
        help=(
            "Only count local tokens for this model "
            "(case-insensitive exact, prefix, or substring). "
            "Remembered when you recalibrate."
        ),
    )
    parser.add_argument(
        "--zeros",
        type=int,
        metavar="N",
        default=None,
        help=(
            "Times the weekly pool was reset to 0%% in this billing window "
            "(completed full cycles). Defaults to saved value, else 0. "
            "Pass with --recalibrate to set/update."
        ),
    )
    parser.add_argument(
        "--recalibrate",
        action="store_true",
        help=(
            "Re-estimate capacity from billing API %% + raw week tokens, "
            "save it, and use the token meter for usage %%"
        ),
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--weekly", action="store_true", help="Weekly pool only"
    )
    scope.add_argument(
        "--monthly", action="store_true", help="Monthly allotment only"
    )
    return parser


def _want_json(args: argparse.Namespace) -> bool:
    return bool(args.json or args.output_format == "json")


def _emit_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _fail(*, as_json: bool, code: str, message: str, exit_code: int) -> int:
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


def _week_start_iso(report: UsageReport) -> str:
    start = report.weekly.period.start
    return start.isoformat() if start is not None else ""


def _meter_matches_week(
    saved: Optional[TokenMeterState],
    *,
    week_start_iso: str,
    model_key: Optional[str],
) -> bool:
    if saved is None:
        return False
    if saved.week_start != week_start_iso:
        return False
    # If a model filter was saved, require the same filter (or auto-apply it).
    if (saved.model_filter or None) != (model_key or None):
        return False
    return True


def _apply_token_meter(
    report: UsageReport,
    local: LocalTokenReport,
    *,
    zeros: int,
    capacity: int,
    capacity_source: str,
) -> LocalTokenReport:
    billing_pct = float(report.weekly.credit_usage_percent or 0.0)
    return with_zero_estimate(
        local,
        zeros=zeros,
        capacity_tokens=capacity,
        capacity_source=capacity_source,
        billing_pool_percent=billing_pct,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    as_json = _want_json(args)

    try:
        creds = load_credentials()
        report = fetch_usage(creds)
    except AuthError as exc:
        return _fail(as_json=as_json, code="auth", message=str(exc), exit_code=2)
    except BillingError as exc:
        return _fail(as_json=as_json, code="billing", message=str(exc), exit_code=1)

    if args.weekly:
        sections: tuple[str, ...] = ("weekly",)
    elif args.monthly:
        sections = ("monthly",)
    else:
        sections = ("weekly", "monthly")

    if args.zeros is not None and args.zeros < 0:
        return _fail(
            as_json=as_json,
            code="usage",
            message="--zeros must be >= 0",
            exit_code=2,
        )

    saved = None if args.no_local else load_meter_state()
    week_start_iso = _week_start_iso(report)

    # Effective model filter: explicit flag, else remembered filter for this week.
    model_key = args.model
    if (
        model_key is None
        and saved is not None
        and saved.week_start == week_start_iso
        and saved.model_filter
        and not args.no_local
        and "weekly" in sections
    ):
        model_key = saved.model_filter

    local: Optional[LocalTokenReport] = None
    if not args.no_local and "weekly" in sections:
        weekly = report.weekly
        local = scan_local_tokens(
            since=weekly.period.start,
            until=weekly.period.end,
        )
        if model_key:
            try:
                local = filter_report_by_model(local, model_key)
            except ValueError as exc:
                return _fail(
                    as_json=as_json,
                    code="usage",
                    message=str(exc),
                    exit_code=2,
                )

        billing_pct = float(weekly.credit_usage_percent or 0.0)
        zeros_for_save = (
            args.zeros
            if args.zeros is not None
            else (saved.zeros if saved is not None else 0)
        )

        # Recalibrate: billing API % + raw week tokens → new capacity, then save.
        if args.recalibrate:
            z = args.zeros if args.zeros is not None else zeros_for_save
            try:
                capacity = estimate_capacity_tokens(
                    local.total.total_tokens,
                    zeros=z,
                    billing_pool_percent=billing_pct,
                )
            except ValueError as exc:
                return _fail(
                    as_json=as_json,
                    code="usage",
                    message=str(exc),
                    exit_code=2,
                )
            save_meter_state(
                TokenMeterState(
                    week_start=week_start_iso,
                    capacity_tokens=capacity,
                    zeros=z,
                    model_filter=model_key,
                )
            )
            if not as_json:
                print(
                    f"calibrated token capacity ≈ {capacity:,} / cycle "
                    f"(zeros={z}; billing anchor {billing_pct:.1f}%)",
                    file=sys.stderr,
                )
            local = _apply_token_meter(
                report,
                local,
                zeros=z,
                capacity=capacity,
                capacity_source="estimated",
            )
        else:
            # Normal path: reuse saved meter for this week (no --zeros required).
            # Also allow first-time setup via --zeros alone (implies calibrate).
            meter_ok = _meter_matches_week(
                saved,
                week_start_iso=week_start_iso,
                model_key=model_key,
            )
            if meter_ok and saved is not None:
                z = args.zeros if args.zeros is not None else saved.zeros
                # If user changes zeros without --recalibrate, re-estimate capacity.
                if args.zeros is not None and args.zeros != saved.zeros:
                    try:
                        capacity = estimate_capacity_tokens(
                            local.total.total_tokens,
                            zeros=args.zeros,
                            billing_pool_percent=billing_pct,
                        )
                    except ValueError as exc:
                        return _fail(
                            as_json=as_json,
                            code="usage",
                            message=str(exc),
                            exit_code=2,
                        )
                    save_meter_state(
                        TokenMeterState(
                            week_start=week_start_iso,
                            capacity_tokens=capacity,
                            zeros=args.zeros,
                            model_filter=model_key,
                        )
                    )
                    if not as_json:
                        print(
                            f"calibrated token capacity ≈ {capacity:,} / cycle "
                            f"(zeros={args.zeros}; billing anchor {billing_pct:.1f}%)",
                            file=sys.stderr,
                        )
                    local = _apply_token_meter(
                        report,
                        local,
                        zeros=args.zeros,
                        capacity=capacity,
                        capacity_source="estimated",
                    )
                else:
                    local = _apply_token_meter(
                        report,
                        local,
                        zeros=z,
                        capacity=saved.capacity_tokens,
                        capacity_source="saved",
                    )
            elif args.zeros is not None:
                # First calibrate without requiring --recalibrate flag.
                try:
                    capacity = estimate_capacity_tokens(
                        local.total.total_tokens,
                        zeros=args.zeros,
                        billing_pool_percent=billing_pct,
                    )
                except ValueError as exc:
                    return _fail(
                        as_json=as_json,
                        code="usage",
                        message=str(exc),
                        exit_code=2,
                    )
                save_meter_state(
                    TokenMeterState(
                        week_start=week_start_iso,
                        capacity_tokens=capacity,
                        zeros=args.zeros,
                        model_filter=model_key,
                    )
                )
                if not as_json:
                    print(
                        f"calibrated token capacity ≈ {capacity:,} / cycle "
                        f"(zeros={args.zeros}; billing anchor {billing_pct:.1f}%)",
                        file=sys.stderr,
                    )
                local = _apply_token_meter(
                    report,
                    local,
                    zeros=args.zeros,
                    capacity=capacity,
                    capacity_source="estimated",
                )
            # else: no saved meter — show billing API bar + raw week tokens only

    elif args.model and args.no_local:
        return _fail(
            as_json=as_json,
            code="usage",
            message="--model requires a local token scan (omit --no-local)",
            exit_code=2,
        )
    elif args.zeros is not None and args.no_local:
        return _fail(
            as_json=as_json,
            code="usage",
            message="--zeros requires a local token scan (omit --no-local)",
            exit_code=2,
        )
    elif args.recalibrate and args.no_local:
        return _fail(
            as_json=as_json,
            code="usage",
            message="--recalibrate requires a local token scan (omit --no-local)",
            exit_code=2,
        )
    elif (
        (args.model or args.zeros is not None or args.recalibrate)
        and args.monthly
        and not args.weekly
    ):
        return _fail(
            as_json=as_json,
            code="usage",
            message=(
                "--model / --zeros / --recalibrate apply to weekly local tokens; "
                "omit --monthly or use default/weekly view"
            ),
            exit_code=2,
        )

    if as_json:
        payload = report_to_dict(report, history=args.history, local=local)
        if args.weekly:
            payload.pop("monthly", None)
        elif args.monthly:
            payload.pop("weekly", None)
        _emit_json(
            {
                "ok": True,
                "version": __version__,
                "schema_version": JSON_SCHEMA_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                **payload,
            }
        )
        return 0

    render_text(
        report,
        local=local,
        show_history=args.history,
        sections=sections,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
