"""Thread-safe JSON store for migrations, one file per migration under DATA_DIR."""
from __future__ import annotations

import json
import os
import threading

DATA_DIR = os.environ.get("MT_DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
_MIG_DIR = os.path.join(DATA_DIR, "migrations")
_LOCK = threading.RLock()


def init():
    os.makedirs(_MIG_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "uploads"), exist_ok=True)


def uploads_dir() -> str:
    d = os.path.join(DATA_DIR, "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def _path(mig_id: str) -> str:
    return os.path.join(_MIG_DIR, f"{mig_id}.json")


def save(mig: dict):
    with _LOCK:
        tmp = _path(mig["id"]) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(mig, f, indent=2)
        os.replace(tmp, _path(mig["id"]))


def get(mig_id: str):
    with _LOCK:
        p = _path(mig_id)
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return json.load(f)


def list_all() -> list:
    with _LOCK:
        out = []
        if not os.path.isdir(_MIG_DIR):
            return out
        for name in os.listdir(_MIG_DIR):
            if name.endswith(".json"):
                with open(os.path.join(_MIG_DIR, name)) as f:
                    out.append(json.load(f))
        out.sort(key=lambda m: m.get("createdAt", ""), reverse=True)
        return out


def lock():
    return _LOCK
