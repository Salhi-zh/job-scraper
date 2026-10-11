#!/usr/bin/env python3
"""
build_dashboard — render offres.json + emails.json into a single self-contained
dashboard.html (no server, no external assets, opens offline). Re-run after each
scrape to refresh it.

    python3 build_dashboard.py            # -> dashboard.html
    python3 build_dashboard.py --open     # also open in the default browser
"""
from __future__ import annotations

import argparse
import json
import re
import webbrowser
from datetime import date
from pathlib import Path

# --- your details (used to pre-fill the "Écrire" candidature mailto) ----------
ME = {
    "name": "Mohamed SALHI",
    "email": "mohamed.salhi@polytech-lille.net",
    "phone": "+33 6 78 84 13 71",
    "school": "Polytech Lille",
    "seeking": "une alternance ou un stage de fin d'études",
}


def _fmt_salary(s: str) -> str:
    """'40000-50000 EUR/YEAR' -> '40–50 k€/an'; '400 EUR/DAY' -> '400 €/j'."""
    if not s:
        return ""
    unit = {"YEAR": "/an", "MONTH": "/mois", "DAY": "/j", "HOUR": "/h"}
    suffix = ""
    for k, v in unit.items():
        if k in s:
            suffix = v
            break
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    if not nums:
        return s
    annual = "/an" in suffix

    def one(n: float) -> str:
        if annual and n >= 1000:
            return f"{round(n / 1000)}"
        return f"{round(n):,}".replace(",", " ")
    body = "–".join(dict.fromkeys(one(n) for n in nums))
    kilo = " k€" if annual and max(nums) >= 1000 else " €"
    return f"{body}{kilo}{suffix}".replace("  ", " ").strip()


def _region(location: str) -> str:
    """French région from a 'City, Région' location string ('' for national)."""
    loc = (location or "").strip()
    if not loc or loc.lower() == "france":
        return ""
    return loc.split(",")[-1].strip() if "," in loc else loc


def load_records() -> list[dict]:
    offers = json.load(open("offres.json", encoding="utf-8"))
    emails = {r["company"].lower(): r for r in json.load(open("emails.json", encoding="utf-8"))}
    out = []
    for o in offers:
        c = (o.get("company") or "").strip()
        e = emails.get(c.lower(), {})
        out.append({
            "score": o.get("score", 0),
            "contract": o.get("contract", "?"),
            "title": o.get("title", ""),
            "company": c,
            "location": o.get("location", ""),
            "region": _region(o.get("location", "")),
            "months": o.get("months_experience"),
            "salary": _fmt_salary(o.get("salary", "")),
            "posted": o.get("date_posted", ""),
            "expires": o.get("valid_through", ""),
            "source": o.get("source", ""),
            "url": o.get("url", ""),
            "fit": [f for f in (o.get("fit", "") or "").split(", ") if f],
            "description": o.get("description", "") or o.get("snippet", ""),
            "email": e.get("best_email", ""),
            "verified": bool(e.get("best_email") and not e.get("guessed")),
            "phone": (e.get("phones") or [""])[0],
            "email_note": e.get("note", ""),
            "is_new": bool(o.get("is_new")),
            "first_seen": o.get("first_seen", ""),
            "direct_apply": o.get("direct_apply"),
            "remote": bool(re.search(r"t[ée]l[ée]travail|remote|hybride|distanciel",
                                     (o.get("description", "") or ""), re.I)),
        })
    return _dedup(out)


def _dedup(records: list[dict]) -> list[dict]:
    """Collapse the same role reposted across cities (e.g. one Sopra offer in
    Aix / Toulouse / Île-de-France) into a single card, keeping the best-scored
    one and listing the other locations. History keeps every original."""
    def base(t: str) -> str:
        t = re.sub(r"\s*h/f\s*$", "", (t or "").strip(), flags=re.I)
        return re.split(r"\s[-–]\s", t)[0].strip().lower()

    groups: dict[tuple, list[dict]] = {}
    for r in records:
        groups.setdefault((base(r["title"]), r["company"].lower()), []).append(r)
    out = []
    for g in groups.values():
        g.sort(key=lambda r: -r["score"])
        best = g[0]
        best["is_new"] = any(x["is_new"] for x in g)
        best["alt_locations"] = sorted({x["location"] for x in g[1:]
                                        if x["location"] and x["location"] != best["location"]})
        out.append(best)
    return out


def _meta(records: list[dict]) -> dict:
    return {
        "generated": date.today().isoformat(),
        "offers": len(records),
        "companies": len({r["company"].lower() for r in records if r["company"]}),
        "verified": sum(1 for r in records if r["verified"]),
        "new": sum(1 for r in records if r["is_new"]),
        "me": ME,
    }


def dashboard_payload() -> dict:
    """Live data for the backend's /api/data — same shape as the embedded blob."""
    records = load_records()
    records.sort(key=lambda r: -r["score"])
    return {"records": records, "meta": _meta(records)}


def build_html(records: list[dict]) -> str:
    meta = _meta(records)
    data_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    meta_json = json.dumps(meta, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("/*__DATA__*/", data_json).replace("/*__META__*/", meta_json)


TEMPLATE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recherche — Cybersécurité / GRC</title>
<style>
  :root{
    --bg:#f5f6f8; --surface:#ffffff; --surface-2:#fbfcfd; --border:#e4e7ec;
    --ink:#141a22; --muted:#5b6673; --faint:#8a94a2; --accent:#0f6d67;
    --accent-soft:#e6f2f1; --shadow:0 1px 2px rgba(16,24,40,.04),0 1px 3px rgba(16,24,40,.06);
    --ok:#0a7d4f; --ok-soft:#e6f4ec; --warn:#9a6b00; --warn-soft:#fbf1dd;
  }
  @media (prefers-color-scheme:dark){
    :root{
      --bg:#0e1116; --surface:#161a20; --surface-2:#1b2028; --border:#262d37;
      --ink:#e7ebf1; --muted:#9aa4b2; --faint:#6b7583; --accent:#5fd3c9;
      --accent-soft:#123331; --shadow:none;
      --ok:#57c98a; --ok-soft:#12331f; --warn:#e3b45f; --warn-soft:#332912;
    }
  }
  *{box-sizing:border-box}
  [hidden]{display:none !important}   /* beat class rules like .btn{display:inline-flex} */
  html,body{margin:0}
  body{
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    color:var(--ink); background:var(--bg); -webkit-font-smoothing:antialiased;
  }
  a{color:inherit}
  .wrap{max-width:1180px; margin:0 auto; padding:0 20px 64px}

  header.top{padding:26px 0 18px; border-bottom:1px solid var(--border); margin-bottom:18px}
  .top h1{font-size:19px; font-weight:650; letter-spacing:-.01em; margin:0 0 3px; padding-right:46px}
  .top .sub{color:var(--muted); font-size:13px; padding-right:46px}
  .stats{display:flex; gap:22px; margin-top:14px; flex-wrap:wrap}
  .stat{display:flex; flex-direction:column}
  .stat b{font-size:20px; font-weight:640; font-variant-numeric:tabular-nums; letter-spacing:-.02em}
  .stat span{color:var(--faint); font-size:11px; text-transform:uppercase; letter-spacing:.06em}

  .toolbar{position:sticky; top:0; z-index:5; background:color-mix(in srgb,var(--bg) 88%,transparent);
    backdrop-filter:saturate(180%) blur(8px); padding:12px 0 11px;
    display:flex; gap:10px; align-items:center; flex-wrap:wrap}
  .filterbar{display:flex; gap:10px 12px; align-items:center; flex-wrap:wrap;
    padding:0 0 13px; margin-bottom:8px; border-bottom:1px solid var(--border)}
  .btn.export{margin-left:auto; white-space:nowrap}
  .search{flex:1 1 260px; min-width:200px; display:flex; align-items:center; gap:8px;
    background:var(--surface); border:1px solid var(--border); border-radius:9px; padding:8px 11px}
  .search svg{width:15px; height:15px; color:var(--faint); flex:none}
  .search input{border:0; outline:0; background:transparent; color:var(--ink); width:100%; font-size:13.5px}
  .chips{display:flex; gap:6px; flex-wrap:wrap}
  .chip{border:1px solid var(--border); background:var(--surface); color:var(--muted);
    border-radius:20px; padding:6px 12px; font-size:12.5px; cursor:pointer; user-select:none;
    transition:all .12s}
  .chip:hover{border-color:var(--accent); color:var(--ink)}
  .chip.on{background:var(--accent); border-color:var(--accent); color:#fff}
  @media (prefers-color-scheme:dark){ .chip.on{color:#08130f} }
  .toggle{display:flex; align-items:center; gap:7px; color:var(--muted); font-size:12.5px;
    cursor:pointer; user-select:none}
  .toggle input{accent-color:var(--accent); width:15px; height:15px}
  select.sort{border:1px solid var(--border); background:var(--surface); color:var(--ink);
    border-radius:9px; padding:8px 10px; font-size:12.5px; cursor:pointer; outline:0}
  .count{color:var(--faint); font-size:12.5px; margin:6px 2px 14px}

  .card{background:var(--surface); border:1px solid var(--border); border-radius:12px;
    padding:16px 18px; margin-bottom:10px; box-shadow:var(--shadow); display:grid;
    grid-template-columns:46px 1fr; gap:16px;
    transition:border-color .15s, box-shadow .15s}
  .card:hover{border-color:color-mix(in srgb,var(--accent) 45%,var(--border));
    box-shadow:0 6px 22px rgba(16,24,40,.07)}
  @media (prefers-color-scheme:dark){ .card:hover{box-shadow:0 6px 24px rgba(0,0,0,.4)} }
  .scorebox{display:flex; flex-direction:column; align-items:center; gap:4px; padding-top:1px}
  .scorebox .n{font-size:16px; font-weight:680; font-variant-numeric:tabular-nums;
    letter-spacing:-.02em; color:var(--muted); display:inline-flex; align-items:center;
    justify-content:center; min-width:40px; height:34px; border-radius:9px;
    background:var(--surface-2); border:1px solid var(--border)}
  .scorebox.mid .n{color:var(--accent); background:var(--accent-soft); border-color:transparent}
  .scorebox.hi .n{color:#fff; background:var(--accent); border-color:transparent}
  @media (prefers-color-scheme:dark){ .scorebox.hi .n{color:#08130f} }
  :root[data-theme="light"] .scorebox.hi .n{color:#fff}
  :root[data-theme="dark"] .scorebox.hi .n{color:#08130f}
  .scorebox .l{font-size:9px; text-transform:uppercase; letter-spacing:.05em; color:var(--faint)}
  .body h2{font-size:15.5px; font-weight:620; margin:0 0 4px; letter-spacing:-.01em}
  .body h2 a{text-decoration:none}
  .body h2 a:hover{color:var(--accent); text-decoration:underline}
  .meta{display:flex; gap:8px 14px; flex-wrap:wrap; color:var(--muted); font-size:12.5px; margin-bottom:9px}
  .meta .co{color:var(--ink); font-weight:560}
  .meta .dot{color:var(--border)}
  .meta .altloc{color:var(--faint); font-size:11px}
  .meta .direct{color:var(--ok); font-weight:560}
  .tags{display:flex; gap:6px; flex-wrap:wrap; margin-bottom:11px}
  .tag{font-size:11px; padding:3px 9px; border-radius:6px; font-weight:560; letter-spacing:.01em}
  .tag.alternance{background:#eee7fb; color:#5b34b3} .tag.cdi{background:var(--accent-soft); color:var(--accent)}
  .tag.freelance{background:#fbf1dd; color:#8a5a00} .tag.interim{background:#fde8df; color:#a34a1c}
  .tag.stage{background:#e3f3e8; color:#1c7a44} .tag.cdd{background:#eceef1; color:#5b6673}
  .tag[class~="?"]{background:#eceef1; color:#5b6673}
  @media (prefers-color-scheme:dark){
    .tag.alternance{background:#241a3a; color:#c3a7f2} .tag.freelance{background:#2e2510; color:#e3b45f}
    .tag.interim{background:#31200f; color:#e39a6f} .tag.stage{background:#12331f; color:#78d39c}
    .tag.cdd{background:#232830; color:#9aa4b2}
  }
  .exp{font-size:11px; padding:3px 9px; border-radius:6px; background:var(--surface-2);
    border:1px solid var(--border); color:var(--muted)}
  .exp.jr{color:var(--ok); border-color:transparent; background:var(--ok-soft)}
  .exp.sr{color:var(--warn); border-color:transparent; background:var(--warn-soft)}
  .fit{display:flex; gap:5px; flex-wrap:wrap; margin-bottom:12px}
  .fit b{font-size:10.5px; color:var(--faint); text-transform:uppercase; letter-spacing:.05em;
    align-self:center; margin-right:2px}
  .kw{font-size:11px; color:var(--muted); background:var(--surface-2); border:1px solid var(--border);
    padding:2px 8px; border-radius:20px}
  .kw.neg{color:#b4472a; border-color:transparent; background:#fbeae5}
  @media (prefers-color-scheme:dark){ .kw.neg{color:#e39a6f; background:#31200f} }

  .contact{display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding-top:12px;
    border-top:1px solid var(--border)}
  .mail{display:inline-flex; align-items:center; gap:7px; font-size:13px; font-family:ui-monospace,
    SFMono-Regular,Menlo,monospace}
  .badge{font-size:10px; font-weight:640; padding:2px 7px; border-radius:5px; letter-spacing:.02em}
  .badge.v{background:var(--ok-soft); color:var(--ok)} .badge.g{background:var(--warn-soft); color:var(--warn)}
  .contact .phone{color:var(--muted); font-size:12.5px; font-family:ui-monospace,monospace}
  .spacer{flex:1}
  .btn{display:inline-flex; align-items:center; gap:6px; border:1px solid var(--border);
    background:var(--surface); color:var(--ink); border-radius:8px; padding:7px 12px;
    font-size:12.5px; cursor:pointer; text-decoration:none; transition:all .12s}
  .btn:hover{border-color:var(--accent); color:var(--accent)}
  .btn.primary{background:var(--accent); border-color:var(--accent); color:#fff}
  @media (prefers-color-scheme:dark){ .btn.primary{color:#08130f} }
  .btn svg{width:14px; height:14px}
  details.desc{margin-top:11px}
  details.desc summary{cursor:pointer; color:var(--muted); font-size:12.5px; list-style:none;
    display:inline-flex; align-items:center; gap:5px}
  details.desc summary::-webkit-details-marker{display:none}
  details.desc summary .car{transition:transform .15s}
  details.desc[open] summary .car{transform:rotate(90deg)}
  details.desc .txt{margin-top:9px; padding:12px 14px; background:var(--surface-2);
    border:1px solid var(--border); border-radius:9px; color:var(--muted); font-size:13px;
    white-space:pre-wrap; max-height:340px; overflow:auto}
  .empty{text-align:center; color:var(--faint); padding:60px 0}
  footer{margin-top:26px; padding-top:18px; border-top:1px solid var(--border);
    color:var(--faint); font-size:12px; line-height:1.7}
  footer b{color:var(--muted)}
  .toast{position:fixed; bottom:22px; left:50%; transform:translateX(-50%) translateY(20px);
    background:var(--ink); color:var(--bg); padding:9px 16px; border-radius:8px; font-size:13px;
    opacity:0; transition:all .2s; pointer-events:none}
  .toast.show{opacity:1; transform:translateX(-50%) translateY(0)}

  /* manual theme override (button in header) beats the media query */
  :root[data-theme="light"]{
    --bg:#f5f6f8; --surface:#fff; --surface-2:#fbfcfd; --border:#e4e7ec;
    --ink:#141a22; --muted:#5b6673; --faint:#8a94a2; --accent:#0f6d67;
    --accent-soft:#e6f2f1; --shadow:0 1px 2px rgba(16,24,40,.04),0 1px 3px rgba(16,24,40,.06);
    --ok:#0a7d4f; --ok-soft:#e6f4ec; --warn:#9a6b00; --warn-soft:#fbf1dd;
  }
  :root[data-theme="dark"]{
    --bg:#0e1116; --surface:#161a20; --surface-2:#1b2028; --border:#262d37;
    --ink:#e7ebf1; --muted:#9aa4b2; --faint:#6b7583; --accent:#5fd3c9;
    --accent-soft:#123331; --shadow:none;
    --ok:#57c98a; --ok-soft:#12331f; --warn:#e3b45f; --warn-soft:#332912;
  }
  .top{position:relative}
  .theme-btn{position:absolute; top:26px; right:0; border:1px solid var(--border);
    background:var(--surface); color:var(--muted); border-radius:8px; width:34px; height:34px;
    cursor:pointer; display:flex; align-items:center; justify-content:center}
  .theme-btn:hover{border-color:var(--accent); color:var(--accent)}
  .theme-btn svg{width:16px; height:16px}
  .newpill{font-size:9.5px; font-weight:700; letter-spacing:.04em; padding:2px 7px; border-radius:5px;
    background:var(--accent); color:#fff; margin-left:8px; vertical-align:middle}
  @media (prefers-color-scheme:dark){ .newpill{color:#08130f} }
  :root[data-theme="light"] .newpill{color:#fff} :root[data-theme="dark"] .newpill{color:#08130f}
  .card.done{opacity:.6}
  .card.expired{opacity:.5}
  .card{border-left:3px solid transparent}
  .card[data-st="sent"]{border-left-color:#2f77c9} .card[data-st="followup"]{border-left-color:var(--warn)}
  .card[data-st="reply"]{border-left-color:var(--ok)} .card[data-st="rejected"]{border-left-color:#b4472a}
  .track{display:flex; align-items:center; gap:8px; margin-top:11px; padding-top:11px;
    border-top:1px dashed var(--border); flex-wrap:wrap}
  select.st{border:1px solid var(--border); background:var(--surface-2); color:var(--ink);
    border-radius:7px; padding:5px 8px; font-size:12px; cursor:pointer; outline:0}
  .track input.note{flex:1; min-width:160px; border:1px solid var(--border); background:var(--surface-2);
    color:var(--ink); border-radius:7px; padding:6px 9px; font-size:12.5px; outline:0}
  .track input.note:focus{border-color:var(--accent)}
  .exp-flag{font-size:10.5px; color:#b4472a; font-weight:600}

  /* --- scraper control panel (shown only when served by server.py) --- */
  .runner{background:var(--surface); border:1px solid var(--border); border-radius:12px;
    margin-bottom:14px; box-shadow:var(--shadow); overflow:hidden}
  .runner-head{display:flex; align-items:center; gap:12px; padding:13px 16px; cursor:pointer; user-select:none}
  .runner-head b{font-size:14px}
  .runner-head .rh{color:var(--faint); font-size:12px; flex:1}
  .runner-body{padding:0 16px 16px; display:none; flex-direction:column; gap:13px}
  .runner.open .runner-body{display:flex}
  .opt{display:flex; flex-direction:column; gap:5px}
  .opt>label{font-size:10.5px; text-transform:uppercase; letter-spacing:.05em; color:var(--faint)}
  .optrow{display:flex; gap:20px; flex-wrap:wrap; align-items:flex-end}
  .opt textarea,.opt input[type=number]{background:var(--surface-2); border:1px solid var(--border);
    border-radius:8px; color:var(--ink); padding:8px 10px; font-size:13px; outline:0; font-family:inherit}
  .opt textarea{min-width:min(520px,90vw)} .opt input[type=number]{width:130px}
  .opt textarea:focus,.opt input:focus{border-color:var(--accent)}
  .checks{display:flex; gap:12px; flex-wrap:wrap}
  .checks label{display:flex; align-items:center; gap:5px; font-size:12.5px; color:var(--muted); cursor:pointer}
  .checks input{accent-color:var(--accent)}
  .runner-actions{display:flex; align-items:center; gap:14px; flex-wrap:wrap}
  .runstatus{font-size:12.5px; color:var(--muted)}
  .runlog{background:#0b0e12; color:#c7d0da; border:1px solid var(--border); border-radius:8px;
    padding:11px 13px; font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace; margin:0;
    max-height:230px; overflow:auto; white-space:pre-wrap; word-break:break-word}
  .preset-row{display:flex; gap:8px; flex-wrap:wrap; align-items:center}
  .preset-row select,.preset-row input{background:var(--surface-2); border:1px solid var(--border);
    border-radius:8px; color:var(--ink); padding:7px 10px; font-size:12.5px; outline:0; font-family:inherit}
  .preset-row input:focus,.preset-row select:focus{border-color:var(--accent)}
  #stopBtn{border-color:#b4472a; color:#b4472a} #stopBtn:hover{background:#b4472a; color:#fff}
  .serve-hint{color:var(--faint); font-size:12.5px; margin:-6px 2px 14px}
  .serve-hint code{background:var(--surface-2); border:1px solid var(--border); border-radius:5px;
    padding:1px 6px; font-size:12px}
  .spin{display:inline-block; width:12px; height:12px; border:2px solid var(--border);
    border-top-color:var(--accent); border-radius:50%; animation:sp .8s linear infinite;
    vertical-align:-2px; margin-right:6px}
  @keyframes sp{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <button class="theme-btn" id="themeBtn" title="Thème clair / sombre" aria-label="Thème">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19"/></svg>
    </button>
    <h1>Recherche d'emploi — Cybersécurité / GRC</h1>
    <div class="sub" id="sub"></div>
    <div class="stats" id="stats"></div>
  </header>

  <div class="serve-hint" id="serveHint" hidden>
    Astuce : lancez <code>./jobs serve</code> pour piloter le scraper (choix des options, lancement) depuis cette page.
  </div>

  <section class="runner" id="runner" hidden>
    <div class="runner-head" id="runnerHead">
      <b>⚙ Piloter le scraper</b>
      <span class="rh">choisissez les options puis lancez une recherche</span>
      <span class="btn" id="runnerToggle">Options ▾</span>
    </div>
    <div class="runner-body">
      <div class="opt"><label>Requêtes (séparées par des virgules ou des retours à la ligne)</label>
        <textarea id="optQueries" rows="2"></textarea></div>
      <div class="optrow">
        <div class="opt"><label>Lieux</label><div id="optLocations" class="checks"></div></div>
        <div class="opt"><label>Sites</label><div id="optSites" class="checks"></div></div>
        <div class="opt"><label>Contrats</label><div id="optContracts" class="checks"></div></div>
      </div>
      <div class="optrow">
        <div class="opt"><label>Pages / recherche</label><input type="number" id="optPages" min="1" max="5"></div>
        <div class="opt"><label>Annonces lues en détail</label><input type="number" id="optDetail" min="1" max="200"></div>
        <div class="opt"><label>Score minimum</label><input type="number" id="optScore" min="0" max="20"></div>
        <label class="toggle"><input type="checkbox" id="optNoDetail"> sans détail (rapide)</label>
        <label class="toggle"><input type="checkbox" id="optDebug"> journal détaillé</label>
      </div>
      <div class="opt"><label>Préréglages</label>
        <div class="preset-row">
          <select id="presetSelect"><option value="">— charger un préréglage —</option></select>
          <input id="presetName" class="preset-name" placeholder="nom à enregistrer">
          <button class="btn" id="presetSave">💾 Enregistrer</button>
          <button class="btn" id="presetDel">Supprimer</button>
        </div>
      </div>
      <div class="runner-actions">
        <button class="btn primary" id="runBtn">▶ Lancer la recherche</button>
        <button class="btn" id="stopBtn" hidden>■ Arrêter</button>
        <span id="runStatus" class="runstatus"></span>
      </div>
      <pre class="runlog" id="runLog" hidden></pre>
    </div>
  </section>

  <div class="toolbar">
    <label class="search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
      <input id="q" type="search" placeholder="Filtrer par poste, entreprise, mot-clé…">
    </label>
    <select class="sort" id="sort">
      <option value="score">Tri : pertinence</option>
      <option value="new">Tri : nouveautés</option>
      <option value="expires">Tri : expire bientôt</option>
      <option value="posted">Tri : plus récent</option>
      <option value="junior">Tri : junior d'abord</option>
    </select>
    <button class="btn export" id="exportBtn" title="Exporter vos candidatures suivies (CSV)">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0-4-4m4 4 4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>
      Exporter le suivi
    </button>
  </div>
  <div class="filterbar">
    <div class="chips" id="contractChips"></div>
    <select class="sort" id="regionFilter"><option value="">Région : toutes</option></select>
    <select class="sort" id="statusFilter">
      <option value="active">À traiter</option>
      <option value="all">Toutes</option>
      <option value="sent">Postulées</option>
      <option value="reply">Réponses</option>
      <option value="new">Nouvelles</option>
    </select>
    <label class="toggle"><input type="checkbox" id="verifiedOnly"> email vérifié</label>
    <label class="toggle"><input type="checkbox" id="remoteOnly"> télétravail</label>
    <label class="toggle"><input type="checkbox" id="juniorOnly"> junior / alternance</label>
  </div>
  <div class="count" id="count"></div>
  <div id="list"></div>

  <footer id="foot"></footer>
</div>
<div class="toast" id="toast"></div>

<script id="data" type="application/json">/*__DATA__*/</script>
<script id="meta" type="application/json">/*__META__*/</script>
<script>
let DATA = JSON.parse(document.getElementById('data').textContent);
let META = JSON.parse(document.getElementById('meta').textContent);
const $ = s => document.querySelector(s);
const esc = s => (s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// ---- header + stats
function applyMeta(){
  $('#sub').textContent = `Généré le ${META.generated} · données locales (offres.json + emails.json)`
    + (META.live ? ' · backend actif' : '');
  $('#stats').innerHTML = [
    ['offres', META.offers], ['nouvelles', META.new || 0], ['entreprises', META.companies],
    ['emails vérifiés', META.verified]
  ].map(([l,n])=>`<div class="stat"><b>${n}</b><span>${l}</span></div>`).join('');
}
applyMeta();

// ---- theme toggle (persisted; overrides OS preference)
const THEME_KEY='jobs_theme';
function applyTheme(t){ if(t) document.documentElement.dataset.theme=t; }
applyTheme(localStorage.getItem(THEME_KEY));
$('#themeBtn').addEventListener('click', ()=>{
  const cur = document.documentElement.dataset.theme
    || (matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');
  const next = cur==='dark'?'light':'dark';
  applyTheme(next); localStorage.setItem(THEME_KEY, next);
});

// ---- application tracking store (localStorage, keyed by offer URL)
const TRACK_KEY='jobs_track';
let TRACK = {};
try { TRACK = JSON.parse(localStorage.getItem(TRACK_KEY)) || {}; } catch(e){ TRACK = {}; }
function saveTrack(){ localStorage.setItem(TRACK_KEY, JSON.stringify(TRACK)); }
function getSt(url){ return (TRACK[url]||{}).status || 'todo'; }
function setSt(url, st){ TRACK[url]={...(TRACK[url]||{}), status:st}; saveTrack(); render(); }
function setNote(url, note){ TRACK[url]={...(TRACK[url]||{}), note}; saveTrack(); }
const ST_LABELS={todo:'À postuler', sent:'Postulé', followup:'Relancé', reply:'Réponse ✅',
                 rejected:'Refusé', hidden:'Masqué'};
const TODAY = new Date().toISOString().slice(0,10);

// ---- contract filter chips
let activeContracts = new Set();
function buildChips(){
  const contracts = [...new Set(DATA.map(r=>r.contract))].sort();
  $('#contractChips').innerHTML =
    `<span class="chip ${activeContracts.size===0?'on':''}" data-c="__all">tous</span>` +
    contracts.map(c=>`<span class="chip ${activeContracts.has(c)?'on':''}" data-c="${c}">${c}</span>`).join('');
}
buildChips();
function buildRegions(){
  const regions = [...new Set(DATA.map(r=>r.region).filter(Boolean))].sort();
  const cur = $('#regionFilter').value;
  $('#regionFilter').innerHTML = '<option value="">Région : toutes</option>'
    + '<option value="__remote">🖥 Télétravail</option>'
    + regions.map(r=>`<option value="${esc(r)}">${esc(r)}</option>`).join('');
  $('#regionFilter').value = cur;
}
buildRegions();
$('#contractChips').addEventListener('click', e=>{
  const chip = e.target.closest('.chip'); if(!chip) return;
  const c = chip.dataset.c;
  if(c==='__all'){ activeContracts.clear(); }
  else { activeContracts.has(c)?activeContracts.delete(c):activeContracts.add(c); }
  [...$('#contractChips').children].forEach(ch=>{
    ch.classList.toggle('on', ch.dataset.c==='__all' ? activeContracts.size===0
                                                       : activeContracts.has(ch.dataset.c));
  });
  render();
});
['#q','#verifiedOnly','#remoteOnly','#juniorOnly','#regionFilter','#statusFilter','#sort'].forEach(s=>
  $(s).addEventListener(s==='#q'?'input':'change', render));

// ---- export tracked applications to CSV ----
function csvCell(v){ v = (v==null?'':String(v)); return /[";\n]/.test(v) ? '"'+v.replace(/"/g,'""')+'"' : v; }
function exportTracking(){
  const rows = DATA.filter(r=>['sent','followup','reply','rejected'].includes(getSt(r.url)))
    .map(r=>({statut: ST_LABELS[getSt(r.url)], note: (TRACK[r.url]||{}).note||'',
              entreprise:r.company, poste:r.title, contrat:r.contract, email:r.email,
              score:r.score, lieu:r.location, url:r.url}));
  if(!rows.length){ toast('Aucune candidature suivie à exporter'); return; }
  const cols = ['statut','entreprise','poste','contrat','email','note','score','lieu','url'];
  const csv = '﻿' + [cols.join(';')].concat(
    rows.map(o=>cols.map(c=>csvCell(o[c])).join(';'))).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'}));
  a.download = `candidatures-${new Date().toISOString().slice(0,10)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  toast(`${rows.length} candidature(s) exportée(s)`);
}
$('#exportBtn').addEventListener('click', exportTracking);

// ---- mailto candidature
function mailto(r){
  const contract = (r.contract==='alternance'||r.contract==='stage') ? r.contract : 'poste';
  const subj = `Candidature — ${r.title} — ${META.me.name}, ${META.me.school}`;
  const body =
`Madame, Monsieur,

Votre ${contract==='poste'?'poste':'offre en '+contract} « ${r.title} »${r.company?` chez ${r.company}`:''} correspond à mon profil, et je vous adresse ma candidature pour ${META.me.seeking} en cybersécurité.

Élève-ingénieur en cybersécurité à ${META.me.school}, j'ai conçu lors de mon dernier stage une plateforme de gouvernance de la sécurité (ISO 27001, analyses de risques, RGPD) et mené une démarche adversariale (tests automatisés, scénarios d'attaque) sur la pile réelle. Mon CV est joint.

Disponible pour échanger à votre convenance.
Bien cordialement,
${META.me.name}
${META.me.phone} · ${META.me.email}`;
  return `mailto:${r.email}?subject=${encodeURIComponent(subj)}&body=${encodeURIComponent(body)}`;
}

function copy(txt){
  navigator.clipboard?.writeText(txt).then(()=>toast('Copié : '+txt));
}
let toastT;
function toast(m){ const t=$('#toast'); t.textContent=m; t.classList.add('show');
  clearTimeout(toastT); toastT=setTimeout(()=>t.classList.remove('show'),1600); }

// ---- render
function daysAgo(d){
  const days = Math.round((Date.now() - new Date(d+'T00:00:00'))/86400000);
  if(isNaN(days)) return '';
  if(days<=0) return "publié aujourd'hui";
  if(days===1) return "publié hier";
  if(days<7) return `publié il y a ${days} j`;
  if(days<31) return `publié il y a ${Math.floor(days/7)} sem.`;
  return `publié il y a ${Math.floor(days/30)} mois`;
}
function expBadge(m){
  if(m==null) return `<span class="exp">exp. n.c.</span>`;
  const yrs = (m/12);
  if(m<=24) return `<span class="exp jr">junior · ${m} mois</span>`;
  if(m>=60) return `<span class="exp sr">senior · ${yrs|0} ans</span>`;
  return `<span class="exp">${yrs.toFixed(0)} an(s)</span>`;
}
function card(r){
  const fit = r.fit.map(k=>{
    const neg = k.startsWith('-');
    return `<span class="kw${neg?' neg':''}">${esc(neg?k.slice(1):k)}</span>`;
  }).join('');
  const alt = (r.alt_locations && r.alt_locations.length)
    ? ` <span class="altloc">+${r.alt_locations.length} lieu${r.alt_locations.length>1?'x':''}</span>` : '';
  const metaBits = [
    r.company?`<span class="co">${esc(r.company)}</span>`:'',
    r.location?esc(r.location)+alt:'',
    r.posted?daysAgo(r.posted):'',
    r.salary?esc(r.salary):'',
    r.expires?`expire le ${esc(r.expires)}`:'',
    r.direct_apply===true?'<span class="direct">candidature directe</span>':'',
    r.source
  ].filter(Boolean).join('<span class="dot">•</span>');
  let contact;
  if(r.email){
    const badge = r.verified ? `<span class="badge v">✓ vérifié</span>` : `<span class="badge g">~ deviné</span>`;
    contact = `
      <span class="mail">${esc(r.email)} ${badge}</span>
      ${r.phone?`<span class="phone">☎ ${esc(r.phone)}</span>`:''}
      <span class="spacer"></span>
      <button class="btn" onclick="copy('${esc(r.email)}')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>Copier</button>
      <a class="btn primary" href="${mailto(r)}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>Écrire</a>`;
  } else {
    contact = `<span class="phone">Pas d'email — postuler via l'offre</span><span class="spacer"></span>`;
  }
  const st = getSt(r.url), note = (TRACK[r.url]||{}).note || '';
  const expired = r.expires && r.expires < TODAY;
  const cls = ['card', (st==='hidden'||st==='rejected')?'done':'', expired?'expired':''].filter(Boolean).join(' ');
  const opts = Object.entries(ST_LABELS).map(([k,l])=>
    `<option value="${k}"${k===st?' selected':''}>${l}</option>`).join('');
  const newpill = r.is_new ? `<span class="newpill">NOUVEAU</span>` : '';
  const remoteBit = r.remote ? `<span class="exp">télétravail</span>` : '';
  const tier = r.score>=28 ? 'hi' : r.score>=18 ? 'mid' : 'lo';
  return `<article class="${cls}" data-st="${st}">
    <div class="scorebox ${tier}"><div class="n">${r.score}</div><div class="l">fit</div></div>
    <div class="body">
      <h2><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)||'(sans titre)'}</a>${newpill}</h2>
      <div class="meta">${metaBits}${expired?'<span class="dot">•</span><span class="exp-flag">expirée</span>':''}</div>
      <div class="tags">
        <span class="tag ${esc(r.contract)}">${esc(r.contract)}</span>
        ${expBadge(r.months)} ${remoteBit}
      </div>
      ${fit?`<div class="fit"><b>match</b>${fit}</div>`:''}
      <div class="contact">
        <a class="btn" href="${esc(r.url)}" target="_blank" rel="noopener">Voir l'offre ↗</a>
        ${contact}
      </div>
      <div class="track">
        <select class="st" onchange="setSt('${esc(r.url)}', this.value)">${opts}</select>
        <input class="note" placeholder="Note (relancé le…, contact, etc.)" value="${esc(note)}"
               oninput="setNote('${esc(r.url)}', this.value)">
      </div>
      ${r.description?`<details class="desc"><summary><span class="car">▸</span> Description complète</summary><div class="txt">${esc(r.description)}</div></details>`:''}
    </div>
  </article>`;
}

function render(){
  const q = $('#q').value.trim().toLowerCase();
  const vOnly = $('#verifiedOnly').checked;
  const rOnly = $('#remoteOnly').checked;
  const jOnly = $('#juniorOnly').checked;
  const region = $('#regionFilter').value;
  const stf = $('#statusFilter').value;
  const sort = $('#sort').value;
  const seniorTitle = /s[eé]nior|confirm[eé]|manager|responsable|lead|principal|architecte|directeur/i;
  let rows = DATA.filter(r=>{
    const st = getSt(r.url);
    if(activeContracts.size && !activeContracts.has(r.contract)) return false;
    if(vOnly && !r.verified) return false;
    if(rOnly && !r.remote) return false;
    if(region==='__remote' && !r.remote) return false;
    else if(region && region!=='__remote' && r.region!==region) return false;
    if(jOnly){
      if(r.months!=null && r.months>=48) return false;
      if(!['alternance','stage'].includes(r.contract) && seniorTitle.test(r.title)) return false;
    }
    if(stf==='active' && (st==='sent'||st==='rejected'||st==='hidden')) return false;
    if(stf==='sent'  && !(st==='sent'||st==='followup')) return false;
    if(stf==='reply' && st!=='reply') return false;
    if(stf==='new'   && !r.is_new) return false;
    if(q){
      const hay = (r.title+' '+r.company+' '+r.location+' '+r.fit.join(' ')+' '+r.description).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
  const cmp = {
    score:(a,b)=>b.score-a.score,
    new:(a,b)=>(b.is_new-a.is_new)||b.score-a.score,
    expires:(a,b)=>(a.expires||'9999').localeCompare(b.expires||'9999'),
    posted:(a,b)=>(b.posted||'').localeCompare(a.posted||''),
    junior:(a,b)=>((a.months==null?999:a.months)-(b.months==null?999:b.months))||b.score-a.score,
  }[sort];
  rows.sort(cmp);
  const applied = DATA.filter(r=>['sent','followup','reply'].includes(getSt(r.url))).length;
  $('#count').textContent = `${rows.length} offre(s) affichée(s) · ${applied} candidature(s) suivie(s)`;
  $('#list').innerHTML = rows.length ? rows.map(card).join('')
    : `<div class="empty">Aucune offre ne correspond à ces filtres.</div>`;
}

$('#foot').innerHTML =
  `<b>Légende.</b> Le score « fit » pondère vos mots-clés (ISO 27001, EBIOS, GRC, RGPD, nftables…) et la séniorité (junior = bonus). `+
  `<b>✓ vérifié</b> = email publié sur le site de l'entreprise ; <b>~ deviné</b> = motif recrutement@domaine (à confirmer, ou postuler via l'offre). `+
  `Pour les grands groupes, un email générique (contact@, info@) ou un service (DPO, commercial) peut apparaître : préférez alors le portail carrières. `+
  `Le bouton « Écrire » pré-remplit un email de candidature — pensez à joindre votre CV (les liens mailto ne peuvent pas attacher de fichier). `+
  `<b>Suivi.</b> Le statut (À postuler / Postulé / Relancé / Réponse / Refusé) et les notes de chaque offre sont enregistrés dans votre navigateur et survivent aux rafraîchissements. `+
  `<b>NOUVEAU</b> = offre vue pour la première fois au dernier scan. Le thème (☀/🌙) et vos filtres sont à vous.`;

// ================= backend control plane (only when served by server.py) =========
async function api(path, opts){
  const r = await fetch(path, opts);
  if(!r.ok){ const e = new Error(r.status); e.status = r.status; throw e; }
  return r.json();
}
async function reloadData(){
  try{
    const p = await api('api/data');
    DATA = p.records; META = {...p.meta, live:true};
    applyMeta(); buildChips(); buildRegions(); render();
  }catch(e){ /* keep embedded snapshot */ }
}
function checkboxes(el, values, checked){
  el.innerHTML = values.map(v=>
    `<label><input type="checkbox" value="${esc(v)}" ${checked.includes(v)?'checked':''}>${esc(v)}</label>`).join('');
}
function gatherOpts(){
  const picked = sel => [...document.querySelectorAll(sel+' input:checked')].map(c=>c.value);
  return {
    queries: $('#optQueries').value.split(/[\n,]/).map(s=>s.trim()).filter(Boolean),
    locations: picked('#optLocations'), sites: picked('#optSites'), contracts: picked('#optContracts'),
    max_pages: +$('#optPages').value, max_detail: +$('#optDetail').value, min_score: +$('#optScore').value,
    no_detail: $('#optNoDetail').checked, debug: $('#optDebug').checked,
  };
}
let polling=null;
function endRun(){ clearInterval(polling); polling=null;
  $('#runBtn').disabled=false; $('#stopBtn').hidden=true; }
async function pollStatus(){
  try{
    const s = await api('api/status');
    const log = $('#runLog'); log.hidden=false; log.textContent = s.log.join('\n'); log.scrollTop = log.scrollHeight;
    if(s.running){ $('#runStatus').innerHTML = `<span class="spin"></span>${esc(s.step||'…')}`; return; }
    endRun();
    $('#runStatus').textContent = s.returncode===0 ? '✓ terminé — résultats mis à jour'
      : (s.returncode===-2 ? '■ arrêté' : `⚠ arrêté (code ${s.returncode})`);
    await reloadData();
  }catch(e){ endRun(); $('#runStatus').textContent='backend indisponible'; }
}
async function launch(){
  $('#runBtn').disabled=true; $('#stopBtn').hidden=false; $('#stopBtn').disabled=false;
  $('#runStatus').innerHTML='<span class="spin"></span>démarrage…';
  try{
    await api('api/run', {method:'POST', headers:{'Content-Type':'application/json'},
                          body: JSON.stringify(gatherOpts())});
    polling = setInterval(pollStatus, 1500); pollStatus();
  }catch(e){
    endRun();
    $('#runStatus').textContent = e.status===409 ? 'un scan est déjà en cours' : 'erreur: backend indisponible';
  }
}
async function stopRun(){
  $('#stopBtn').disabled=true; $('#runStatus').innerHTML='<span class="spin"></span>arrêt en cours…';
  try{ await api('api/stop', {method:'POST'}); }catch(e){ /* poll will catch up */ }
}

// ---- saved-search presets (built-ins + your own, in localStorage) ----
const PRESET_KEY='jobs_presets';
const BUILTIN_PRESETS = {
  'Lille — alternance': {queries:['cybersécurité','GRC','SOC','administrateur systèmes réseaux'],
     locations:['Lille'], sites:['hellowork','free-work'], contracts:['alternance','stage'],
     max_pages:2, max_detail:40, min_score:2},
  'Paris — freelance': {queries:['cybersécurité','pentest','DevSecOps','sécurité applicative'],
     locations:['Paris'], sites:['free-work','hellowork'], contracts:['freelance'],
     max_pages:2, max_detail:40, min_score:2},
  'GRC / gouvernance': {queries:['GRC','gouvernance','ISO 27001','audit sécurité','conformité'],
     locations:['Lille','Paris','France'], sites:['hellowork','free-work'], contracts:['cdi','alternance'],
     max_pages:2, max_detail:40, min_score:3},
  'Rapide (test)': {locations:['Lille'], sites:['hellowork'], max_pages:1, max_detail:10, min_score:1},
};
function userPresets(){ try{return JSON.parse(localStorage.getItem(PRESET_KEY))||{};}catch(e){return {};} }
function allPresets(){ return {...BUILTIN_PRESETS, ...userPresets()}; }
function setChecks(sel, values){
  document.querySelectorAll(sel+' input').forEach(c=>{ c.checked = values.includes(c.value); });
}
function applyOptsToForm(o){
  if(o.queries) $('#optQueries').value = o.queries.join(', ');
  if(o.locations) setChecks('#optLocations', o.locations);
  if(o.sites) setChecks('#optSites', o.sites);
  if(o.contracts) setChecks('#optContracts', o.contracts);
  if(o.max_pages!=null) $('#optPages').value=o.max_pages;
  if(o.max_detail!=null) $('#optDetail').value=o.max_detail;
  if(o.min_score!=null) $('#optScore').value=o.min_score;
  if('no_detail' in o) $('#optNoDetail').checked=!!o.no_detail;
  if('debug' in o) $('#optDebug').checked=!!o.debug;
}
function refreshPresetSelect(sel){
  const all = allPresets(), u = userPresets();
  $('#presetSelect').innerHTML = '<option value="">— charger un préréglage —</option>' +
    Object.keys(all).map(n=>`<option value="${esc(n)}"${n===sel?' selected':''}>${esc(n)}${n in u?'':' •'}</option>`).join('');
}
function setupPresets(){
  refreshPresetSelect();
  $('#presetSelect').addEventListener('change', e=>{ const p = allPresets()[e.target.value]; if(p) applyOptsToForm(p); });
  $('#presetSave').addEventListener('click', ()=>{
    const name = $('#presetName').value.trim();
    if(!name){ $('#presetName').focus(); return; }
    const u = userPresets(); u[name] = gatherOpts();
    localStorage.setItem(PRESET_KEY, JSON.stringify(u));
    $('#presetName').value=''; refreshPresetSelect(name); toast('Préréglage enregistré : '+name);
  });
  $('#presetDel').addEventListener('click', ()=>{
    const name = $('#presetSelect').value, u = userPresets();
    if(name in u){ delete u[name]; localStorage.setItem(PRESET_KEY, JSON.stringify(u));
                   refreshPresetSelect(); toast('Préréglage supprimé'); }
    else if(name){ toast('Préréglage intégré — non supprimable'); }
  });
}
async function initBackend(){
  try{
    const opt = await api('api/options');
    $('#optQueries').value = opt.queries.join(', ');
    checkboxes($('#optLocations'), opt.locations, opt.locations);
    checkboxes($('#optSites'), opt.sites, opt.sites);
    checkboxes($('#optContracts'), opt.contracts, opt.contracts);
    $('#optPages').value = opt.defaults.max_pages;
    $('#optDetail').value = opt.defaults.max_detail;
    $('#optScore').value = opt.defaults.min_score;
    const runner = $('#runner'); runner.hidden=false; runner.classList.add('open');
    $('#runBtn').addEventListener('click', launch);
    $('#stopBtn').addEventListener('click', stopRun);
    setupPresets();
    const toggle = ()=>runner.classList.toggle('open');
    $('#runnerHead').addEventListener('click', e=>{
      if(!e.target.closest('button,select,input')) toggle();   // don't toggle when using a control
    });
    await reloadData();                    // switch to live data
  }catch(e){
    $('#serveHint').hidden=false;          // static file: guide the user
  }
}

render();
initBackend();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--out", default="dashboard.html")
    args = ap.parse_args()
    records = load_records()
    records.sort(key=lambda r: -r["score"])
    Path(args.out).write_text(build_html(records), encoding="utf-8")
    print(f"-> wrote {args.out}  ({len(records)} offers)")
    if args.open:
        webbrowser.open(f"file://{Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
