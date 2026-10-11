#!/usr/bin/env python3
"""
job_scraper — crawl French job boards for stage / alternance / CDI / CDD /
intérim / freelance offers matching a profile, then filter & score them.

Two phases:
  1. LISTING  — walk each board's search results, collect offer URLs.
  2. DETAIL   — open each offer and read its schema.org **JobPosting** JSON-LD:
                exact title, company (revealed even when the card is anonymised),
                full description, contract type, seniority (months of experience),
                salary range, posting/expiry dates. Falls back to page text only
                if a post has no JSON-LD.

That JSON-LD is what makes the extraction rigorous — it is the same structured
data the board feeds to Google for Jobs, not scraped rendered text.

    pip install scrapling
    python3 scraper.py --debug                 # per-page counts + detail trace
    python3 scraper.py                          # -> offres.csv + offres.json
    python3 scraper.py --no-detail              # phase 1 only (fast, shallow)
    python3 scraper.py --max-detail 40          # cap deep fetches

Responsible use: respect each board's robots.txt / ToS, keep DELAY gentle,
prefer official APIs for sites that forbid scraping (LinkedIn, Indeed). Personal
job-search aid; nothing is ever sent.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from urllib.parse import quote_plus, urljoin

from common import (
    DELAY, fetch, html_of, jsonld_objects, find_jobposting, strip_html,
)


# ===========================================================================
#  YOUR PROFILE — edit this block
# ===========================================================================

QUERIES = [
    "cybersécurité", "sécurité informatique", "SOC", "pentest",
    "DevOps", "DevSecOps", "administrateur systèmes réseaux", "GRC", "audit sécurité",
    "stage cybersécurité", "stage sécurité", "stage PFE", "stage fin d'études",
]
LOCATIONS = ["Lille", "Paris", "France"]      # HelloWork uses {loc}; Free-Work ignores it
WANTED_CONTRACTS = {"stage", "alternance", "cdi", "cdd", "interim", "freelance"}

# keyword -> weight (matched against title + full description + company)
STRONG_KEYWORDS = {
    "iso 27001": 3, "iso 27005": 3, "ebios": 3, "grc": 3, "gouvernance": 2,
    "rgpd": 2, "soc": 2, "csirt": 2, "vulnérab": 2, "vulnerab": 2, "audit": 2,
    "hardening": 2, "durcissement": 2, "nftables": 3, "iptables": 2, "pentest": 2,
    "appsec": 3, "sécurité applicative": 3, "django": 3, "remédiation": 2,
    "conformité": 2, "siem": 2, "pki": 2, "python": 2, "next.js": 2, "llm": 3,
}
BOOST_KEYWORDS = [
    "sécurité", "cyber", "firewall", "docker", "kubernetes", "ansible", "cisco",
    "réseau", "linux", "devops", "typescript", "zabbix", "ssi", "iam", "edr",
    "menace", "incident", "risque", "detection", "détection", "forensic",
]
# titles/roles that are NOT your profile — pushed down hard
NEGATIVE_KEYWORDS = [
    "commercial", "vente", "avant-vente", "avant vente", "ingénieur d'affaires",
    "ingenieur d'affaires", "business developer", "account manager", "comptab",
    "ressources humaines", "marketing", "product owner", "chef de produit",
    "alternance bts", "bts sio",
]

MIN_SCORE = 2         # keep offers scoring >= this
MAX_PAGES = 3         # result pages per (query, location, site)
MAX_DETAIL = 80       # cap on deep detail fetches per run


# ===========================================================================
#  Data model
# ===========================================================================

@dataclass
class Offer:
    title: str
    company: str = ""
    location: str = ""
    contract: str = ""
    url: str = ""
    source: str = ""
    # deep fields (from JobPosting JSON-LD)
    months_experience: int | None = None
    salary: str = ""
    employment_type: str = ""
    date_posted: str = ""
    valid_through: str = ""
    direct_apply: bool | None = None
    description: str = ""
    snippet: str = ""
    score: int = 0
    fit: str = ""          # human-readable why-it-matches
    date: str = ""
    first_seen: str = ""   # date this URL was first observed (history store)
    is_new: bool = False   # first seen in THIS run

    def key(self) -> str:
        return _norm(self.title) + "|" + _norm(self.company)


# ===========================================================================
#  Helpers
# ===========================================================================

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def _text(el) -> str:
    if el is None:
        return ""
    v = el.get_all_text() if hasattr(el, "get_all_text") else str(el)
    return re.sub(r"[ \t]+", " ", str(v)).strip()


def _lines(el) -> list[str]:
    return [ln.strip() for ln in _text(el).split("\n") if ln.strip()]


def _ancestor(a, xpath: str):
    try:
        res = a.xpath(xpath)
    except Exception:  # noqa: BLE001
        return None
    return res[0] if res else None


def classify_contract(*texts: str, employment_type: str = "") -> str:
    """Contract type. Work-study/internship/CDD are detected from wording (the
    board's employmentType conflates them into FULL_TIME/TEMPORARY); the
    CDI / freelance / intérim axis is driven by employmentType when available,
    since post *descriptions* say "missions" for everyone and can't be trusted
    for it. Word-boundaried so "missions du poste" never reads as a freelance
    "mission", and "cdi"/"cdd" don't match inside other words."""
    t = _norm(" ".join(texts))
    et = (employment_type or "").upper()

    def has(*words):
        return any(re.search(rf"\b{re.escape(w)}\b", t) for w in words)

    # 1. work-study & internship — from wording (most reliable signal)
    if has("alternance", "alternant", "alternante", "apprenti", "apprentissage") \
       or "contrat pro" in t or "professionnalisation" in t:
        return "alternance"
    if has("stage", "stagiaire", "internship") or "fin d'etudes" in t or " pfe" in f" {t}":
        return "stage"
    # 2. freelance — explicit signals or CONTRACTOR (never the word "mission")
    if "CONTRACTOR" in et or has("freelance", "portage", "independant", "indépendant"):
        return "freelance"
    # 3. intérim
    if "TEMPORARY" in et or has("interim", "intérim", "intérimaire"):
        return "interim"
    # 4. CDD explicit
    if has("cdd") or "duree determinee" in t or "durée déterminée" in t:
        return "cdd"
    # 5. CDI — default for a permanent full-time post
    if et in ("FULL_TIME", "PART_TIME") or has("cdi", "permanent") or "duree indeterminee" in t:
        return "cdi"
    return "?"


def score_offer(o: Offer) -> tuple[int, str]:
    hay = _norm(" ".join([o.title, o.description, o.company]))
    reasons = []
    score = 0
    for kw, w in STRONG_KEYWORDS.items():
        if _norm(kw) in hay:
            score += w
            reasons.append(kw)
    score += sum(1 for kw in BOOST_KEYWORDS if _norm(kw) in hay)
    if _norm("cyber") in _norm(o.title) or _norm("sécur") in _norm(o.title):
        score += 2
    for n in NEGATIVE_KEYWORDS:
        if _norm(n) in hay:
            score -= 4
            reasons.append(f"-{n}")
    # seniority signals in the TITLE — a junior/student can't land a
    # "sénior / confirmé / manager / responsable" role. Skipped for
    # alternance/stage, where such words denote a grade track, not a requirement.
    tl = _norm(o.title)
    if o.contract not in ("alternance", "stage"):
        for w in ("senior", "confirme", "manager", "responsable", "lead",
                  "principal", "architecte", "directeur"):
            if w in tl:
                score -= 4
                reasons.append(f"-titre:{w}")
                break
    # seniority by exact required experience (junior bonus, senior demotion)
    m = o.months_experience
    if m is not None:
        if m <= 24:
            score += 2
            reasons.append(f"jr-ok({m}mo)")
        elif m < 48:
            pass
        elif m < 60:
            score -= 2
            reasons.append(f"exp{m}mo")
        elif m < 84:
            score -= 4
            reasons.append(f"senior{m}mo")
        else:
            score -= 7
            reasons.append(f"senior{m}mo")
    fit = ", ".join(dict.fromkeys(reasons))  # dedup, keep order
    return score, fit


def _salary_str(bs) -> str:
    if not isinstance(bs, dict):
        return ""
    cur = bs.get("currency", "")
    v = bs.get("value", {})
    if isinstance(v, dict):
        lo, hi, one = v.get("minValue"), v.get("maxValue"), v.get("value")
        unit = v.get("unitText", "")
        rng = f"{lo}-{hi}" if (lo or hi) else (str(one) if one else "")
        return f"{rng} {cur}/{unit}".strip() if rng else ""
    return f"{v} {cur}".strip()


def _location_str(jl) -> str:
    if isinstance(jl, list):
        jl = jl[0] if jl else {}
    addr = (jl or {}).get("address", {}) if isinstance(jl, dict) else {}
    if isinstance(addr, list):
        addr = addr[0] if addr else {}
    parts = [addr.get("addressLocality"), addr.get("addressRegion")] if isinstance(addr, dict) else []
    return ", ".join(p for p in parts if p)


# ===========================================================================
#  Phase 2 — DETAIL extraction (JobPosting JSON-LD)
# ===========================================================================

def extract_detail(url: str, source: str, debug: bool) -> Offer | None:
    page = fetch(url, debug)
    if page is None:
        return None
    html = html_of(page)
    jp = find_jobposting(jsonld_objects(html))
    if not jp:
        # fallback: title + visible text
        txt = strip_html(html)
        m = re.search(r"(missions|le poste|détail du poste|profil)", txt, re.I)
        desc = txt[m.start(): m.start() + 2500] if m else txt[:1500]
        o = Offer(title="(sans titre)", url=url, source=source, description=desc,
                  snippet=desc[:220])
        o.contract = classify_contract(desc)
        return o

    org = jp.get("hiringOrganization") or {}
    company = org.get("name", "") if isinstance(org, dict) else str(org)
    et = jp.get("employmentType", "")
    et = ",".join(et) if isinstance(et, list) else str(et)
    exp = jp.get("experienceRequirements") or {}
    months = exp.get("monthsOfExperience") if isinstance(exp, dict) else None
    desc = strip_html(jp.get("description", ""))

    o = Offer(
        title=(jp.get("title") or "").strip(),
        company=(company or "").strip(),
        location=_location_str(jp.get("jobLocation")),
        url=url,
        source=source,
        months_experience=months if isinstance(months, int) else None,
        salary=_salary_str(jp.get("baseSalary")),
        employment_type=et,
        date_posted=str(jp.get("datePosted", ""))[:10],
        valid_through=str(jp.get("validThrough", ""))[:10],
        direct_apply=jp.get("directApply") if isinstance(jp.get("directApply"), bool) else None,
        description=desc,
        snippet=desc[:220],
    )
    o.contract = classify_contract(o.title, desc, employment_type=et)
    if debug:
        exp_s = f"{months}mo" if months is not None else "?"
        print(f"      · {o.company[:24]:<24} {o.contract:<9} exp={exp_s:<5} {o.salary[:18]:<18} {o.title[:44]}")
    return o


# ===========================================================================
#  Phase 1 — LISTING parsers (collect URLs)   verified live 2026-08-28/29
# ===========================================================================

def list_hellowork(page, base: str) -> list[tuple[str, str, str]]:
    out, seen = [], set()
    for a in page.css("a[href*='/emplois/']"):
        href = (getattr(a, "attrib", {}) or {}).get("href", "")
        if not href or href in seen or "/emplois/" not in href:
            continue
        if not re.search(r"/emplois/\d+\.html", href):
            continue
        seen.add(href)
        lines = _lines(a)
        title = lines[0] if lines else ""
        company = lines[1] if len(lines) > 1 else ""
        out.append((urljoin(base, href), title, company))
    return out


def list_freework(page, base: str) -> list[tuple[str, str, str]]:
    out, seen = [], set()
    for a in page.css("a[href*='/job-mission/']"):
        href = (getattr(a, "attrib", {}) or {}).get("href", "")
        if not href or href in seen:
            continue
        seen.add(href)
        out.append((urljoin(base, href), _text(a), ""))
    return out


@dataclass
class SiteAdapter:
    name: str
    search_url: str      # placeholders: {query} {loc} {page}
    lister: object       # callable(page, base) -> list[(url, title, company)]
    base: str


ADAPTERS: list[SiteAdapter] = [
    SiteAdapter(
        "hellowork",
        "https://www.hellowork.com/fr-fr/emploi/recherche.html?k={query}&l={loc}&p={page}",
        list_hellowork, "https://www.hellowork.com",
    ),
    SiteAdapter(
        "free-work",
        "https://www.free-work.com/fr/tech-it/jobs?query={query}&page={page}",
        list_freework, "https://www.free-work.com",
    ),
]


def crawl_listing(ad: SiteAdapter, query: str, loc: str, debug: bool) -> list[tuple[str, str, str, str]]:
    """Return (url, card_title, card_company, source) stubs."""
    out = []
    for p in range(1, MAX_PAGES + 1):
        url = ad.search_url.format(query=quote_plus(query), loc=quote_plus(loc), page=p)
        page = fetch(url, debug)
        if page is None:
            break
        stubs = ad.lister(page, ad.base)
        if debug:
            print(f"    [{ad.name}] {query!r}@{loc!r} p{p}: {len(stubs)} links")
        if not stubs:
            break
        out.extend((u, t, c, ad.name) for (u, t, c) in stubs)
        time.sleep(DELAY)
    return out


# ===========================================================================
#  Filter + output
# ===========================================================================

def _title_prescore(title: str) -> int:
    t = _norm(title)
    s = sum(w for kw, w in STRONG_KEYWORDS.items() if _norm(kw) in t)
    s += 2 if ("cyber" in t or "sécur" in _norm(title) or "secur" in t) else 0
    s -= sum(4 for n in NEGATIVE_KEYWORDS if _norm(n) in t)
    return s


def filter_and_rank(offers: list[Offer]) -> list[Offer]:
    today = date.today().isoformat()
    seen, kept = set(), []
    for o in offers:
        if not o.title or o.key() in seen:
            continue
        seen.add(o.key())
        if o.valid_through and o.valid_through < today:
            continue                                   # expired
        if o.contract not in WANTED_CONTRACTS and o.contract != "?":
            continue
        o.score, o.fit = score_offer(o)
        if o.score < MIN_SCORE:
            continue
        kept.append(o)
    kept.sort(key=lambda x: (-x.score, x.source))
    return kept


CSV_COLS = ["score", "contract", "title", "company", "location", "months_experience",
            "salary", "date_posted", "valid_through", "direct_apply", "source",
            "is_new", "first_seen", "url", "fit", "snippet"]


def merge_history(current: list[Offer], path: str = "history.json") -> list[dict]:
    """Accumulate offers across runs so a fast daily scrape never loses what an
    earlier run found. Preserves each URL's first_seen date, marks offers seen
    for the first time in THIS run as is_new, keeps previously-found offers that
    weren't re-scraped, and drops expired ones from the returned (displayed)
    set. history.json keeps the full record (expired included, pruned at 120d)."""
    today = date.today().isoformat()
    try:
        hist = json.load(open(path, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        hist = []
    by_url = {o.get("url"): o for o in hist if o.get("url")}
    cur_urls = set()
    for o in current:
        d = asdict(o)
        u = d["url"]
        cur_urls.add(u)
        prev = by_url.get(u)
        d["first_seen"] = (prev or {}).get("first_seen") or today
        d["is_new"] = prev is None
        by_url[u] = d
    for u, d in by_url.items():
        if u not in cur_urls:
            d["is_new"] = False           # only THIS run's fresh finds are new

    def not_too_old(d):
        # prune records expired more than ~120 days (keep history lean)
        vt = d.get("valid_through") or ""
        return not (vt and vt < today and (date.fromisoformat(today) -
                    date.fromisoformat(vt)).days > 120)

    merged = [d for d in by_url.values() if not_too_old(d)]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    out = [d for d in merged if not (d.get("valid_through") and d["valid_through"] < today)]
    out.sort(key=lambda d: (-d.get("score", 0), not d.get("is_new"), d.get("source", "")))
    return out


def write_outputs(offers: list[dict], stem: str) -> None:
    with open(f"{stem}.json", "w", encoding="utf-8") as f:
        json.dump(offers, f, ensure_ascii=False, indent=2)
    with open(f"{stem}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(CSV_COLS)
        for d in offers:
            w.writerow([d.get(c, "") for c in CSV_COLS])
    n_new = sum(1 for d in offers if d.get("is_new"))
    print(f"\n-> wrote {stem}.json and {stem}.csv  ({len(offers)} offers, {n_new} new)")


# ===========================================================================
#  Main
# ===========================================================================

def main() -> None:
    global MIN_SCORE, MAX_PAGES, WANTED_CONTRACTS
    ap = argparse.ArgumentParser(description="Scrape & filter FR job boards (deep JSON-LD).")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--out", default="offres")
    ap.add_argument("--sites", nargs="*", default=None)
    ap.add_argument("--no-detail", action="store_true", help="phase 1 only (shallow)")
    ap.add_argument("--max-detail", type=int, default=MAX_DETAIL)
    ap.add_argument("--min-score", type=int, default=MIN_SCORE)
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES, help="result pages per search")
    ap.add_argument("--locations", default=None, help="comma-separated override of LOCATIONS")
    ap.add_argument("--queries", default=None, help="comma-separated override of QUERIES")
    ap.add_argument("--contracts", default=None,
                    help="comma-separated override of WANTED_CONTRACTS (kept contract types)")
    args = ap.parse_args()
    MIN_SCORE = args.min_score
    MAX_PAGES = args.max_pages
    queries = [q.strip() for q in args.queries.split(",")] if args.queries else QUERIES
    locations = [x.strip() for x in args.locations.split(",")] if args.locations else LOCATIONS
    if args.contracts:
        WANTED_CONTRACTS = {c.strip().lower() for c in args.contracts.split(",") if c.strip()}

    adapters = ADAPTERS if not args.sites else [a for a in ADAPTERS if a.name in args.sites]

    # ---- phase 1: collect URL stubs
    stubs: dict[str, tuple[str, str, str]] = {}   # url -> (title, company, source)
    for ad in adapters:
        for query in queries:
            for loc in locations:
                print(f"[list:{ad.name}] {query} @ {loc}")
                for (u, t, c, src) in crawl_listing(ad, query, loc, args.debug):
                    if u not in stubs:
                        stubs[u] = (t, c, src)
    print(f"\nPhase 1: {len(stubs)} unique offer URLs.")

    # ---- phase 2: deep detail (prioritised by cheap title pre-score)
    if args.no_detail:
        offers = [Offer(title=t, company=c, url=u, source=src,
                        contract=classify_contract(t))
                  for u, (t, c, src) in stubs.items()]
    else:
        ordered = sorted(stubs.items(), key=lambda kv: -_title_prescore(kv[1][0]))
        offers = []
        n = min(len(ordered), args.max_detail)
        print(f"Phase 2: deep-reading {n} posts (JobPosting JSON-LD)...\n")
        for i, (u, (t, c, src)) in enumerate(ordered[:n], 1):
            o = extract_detail(u, src, args.debug)
            if o is None:
                o = Offer(title=t, company=c, url=u, source=src, contract=classify_contract(t))
            elif not o.company and c:
                o.company = c
            offers.append(o)
            time.sleep(DELAY)

    for o in offers:
        o.date = datetime.now().strftime("%Y-%m-%d")

    print(f"\nCollected {len(offers)} offers; filtering...")
    ranked = filter_and_rank(offers)
    # accumulate across runs + flag new (only for the canonical 'offres' store)
    final = merge_history(ranked) if args.out == "offres" else [asdict(o) for o in ranked]
    for d in final[:25]:
        exp = f"{d['months_experience']}mo" if d.get("months_experience") is not None else "?"
        tag = "NEW " if d.get("is_new") else "    "
        print(f"  {tag}[{d['score']:>2}] {d['contract']:<9} exp={exp:<5} "
              f"{d['title'][:46]:<46} | {d['company'][:20]:<20}")
    write_outputs(final, args.out)


if __name__ == "__main__":
    main()
