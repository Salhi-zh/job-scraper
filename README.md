# job-scraper

Personal job-search tool for French cybersécurité / GRC roles. Scrapes job
boards, resolves recruiter/company emails, and renders a self-contained
dashboard — with application tracking and daily auto-refresh.

## Quick start

```bash
cd ~/job-scraper
./jobs update      # scrape (fast) → emails → dashboard
./jobs open        # open the dashboard in your browser
./jobs status      # last refresh, offer count, new since last scan
```

That's it. Run `./jobs` with no argument to see every command.

| command | what it does |
|---|---|
| `./jobs update` | fast scrape (Lille + France, 2 pages) → emails → dashboard |
| `./jobs full` | maximum-scope scrape (all locations, 3 pages) — slow (~15 min) |
| `./jobs open` | rebuild + open `dashboard.html` |
| `./jobs serve` | **control panel** — pick options and run scrapes from the web page |
| `./jobs build` | rebuild the dashboard from existing data (no scraping) |
| `./jobs auto on` / `off` | daily auto-refresh at 08:00 (cron) |
| `./jobs status` | last refresh time, offers, new count, auto state |

## The dashboard (`dashboard.html`)

Static, self-contained (data embedded), opens offline. Features:

- **Ranked by CV-fit** — weighted keywords (ISO 27001, EBIOS, GRC, RGPD, nftables…)
  and seniority (junior = bonus, senior = penalty). Matched terms shown per card.
- **Application tracking** — set each offer's status (À postuler / Postulé /
  Relancé / Réponse / Refusé / Masqué) and a free-text note. Stored in your
  browser (localStorage), survives refreshes.
- **NOUVEAU badge** — offers first seen at the last scan.
- **Rich card info** — company, location (+ other cities if the role is reposted
  elsewhere), *publié il y a…*, salary, expiry, *candidature directe*, seniority
  (exact months), télétravail, and the matched keywords.
- **Cross-city de-duplication** — the same role in 3 cities becomes one card.
- **Filters** — search, contract type, **région** (or télétravail), verified-email,
  **junior / alternance** (hides senior/manager roles), and application status.
- **Export** — download your tracked applications (Postulé / Relancé / Réponse /
  Refusé + notes) as an Excel-ready CSV, so they survive a browser reset.
- **« Écrire »** — pre-fills a French candidature email (attach your CV yourself).
- **Theme toggle** — light / dark, remembered.

## Control panel (`./jobs serve`)

Runs a tiny **local** server (`127.0.0.1:8777` only) so the web page can drive
the pipeline: choose queries, locations, sites, contract types, pages, detail
depth, min-score, then hit **Lancer la recherche** and watch the live log —
results refresh in place when it finishes. Options are validated/clamped and the
scraper runs with list-form arguments (no shell), so the page can't inject
commands. Email resolution is **incremental** — companies already resolved are
reused from cache, so repeat runs take seconds, not minutes. Opening
`dashboard.html` directly (without the server) still works as a static snapshot;
the control panel simply stays hidden.

## How it works

```
scraper.py       two-phase: search listings → schema.org JobPosting JSON-LD
  → offres.json  (exact title, company, description, salary, months-experience,
                  expiry; accumulates across runs via history.json, flags new)
enrich_emails.py company site → published email (de-obfuscates Cloudflare
  → emails.json  cfemail / [at] / entities; drops hosting/registrar; flags guesses)
merge_shortlist  join offers ↔ emails → shortlist.csv
build_dashboard  → dashboard.html
common.py        shared fetch / JSON-LD / email primitives
```

Tune your profile at the top of `scraper.py` (`QUERIES`, `LOCATIONS`,
`STRONG_KEYWORDS`, `NEGATIVE_KEYWORDS`) and your contact details in
`build_dashboard.py` (`ME`).

## Honest limitations

- **Static dashboard** — a browser file can't scrape boards itself (CORS + bot
  blocks). It's a snapshot; `./jobs update` (or the daily cron) refreshes it.
- **Two boards only** — HelloWork + Free-Work. APEC and Welcome to the Jungle
  are JS single-page apps (nothing in static HTML) and APEC's private API is
  undocumented/unstable; adding them would need a headless browser. Not done.
- **Guessed emails** — flagged `~ deviné`; verify or apply via the offer link.
  Acronym company names can resolve to a look-alike domain — eyeball it.
- **Daily cron** needs the machine on at 08:00 (cron doesn't catch up misses).

## Responsible use

Respects robots.txt / ToS, keeps a gentle rate limit (`DELAY` ≥ 3s), and never
scrapes sites that forbid it (LinkedIn, Indeed). A personal job-search aid —
it collects public contact addresses and **never sends anything**.
