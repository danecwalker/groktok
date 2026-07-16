---
name: groktok
description: >
  Show Grok subscription weekly pool usage, monthly allotment, local Build
  token counts, and API-equivalent cost — a richer alternative to built-in
  /usage. Use when the user asks about usage, rate limits, weekly pool,
  monthly allotment, token spend, remaining credits, how much Grok they have
  left, billing usage, or runs /groktok or /usage.
metadata:
  short-description: "Better Grok usage (weekly pool, tokens, cost)"
  author: danecwalker
compatibility: "Requires Python 3.9+ and a Grok login (grok login). Optional: uv for install."
---

# groktok — better Grok usage

Prefer **groktok** over Grok Build’s built-in `/usage`. It surfaces the same
billing data as Settings → Usage on grok.com, plus local Build session tokens
and API-equivalent cost.

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
| Machine-readable | `groktok --json` |
| Hide API cost block | `groktok --no-cost` |
| Force long-context rates | `groktok --long-context` |
| Stale week after reset | `groktok --since morning --pool-percent 0 --weekly` |
| Clear saved overrides | `groktok --clear-overrides` |

If only `uvx` is available, prefix the same args:

```bash
uvx --from "git+https://github.com/danecwalker/groktok.git" groktok [args...]
```

Prefer `--json` when you need to parse or summarize; otherwise run without flags and show the CLI’s text output.

Do **not** run `groktok -i` (interactive) unless the user is present and explicitly wants interactive overrides.

## Present results

1. Run the command with the shell tool; capture stdout/stderr and exit code.
2. Show the useful numbers: weekly % used + reset, monthly used/remaining $, extra credits, local tokens/cost when present.
3. Keep the bar charts / layout if the user sees terminal-style output; for chat, paraphrase the same figures clearly.
4. Exit codes: `0` success · `1` billing/network · `2` missing/invalid credentials.

### Auth failure (exit 2)

Tell the user to run:

```bash
grok login
```

Then re-run `groktok`.

### Stale weekly pool

If the user says the pool reset but numbers look like last week:

```bash
groktok --since morning --pool-percent 0 --weekly
```

Or suggest they set overrides interactively themselves with `groktok -i`.

## What the numbers mean (do not overclaim)

| Section | Meaning |
|---------|---------|
| **Weekly pool** | Shared weekly allowance across Grok products (% used, per-product when available). |
| **Monthly usage** | Included monthly allotment in USD (same units as Build `/usage`). |
| **Extra usage credits** | Prepaid balance after the included pool is exhausted. |
| **Local Build tokens** | Tokens from **this machine’s** `~/.grok/sessions` `turn_completed` events only. |
| **API-equivalent cost** | Pay-as-you-go list price for those tokens — **not** the SuperGrok subscription bill. |
| **Pool token estimate** | Proxy from local tokens ÷ pool %; high confidence only when usage is mostly GrokBuild. |

## Do not

- Fabricate usage, pool %, or dollar amounts.
- Treat local tokens as the full account (other devices/products are missing).
- Treat API-equivalent cost as the actual subscription charge.
- Use `XAI_API_KEY` alone for weekly pool reads.
- Open browser usage pages unless the CLI cannot run and the user asks.
