#!/usr/bin/env python3
"""Local app for wlan-snapshot.

Opens a page where you enter a Meraki API key, pick a wireless network and click
Generate report. Snapshots and reports are saved in ./snapshots.

Only GET requests are sent to the Meraki Dashboard API; nothing is changed.
The API key is kept in your browser and sent only to this local server, which
passes it to api.meraki.com.

Usage:  python3 server.py [--port 8770] [--open]
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import uuid
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse, unquote

import wlan_snapshot as ws

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, "snapshots")
KEY_HEADER = "X-Meraki-API-Key"
JOBS = {}
RUN_LOCK = threading.Lock()   # one snapshot at a time keeps us well under Meraki's rate limit


class Fail(Exception):
    pass


def snapshot_files():
    out = []
    if not os.path.isdir(SNAP_DIR):
        return out
    for f in sorted(os.listdir(SNAP_DIR), reverse=True):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(SNAP_DIR, f)) as fh:
                s = json.load(fh)
            if s.get("tool") != "wlan-snapshot":
                continue
            out.append({"file": f, "report": f[:-5] + ".html" if os.path.exists(os.path.join(SNAP_DIR, f[:-5] + ".html")) else None,
                        "network": s["network"]["name"], "networkId": s["network"]["id"], "org": s["org"]["name"],
                        "label": s.get("label"), "t0": s["window"]["t0"], "t1": s["window"]["t1"], "capturedAt": s.get("capturedAt")})
        except Exception:
            continue
    return out


def run_job(job_id, key, org_id, net_id, label, days):
    job = JOBS[job_id]

    def hook(msg):
        job["lines"].append(msg.strip())

    with RUN_LOCK:
        ws.LOG_HOOK = hook
        try:
            m = ws.Meraki(key, verbose=False)
            args = SimpleNamespace(network=net_id, org=org_id, label=label, days=days, end=None,
                                   out=SNAP_DIR, no_report=False)
            ws.snapshot(m, args)
            files = sorted((f for f in os.listdir(SNAP_DIR) if f.endswith(".html")),
                           key=lambda f: os.path.getmtime(os.path.join(SNAP_DIR, f)))
            job["report"] = files[-1] if files else None
            job["status"] = "done"
        except SystemExit as e:
            job["status"], job["error"] = "error", str(e)
        except Exception as e:  # keep the app alive and tell the user
            job["status"], job["error"] = "error", "Unexpected error: %s" % e
        finally:
            ws.LOG_HOOK = None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        # Reports are rebuilt in place, so make browsers check for a newer copy on every load.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def local_only(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("localhost", "127.0.0.1", "[::1]"):
            self.send_error(403, "Forbidden host")
            return False
        origin = self.headers.get("Origin")
        if origin and not re.match(r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$", origin):
            self.send_error(403, "Forbidden origin")
            return False
        return True

    def key(self):
        k = (self.headers.get(KEY_HEADER) or "").strip()
        if not k:
            raise Fail("Enter your Meraki API key first.")
        return k

    def do_GET(self):
        if not self.local_only():
            return
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                self.path = "/app.html"
                return super().do_GET()
            if u.path == "/local/networks":
                m = ws.Meraki(self.key(), verbose=False)
                orgs = m.get("/organizations", allow_fail=True)
                if orgs is None:
                    err = (m.calls[-1].get("error") if m.calls else "") or ""
                    raise Fail("Meraki rejected that API key." if "401" in str(m.calls[-1].get("status")) else "Could not list organizations: " + err)
                out = []
                for o in orgs:
                    nets = m.get("/organizations/%s/networks" % o["id"], {"perPage": 1000}, allow_fail=True)
                    if nets is None:
                        out.append({"id": o["id"], "name": o["name"], "error": "API access disabled or not allowed", "networks": []})
                        continue
                    out.append({"id": o["id"], "name": o["name"],
                                "networks": [{"id": n["id"], "name": n["name"]} for n in nets if "wireless" in n.get("productTypes", [])]})
                return self.send_json(200, {"orgs": out})
            if u.path == "/local/endpoints":
                return self.send_json(200, {"endpoints": ws.ENDPOINTS, "eventTypes": ws.EVENT_TYPES})
            if u.path == "/local/snapshots":
                return self.send_json(200, {"snapshots": snapshot_files()})
            if u.path.startswith("/local/job/"):
                j = JOBS.get(u.path.rsplit("/", 1)[-1])
                return self.send_json(200 if j else 404, j or {"error": "No such job"})
            if u.path.startswith("/snapshots/"):
                name = os.path.basename(unquote(u.path))
                if not re.match(r"^[A-Za-z0-9_.-]+\.(json|html)$", name):
                    return self.send_error(404)
                self.path = "/snapshots/" + name
                return super().do_GET()
            if u.path in ("/app.html",):
                return super().do_GET()
            return self.send_error(404)
        except Fail as e:
            return self.send_json(400, {"error": str(e)})

    def do_POST(self):
        # Local endpoint only: starts a snapshot. Meraki itself only ever receives GET requests.
        if not self.local_only():
            return
        if urlparse(self.path).path != "/local/generate":
            return self.send_error(405)
        try:
            key = self.key()
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            org_id, net_id = str(body.get("orgId") or ""), str(body.get("networkId") or "")
            if not org_id or not net_id:
                raise Fail("Pick a network first.")
            label = re.sub(r"[^A-Za-z0-9_-]+", "-", (body.get("label") or "snapshot").strip())[:40] or "snapshot"
            days = max(1, min(14, int(body.get("days") or 14)))
        except Fail as e:
            return self.send_json(400, {"error": str(e)})
        except (ValueError, json.JSONDecodeError):
            return self.send_json(400, {"error": "Bad request."})
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"id": job_id, "status": "running", "lines": [], "report": None, "error": None, "started": time.time()}
        threading.Thread(target=run_job, args=(job_id, key, org_id, net_id, label, days), daemon=True).start()
        self.send_json(202, {"job": job_id})

    def do_PUT(self):
        self.send_error(405, "Read-only")
    do_DELETE = do_PATCH = do_PUT


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8770)))
    p.add_argument("--open", action="store_true", help="open the app in your browser")
    args = p.parse_args()
    os.makedirs(SNAP_DIR, exist_ok=True)
    srv = None
    for port in range(args.port, args.port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if not srv:
        sys.exit("No free port between %d and %d." % (args.port, args.port + 19))
    url = "http://localhost:%d" % srv.server_port
    print("Cisco Meraki WLAN Snapshot running at %s  (Ctrl+C to stop)" % url, flush=True)
    print("Read-only: only GET requests are sent to the Meraki Dashboard API. No changes are made to your", flush=True)
    print("Meraki environment, so a read-only admin API key is all it needs.", flush=True)
    if args.open:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
