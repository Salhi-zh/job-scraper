#!/usr/bin/env python3
"""
common — shared, rigorous primitives for the job tools:

  * fetch()            polite HTTP GET with retry/backoff (Scrapling)
  * JobPosting JSON-LD parsing (schema.org) — the EXACT source of a post's
    title / company / description / salary / seniority, far more reliable than
    scraping rendered text (verified live: both HelloWork and Free-Work embed it)
  * email harvesting with de-obfuscation — Cloudflare data-cfemail / email-
    protection, [at]/(at)/ at /&#64;, [dot]/ dot /&#46;, HTML entities, mailto
    %40 — plus rejection of hosting/registrar/telemetry/image false-positives.

Responsible use: gentle rate limit (DELAY), public pages only, for a personal
job search. Nothing is ever sent.
"""
from __future__ import annotations

import html as _html
import json
import re
import time
from urllib.parse import urlparse

try:
    from scrapling.fetchers import Fetcher
except ImportError:  # pragma: no cover
    raise SystemExit("Scrapling not installed. Run:  pip install scrapling")


DELAY = 3.0            # seconds between fetches (be polite)
RETRIES = 2            # extra attempts on transient failure
TIMEOUT = 30

# ---------------------------------------------------------------------------
# Domain buckets
# ---------------------------------------------------------------------------

# never the company's own site (job boards, socials, registries, telemetry)
JUNK_DOMAINS = {
    "hellowork.com", "free-work.com", "indeed.com", "linkedin.com", "apec.fr",
    "welcometothejungle.com", "glassdoor.fr", "glassdoor.com", "meteojob.com",
    "monster.fr", "jobijoba.com", "diplomeo.com", "hellocv.com", "cadremploi.fr",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "tiktok.com",
    "youtube.com", "wikipedia.org", "societe.com", "pappers.fr", "verif.com",
    "google.com", "googletagmanager.com", "doubleclick.net", "w3.org",
    "schema.org", "sentry.io", "wixpress.com", "wix.com", "gstatic.com",
    "cloudflare.com", "gravatar.com", "example.com", "domain.com", "sentry.wixpress.com",
    "xxx.com", "test.com", "exemple.com", "email.com", "mail.com", "site.com",
    "offres-travail.com", "optioncarriere.com", "jobted.fr", "jooble.org",
    "trovit.fr", "neuvoo.fr", "figaro-emploi.fr", "keljob.com", "regionsjob.com",
    "jobrapido.com", "talent.com", "emploi.org",
}
# hosting / registrar / CDN providers whose contact email is NOT the company's
HOSTING_DOMAINS = {
    "ovh.com", "ovh.net", "ovhcloud.com", "gandi.net", "ionos.fr", "ionos.com",
    "o2switch.fr", "hostinger.com", "hostinger.fr", "godaddy.com", "1and1.fr",
    "amen.fr", "lws.fr", "phpnet.org", "infomaniak.com", "scaleway.com",
    "online.net", "planethoster.com", "hostgator.com", "bluehost.com",
    "squarespace.com", "shopify.com", "wordpress.com", "wix.com",
}
# role / functional local-parts (trustworthy even off the company's own domain)
ROLE_LOCALPARTS = {
    "recrutement", "recrutements", "recrut", "rh", "drh", "jobs", "job",
    "career", "careers", "carriere", "carrieres", "candidature", "candidatures",
    "talent", "talents", "contact", "hello", "bonjour", "info", "infos",
    "recrute", "emploi", "hr", "join",
}
# most-wanted local-parts, in priority order
GOOD_LOCALPARTS = [
    "recrutement", "recrut", "recrute", "rh", "drh", "hr", "jobs", "job",
    "career", "careers", "carriere", "carrieres", "candidature", "candidatures",
    "talent", "talents", "emploi", "join", "hello", "bonjour", "contact",
    "info", "infos",
]
COLD_LOCALPARTS = {"noreply", "no-reply", "nepasrepondre", "newsletter",
                   "support", "webmaster", "postmaster", "abuse", "dpo",
                   "privacy", "rgpd"}
BAD_LOCALPARTS = {"example", "exemple", "nom", "name", "prenom", "firstname",
                  "lastname", "votre", "your", "email", "mail", "user", "test",
                  "sample", "adresse", "monadresse", "ei"}  # 'ei' too short/generic-guard handled below
IMG_TAIL = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js",
            ".ico", ".woff", ".woff2", ".mp4", ".pdf")
# Allow-list of real TLDs — rejects truncated-domain artefacts like
# "careers@capgemini.our" (regex grabbing a word after the domain).
VALID_TLDS = {
    "com", "fr", "net", "org", "io", "eu", "co", "uk", "de", "es", "it", "be",
    "ch", "lu", "ca", "us", "ma", "tn", "dz", "sn", "ci", "info", "biz", "pro",
    "name", "tech", "dev", "ai", "app", "cloud", "group", "consulting", "agency",
    "solutions", "digital", "online", "site", "email", "africa", "asia", "eu",
    "nl", "pt", "pl", "se", "no", "fi", "dk", "ie", "at", "cz", "ro", "gr",
    "world", "global", "systems", "services", "company", "team", "work", "jobs",
    "int", "edu", "gov", "mobi", "one", "xyz", "re", "fm", "tv", "cc", "gg",
}


def registrable(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    # handle a few common 2-level ccTLDs (co.uk, com.fr…) minimally
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "gouv"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def domain_of(url_or_email: str) -> str:
    if "@" in url_or_email and "://" not in url_or_email:
        return url_or_email.split("@")[-1].lower().split(":")[0]
    return urlparse(url_or_email).netloc.lower().split(":")[0]


def is_junk_domain(host: str) -> bool:
    reg = registrable(host)
    return reg in JUNK_DOMAINS or host in JUNK_DOMAINS


def is_hosting_domain(host: str) -> bool:
    return registrable(host) in HOSTING_DOMAINS


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def html_of(page) -> str:
    return page.html_content if hasattr(page, "html_content") else str(page)


def fetch(url: str, debug: bool = False, retries: int = RETRIES):
    last = None
    for attempt in range(retries + 1):
        try:
            page = Fetcher.get(url, stealthy_headers=True, timeout=TIMEOUT)
            status = getattr(page, "status", 200)
            if status and status >= 400:
                last = f"status {status}"
                if status in (404, 410):     # not transient — give up
                    break
            else:
                return page
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    if debug:
        print(f"    [fetch fail] {url} :: {last}")
    return None


# ---------------------------------------------------------------------------
# JSON-LD JobPosting
# ---------------------------------------------------------------------------

def jsonld_objects(html: str) -> list:
    out = []
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.S | re.I,
    ):
        block = block.strip()
        for candidate in (block, re.sub(r",\s*([}\]])", r"\1", block)):
            try:
                out.append(json.loads(candidate))
                break
            except Exception:  # noqa: BLE001
                continue
    return out


def find_jobposting(objs: list) -> dict | None:
    stack = list(objs)
    while stack:
        x = stack.pop(0)
        if isinstance(x, dict):
            t = x.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                return x
            if "@graph" in x:
                stack = list(x["@graph"]) + stack
            else:
                stack = list(x.values()) + stack
        elif isinstance(x, list):
            stack = list(x) + stack
    return None


def strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|li|div|h[1-6]|tr)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Email harvesting + de-obfuscation
# ---------------------------------------------------------------------------

_CF_ATTR = re.compile(r'data-cfemail="([0-9a-fA-F]{4,})"')
_CF_HREF = re.compile(r'/cdn-cgi/l/email-protection#([0-9a-fA-F]{4,})')
_PLAIN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# "user [at] domain [dot] fr" and friends (NOT matching image @2x etc.)
_OBF = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*(?:\[at\]|\(at\)|\{at\}|&#0*64;|&#x40;|\sat\s|@)\s*"
    r"([A-Za-z0-9.\-]+)\s*(?:\[dot\]|\(dot\)|\{dot\}|&#0*46;|&#x2e;|\sdot\s|\.)\s*"
    r"([A-Za-z]{2,24})",
    re.I,
)


def decode_cfemail(hexstr: str) -> str:
    try:
        b = bytes.fromhex(hexstr)
        key = b[0]
        return "".join(chr(c ^ key) for c in b[1:])
    except Exception:  # noqa: BLE001
        return ""


def _valid_email(e: str) -> bool:
    e = e.strip().strip(".").lower()
    if not e or e.count("@") != 1:
        return False
    if any(e.endswith(t) for t in IMG_TAIL):
        return False
    local, _, dom = e.partition("@")
    if "@2x" in e or re.search(r"\d+x\b", local):     # image sprite artefacts
        return False
    if "." not in dom or len(dom) < 4:
        return False
    if dom.rsplit(".", 1)[-1] not in VALID_TLDS:      # reject truncated/fake TLDs
        return False
    if local in BAD_LOCALPARTS:
        return False
    if len(local) > 24 and re.fullmatch(r"[0-9a-f]+", local):  # telemetry hash
        return False
    if is_junk_domain(dom):
        return False
    return True


def harvest_emails(html: str) -> list[str]:
    """All published emails on a page, de-obfuscated, junk removed. Hosting/
    registrar addresses are DROPPED (they are not the company's inbox)."""
    found: set[str] = set()
    for hx in _CF_ATTR.findall(html) + _CF_HREF.findall(html):
        e = decode_cfemail(hx)
        if e:
            found.add(e.lower())
    for m in re.findall(r"mailto:([^\"'>?\s]+)", html):
        found.add(_html.unescape(m).replace("%40", "@").strip().lower())
    for m in _PLAIN.findall(_html.unescape(html)):
        found.add(m.strip().lower())
    for local, dom, tld in _OBF.findall(html):
        found.add(f"{local}@{dom}.{tld}".lower())
    clean = []
    for e in found:
        e = e.strip().strip(".").lower()
        if _valid_email(e) and not is_hosting_domain(e.split("@")[-1]):
            clean.append(e)
    return sorted(set(clean))


def rank_emails(emails: list[str], company_domain: str) -> list[str]:
    reg_company = registrable(company_domain) if company_domain else ""

    def key(e: str):
        local, _, dom = e.partition("@")
        same = registrable(dom) == reg_company if reg_company else False
        try:
            gi = next(i for i, g in enumerate(GOOD_LOCALPARTS) if local.startswith(g))
        except StopIteration:
            gi = len(GOOD_LOCALPARTS)
        named = bool(re.fullmatch(r"[a-z]+\.[a-z]+", local))  # prenom.nom
        cold = local in COLD_LOCALPARTS
        # prefer: same-domain, then (named person OR good role), warm over cold
        return (0 if same else 1, cold, 0 if (named or gi < len(GOOD_LOCALPARTS)) else 1, gi, len(e))

    return sorted(emails, key=key)
