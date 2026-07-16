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
uv tool install "git+https://github.com/danecwalker/groktok.git@v0.1.0"
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
pip install "git+https://github.com/danecwalker/groktok.git@v0.1.0"
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
pip install groktok-0.1.0-py3-none-any.whl
# or
uv pip install groktok-0.1.0-py3-none-any.whl
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
groktok                    # weekly + monthly + local Build tokens
groktok -i                 # interactive: fix week start / pool % after a reset
groktok --since morning --pool-percent 0   # non-interactive override
groktok --weekly
groktok --monthly
groktok --tokens
groktok --tokens --period all
groktok --period week|7d|today|morning|month|all
groktok --clear-overrides  # drop ~/.grok/groktok.json
groktok --json
```

### When the week “resets” but the API is stale

xAI’s billing payload sometimes still shows the previous week window / %.
If you know the pool reset this morning:

```bash
groktok -i
# choose [2] today at local midnight
# set pool % to 0 (or whatever the UI shows)
# optionally save for next runs
```

Or one-shot:

```bash
groktok --since morning --pool-percent 0 --weekly
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
git tag v0.1.0
git push origin v0.1.0
```

3. GitHub Actions builds the sdist + wheel and attaches them to a GitHub Release.

## License

MIT
