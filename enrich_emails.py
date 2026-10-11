#!/usr/bin/env python3
"""
enrich_emails — resolve each company from offres.json to its PUBLISHED
recruitment / contact email (and phone), rigorously.

Why a separate pass: job boards deliberately do NOT expose recruiter emails on
offer pages — applications route through their forms. The legitimate route to a
real inbox is the company's own site: its "carrières / recrutement / contact"
page, or the "mentions légales" page that French law (LCEN art. 6-III) requires.
This reads those public, self-published addresses, for the purpose of sending a
candidature. It does NOT send anything.

Rigour beyond a naive scrape (all in common.py, verified live):
  * de-obfuscates Cloudflare cfemail / [at] / (dot) / HTML entities / mailto %40
    (e.g. ADN-Consulting hides romane.remoue@… behind Cloudflare — a naive
    scrape returns nothing and falls back to a guess);
  * drops hosting/registrar addresses (support@ovh.com) and image artefacts;
  * tries several mentions-légales / contact / recrutement paths, follows the
    homepage's own links, and prefers a named recruiter or role inbox on the
    company's own domain;
  * flags every generic guess (guessed=True) so it's never mistaken for verified.

    python3 enrich_emails.py                 # reads offres.json -> emails.csv/json
    python3 enrich_emails.py --in offres.json --out emails --debug --limit 5
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from common import (
    DELAY, fetch, html_of, harvest_emails, rank_emails, registrable, domain_of,
    is_junk_domain, is_hosting_domain, ROLE_LOCALPARTS,
)

DDG = "https://html.duckduckgo.com/html/?q="
MAX_PAGES = 6                       # company-owned pages fetched per company
LEGAL_PATHS = [                     # LCEN mentions-légales usually carries an email
    "/mentions-legales", "/mentions-legales/", "/mentions-legales.html",
    "/fr/mentions-legales.html", "/mentions-legales-rgpd", "/legal",
    "/contact", "/contact/", "/nous-contacter", "/contactez-nous",
    "/recrutement", "/recrutement/", "/carriere", "/carrieres", "/nous-rejoindre",
    "/rgpd", "/politique-de-confidentialite",
]
FOLLOW_HINTS = ("mentions-legales", "mentions_legales", "mentionslegales",
                "contact", "recrut", "carriere", "carrieres", "rejoind",
                "nous-rejoindre", "jobs", "job-openings", "legal", "rgpd",
                "confidentialite")
# Mandatory separators between pairs + digit boundaries, so a SIRET/RCS/other
# long digit run can never be mis-read as a phone (only real formatted numbers).
PHONE_RE = re.compile(r"(?<!\d)(?:\+33\s?|0033\s?|0)[1-9](?:[\s.\-]\d{2}){4}(?!\d)")


def _strip_www(host: str) -> str:
    return host[4:] if host.lower().startswith("www.") else host


def _norm_phone(raw: str) -> str:
    """Normalise a French number to '0X XX XX XX XX'; '' if not a valid 10-digit."""
    d = re.sub(r"\D", "", raw)
    if d.startswith("0033"):
        d = "0" + d[4:]
    elif d.startswith("33") and len(d) == 11:
        d = "0" + d[2:]
    if len(d) == 10 and d[0] == "0" and d[1] in "123456789":
        return " ".join(d[i:i + 2] for i in range(0, 10, 2))
    return ""


@dataclass
class CompanyContact:
    company: str
    domain: str = ""
    best_email: str = ""
    guessed: bool = False
    all_emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    source_page: str = ""
    note: str = ""


def _company_tokens(name: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) >= 4
            and w not in {"group", "groupe", "france", "consulting", "technologies",
                          "digital", "school", "retail", "security", "solutions"}]


def ddg_company_urls(company: str, debug: bool) -> list[str]:
    q = re.sub(r"\s+", "+", f"{company} site officiel recrutement contact")
    page = fetch(DDG + q, debug)
    if page is None:
        return []
    html = html_of(page)
    urls: list[str] = []
    for href in re.findall(r'href="(//duckduckgo\.com/l/\?[^"]+)"', html):
        qs = parse_qs(urlparse("https:" + href.replace("&amp;", "&")).query)
        if "uddg" in qs:
            urls.append(unquote(qs["uddg"][0]))
    if not urls:
        urls = re.findall(r'class="result__a"[^>]*href="([^"]+)"', html)
    out, seen = [], set()
    for u in urls:
        host = domain_of(u)
        if not host or is_junk_domain(host) or is_hosting_domain(host):
            continue
        if host in seen:
            continue
        seen.add(host)
        out.append(u)
    return out


def pick_company_site(company: str, urls: list[str]) -> str:
    """Prefer a domain whose name overlaps the company (adn-consulting.fr for
    'ADN-Consulting'); otherwise the top non-junk result (handles acronyms like
    EURO INFORMATION -> e-i.com where nothing overlaps)."""
    toks = _company_tokens(company)
    best, best_score = "", -1
    for i, u in enumerate(urls):
        host = registrable(domain_of(u))
        score = 2 if any(t in host for t in toks) else 0
        score -= i * 0.01                      # keep DDG order on ties
        if score > best_score:
            best, best_score = u, score
    return best or (urls[0] if urls else "")


def enrich_company(company: str, debug: bool) -> CompanyContact:
    cc = CompanyContact(company=company)
    candidates = ddg_company_urls(company, debug)
    time.sleep(DELAY)
    if not candidates:
        cc.note = "no company site found via search"
        return cc

    home = pick_company_site(company, candidates)
    cc.domain = _strip_www(domain_of(home))
    scheme = urlparse(home).scheme or "https"
    root = f"{scheme}://{urlparse(home).netloc}"

    # fetch list: discovered same-domain URLs, homepage, then curated legal paths
    to_fetch: list[str] = []
    for u in candidates:
        if registrable(domain_of(u)) == registrable(cc.domain) and u not in to_fetch:
            to_fetch.append(u)
    if root + "/" not in to_fetch:
        to_fetch.insert(0, root + "/")
    for path in LEGAL_PATHS:
        cand = root + path
        if cand not in to_fetch:
            to_fetch.append(cand)

    all_emails: list[str] = []
    phones: set[str] = set()
    src_of: dict[str, str] = {}
    fetched = 0
    i = 0
    while i < len(to_fetch) and fetched < MAX_PAGES:
        url = to_fetch[i]
        i += 1
        page = fetch(url, debug)
        if page is None:
            continue
        fetched += 1
        time.sleep(DELAY)
        html = html_of(page)
        if fetched <= 2:                       # discover follow links from home
            for href in re.findall(r'href="([^"#?]+)"', html):
                low = href.lower()
                if any(h in low for h in FOLLOW_HINTS):
                    nxt = urljoin(url, href)
                    if registrable(domain_of(nxt)) == registrable(cc.domain) and nxt not in to_fetch:
                        to_fetch.append(nxt)
        for e in harvest_emails(html):
            if e not in src_of:
                src_of[e] = url
            all_emails.append(e)
        for ph in PHONE_RE.findall(html):
            n = _norm_phone(ph)
            if n:
                phones.add(n)
        if debug:
            print(f"    [{fetched}/{MAX_PAGES}] {url} -> {len(set(all_emails))} email(s)")
        # stop early if we already have a same-domain role/named inbox
        ranked_now = rank_emails(sorted(set(all_emails)), cc.domain)
        if ranked_now and _trustworthy(ranked_now[0], cc.domain) and fetched >= 2:
            break

    cc.phones = sorted(phones)[:2]
    ranked = rank_emails(sorted(set(all_emails)), cc.domain)
    cc.all_emails = ranked
    best = next((e for e in ranked if _trustworthy(e, cc.domain)), "")
    if best:
        cc.best_email = best
        cc.source_page = src_of.get(best, "")
        cc.note = "published on company site"
    elif cc.domain and not is_junk_domain(cc.domain):
        cc.best_email = f"recrutement@{cc.domain}"
        cc.guessed = True
        cc.note = ("GUESSED pattern — no published email found; verify before use"
                   + (f" (site shows only: {ranked[0]})" if ranked else ""))
    else:
        cc.note = "no reliable company site resolved — apply via the offer link"
    return cc


def _trustworthy(email: str, company_domain: str) -> bool:
    local, _, dom = email.partition("@")
    if registrable(dom) == registrable(company_domain):
        return True
    return any(local.startswith(r) for r in ROLE_LOCALPARTS)


def load_companies(path: str) -> list[str]:
    data = json.load(open(path, encoding="utf-8"))
    seen, out = set(), []
    for o in data:
        c = (o.get("company") or "").strip()
        k = c.lower()
        if c and k not in seen:
            seen.add(k)
            out.append(c)
    return out


def write_outputs(rows: list[CompanyContact], stem: str) -> None:
    with open(f"{stem}.json", "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in rows], f, ensure_ascii=False, indent=2)
    with open(f"{stem}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["company", "best_email", "guessed", "domain", "phones",
                    "source_page", "all_emails", "note"])
        for r in rows:
            w.writerow([r.company, r.best_email, r.guessed, r.domain,
                        " | ".join(r.phones), r.source_page,
                        " | ".join(r.all_emails), r.note])
    print(f"\n-> wrote {stem}.json and {stem}.csv  ({len(rows)} companies)")


def load_cache(path: str) -> dict[str, dict]:
    """Existing emails.json keyed by company (lowercased) — lets repeat runs
    skip companies already resolved instead of re-fetching every one."""
    try:
        return {r["company"].lower(): r for r in json.load(open(path, encoding="utf-8"))}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(description="Find companies' published recruitment emails.")
    ap.add_argument("--in", dest="inp", default="offres.json")
    ap.add_argument("--out", default="emails")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true",
                    help="re-resolve every company (default: reuse already-known ones)")
    args = ap.parse_args()

    companies = load_companies(args.inp)
    if args.limit:
        companies = companies[: args.limit]
    cache = {} if args.refresh else load_cache(f"{args.out}.json")
    reused = sum(1 for c in companies if c.lower() in cache)
    print(f"Resolving emails for {len(companies)} companies "
          f"({reused} reused from cache, {len(companies) - reused} to fetch)...\n")

    rows: list[CompanyContact] = []
    for n, comp in enumerate(companies, 1):
        cached = cache.get(comp.lower())
        if cached and cached.get("best_email"):
            rows.append(CompanyContact(**{k: cached.get(k) for k in
                        CompanyContact.__dataclass_fields__ if k in cached}))
            continue
        print(f"[{n}/{len(companies)}] {comp}")
        cc = enrich_company(comp, args.debug)
        flag = "  (guessed)" if cc.guessed else ""
        tail = f"  ☎ {cc.phones[0]}" if cc.phones else ""
        print(f"      -> {cc.best_email}{flag}   [{cc.note}]{tail}")
        rows.append(cc)

    rows.sort(key=lambda r: (r.guessed, not r.best_email, r.company.lower()))
    write_outputs(rows, args.out)


if __name__ == "__main__":
    main()
