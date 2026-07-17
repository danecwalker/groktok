---
name: groktok
description: >
  Show Grok subscription weekly pool usage and monthly allotment — a clearer
  alternative to built-in /usage. Use when the user asks about usage, rate
  limits, weekly pool, monthly allotment, remaining credits, how much Grok
  they have left, billing usage, or runs /groktok or /usage.
metadata:
  short-description: "Grok usage (weekly pool + monthly allotment)"
  author: danecwalker
compatibility: "Requires Python 3.9+ and a Grok login (grok login). Optional: uv for install."
---

# groktok — Grok usage

Prefer **groktok** over Grok Build’s built-in `/usage`. It shows the same
billing data as Settings → Usage on grok.com.

## When to use

- “What’s my usage?” / “How much Grok do I have left?”
- Weekly pool %, monthly allotment, extra credits
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

3. **Auth**: needs a Grok session from `grok login` (`~/.grok/auth.json`), or
   `GROKTOK_TOKEN` / `GROK_TOKEN`. A plain `XAI_API_KEY` is **not** enough.

If install fails or auth is missing, explain clearly and stop — do not invent usage numbers.

## Run commands

Default:

```bash
groktok
```

| Intent | Command |
|--------|---------|
| Full picture (default) | `groktok` |
| Weekly pool only | `groktok --weekly` |
| Monthly allotment only | `groktok --monthly` |
| Machine-readable | `groktok --json` |
| Monthly history | `groktok --history` |

If only `uvx` is available, prefix the same args:

```bash
uvx --from "git+https://github.com/danecwalker/groktok.git" groktok [args...]
```

Prefer `--json` when parsing programmatically.

## Present results

1. Run the command; capture stdout/stderr and exit code.
2. Show weekly % used + reset, monthly used/remaining $, extra credits.
3. Exit codes: `0` success · `1` billing/network · `2` credentials.

### Auth failure (exit 2)

```bash
grok login
```

Then re-run `groktok`.

## What the numbers mean (do not overclaim)

| Section | Meaning |
|---------|---------|
| **Weekly pool** | Shared weekly allowance across Grok products (% used). |
| **Monthly usage** | Included monthly allotment in USD. |
| **Extra usage credits** | Prepaid balance after the included pool is exhausted. |

## Do not

- Fabricate usage, pool %, or dollar amounts.
- Use `XAI_API_KEY` alone for weekly pool reads.
- Open browser usage pages unless the CLI cannot run and the user asks.
