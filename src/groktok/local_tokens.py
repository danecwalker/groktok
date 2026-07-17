"""Aggregate LLM token usage from local Grok Build session logs.

Reads ``turn_completed`` events in ``~/.grok/sessions/**/updates.jsonl``
for a time window (typically the billing weekly period).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .auth import grok_home

TOKEN_KEYS = (
    "inputTokens",
    "outputTokens",
    "totalTokens",
    "cachedReadTokens",
    "reasoningTokens",
    "modelCalls",
)


@dataclass
class TokenBucket:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_read_tokens: int = 0
    reasoning_tokens: int = 0
    model_calls: int = 0
    turns: int = 0

    def add_usage(self, usage: dict[str, Any], *, count_turn: bool = True) -> None:
        self.input_tokens += int(usage.get("inputTokens") or 0)
        self.output_tokens += int(usage.get("outputTokens") or 0)
        self.total_tokens += int(usage.get("totalTokens") or 0)
        self.cached_read_tokens += int(usage.get("cachedReadTokens") or 0)
        self.reasoning_tokens += int(usage.get("reasoningTokens") or 0)
        self.model_calls += int(usage.get("modelCalls") or 0)
        if count_turn:
            self.turns += 1

    def merge(self, other: "TokenBucket") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens
        self.cached_read_tokens += other.cached_read_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.model_calls += other.model_calls
        self.turns += other.turns

    @property
    def uncached_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_read_tokens)

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_read_tokens": self.cached_read_tokens,
            "uncached_input_tokens": self.uncached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "model_calls": self.model_calls,
            "turns": self.turns,
        }


@dataclass
class ZeroCycleEstimate:
    """
    Interpret week tokens when the pool was zeroed mid-window.

        cycles   = zeros + (pool_percent / 100)
        capacity ≈ week_tokens / cycles
        current  ≈ capacity × (pool_percent / 100)
    """

    zeros: int
    pool_percent: float
    week_tokens: int
    cycles: float
    capacity_tokens: int
    current_cycle_tokens: int
    completed_cycle_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "zeros": self.zeros,
            "pool_percent": self.pool_percent,
            "week_tokens": self.week_tokens,
            "cycles": round(self.cycles, 4),
            "capacity_tokens": self.capacity_tokens,
            "current_cycle_tokens": self.current_cycle_tokens,
            "completed_cycle_tokens": self.completed_cycle_tokens,
            "method": (
                "capacity = week_tokens / (zeros + pool_percent/100); "
                "current_cycle_tokens = capacity * pool_percent/100"
            ),
        }


@dataclass
class LocalTokenReport:
    sessions_home: Path
    since: Optional[datetime]
    until: Optional[datetime]
    sessions_scanned: int
    sessions_with_usage: int
    total: TokenBucket
    by_model: dict[str, TokenBucket] = field(default_factory=dict)
    model_filter: Optional[str] = None
    matched_models: list[str] = field(default_factory=list)
    zero_estimate: Optional[ZeroCycleEstimate] = None
    source_note: str = (
        "Local Grok Build sessions only (~/.grok/sessions). "
        "Does not include Chat/Imagine/Voice or other machines."
    )

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": str(self.sessions_home),
            "note": self.source_note,
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
            "sessions_scanned": self.sessions_scanned,
            "sessions_with_usage": self.sessions_with_usage,
            "totals": self.total.as_dict(),
            "by_model": {
                m: b.as_dict() for m, b in sorted(self.by_model.items())
            },
        }
        if self.model_filter is not None:
            payload["model_filter"] = self.model_filter
            payload["matched_models"] = list(self.matched_models)
        if self.zero_estimate is not None:
            payload["zero_cycles"] = self.zero_estimate.as_dict()
        return payload


def estimate_zero_cycles(
    week_tokens: int,
    *,
    zeros: int,
    pool_percent: float,
) -> ZeroCycleEstimate:
    """
    Derive per-cycle capacity from week tokens + mid-window zero count.

    ``zeros`` = how many times the pool was wiped to 0% during this week
    (completed full cycles no longer reflected in the live %).
    """
    if zeros < 0:
        raise ValueError("--zeros must be >= 0")
    if week_tokens <= 0:
        raise ValueError("No local tokens in the weekly window to apply --zeros")

    frac = max(0.0, float(pool_percent)) / 100.0
    cycles = float(zeros) + frac
    if cycles <= 0:
        raise ValueError(
            "Cannot estimate capacity: pool is at 0% and --zeros is 0 "
            "(need usage or at least one completed zero-cycle)"
        )

    capacity = max(1, int(round(week_tokens / cycles)))
    current = max(0, int(round(capacity * frac)))
    # Prefer residual so current + completed = week_tokens
    completed = max(0, week_tokens - current)

    return ZeroCycleEstimate(
        zeros=int(zeros),
        pool_percent=float(pool_percent),
        week_tokens=int(week_tokens),
        cycles=cycles,
        capacity_tokens=capacity,
        current_cycle_tokens=current,
        completed_cycle_tokens=completed,
    )


def with_zero_estimate(
    report: LocalTokenReport,
    *,
    zeros: int,
    pool_percent: float,
) -> LocalTokenReport:
    """Attach a zero-cycle estimate to a local token report."""
    est = estimate_zero_cycles(
        report.total.total_tokens,
        zeros=zeros,
        pool_percent=pool_percent,
    )
    return LocalTokenReport(
        sessions_home=report.sessions_home,
        since=report.since,
        until=report.until,
        sessions_scanned=report.sessions_scanned,
        sessions_with_usage=report.sessions_with_usage,
        total=report.total,
        by_model=report.by_model,
        model_filter=report.model_filter,
        matched_models=list(report.matched_models),
        zero_estimate=est,
        source_note=report.source_note,
    )


def resolve_model_names(
    by_model: dict[str, TokenBucket],
    query: str,
) -> list[str]:
    """
    Match model names case-insensitively.

    Order: exact → prefix → substring. Raises ValueError if nothing matches.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("Model filter is empty")
    if not by_model:
        raise ValueError("No local model usage found in this window")

    names = list(by_model.keys())
    ql = q.lower()

    exact = [n for n in names if n.lower() == ql]
    if exact:
        return exact

    prefix = sorted(n for n in names if n.lower().startswith(ql))
    if prefix:
        return prefix

    substr = sorted(n for n in names if ql in n.lower())
    if substr:
        return substr

    available = ", ".join(sorted(names))
    raise ValueError(
        f"No model matching {query!r}. Available: {available}"
    )


def filter_report_by_model(
    report: LocalTokenReport,
    model: str,
) -> LocalTokenReport:
    """Return a new report whose totals are only the matched model(s)."""
    matched = resolve_model_names(report.by_model, model)
    total = TokenBucket()
    by_model: dict[str, TokenBucket] = {}
    for name in matched:
        bucket = report.by_model[name]
        by_model[name] = bucket
        total.merge(bucket)

    note = report.source_note
    if len(matched) == 1:
        note = f"Filtered to model {matched[0]}. " + note
    else:
        note = f"Filtered to models: {', '.join(matched)}. " + note

    return LocalTokenReport(
        sessions_home=report.sessions_home,
        since=report.since,
        until=report.until,
        sessions_scanned=report.sessions_scanned,
        sessions_with_usage=report.sessions_with_usage,
        total=total,
        by_model=by_model,
        model_filter=model,
        matched_models=matched,
        zero_estimate=report.zero_estimate,
        source_note=note,
    )


def sessions_root() -> Path:
    return grok_home() / "sessions"


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _iter_turn_completed(
    updates_path: Path,
) -> Iterator[tuple[Optional[datetime], dict[str, Any]]]:
    try:
        with updates_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "turn_completed" not in line or "inputTokens" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                params = obj.get("params") or {}
                update = params.get("update") or {}
                if update.get("sessionUpdate") != "turn_completed":
                    continue
                usage = update.get("usage")
                if not isinstance(usage, dict):
                    continue
                if not any(usage.get(k) for k in TOKEN_KEYS):
                    continue
                yield _parse_ts(obj.get("timestamp")), usage
    except OSError:
        return


def _in_range(
    dt: Optional[datetime],
    since: Optional[datetime],
    until: Optional[datetime],
) -> bool:
    if dt is None:
        return since is None and until is None
    if since is not None and dt < since:
        return False
    if until is not None and dt >= until:
        return False
    return True


def scan_local_tokens(
    *,
    root: Optional[Path] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> LocalTokenReport:
    """Sum ``turn_completed`` usage in [since, until)."""
    base = root or sessions_root()
    total = TokenBucket()
    by_model: dict[str, TokenBucket] = {}
    scanned = 0
    with_usage = 0

    if base.is_dir():
        for updates in base.rglob("updates.jsonl"):
            scanned += 1
            sess = TokenBucket()
            for dt, usage in _iter_turn_completed(updates):
                if not _in_range(dt, since, until):
                    continue
                sess.add_usage(usage, count_turn=True)

                model_usage = usage.get("modelUsage") or {}
                if isinstance(model_usage, dict) and model_usage:
                    for model, mu in model_usage.items():
                        if not isinstance(mu, dict):
                            continue
                        bucket = by_model.setdefault(str(model), TokenBucket())
                        bucket.add_usage(mu, count_turn=False)
                        bucket.turns += 1
                else:
                    bucket = by_model.setdefault("unknown", TokenBucket())
                    bucket.add_usage(usage, count_turn=True)

            if sess.turns == 0:
                continue
            with_usage += 1
            total.merge(sess)

    return LocalTokenReport(
        sessions_home=base,
        since=since,
        until=until,
        sessions_scanned=scanned,
        sessions_with_usage=with_usage,
        total=total,
        by_model=by_model,
    )
