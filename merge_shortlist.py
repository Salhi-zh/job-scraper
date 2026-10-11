#!/usr/bin/env python3
"""Join offres.json + emails.json into a ranked shortlist (CSV + console)."""
import csv
import json

offers = json.load(open("offres.json", encoding="utf-8"))
emails = {r["company"].lower(): r for r in json.load(open("emails.json", encoding="utf-8"))}
pub = sum(1 for r in emails.values() if r["best_email"] and not r["guessed"])
print(f"{len(offers)} offers | {len(emails)} companies | "
      f"{pub} verified emails, {len(emails) - pub} guessed/none\n")

rows = []
for o in offers:
    c = (o.get("company") or "").strip()
    e = emails.get(c.lower(), {})
    rows.append({
        **o,
        "email": e.get("best_email", ""),
        "verified": bool(e.get("best_email") and not e.get("guessed")),
        "phone": (e.get("phones") or [""])[0],
    })

with open("shortlist.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["score", "contract", "months_exp", "title", "company", "email",
                "verified", "phone", "location", "salary", "valid_through", "url", "fit"])
    for o in rows:
        w.writerow([o["score"], o["contract"], o.get("months_experience", ""),
                    o["title"], o["company"], o["email"], o["verified"],
                    o["phone"], o["location"], o["salary"], o["valid_through"],
                    o["url"], o["fit"]])

print("=== TOP 20 — offer + contact ===")
for o in rows[:20]:
    exp = f"{o['months_experience']}mo" if o.get("months_experience") is not None else "?"
    mark = "✓" if o["verified"] else ("~" if o["email"] else "·")
    print(f"[{o['score']:>2}] {o['contract']:<10} exp={exp:<5} "
          f"{o['title'][:40]:<40} | {o['company'][:18]:<18} {mark} {o['email']}")
print("\n-> wrote shortlist.csv (offer joined to contact email)")
