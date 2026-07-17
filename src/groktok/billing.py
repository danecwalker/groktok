"""Client for Grok consumer billing / usage endpoints."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .auth import Credentials

BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing"
DEFAULT_TIMEOUT = 20
CLIENT_VERSION = "0.3.5"


class BillingError(RuntimeError):
    """Raised when the billing API fails."""


def _val(obj: Any, key: str = "val") -> Optional[int]:
    if obj is None:
        return None
    if isinstance(obj, dict) and key in obj:
        raw = obj[key]
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    if isinstance(obj, (int, float)):
        return int(obj)
    return None


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class Period:
    type: Optional[str]
    start: Optional[datetime]
    end: Optional[datetime]


@dataclass
class ProductUsage:
    product: str
    usage_percent: float


@dataclass
class MonthlyHistoryRow:
    year: int
    month: int
    included_used_cents: int
    on_demand_used_cents: int
    total_used_cents: int


@dataclass
class WeeklyUsage:
    """Unified weekly subscription usage pool (`format=credits`)."""

    period: Period
    credit_usage_percent: float
    product_usage: list[ProductUsage] = field(default_factory=list)
    prepaid_balance_cents: Optional[int] = None
    on_demand_cap_cents: Optional[int] = None
    on_demand_used_cents: Optional[int] = None
    is_unified_billing_user: Optional[bool] = None
    top_up_method: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class MonthlyUsage:
    """Monthly included / on-demand usage (`format=tokens`, values in USD cents)."""

    used_cents: int
    monthly_limit_cents: int
    on_demand_cap_cents: Optional[int]
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    history: list[MonthlyHistoryRow] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def used_usd(self) -> float:
        return self.used_cents / 100.0

    @property
    def monthly_limit_usd(self) -> float:
        return self.monthly_limit_cents / 100.0

    @property
    def usage_percent(self) -> float:
        if self.monthly_limit_cents <= 0:
            return 0.0
        return min(100.0, 100.0 * self.used_cents / self.monthly_limit_cents)


@dataclass
class UsageReport:
    weekly: WeeklyUsage
    monthly: MonthlyUsage
    credentials_source: str
    email: Optional[str] = None
    user_id: Optional[str] = None
    team_id: Optional[str] = None


def _request_billing(creds: Credentials, format_param: str) -> dict[str, Any]:
    url = f"{BILLING_URL}?format={format_param}"
    headers = {
        "Authorization": f"Bearer {creds.access_token}",
        "Accept": "application/json",
        "User-Agent": f"groktok/{CLIENT_VERSION}",
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-grok-client-version": CLIENT_VERSION,
        "x-grok-client-mode": "cli",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        if exc.code in (401, 403):
            raise BillingError(
                f"Billing API returned {exc.code} (auth rejected).\n"
                "  Run `grok login` again, then retry.\n"
                f"  Detail: {detail}"
            ) from exc
        raise BillingError(f"Billing API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BillingError(f"Could not reach billing API: {exc.reason}") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BillingError(f"Billing API returned non-JSON: {body[:200]!r}") from exc

    if not isinstance(data, dict):
        raise BillingError(f"Unexpected billing payload type: {type(data).__name__}")
    return data


def parse_weekly(payload: dict[str, Any]) -> WeeklyUsage:
    config = payload.get("config") or payload
    period_raw = config.get("currentPeriod") or {}
    products = []
    for item in config.get("productUsage") or []:
        if not isinstance(item, dict):
            continue
        products.append(
            ProductUsage(
                product=str(item.get("product") or "Unknown"),
                usage_percent=float(item.get("usagePercent") or 0.0),
            )
        )

    start = _parse_dt(period_raw.get("start") or config.get("billingPeriodStart"))
    end = _parse_dt(period_raw.get("end") or config.get("billingPeriodEnd"))

    return WeeklyUsage(
        period=Period(
            type=period_raw.get("type"),
            start=start,
            end=end,
        ),
        credit_usage_percent=float(config.get("creditUsagePercent") or 0.0),
        product_usage=products,
        prepaid_balance_cents=_val(config.get("prepaidBalance")),
        on_demand_cap_cents=_val(config.get("onDemandCap")),
        on_demand_used_cents=_val(config.get("onDemandUsed")),
        is_unified_billing_user=config.get("isUnifiedBillingUser"),
        top_up_method=config.get("topUpMethod"),
        raw=payload,
    )


def parse_monthly(payload: dict[str, Any]) -> MonthlyUsage:
    config = payload.get("config") or payload
    history: list[MonthlyHistoryRow] = []
    for row in config.get("history") or []:
        if not isinstance(row, dict):
            continue
        cycle = row.get("billingCycle") or {}
        try:
            year = int(cycle.get("year"))
            month = int(cycle.get("month"))
        except (TypeError, ValueError):
            continue
        history.append(
            MonthlyHistoryRow(
                year=year,
                month=month,
                included_used_cents=_val(row.get("includedUsed")) or 0,
                on_demand_used_cents=_val(row.get("onDemandUsed")) or 0,
                total_used_cents=_val(row.get("totalUsed")) or 0,
            )
        )

    used = _val(config.get("used"))
    limit = _val(config.get("monthlyLimit"))
    if used is None or limit is None:
        raise BillingError(
            "Monthly usage payload missing used/monthlyLimit fields: "
            f"{json.dumps(config)[:300]}"
        )

    return MonthlyUsage(
        used_cents=used,
        monthly_limit_cents=limit,
        on_demand_cap_cents=_val(config.get("onDemandCap")),
        period_start=_parse_dt(config.get("billingPeriodStart")),
        period_end=_parse_dt(config.get("billingPeriodEnd")),
        history=history,
        raw=payload,
    )


def fetch_usage(creds: Credentials) -> UsageReport:
    """Fetch weekly pool + monthly usage for the authenticated account."""
    weekly_payload = _request_billing(creds, "credits")
    monthly_payload = _request_billing(creds, "tokens")
    return UsageReport(
        weekly=parse_weekly(weekly_payload),
        monthly=parse_monthly(monthly_payload),
        credentials_source=creds.source,
        email=creds.email,
        user_id=creds.user_id,
        team_id=creds.team_id,
    )
