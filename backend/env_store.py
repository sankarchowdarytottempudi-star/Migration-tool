"""Registered target environments a migration is connected to and verified against.

An environment is a named upstream the migrated API talks to (e.g. dev / test / uat), with
its base URL and an auth token. A migration is only "Verified" once a real request to the
selected environment returns data in the Playground.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

from store import DATA_DIR

_PATH = os.path.join(DATA_DIR, "environments.json")
_LOCK = threading.RLock()


def _load() -> dict:
    with _LOCK:
        if not os.path.exists(_PATH):
            return {"envs": []}
        with open(_PATH) as f:
            return json.load(f)


def _save(d: dict):
    with _LOCK:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, _PATH)


def add(entry: dict) -> dict:
    with _LOCK:
        d = _load()
        e = {
            "id": uuid.uuid4().hex[:8],
            "name": (entry.get("name") or "env").strip(),
            "workspace": entry.get("workspace"),
            "baseUrl": (entry.get("baseUrl") or "").rstrip("/"),
            "token": entry.get("token") or "",
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        d["envs"].append(e)
        _save(d)
        return _public(e)


def list_all() -> list:
    return [_public(e) for e in _load()["envs"]]


def get(env_id: str):
    for e in _load()["envs"]:
        if e["id"] == env_id:
            return e
    return None


def delete(env_id: str) -> bool:
    with _LOCK:
        d = _load()
        n = len(d["envs"])
        d["envs"] = [e for e in d["envs"] if e["id"] != env_id]
        _save(d)
        return len(d["envs"]) < n


def _public(e: dict) -> dict:
    """Never expose the stored token to the browser."""
    return {k: v for k, v in e.items() if k != "token"} | {"hasToken": bool(e.get("token"))}
