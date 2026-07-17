# groktok

CLI for your **Grok subscription weekly usage** and **monthly allotment**.

It reads the same billing data that powers **Settings → Usage** on
[grok.com](https://grok.com/?_s=usage) and Grok Build’s `/usage` command.

```
$ groktok

Grok usage
  account  you@example.com

Weekly pool  (Weekly)
  ███████████████████░░░░░░░░░  68.0% used
  Jul 13, 2026 16:52 PDT  →  Jul 20, 2026 16:52 PDT
  Resets in 3d 12h

  By product
    GrokBuild      ████████████████  68.0%

  Extra usage credits  $9.38 remaining

Monthly usage  (included allotment)
  ██████████████████████░░░░░░  $139.05 / $180.00  (77.3%)
  Jul 01, 2026 00:00 UTC  →  Aug 01, 2026 00:00 UTC
  Remaining            $40.95
```

## Requirements

- **Python 3.9+**
- A **Grok login** (see [Auth](#auth))
- Zero third-party dependencies

## Install

### Recommended: uv tool

```bash
uv tool install "git+https://github.com/danecwalker/groktok.git"
# pin a release
uv tool install "git+https://github.com/danecwalker/groktok.git@v0.3.0"
```

Upgrade:

```bash
uv tool upgrade groktok
```

### One-shot

```bash
uvx --from "git+https://github.com/danecwalker/groktok.git" groktok
```

### pip

```bash
pip install "git+https://github.com/danecwalker/groktok.git"
```

## Auth

Preferred: sign in with the official Grok CLI (stores a session in `~/.grok/auth.json`):

```bash
grok login
```

| Variable | Purpose |
|---|---|
| `GROKTOK_TOKEN` / `GROK_TOKEN` | Explicit session access token |
| `GROK_HOME` | Alternate Grok config directory (default `~/.grok`) |

Consumer weekly usage requires a **Grok/xAI session**. A plain `XAI_API_KEY` is
for the developer API and generally cannot read the subscription usage pool.

## Usage

```bash
groktok              # weekly + monthly
groktok --weekly     # weekly pool only
groktok --monthly    # monthly allotment only
groktok --history    # include monthly history
groktok --json       # machine-readable JSON
groktok --format json
```

### What the numbers mean

| Section | Source | Meaning |
|---|---|---|
| **Weekly pool** | `GET …/billing?format=credits` | Shared weekly usage allowance across Grok products (% used, per-product when available). |
| **Monthly usage** | `GET …/billing?format=tokens` | Included monthly allotment in **USD** (values from the API are cents). |
| **Extra usage credits** | prepaid balance on the weekly payload | Pay-as-you-go top-up after the included pool is exhausted. |

### Machine-readable JSON

```bash
groktok --json
groktok --json --weekly
```

```json
{
  "ok": true,
  "version": "0.3.0",
  "schema_version": 1,
  "generated_at": "…",
  "account": { "email": "…", "user_id": "…", "team_id": "…", "auth_source": "…" },
  "weekly": {
    "usage_percent": 68.0,
    "start": "…",
    "end": "…",
    "resets_in_seconds": 123456,
    "product_usage": [{ "product": "GrokBuild", "usage_percent": 68.0 }],
    "extra_usage_credits_usd": 9.38
  },
  "monthly": {
    "used_usd": 139.05,
    "limit_usd": 180.0,
    "usage_percent": 77.25
  }
}
```

On failure (exit non-zero):

```json
{
  "ok": false,
  "error": { "code": "auth", "message": "…" }
}
```

| `error.code` | Exit | Meaning |
|---|---|---|
| `auth` | `2` | Missing/invalid credentials |
| `billing` | `1` | Billing API / network failure |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Billing API / network error |
| `2` | Missing or invalid credentials |

## Grok skill

Install the skill for agent-assisted usage:

```bash
npx skills add https://github.com/danecwalker/groktok --skill groktok
```

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml` and `src/groktok/__init__.py`.
2. Commit, tag, and push:

```bash
git tag v0.3.0
git push origin v0.3.0
```

3. GitHub Actions builds the sdist + wheel and attaches them to a GitHub Release.

## License

MIT
