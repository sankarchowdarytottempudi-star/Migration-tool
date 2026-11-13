"""Migration-tool control plane: stdlib HTTP server, JSON API + static UI.

Auth: every /api/* call (except /api/health) needs the admin token, sent as the
`X-Admin-Token` header. The token comes from MT_ADMIN_TOKEN, or is generated on first
start and written to data/admin-token.txt. The UI stores it in the browser after login.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import model as M
import orchestrator
import store
import seed

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
PORT = int(os.environ.get("MT_PORT", "8080"))


def _admin_token() -> str:
    tok = os.environ.get("MT_ADMIN_TOKEN")
    if tok:
        return tok
    path = os.path.join(store.DATA_DIR, "admin-token.txt")
    if os.path.exists(path):
        return open(path).read().strip()
    tok = secrets.token_urlsafe(18)
    os.makedirs(store.DATA_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(tok)
    return tok


ADMIN_TOKEN = None  # set in main()
_CT = {".html": "text/html", ".js": "application/javascript", ".css": "text/css",
       ".svg": "image/svg+xml", ".ico": "image/x-icon", ".json": "application/json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "MigrationTool/1.0"

    def log_message(self, *a):  # quieter logs
        pass

    # ---- helpers ----
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        got = self.headers.get("X-Admin-Token", "")
        return bool(got) and hmac.compare_digest(got, ADMIN_TOKEN)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(n) if n else b""

    def _static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        if path.startswith("/static/"):
            path = path[len("/static"):]   # files live at the frontend root
        full = os.path.normpath(os.path.join(FRONTEND_DIR, path.lstrip("/")))
        if not full.startswith(os.path.normpath(FRONTEND_DIR)) or not os.path.isfile(full):
            self.send_error(404)
            return
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", _CT.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- GET ----
    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/health":
            return self._json(200, {"status": "UP", "runner": os.environ.get("MT_RUNNER", "local")})
        if not p.startswith("/api/"):
            return self._static(p)
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        if p == "/api/phases":
            return self._json(200, {"phases": M.PHASE_DEFS})
        if p == "/api/migrations":
            migs = [M.summarize(m) for m in store.list_all()]
            for m in migs:
                m["busy"] = orchestrator.is_busy(m["id"])
            return self._json(200, {"migrations": migs})
        if p.startswith("/api/migrations/"):
            mid = p.split("/")[3]
            mig = store.get(mid)
            if not mig:
                return self._json(404, {"error": "not found"})
            out = M.summarize(mig)
            out["busy"] = orchestrator.is_busy(mid)
            return self._json(200, out)
        return self._json(404, {"error": "not found"})

    # ---- POST ----
    def do_POST(self):
        p = urlparse(self.path).path
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        if p == "/api/migrations":
            return self._create()
        parts = p.split("/")
        if len(parts) >= 5 and parts[1] == "api" and parts[2] == "migrations":
            mid = parts[3]
            if not store.get(mid):
                return self._json(404, {"error": "not found"})
            if parts[4] == "run" and len(parts) >= 6:
                ok, msg = orchestrator.trigger(mid, parts[5])
                return self._json(202 if ok else 409, {"ok": ok, "message": msg})
            if parts[4] == "advance":
                return self._advance(mid)
        return self._json(404, {"error": "not found"})

    def do_DELETE(self):
        p = urlparse(self.path).path
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        parts = p.split("/")
        if len(parts) >= 4 and parts[2] == "migrations":
            mid = parts[3]
            path = os.path.join(store._MIG_DIR, f"{mid}.json")
            if os.path.exists(path):
                os.remove(path)
                return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    def _create(self):
        name = self.headers.get("X-Name") or "unnamed-api"
        fname = os.path.basename(self.headers.get("X-Filename") or "source.zip")
        data = self._body()
        if not data:
            return self._json(400, {"error": "empty upload"})
        dest = os.path.join(store.uploads_dir(), f"{secrets.token_hex(4)}-{fname}")
        with open(dest, "wb") as f:
            f.write(data)
        mig = M.new_migration(name, dest)
        store.save(mig)
        return self._json(201, M.summarize(mig))

    def _advance(self, mid):
        try:
            payload = json.loads(self._body() or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        pk, sk = payload.get("phase"), payload.get("step")
        status = payload.get("status", M.DONE)
        with store.lock():
            mig = store.get(mid)
            try:
                if sk:
                    M.set_step(mig, pk, sk, status, payload.get("detail", ""))
                else:
                    for s in M.phase(mig, pk)["steps"]:
                        M.set_step(mig, pk, s["key"], status)
                M.log(mig, f"manual: {pk}/{sk or '*'} -> {status}")
            except KeyError as e:
                return self._json(400, {"error": f"unknown phase/step {e}"})
            store.save(mig)
        return self._json(200, M.summarize(store.get(mid)))


def main():
    global ADMIN_TOKEN
    store.init()
    ADMIN_TOKEN = _admin_token()
    seed.ensure_seed()
    print(f"Migration-tool on :{PORT}  runner={os.environ.get('MT_RUNNER','local')}")
    print(f"admin token: {ADMIN_TOKEN}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
