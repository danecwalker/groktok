"""Load Grok / xAI credentials from the local Grok Build auth store or env."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class AuthError(RuntimeError):
    """Raised when no usable credentials are available."""


@dataclass(frozen=True)
class Credentials:
    access_token: str
    source: str
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    email: Optional[str] = None


def grok_home() -> Path:
    override = os.environ.get("GROK_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".grok"


def _pick_auth_entry(data: Any) -> Optional[dict[str, Any]]:
    """Pick the best session entry from ~/.grok/auth.json."""
    if not isinstance(data, dict) or not data:
        return None

    # Prefer the newest non-empty key field.
    best: Optional[dict[str, Any]] = None
    best_score = -1
    for _key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        token = entry.get("key") or entry.get("access_token")
        if not token or not isinstance(token, str):
            continue
        score = 0
        if entry.get("auth_mode") in ("oauth", "browser", "oidc"):
            score += 2
        if entry.get("refresh_token"):
            score += 1
        if entry.get("expires_at") or entry.get("create_time"):
            score += 1
        if score >= best_score:
            best = entry
            best_score = score
    return best


def load_credentials() -> Credentials:
    """
    Resolve credentials in this order:
      1. GROKTOK_TOKEN / GROK_TOKEN env (explicit override)
      2. ~/.grok/auth.json session from `grok login`
      3. XAI_API_KEY (usually insufficient for consumer billing)
    """
    for env_name in ("GROKTOK_TOKEN", "GROK_TOKEN"):
        token = os.environ.get(env_name)
        if token:
            return Credentials(access_token=token.strip(), source=env_name)

    auth_path = grok_home() / "auth.json"
    if auth_path.is_file():
        try:
            data = json.loads(auth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthError(f"Could not read {auth_path}: {exc}") from exc

        entry = _pick_auth_entry(data)
        if entry:
            token = entry.get("key") or entry.get("access_token")
            assert isinstance(token, str)
            return Credentials(
                access_token=token,
                source=str(auth_path),
                user_id=entry.get("user_id"),
                team_id=entry.get("team_id"),
                email=entry.get("email"),
            )

    api_key = os.environ.get("XAI_API_KEY")
    if api_key:
        return Credentials(access_token=api_key.strip(), source="XAI_API_KEY")

    raise AuthError(
        "No Grok credentials found.\n"
        "  • Run `grok login` (recommended), or\n"
        "  • Set GROKTOK_TOKEN to a session access token."
    )
