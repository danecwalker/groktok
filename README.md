# groktok

CLI for your **Grok subscription weekly usage** and **monthly usage**.

It reads the same billing data that powers **Settings → Usage** on [grok.com](https://grok.com/?_s=usage) and Grok Build’s `/usage` command.

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

You do **not** need to clone this repo. Install from GitHub (or a release tag) with **uv** or **pip**.

### Recommended: uv tool (puts `groktok` on your PATH)

```bash
# latest main
uv tool install "git+https://github.com/danecwalker/groktok.git"

# pin to a release tag
uv tool install "git+https://github.com/danecwalker/groktok.git@v0.2.1"
```

Upgrade later:

```bash
uv tool upgrade groktok
```

### One-shot (no permanent install)

```bash
uvx --from "git+https://github.com/danecwalker/groktok.git" groktok
```

### pip

```bash
# latest main
pip install "git+https://github.com/danecwalker/groktok.git"

# pin to a release tag
pip install "git+https://github.com/danecwalker/groktok.git@v0.2.1"
```

Prefer a virtualenv for pip installs:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install "git+https://github.com/danecwalker/groktok.git"
groktok
```

### From a GitHub Release artifact

Each tagged release builds **sdist** and **wheel** packages. Download them from the [Releases](https://github.com/danecwalker/groktok/releases) page, then:

```bash
pip install groktok-0.2.1-py3-none-any.whl
# or
uv pip install groktok-0.2.1-py3-none-any.whl
```

### From a local clone (development)

```bash
git clone https://github.com/danecwalker/groktok.git
cd groktok
pip install -e .
# or
uv pip install -e .
groktok
```

You can also run without installing:

```bash
./groktok
# or
PYTHONPATH=src python3 -m groktok
```

## Grok skill (agent `/usage` upgrade)

Install a Grok skill so the agent runs **groktok** instead of the thin built-in
usage view when you ask about pool, tokens, remaining credits, or `/usage`.

### Recommended: skills CLI (global)

```bash
# installs the skill for your agents (Grok, Claude, Cursor, …)
npx skills add danecwalker/groktok -g -y
```

Or install only the `groktok` skill:

```bash
npx skills add danecwalker/groktok -g -y -s groktok
```

### Manual (Grok user skills)

```bash
mkdir -p ~/.grok/skills/groktok
curl -fsSL https://raw.githubusercontent.com/danecwalker/groktok/main/.grok/skills/groktok/SKILL.md \
  -o ~/.grok/skills/groktok/SKILL.md
```

### From a clone of this repo

Project skills under `.grok/skills/groktok/` load automatically when you open
the repo in Grok Build. No extra step.

### Use it

- Slash: `/groktok` (or ask “what’s my usage?”)
- The skill will ensure the `groktok` CLI is installed, then run it and summarize

Install the CLI as well (skill alone does not put `groktok` on your PATH):

```bash
uv tool install "git+https://github.com/danecwalker/groktok.git"
```

## Auth

Preferred: sign in with the official Grok CLI (stores a session in `~/.grok/auth.json`):

```bash
grok login
groktok
```

Overrides:

| Variable | Purpose |
|---|---|
| `GROKTOK_TOKEN` / `GROK_TOKEN` | Explicit session access token |
| `GROK_HOME` | Alternate Grok config directory (default `~/.grok`) |

Consumer weekly usage requires a **Grok/xAI session** (from `grok login`). A plain `XAI_API_KEY` is for the developer API and generally cannot read the subscription usage pool.

## Usage

```bash
groktok                    # weekly + monthly + local tokens + SuperGrok economics
groktok -i                 # interactive: fix week start / pool % after a reset
groktok --since morning --pool-percent 0 --recalibrate-window
groktok --weekly
groktok --monthly
groktok --tokens
groktok --tokens --period all
groktok --period week|7d|today|morning|month|all
groktok --plan-price 30    # save monthly plan fee for amortized $/MTok
groktok --recalibrate      # re-anchor capacity from API % + local tokens
groktok --usage-source local|api|auto
groktok --clear-calibration
groktok --clear-overrides  # drop ~/.grok/groktok.json
groktok --json             # machine-readable (tools/scripts)
groktok --format json      # same as --json
```

### Local-first pool usage (less dependent on laggy billing API)

Billing `creditUsagePercent` can lag after early / mid-week resets. `groktok`
**calibrates** full-week capacity once, then tracks usage from **local Build
tokens**:

```text
capacity ≈ local_build_tokens / (build_pool_% / 100)     # at calibration time
live_%   ≈ 100 × tokens_since_week_start / capacity      # thereafter
```

Calibration is stored in `~/.grok/groktok.json`. Re-anchor when the real pool
changes size, or after you trust a new API/% reading:

```bash
groktok --recalibrate
groktok --pool-percent 40 --recalibrate   # if you trust UI % more than API
```

After an early fleet reset (UI at 0%, API still 100%):

```bash
groktok --since morning --pool-percent 0 --recalibrate-window --weekly
# keeps capacity; moves week start; live % climbs from local tokens only
```

Default `--usage-source auto` prefers the local estimate when calibrated, and
still shows the billing API % as a secondary line when they disagree.

### SuperGrok economics ($/MTok)

Alongside API list-price cost, `groktok` derives:

| Rate | Meaning |
|---|---|
| **SuperGrok allotment** | `(monthly used $ × Build share) / Build tokens` |
| **Plan amortized** | `plan_price_usd / Build tokens` (set `--plan-price`) |
| **API list-equivalent** | xAI pay-as-you-go rates on the same tokens |

Allotment $ is the **included monthly compute budget**, not your card charge.
Build share comes from the billing product mix when available (else calibration /
assume all Build).

### Machine-readable JSON (for tools)

Use `--json` or `--format json` when another process needs to parse usage:

```bash
groktok --json
groktok --json --weekly
groktok --json --tokens --period today
groktok --json --monthly
```

Stdout is a single JSON object. Success envelope (`schema_version` **2**):

```json
{
  "ok": true,
  "version": "0.2.1",
  "schema_version": 2,
  "generated_at": "2026-07-17T12:00:00+00:00",
  "usage_source": "local_calibration",
  "effective_pool_percent": 68.0,
  "account": { "email": "...", "user_id": "...", "team_id": "...", "auth_source": "..." },
  "weekly": {
    "usage_percent": 68.0,
    "start": "...",
    "end": "...",
    "resets_in_seconds": 123456,
    "product_usage": [{ "product": "GrokBuild", "usage_percent": 68.0 }],
    "extra_usage_credits_usd": 9.38,
    "build_tokens": { "...": "local token window + api_cost when present" },
    "token_pool_estimate": { "...": "optional pool capacity proxy" }
  },
  "monthly": {
    "used_usd": 139.05,
    "limit_usd": 180.0,
    "usage_percent": 77.25
  },
  "local_tokens": {
    "period_label": "subscription week",
    "totals": { "total_tokens": 0, "input_tokens": 0, "output_tokens": 0 },
    "api_cost": { "total_usd": 0.0 }
  },
  "calibration": {
    "capacity_total": 1000000000,
    "invert_percent": 72.0,
    "source": "api"
  },
  "local_pool_estimate": {
    "build_pool_percent": 50.0,
    "estimated_overall_percent": 68.0
  },
  "supergrok_economics": {
    "rates_usd_per_mtok": {
      "supergrok_allotment": 0.17,
      "plan_amortized": null,
      "api_list_equivalent": 0.58
    }
  }
}
```

Errors also print JSON on stdout (exit code still non-zero):

```json
{
  "ok": false,
  "version": "0.2.1",
  "schema_version": 2,
  "generated_at": "...",
  "error": { "code": "auth", "message": "..." }
}
```

| `error.code` | Exit | Meaning |
|---|---|---|
| `auth` | `2` | Missing/invalid credentials |
| `billing` | `1` | Billing API / network failure |
| `usage` | `2` | Bad flags / parse error |

`schema_version` is bumped only for breaking shape changes. Soft warnings (e.g. auth missing when falling back to `--tokens`-style local data) still go to **stderr** so they do not corrupt the JSON document.

### When the week “resets” but the API is stale

xAI’s billing payload sometimes still shows the previous week window / %.
If you know the pool reset this morning:

```bash
groktok -i
# choose [2] today at local midnight
# set pool % to 0 (or whatever the UI shows)
# optionally save for next runs
```

Or one-shot (keeps calibrated capacity, moves the window):

```bash
groktok --since morning --pool-percent 0 --recalibrate-window --weekly
```

## What the numbers mean

| Section | Source | Meaning |
|---|---|---|
| **Weekly pool** | `GET …/billing?format=credits` | Shared weekly usage allowance across Grok products (Chat, Build, Imagine, Voice, …). Shown as **% used**, with per-product breakdown when available. |
| **Monthly usage** | `GET …/billing?format=tokens` | Included monthly allotment. Values are **USD cents** (same unit Grok Build’s `/usage` uses for `$` limits). |
| **Extra usage credits** | prepaid balance on the weekly payload | Pay-as-you-go top-up balance that applies after the included pool is exhausted. |
| **Local Build tokens** | `~/.grok/sessions/**/updates.jsonl` | Real LLM token counts from Grok Build on **this machine**, summed from each `turn_completed` event’s `usage` block. |

### Local token fields

Each finished turn records something like:

```json
"usage": {
  "inputTokens": 596477,
  "outputTokens": 11345,
  "totalTokens": 607822,
  "cachedReadTokens": 544384,
  "reasoningTokens": 10485,
  "modelCalls": 17,
  "modelUsage": { "grok-4.5": { "...": "..." } }
}
```

`groktok` sums those across sessions (optionally filtered by `--period`).

### API-equivalent cost analysis

Local tokens are priced at [xAI Text API rates](https://docs.x.ai/docs/models)
(e.g. Grok 4.5: **$2 / $0.50 / $6** per 1M uncached input / cached input / output).

```
uncached_input × $input  +  cached_input × $cached  +  output × $output
```

This is **not** your SuperGrok bill — it’s “what this compute would cost on
pay-as-you-go API.” Useful for comparing intensity and cache savings.

```bash
groktok --weekly              # includes cost block by default
groktok --no-cost             # hide it
groktok --long-context        # force ≥200k prompt tier rates
groktok --standard-rates      # force short-context rates
```

When pool % is known, cost is also inverted to **full-week $** and **remaining $**.

### Estimating the full weekly token pool

When the weekly pool start is known and the remote API reports e.g. **68% used**,
`groktok` inverts local tokens since that start:

```
full_week_tokens ≈ local_tokens_since_week_start / (pool_percent_used / 100)
remaining        ≈ full_week_tokens × (1 − pool_percent_used / 100)
```

If the product breakdown is (almost) all **GrokBuild**, confidence is **high**.
If Chat/Imagine/etc. also used the pool, the estimate is rougher.

**Caveats**

- **Grok Build only** — not Chat, Imagine, Voice, or other devices.
- **This machine’s** `~/.grok/sessions` only.
- **Input tokens** include prompt-cache hits; **uncached** ≈ `input − cached` (best-effort).
- Incomplete/crashed turns with no `turn_completed` event are not counted.
- Pool “% used” is xAI compute accounting, not a pure token meter — this is a **proxy**.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Billing API / network error |
| `2` | Missing or invalid credentials |

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml` and `src/groktok/__init__.py`.
2. Commit, then tag and push:

```bash
git tag v0.2.1
git push origin v0.2.1
```

3. GitHub Actions builds the sdist + wheel and attaches them to a GitHub Release.

## License

MIT
