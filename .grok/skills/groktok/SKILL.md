---
name: groktok
description: >
  Show Grok subscription weekly pool usage, monthly allotment, local Build
  token counts, SuperGrok economics ($/MTok), and local-first pool % — a richer
  alternative to built-in /usage. Use when the user asks about usage, rate
  limits, weekly pool, monthly allotment, token spend, cost per token,
  remaining credits, how much Grok they have left, billing usage, or runs
  /groktok or /usage.
metadata:
  short-description: "Better Grok usage (local-first pool, SuperGrok $/token)"
  author: danecwalker
compatibility: "Requires Python 3.9+ and a Grok login (grok login). Optional: uv for install."
---

# groktok — better Grok usage

Prefer **groktok** over Grok Build’s built-in `/usage`. It surfaces billing
data from Settings → Usage, local Build session tokens, **local-first pool %**
(calibrated capacity so laggy billing API is secondary), and **SuperGrok
economics** (allotment / plan / API-list $/MTok).

## When to use

- “What’s my usage?” / “How much Grok do I have left?”
- Weekly pool %, monthly allotment, extra credits
- Local Build token totals or cost estimates
- User runs `/groktok` or wants a better `/usage`

## Prerequisites

1. **Resolve the CLI** (first match wins):

```bash
command -v groktok || command -v uvx
```

2. **If `groktok` is missing**, install (prefer uv tool):

```bash
uv tool install "git+https://github.com/danecwalker/groktok.git"
```

One-shot without permanent install:

```bash
uvx --from "git+https://github.com/danecwalker/groktok.git" groktok
```

pip alternative:

```bash
pip install "git+https://github.com/danecwalker/groktok.git"
```

3. **Auth**: needs a Grok session from `grok login` (`~/.grok/auth.json`), or
   `GROKTOK_TOKEN` / `GROK_TOKEN`. A plain `XAI_API_KEY` is **not** enough for
   the consumer weekly pool.

If install fails or auth is missing, explain clearly and stop — do not invent usage numbers.

## Run commands

Default command (use this unless the user asks for a subset):

```bash
groktok
```

| Intent | Command |
|--------|---------|
| Full picture (default) | `groktok` |
| Weekly pool + local tokens | `groktok --weekly` |
| Monthly allotment only | `groktok --monthly` |
| Local Build tokens only (offline) | `groktok --tokens` |
| Tokens for a window | `groktok --tokens --period today` / `week` / `7d` / `month` / `all` |
| Machine-readable (tools) | `groktok --json` or `groktok --format json` |
| Save plan fee for amortized $/MTok | `groktok --plan-price 30` |
| Force re-anchor capacity | `groktok --recalibrate` |
| Early reset (keep capacity) | `groktok --since morning --pool-percent 0 --recalibrate-window --weekly` |
| Prefer billing API % | `groktok --usage-source api` |
| Hide API cost block | `groktok --no-cost` |
| Hide SuperGrok economics | `groktok --no-economics` |
| Force long-context rates | `groktok --long-context` |
| Clear calibration only | `groktok --clear-calibration` |
| Clear saved overrides | `groktok --clear-overrides` |

If only `uvx` is available, prefix the same args:

```bash
uvx --from "git+https://github.com/danecwalker/groktok.git" groktok [args...]
```

**Prefer `groktok --json` whenever you will parse or summarize programmatically** (agent tools, scripts, other skills). Combine with scope flags as needed, e.g. `groktok --json --weekly`. For a human-facing terminal dump, omit `--json`.

JSON envelope (`schema_version` 2): `ok`, `version`, `schema_version`, `generated_at`, plus `effective_pool_percent`, `usage_source`, `calibration`, `local_pool_estimate`, `supergrok_economics` when available. On failure, `ok` is `false` and `error.code` is one of `auth` | `billing` | `usage`. Soft warnings may still appear on stderr.

Do **not** run `groktok -i` (interactive) unless the user is present and explicitly wants interactive overrides. Do not combine `-i` with `--json`.

## Present results

1. Run the command with the shell tool; capture stdout/stderr and exit code. Prefer `--json` for agents.
2. If JSON: check `ok`; prefer `effective_pool_percent` + `usage_source` over raw `weekly.usage_percent` when they differ (local-first). Also surface `supergrok_economics.rates_usd_per_mtok` and monthly used/limit.
3. If text: show weekly % (note local-first vs API), monthly $, SuperGrok $/MTok, local tokens/cost.
4. Keep the bar charts / layout if the user sees terminal-style output; for chat, paraphrase the same figures clearly.
5. Exit codes: `0` success · `1` billing/network · `2` missing/invalid credentials.

### Auth failure (exit 2)

Tell the user to run:

```bash
grok login
```

Then re-run `groktok`.

### Stale weekly pool

If the user says the pool reset but numbers look like last week:

```bash
groktok --since morning --pool-percent 0 --recalibrate-window --weekly
```

Or suggest they set overrides interactively themselves with `groktok -i`.

## What the numbers mean (do not overclaim)

| Section | Meaning |
|---------|---------|
| **Weekly pool (effective)** | Prefer local-first % from calibrated capacity + local tokens; billing API % may lag. |
| **Monthly usage** | Included monthly allotment in USD (same units as Build `/usage`). |
| **Extra usage credits** | Prepaid balance after the included pool is exhausted. |
| **Local Build tokens** | Tokens from **this machine’s** `~/.grok/sessions` `turn_completed` events only. |
| **API-equivalent cost** | Pay-as-you-go list price for those tokens — **not** the SuperGrok subscription bill. |
| **SuperGrok allotment $/MTok** | `(monthly used $ × Build share) / Build tokens` — included compute budget, not card charge. |
| **Plan amortized $/MTok** | `plan_price / Build tokens` when `--plan-price` is set. |
| **Pool capacity** | Calibrated full-week Build-token-equivalent size; high confidence only when usage is mostly GrokBuild. |

## Do not

- Fabricate usage, pool %, or dollar amounts.
- Treat local tokens as the full account (other devices/products are missing).
- Treat API-equivalent cost or allotment $/MTok as the actual subscription charge.
- Use `XAI_API_KEY` alone for weekly pool reads.
- Open browser usage pages unless the CLI cannot run and the user asks.
