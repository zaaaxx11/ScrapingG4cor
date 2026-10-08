---
name: scrapingg4cor
description: "Free scraper for any site — one command, cheapest working layer wins (anonymous Jina → keyed Jina → key+free proxy → local). User-defined targets, content verdicts, no paid API."
version: 1.0.0
metadata:
  hermes:
    tags: [scraping, jina, free-proxy, all-sites, content-verdict]
---

# ScrapingG4cor — free scraper for any site

**Repo:** https://github.com/zaaaxx11/ScrapingG4cor (MIT, English).
**Use when:** scraping any site with zero budget — no paid API, no residential
proxies, only anonymous Jina Reader, a free Jina key, and free proxy lists.

**One door:** everything runs through `scrape.py`.

## Setup

```bash
git clone https://github.com/zaaaxx11/ScrapingG4cor.git && cd ScrapingG4cor
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # L3 fallback needs it
```

Keys: `JINA_API_KEY` env first, then `~/.jina_key` (chmod 600). Never hardcoded,
never committed. `scrape.py key set` strips pasted spaces automatically.

## Commands (all via `scrape.py`)

| Command | Effect |
|---|---|
| `fetch URL [--site KEY] [--pool pool.txt] [--out page.md]` | one page through L0→L1→L2→L3, first content-verified layer wins |
| `sites` | list builtin + user targets (user rows tagged `[yours]`) |
| `my-sites` | user targets only |
| `add-site KEY URL [--kind K] [--price P] [--ok O]` | register a user target (`{q}` = keyword slot, optional) |
| `remove-site KEY` | delete a user target |
| `key guide\|set\|status` | where to get a free key / validate + store / show state |
| `prep [--big]` | refresh free-proxy pools (GB ~300; `--big` adds general sources) |
| `hunt "kw1" "kw2" [--max 100] [--want 3]` | batch hunt with auto check+fix |

## The chain (cheapest working layer wins)

1. **L0 anonymous Jina ($0)** — no key. If content verifies, STOP.
2. **L1 keyed Jina** — free key. Unlocks higher limits.
3. **L2 key + free proxy** — `X-Proxy-Url` combo for locale/WAF-guarded pages.
4. **L3 local** — direct fetch first, then StealthyFetcher through proxies.

## Rules (measured live)

1. **Content verdicts only.** Price + product markers must be present, or the
   layer counts as empty. HTTP status alone never counts.
2. **Free proxies burn in minutes.** Refresh the pool on EVERY hunt; validate
   content, never gate alone. Gate 202 is NOT success.
3. **User targets live in `targets.json`** (chmod 600, git-ignored — never
   pushed). `targets.example.json` shows the shape. User wins on key clash.
