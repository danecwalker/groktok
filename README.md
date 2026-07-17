# groktok

CLI for your **Grok subscription weekly usage**, **monthly allotment**, and
**local Build tokens** for the current weekly billing window.

It reads the same billing data that powers **Settings → Usage** on
[grok.com](https://grok.com/?_s=usage), plus token counts from this machine’s
`~/.grok/sessions` logs for that week’s start → end.

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

  Local Build tokens  (this machine, weekly window)
    Total              12.34M  (12,340,000)
    Input / uncached   11.00M / 2.00M
    Output / reasoning 1.34M / 0.50M

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
uv tool install "git+https://github.com/danecwalker/groktok.git@v0.3.1"
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
groktok              # weekly + monthly + local tokens
groktok --weekly     # weekly pool + local tokens
groktok --monthly    # monthly allotment only
groktok --history    # include monthly history
groktok --no-local   # skip local session token scan
groktok --model grok-4.5   # local tokens for one model only
groktok --zeros 1    # pool was wiped to 0% once during this week
groktok --json       # machine-readable JSON
groktok --format json
```

`--model` matches case-insensitively (exact, then prefix, then substring). Example: `--model 4.5` or `--model kimi`.

`--zeros N` is how many times the **weekly pool was reset to 0%** during the
current billing window. With `--zeros`, **usage % is from local tokens**, not
the billing API bar:

```text
capacity ≈ week_tokens / (N + billing_%/100)   # estimated once, then saved
usage_%  = 100 × (week_tokens / capacity − N)
```

Billing % is only used as a one-shot anchor to estimate capacity (refresh with
`--recalibrate`). After that the weekly bar is token-driven and can exceed 100%
when you burn extra credits.

### What the numbers mean

| Section | Source | Meaning |
|---|---|---|
| **Weekly pool** | `GET …/billing?format=credits` | Shared weekly usage allowance across Grok products (% used, per-product when available). |
| **Local Build tokens** | `~/.grok/sessions/**/updates.jsonl` | Tokens from Grok Build on **this machine** for turns in the weekly billing window `[start, end)`. |
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
  "version": "0.3.1",
  "schema_version": 1,
  "generated_at": "…",
  "account": { "email": "…", "user_id": "…", "team_id": "…", "auth_source": "…" },
  "weekly": {
    "usage_percent": 68.0,
    "start": "…",
    "end": "…",
    "resets_in_seconds": 123456,
    "product_usage": [{ "product": "GrokBuild", "usage_percent": 68.0 }],
    "extra_usage_credits_usd": 9.38,
    "local_build_tokens": {
      "totals": { "total_tokens": 0, "input_tokens": 0, "output_tokens": 0 }
    }
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
git tag v0.3.1
git push origin v0.3.1
```

3. GitHub Actions builds the sdist + wheel and attaches them to a GitHub Release.

## License

MIT
