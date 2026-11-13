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
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# Deployed APIs the playground may call, by in-cluster DNS (allowlist prevents SSRF).
PROXY_TARGETS = {
    "exp": "http://reservation-mgr-exp-api.wso2.svc",
    "sys": "http://seaware-sys-api.wso2.svc",
    "proc": "http://seaware-cache-proc-api.wso2.svc",
    "zoho": "http://zoho-sys-api.wso2.svc",
}

import env_store
import kb
import model as M
import orchestrator
import store
import seed
from runner import get_runner, PLATFORM_DIR


def _http_call(base, path, method, headers, body, timeout=20):
    """Returns (status, text, ms). ms is the measured round-trip latency."""
    import time as _t
    if not path.startswith("/"):
        path = "/" + path
    data = body.encode() if isinstance(body, str) and body else None
    req = urllib.request.Request(base + path, data=data,
                                 method=(method or "POST").upper(), headers=headers or {})
    t0 = _t.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = (r.status, r.read().decode(errors="replace"))
    except urllib.error.HTTPError as e:
        out = (e.code, e.read().decode(errors="replace"))
    except Exception as e:
        out = (0, f"connection error: {e}")
    return out[0], out[1], round((_t.time() - t0) * 1000)


# Response-time targets (ms): at or under FAST is ideal; over MAX is too slow.
SLO_FAST_MS = 250
SLO_MAX_MS = 1000


def _slo(ms):
    if ms <= SLO_FAST_MS:
        return "fast"
    if ms <= SLO_MAX_MS:
        return "ok"
    return "slow"

_DOC_NAMES = {"TECHNICAL.md", "MAPPING.md", "FUNCTIONAL.md", "ESTIMATE.md", "inventory.json"}

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
        if p == "/api/workspaces":
            return self._json(200, {"workspaces": self._workspaces()})
        if p == "/api/knowledge":
            return self._json(200, {"feedback": kb.all_feedback()})
        if p == "/api/environments":
            return self._json(200, {"environments": env_store.list_all()})
        if p.startswith("/api/migrations/"):
            parts = p.split("/")          # '', api, migrations, id, [docs|feedback], [name]
            mid = parts[3]
            mig = store.get(mid)
            if not mig:
                return self._json(404, {"error": "not found"})
            if len(parts) == 4:
                out = M.summarize(mig)
                out["busy"] = orchestrator.is_busy(mid)
                return self._json(200, out)
            if parts[4] == "docs" and len(parts) == 5:
                return self._json(200, {"docs": self._list_docs(mig)})
            if parts[4] == "docs" and len(parts) == 6:
                return self._download_doc(mig, parts[5])
            if parts[4] == "feedback":
                return self._json(200, {"feedback": kb.list_feedback(migration_id=mid)})
        return self._json(404, {"error": "not found"})

    # ---- workspace + docs + feedback helpers ----
    def _workspaces(self):
        groups = {}
        for m in store.list_all():
            s = M.summarize(m)
            ws = m.get("workspace") or "Unassigned"
            g = groups.setdefault(ws, {"name": ws, "total": 0, "deployed": 0, "failed": 0})
            g["total"] += 1
            if s["progress"]["outcome"] == "Deployed":
                g["deployed"] += 1
            if s["progress"]["outcome"] == "Failed":
                g["failed"] += 1
        return list(groups.values())

    def _gen_dir(self, mig):
        api = mig.get("apiName")
        return f"{PLATFORM_DIR}/docs/generated/{api}" if api else None

    def _list_docs(self, mig):
        gd = self._gen_dir(mig)
        if not gd:
            return []
        r = get_runner()
        names = [n for n in r.listdir(gd) if n in _DOC_NAMES]
        return [{"name": n} for n in names]

    def _download_doc(self, mig, name):
        if name not in _DOC_NAMES:
            return self._json(400, {"error": "unknown document"})
        gd = self._gen_dir(mig)
        if not gd:
            return self._json(404, {"error": "no documents yet"})
        content = get_runner().read(f"{gd}/{name}", limit=4_000_000)
        if not content:
            return self._json(404, {"error": "document not found"})
        body = content.encode()
        ctype = "application/json" if name.endswith(".json") else "text/markdown"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         f'attachment; filename="{mig.get("apiName","api")}-{name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- POST ----
    def do_POST(self):
        p = urlparse(self.path).path
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        if p == "/api/migrations":
            return self._create()
        if p == "/api/proxy":
            return self._proxy()
        if p == "/api/environments":
            return self._create_env()
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
            if parts[4] == "feedback":
                return self._feedback(mid)
            if parts[4] == "verify":
                return self._verify(mid)
        return self._json(404, {"error": "not found"})

    def _create_env(self):
        try:
            payload = json.loads(self._body() or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        if not payload.get("name") or not payload.get("baseUrl"):
            return self._json(400, {"error": "name and baseUrl required"})
        return self._json(201, env_store.add(payload))

    def _resolve_target(self, payload):
        """Return (base_url, headers) for a request: a registered environment (envId) or the
        in-cluster test double (service). Adds the env's token unless the caller set one."""
        headers = dict(payload.get("headers") or {})
        env_id = payload.get("envId")
        if env_id:
            env = env_store.get(env_id)
            if not env:
                return None, None
            if env.get("token") and not any(k.lower() == "authorization" for k in headers):
                headers["Authorization"] = "Bearer " + env["token"]
            return env["baseUrl"], headers
        base = PROXY_TARGETS.get(payload.get("service"))
        return base, headers

    def _proxy(self):
        """Server-side call so the browser never faces CORS/CSP. Targets a registered
        environment (envId) or the in-cluster test double (service)."""
        try:
            payload = json.loads(self._body() or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        base, headers = self._resolve_target(payload)
        if not base:
            return self._json(400, {"error": "unknown service or environment"})
        status, text, ms = _http_call(base, payload.get("path", "/"), payload.get("method"),
                                       headers, payload.get("body"))
        return self._json(200, {"status": status, "body": text, "ms": ms, "slo": _slo(ms)})

    def _verify(self, mid):
        """Fetch data from the chosen environment; mark the migration verified only on success."""
        try:
            payload = json.loads(self._body() or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        env = env_store.get(payload.get("envId"))
        if not env:
            return self._json(400, {"error": "unknown environment"})
        base, headers = self._resolve_target(payload)
        status, text, ms = _http_call(base, payload.get("path", "/"), payload.get("method"),
                                       headers, payload.get("body"))
        ok = status == 200 and len(text.strip()) > 0
        slo = _slo(ms)
        with store.lock():
            m = store.get(mid)
            if ok:
                m["verifiedEnv"] = env["name"]
                m["verifiedAt"] = M._now()
                m["verifySample"] = text[:2000]
                m["verifyMs"] = ms
                M.log(m, f"verified against '{env['name']}' (HTTP {status}, {ms} ms, {slo}); "
                         f"migration complete")
                if slo == "slow":
                    M.log(m, f"response {ms} ms exceeds the 1000 ms target — optimization needed", "warn")
            else:
                M.log(m, f"verification against '{env['name']}' failed (HTTP {status})", "error")
            store.save(m)
        return self._json(200, {"ok": ok, "status": status, "body": text, "ms": ms, "slo": slo})

    def _feedback(self, mid):
        try:
            payload = json.loads(self._body() or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        mig = store.get(mid)
        tech = mig.get("technology")
        if tech in (None, "", "unknown"):
            tech = None   # tech-agnostic functional lesson: applies to any migration
        entry = kb.add_feedback({
            "migrationId": mid,
            "apiName": mig.get("apiName"),
            "workspace": mig.get("workspace"),
            # functional lessons are keyed by the migration kind so they help the same
            # technology migration in any workspace
            "technology": tech,
            "scope": payload.get("scope", "api"),
            "phase": payload.get("phase", "document"),
            "docType": payload.get("docType", ""),
            "summary": payload.get("summary", ""),
            "mistakes": payload.get("mistakes", []),
            "rating": payload.get("rating"),
        })
        with store.lock():
            m = store.get(mid)
            scope_txt = {"functional": "functional (shared to all workspaces)",
                         "workspace": "workspace-private", "api": "API-private"}.get(entry["scope"], entry["scope"])
            M.log(m, f"feedback captured [{scope_txt}]: {entry['summary'][:80]}")
            store.save(m)
        return self._json(201, entry)

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
        if len(parts) >= 4 and parts[2] == "environments":
            return self._json(200, {"ok": env_store.delete(parts[3])})
        return self._json(404, {"error": "not found"})

    def _create(self):
        name = self.headers.get("X-Name") or "unnamed-api"
        workspace = self.headers.get("X-Workspace") or M.DEFAULT_WORKSPACE
        fname = os.path.basename(self.headers.get("X-Filename") or "source.zip")
        data = self._body()
        if not data:
            return self._json(400, {"error": "empty upload"})
        dest = os.path.join(store.uploads_dir(), f"{secrets.token_hex(4)}-{fname}")
        with open(dest, "wb") as f:
            f.write(data)
        mig = M.new_migration(name, dest, workspace=workspace)
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


def _backfill():
    """Keep older records valid as the model grows (workspace tag, lesson counter)."""
    for m in store.list_all():
        changed = False
        if not m.get("workspace"):
            m["workspace"] = M.DEFAULT_WORKSPACE
            changed = True
        if "lessonsApplied" not in m:
            m["lessonsApplied"] = 0
            changed = True
        if changed:
            store.save(m)


def main():
    global ADMIN_TOKEN
    store.init()
    ADMIN_TOKEN = _admin_token()
    seed.ensure_seed()
    _backfill()
    print(f"Migration-tool on :{PORT}  runner={os.environ.get('MT_RUNNER','local')}")
    print(f"admin token: {ADMIN_TOKEN}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
