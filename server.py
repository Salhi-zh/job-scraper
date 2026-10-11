#!/usr/bin/env python3
"""
server — a tiny LOCAL control plane for the dashboard. Lets the web page drive
the Python pipeline: pick options in the browser, hit "Lancer", watch the log,
see fresh results — no terminal.

Security: binds to 127.0.0.1 ONLY (never exposed to the network). It runs the
pipeline via subprocess with LIST arguments (no shell), and every option from
the browser is validated/whitelisted/clamped before it becomes a CLI flag, so
the page cannot inject arbitrary commands.

    python3 server.py            # -> http://127.0.0.1:8777
    ./jobs serve                 # same, and opens the browser
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
HOST, PORT = "127.0.0.1", 8777

ALLOWED_SITES = {"hellowork", "free-work"}
ALLOWED_CONTRACTS = {"stage", "alternance", "cdi", "cdd", "interim", "freelance"}
DEFAULT_QUERIES = [
    "cybersécurité", "sécurité informatique", "SOC", "pentest",
    "DevOps", "DevSecOps", "administrateur systèmes réseaux", "GRC", "audit sécurité",
]
DEFAULT_LOCATIONS = ["Lille", "Paris", "France"]

STATE = {"running": False, "started": "", "finished": "", "returncode": None,
         "step": "", "log": deque(maxlen=500)}
_LOCK = threading.Lock()
_STOP = threading.Event()     # set by /api/stop to abort the current run
_PROC = None                  # the subprocess currently executing a step


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _clamp(v, lo, hi, default) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def build_scraper_cmd(opts: dict) -> list[str]:
    """Turn validated browser options into a safe argv for scraper.py."""
    cmd = [PY, "scraper.py"]
    sites = [s for s in opts.get("sites") or [] if s in ALLOWED_SITES]
    if sites:
        cmd += ["--sites", *sites]
    queries = [q.strip() for q in (opts.get("queries") or []) if q.strip()]
    if queries:
        cmd += ["--queries", ",".join(queries)]
    locations = [x.strip() for x in (opts.get("locations") or []) if x.strip()]
    if locations:
        cmd += ["--locations", ",".join(locations)]
    contracts = [c for c in (opts.get("contracts") or []) if c in ALLOWED_CONTRACTS]
    if contracts:
        cmd += ["--contracts", ",".join(contracts)]
    cmd += ["--max-pages", str(_clamp(opts.get("max_pages"), 1, 5, 2))]
    cmd += ["--max-detail", str(_clamp(opts.get("max_detail"), 1, 200, 40))]
    cmd += ["--min-score", str(_clamp(opts.get("min_score"), 0, 20, 2))]
    if opts.get("no_detail"):
        cmd += ["--no-detail"]
    if opts.get("debug"):
        cmd += ["--debug"]
    return cmd


def run_pipeline(opts: dict) -> None:
    global _PROC
    steps = [
        ("scrape", build_scraper_cmd(opts)),
        ("emails", [PY, "enrich_emails.py"]),
        ("shortlist", [PY, "merge_shortlist.py"]),
        ("dashboard", [PY, "build_dashboard.py"]),
    ]
    _STOP.clear()
    with _LOCK:
        STATE.update(running=True, started=_now(), finished="", returncode=None, step="")
        STATE["log"].clear()
    rc = 0
    for name, cmd in steps:
        if _STOP.is_set():
            STATE["log"].append("[arrêté par l'utilisateur]")
            rc = -2
            break
        STATE["step"] = name
        STATE["log"].append(f"$ {' '.join(cmd)}")
        try:
            p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
        except Exception as exc:  # noqa: BLE001
            STATE["log"].append(f"[{name} failed to start: {exc}]")
            rc = 1
            break
        _PROC = p
        for line in p.stdout:                       # ends when the step exits OR is terminated
            line = line.rstrip()
            if line and "INFO:" not in line and "WARNING:" not in line:
                STATE["log"].append(line)
        p.wait()
        _PROC = None
        if _STOP.is_set():
            STATE["log"].append("[arrêté par l'utilisateur]")
            rc = -2
            break
        if p.returncode != 0:
            rc = p.returncode
            STATE["log"].append(f"[{name} exited with {rc}]")
            break
        STATE["log"].append(f"[{name} ✓]")
    STATE.update(running=False, finished=_now(), returncode=rc, step="")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):        # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/dashboard.html"):
            f = HERE / "dashboard.html"
            if not f.exists():
                subprocess.run([PY, "build_dashboard.py"], cwd=HERE, check=False)
            self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/options":
            self._send(200, {
                "queries": DEFAULT_QUERIES, "locations": DEFAULT_LOCATIONS,
                "sites": sorted(ALLOWED_SITES), "contracts": sorted(ALLOWED_CONTRACTS),
                "defaults": {"max_pages": 2, "max_detail": 40, "min_score": 2},
            })
        elif path == "/api/data":
            try:
                import importlib, build_dashboard
                importlib.reload(build_dashboard)
                self._send(200, build_dashboard.dashboard_payload())
            except Exception as exc:  # noqa: BLE001
                self._send(500, {"error": str(exc)})
        elif path == "/api/status":
            self._send(200, {
                "running": STATE["running"], "step": STATE["step"],
                "started": STATE["started"], "finished": STATE["finished"],
                "returncode": STATE["returncode"], "log": list(STATE["log"]),
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/stop":
            if not STATE["running"]:
                return self._send(200, {"stopped": False, "note": "aucun scan en cours"})
            _STOP.set()
            p = _PROC
            if p is not None and p.poll() is None:
                try:
                    p.terminate()
                except Exception:  # noqa: BLE001
                    pass
            return self._send(200, {"stopped": True})
        if path != "/api/run":
            return self._send(404, {"error": "not found"})
        if STATE["running"]:
            return self._send(409, {"error": "un scan est déjà en cours"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            opts = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "corps JSON invalide"})
        threading.Thread(target=run_pipeline, args=(opts,), daemon=True).start()
        self._send(200, {"started": True, "cmd": " ".join(build_scraper_cmd(opts))})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"job-scraper control panel → {url}   (Ctrl-C to stop)")
    if "--open" in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        srv.shutdown()


if __name__ == "__main__":
    main()
