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
class LocalTokenReport:
    sessions_home: Path
    since: Optional[datetime]
    until: Optional[datetime]
    sessions_scanned: int
    sessions_with_usage: int
    total: TokenBucket
    by_model: dict[str, TokenBucket] = field(default_factory=dict)
    source_note: str = (
        "Local Grok Build sessions only (~/.grok/sessions). "
        "Does not include Chat/Imagine/Voice or other machines."
    )

    def as_dict(self) -> dict[str, Any]:
        return {
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
