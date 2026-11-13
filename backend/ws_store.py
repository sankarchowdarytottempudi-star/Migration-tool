"""Workspace configuration: the target servers WSO2 + Kubernetes get installed on.

A workspace names up to four servers — Dev, QA, UAT, Prod — each with host/port/username/
password. The current POC box (13.140.56.229) is NOT a target; real deployments go to these.
Rule: at least one of Dev/QA/UAT must be provided; Prod may be added later.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

from store import DATA_DIR

_PATH = os.path.join(DATA_DIR, "workspaces.json")
_LOCK = threading.RLock()
TIERS = ["dev", "qa", "uat", "prod"]
NONPROD = ["dev", "qa", "uat"]


class ValidationError(Exception):
    pass


def _load() -> dict:
    with _LOCK:
        if not os.path.exists(_PATH):
            return {"workspaces": []}
        with open(_PATH) as f:
            return json.load(f)


def _save(d: dict):
    with _LOCK:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, _PATH)


def _clean_server(raw: dict):
    if not raw:
        return None
    host = (raw.get("host") or "").strip()
    user = (raw.get("username") or "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(raw.get("port") or 22),
        "username": user or "root",
        "password": raw.get("password") or "",
        "provisioned": False,
    }


def add(entry: dict) -> dict:
    name = (entry.get("name") or "").strip()
    if not name:
        raise ValidationError("workspace name is required")
    servers_in = entry.get("servers") or {}
    servers = {}
    for tier in TIERS:
        s = _clean_server(servers_in.get(tier))
        if s:
            servers[tier] = s
    if not any(t in servers for t in NONPROD):
        raise ValidationError("at least one of Dev, QA or UAT must have a host")
    with _LOCK:
        d = _load()
        if any(w["name"].lower() == name.lower() for w in d["workspaces"]):
            raise ValidationError(f"workspace '{name}' already exists")
        ws = {"id": uuid.uuid4().hex[:8], "name": name, "servers": servers,
              "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        d["workspaces"].append(ws)
        _save(d)
        return _public(ws)


def list_all() -> list:
    return [_public(w) for w in _load()["workspaces"]]


def get(ws_id: str):
    for w in _load()["workspaces"]:
        if w["id"] == ws_id:
            return w
    return None


def set_provisioned(ws_id: str, tier: str, value: bool, note: str = ""):
    with _LOCK:
        d = _load()
        for w in d["workspaces"]:
            if w["id"] == ws_id and tier in (w.get("servers") or {}):
                w["servers"][tier]["provisioned"] = value
                if note:
                    w["servers"][tier]["provisionNote"] = note
                _save(d)
                return True
    return False


def delete(ws_id: str) -> bool:
    with _LOCK:
        d = _load()
        n = len(d["workspaces"])
        d["workspaces"] = [w for w in d["workspaces"] if w["id"] != ws_id]
        _save(d)
        return len(d["workspaces"]) < n


def _public(w: dict) -> dict:
    servers = {}
    for tier, s in (w.get("servers") or {}).items():
        servers[tier] = {"host": s["host"], "port": s["port"], "username": s["username"],
                         "hasPassword": bool(s.get("password")), "provisioned": s.get("provisioned", False)}
    return {"id": w["id"], "name": w["name"], "createdAt": w.get("createdAt"), "servers": servers}
