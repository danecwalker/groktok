"""Command-line entrypoint for groktok."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from . import __version__
from .auth import AuthError, load_credentials
from .billing import BillingError, fetch_usage
from .display import render_text, report_to_dict
from .local_tokens import (
    filter_report_by_model,
    scan_local_tokens,
    with_zero_estimate,
)

JSON_SCHEMA_VERSION = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="groktok",
        description=(
            "Show your Grok subscription weekly usage pool, monthly "
            "allotment, and local Build tokens for the weekly window."
        ),
        epilog=(
            "Auth: ~/.grok/auth.json from `grok login`, or GROKTOK_TOKEN.\n"
            "Local tokens: ~/.grok/sessions (weekly billing window).\n"
            "Model filter: --model grok-4.5  (exact / prefix / substring).\n"
            "Mid-week resets: --zeros 1  (pool wiped to 0%% once this week).\n"
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
        help="Skip local session token scan",
    )
    parser.add_argument(
        "--model",
        metavar="NAME",
        help=(
            "Only show local tokens for this model "
            "(case-insensitive exact, prefix, or substring match)"
        ),
    )
    parser.add_argument(
        "--zeros",
        type=int,
        metavar="N",
        default=None,
        help=(
            "Times the weekly pool was reset to 0%% during this billing window "
            "(completed full cycles). Used with local tokens + live pool %%: "
            "capacity ≈ week_tokens / (N + pool%%/100)"
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

    # Local Build tokens for the billing weekly window [start, end).
    local = None
    if not args.no_local and "weekly" in sections:
        weekly = report.weekly
        local = scan_local_tokens(
            since=weekly.period.start,
            until=weekly.period.end,
        )
        if args.model:
            try:
                local = filter_report_by_model(local, args.model)
            except ValueError as exc:
                return _fail(
                    as_json=as_json,
                    code="usage",
                    message=str(exc),
                    exit_code=2,
                )
        if args.zeros is not None:
            try:
                local = with_zero_estimate(
                    local,
                    zeros=args.zeros,
                    pool_percent=weekly.credit_usage_percent,
                )
            except ValueError as exc:
                return _fail(
                    as_json=as_json,
                    code="usage",
                    message=str(exc),
                    exit_code=2,
                )
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
    elif (args.model or args.zeros is not None) and args.monthly and not args.weekly:
        return _fail(
            as_json=as_json,
            code="usage",
            message=(
                "--model / --zeros apply to local weekly tokens; "
                "omit --monthly or use default/weekly view"
            ),
            exit_code=2,
        )

    if as_json:
        payload = report_to_dict(
            report, history=args.history, local=local
        )
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
