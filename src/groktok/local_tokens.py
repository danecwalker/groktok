"""Aggregate LLM token usage from local Grok Build session logs.

Grok stores per-turn usage on ``turn_completed`` events inside each session's
``updates.jsonl`` under ``~/.grok/sessions/``:

    usage.inputTokens / outputTokens / totalTokens /
          cachedReadTokens / reasoningTokens / modelCalls / modelUsage

These are the same counters shown when a headless session ends (``usage`` /
``modelUsage``). They cover **Grok Build only** on this machine — not Chat,
Imagine, Voice, or other devices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
        """Input tokens not served from the prompt cache (best-effort)."""
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
class SessionTokenSummary:
    session_id: str
    path: Path
    title: Optional[str]
    cwd: Optional[str]
    model: Optional[str]
    first_turn_at: Optional[datetime]
    last_turn_at: Optional[datetime]
    usage: TokenBucket = field(default_factory=TokenBucket)


@dataclass
class LocalTokenReport:
    """Token usage scanned from local Grok Build sessions."""

    sessions_home: Path
    since: Optional[datetime]
    until: Optional[datetime]
    sessions_scanned: int
    sessions_with_usage: int
    total: TokenBucket
    by_model: dict[str, TokenBucket]
    top_sessions: list[SessionTokenSummary]
    source_note: str = (
        "Local Grok Build sessions only (~/.grok/sessions). "
        "Does not include Chat/Imagine/Voice or other machines."
    )


def sessions_root() -> Path:
    return grok_home() / "sessions"


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    try:
        # Session logs use Unix seconds.
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _read_summary(session_dir: Path) -> dict[str, Any]:
    path = session_dir / "summary.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _iter_turn_completed(updates_path: Path) -> Iterator[tuple[Optional[datetime], dict[str, Any]]]:
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
        # Keep undated turns only when no filter is active.
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
    top_n: int = 8,
) -> LocalTokenReport:
    """Scan session ``updates.jsonl`` files and sum ``turn_completed`` usage."""
    base = root or sessions_root()
    total = TokenBucket()
    by_model: dict[str, TokenBucket] = {}
    session_rows: list[SessionTokenSummary] = []
    scanned = 0
    with_usage = 0

    if base.is_dir():
        for updates in base.rglob("updates.jsonl"):
            scanned += 1
            session_dir = updates.parent
            session_id = session_dir.name
            summary = _read_summary(session_dir)
            info = summary.get("info") if isinstance(summary.get("info"), dict) else {}

            sess = TokenBucket()
            first_at: Optional[datetime] = None
            last_at: Optional[datetime] = None
            models_seen: set[str] = set()

            for dt, usage in _iter_turn_completed(updates):
                if not _in_range(dt, since, until):
                    continue
                sess.add_usage(usage, count_turn=True)
                if dt is not None:
                    if first_at is None or dt < first_at:
                        first_at = dt
                    if last_at is None or dt > last_at:
                        last_at = dt

                model_usage = usage.get("modelUsage") or {}
                if isinstance(model_usage, dict) and model_usage:
                    for model, mu in model_usage.items():
                        if not isinstance(mu, dict):
                            continue
                        models_seen.add(str(model))
                        bucket = by_model.setdefault(str(model), TokenBucket())
                        # modelUsage is the per-model split of the same turn;
                        # do not increment turns here (turn counted once above).
                        bucket.add_usage(mu, count_turn=False)
                        bucket.turns += 1
                else:
                    # Fallback: attribute whole turn to primary model if known.
                    primary = summary.get("current_model_id") or "unknown"
                    models_seen.add(str(primary))
                    bucket = by_model.setdefault(str(primary), TokenBucket())
                    bucket.add_usage(usage, count_turn=True)

            if sess.turns == 0:
                continue

            with_usage += 1
            total.merge(sess)
            primary_model = (
                summary.get("current_model_id")
                or (sorted(models_seen)[0] if models_seen else None)
            )
            session_rows.append(
                SessionTokenSummary(
                    session_id=session_id,
                    path=session_dir,
                    title=summary.get("generated_title") or summary.get("session_summary"),
                    cwd=info.get("cwd") if isinstance(info, dict) else None,
                    model=primary_model,
                    first_turn_at=first_at,
                    last_turn_at=last_at,
                    usage=sess,
                )
            )

    session_rows.sort(key=lambda s: s.usage.total_tokens, reverse=True)
    return LocalTokenReport(
        sessions_home=base,
        since=since,
        until=until,
        sessions_scanned=scanned,
        sessions_with_usage=with_usage,
        total=total,
        by_model=by_model,
        top_sessions=session_rows[: max(0, top_n)],
    )


def resolve_period(
    period: str,
    *,
    weekly_start: Optional[datetime] = None,
    weekly_end: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> tuple[Optional[datetime], Optional[datetime], str]:
    """
    Map a period name to [since, until).

    - ``week``: subscription weekly window if provided, else rolling 7 days
    - ``7d``: rolling last 7 days
    - ``today`` / ``morning``: local calendar day from midnight
    - ``month``: current UTC calendar month
    - ``all``: no filter
    """
    now = now or datetime.now(timezone.utc)
    key = period.strip().lower()

    if key in ("all", "everything", "total"):
        return None, None, "all time"

    if key in ("week", "weekly", "subscription"):
        if weekly_start and weekly_end:
            return weekly_start, weekly_end, "subscription week"
        if weekly_start:
            return weekly_start, weekly_end, "subscription week (open end)"
        return now - timedelta(days=7), None, "last 7 days"

    if key in ("7d", "7day", "7days", "rolling"):
        return now - timedelta(days=7), None, "last 7 days"

    if key in ("today", "day", "morning"):
        local = now.astimezone()
        start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_local.astimezone(timezone.utc), None, "since local midnight"

    if key in ("month", "monthly"):
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, None, "this month (UTC)"

    raise ValueError(
        f"Unknown period {period!r}. Use: week, 7d, today, morning, month, all"
    )


def parse_since_arg(raw: str, *, now: Optional[datetime] = None) -> datetime:
    """Parse ``--since`` values (ISO, today, morning, -6h, …)."""
    # Imported lazily-shaped logic mirrored for CLI without pulling interactive UI.
    now = now or datetime.now().astimezone()
    text = raw.strip().lower()

    if text in ("now",):
        return now.astimezone(timezone.utc)
    if text in ("today", "midnight", "this morning", "morning"):
        local = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return local.astimezone(timezone.utc)
    if text in ("yesterday",):
        local = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return local.astimezone(timezone.utc)

    import re

    m = re.fullmatch(r"-(\d+)\s*h(ours?)?", text)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).astimezone(timezone.utc)
    m = re.fullmatch(r"-(\d+)\s*d(ays?)?", text)
    if m:
        return (now - timedelta(days=int(m.group(1)))).astimezone(timezone.utc)

    candidates = [raw.strip()]
    if " " in raw.strip() and "T" not in raw:
        candidates.append(raw.strip().replace(" ", "T"))
    for c in candidates:
        try:
            if c.endswith("Z"):
                c = c[:-1] + "+00:00"
            dt = datetime.fromisoformat(c)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=now.tzinfo or timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue

    raise ValueError(
        f"Could not parse --since {raw!r}. Try: morning, today, 2026-07-16, "
        f"2026-07-16T09:00, -6h"
    )


def report_to_dict(report: LocalTokenReport) -> dict[str, Any]:
    return {
        "source": str(report.sessions_home),
        "note": report.source_note,
        "since": report.since.isoformat() if report.since else None,
        "until": report.until.isoformat() if report.until else None,
        "sessions_scanned": report.sessions_scanned,
        "sessions_with_usage": report.sessions_with_usage,
        "totals": report.total.as_dict(),
        "by_model": {m: b.as_dict() for m, b in sorted(report.by_model.items())},
        "top_sessions": [
            {
                "session_id": s.session_id,
                "title": s.title,
                "cwd": s.cwd,
                "model": s.model,
                "first_turn_at": s.first_turn_at.isoformat() if s.first_turn_at else None,
                "last_turn_at": s.last_turn_at.isoformat() if s.last_turn_at else None,
                "usage": s.usage.as_dict(),
            }
            for s in report.top_sessions
        ],
    }
