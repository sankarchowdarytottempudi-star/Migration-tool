"""Knowledge base: user feedback on what went wrong becomes lessons the agent applies.

Flow: a user reviews the generated documents / scaffold, reports mistakes -> stored here as
feedback. On the next run for that API (or any API in the same workspace, for global lessons)
the orchestrator collects the open lessons, hands them to the migration factory's AI build
agent, and marks them applied with the run reference. The UI shows the whole loop.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

from store import DATA_DIR

_PATH = os.path.join(DATA_DIR, "knowledge.json")
_LOCK = threading.RLock()


def _load() -> dict:
    with _LOCK:
        if not os.path.exists(_PATH):
            return {"feedback": []}
        with open(_PATH) as f:
            return json.load(f)


def _save(d: dict):
    with _LOCK:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, _PATH)


def add_feedback(entry: dict) -> dict:
    with _LOCK:
        d = _load()
        scope = entry.get("scope") or "api"
        if scope not in ("functional", "workspace", "api"):
            scope = "api"
        entry = {
            "id": uuid.uuid4().hex[:10],
            "migrationId": entry.get("migrationId"),
            "apiName": entry.get("apiName"),
            "workspace": entry.get("workspace"),
            "technology": entry.get("technology"),     # for functional lessons: the migration kind
            "phase": entry.get("phase") or "document",
            "docType": entry.get("docType") or "",
            # functional = cross-workspace approach/architecture gap (shared to everyone);
            # workspace = private to this workspace; api = private to this API's specifics.
            "scope": scope,
            "summary": (entry.get("summary") or "").strip(),
            "mistakes": entry.get("mistakes") or [],
            "rating": entry.get("rating"),
            "status": "open",                           # open -> applied
            "appliedIn": [],
            "createdAt": _now(),
        }
        d["feedback"].append(entry)
        _save(d)
        return entry


def list_feedback(migration_id=None, api_name=None) -> list:
    d = _load()
    out = d["feedback"]
    if migration_id:
        out = [f for f in out if f["migrationId"] == migration_id]
    if api_name:
        out = [f for f in out if f["apiName"] == api_name]
    return sorted(out, key=lambda f: f["createdAt"], reverse=True)


def all_feedback() -> list:
    return sorted(_load()["feedback"], key=lambda f: f["createdAt"], reverse=True)


def lessons_for(api_name=None, workspace=None, technology=None) -> list:
    """Open lessons that apply to this run:
      - functional: cross-workspace approach gaps (any workspace), matched by technology
        when the lesson names one; tech-agnostic functional lessons always apply.
      - workspace:  only lessons raised in the same workspace (never leak across workspaces).
      - api:        only lessons raised against this same API (customer-specific details).
    """
    d = _load()
    out = []
    for f in d["feedback"]:
        if f["status"] != "open":
            continue
        sc = f.get("scope", "api")
        if sc == "functional":
            ft = f.get("technology")
            if not ft or (technology and ft == technology):
                out.append(f)
        elif sc == "workspace":
            if workspace and f.get("workspace") == workspace:
                out.append(f)
        else:  # api
            if api_name and f.get("apiName") == api_name:
                out.append(f)
    return out


def render_lessons(lessons: list) -> str:
    """Markdown the AI build agent reads before a run."""
    if not lessons:
        return ""
    lines = ["# Lessons from reviewer feedback", "",
             "Apply these when generating documents and code; do not repeat these mistakes.", ""]
    for i, f in enumerate(lessons, 1):
        lines.append(f"## {i}. {f['summary'] or '(no summary)'}")
        if f.get("docType"):
            lines.append(f"- Area: {f['phase']} / {f['docType']}")
        for m in f.get("mistakes", []):
            lines.append(f"- Mistake to avoid: {m}")
        lines.append("")
    return "\n".join(lines)


def mark_applied(ids: list, run_ref: str):
    with _LOCK:
        d = _load()
        for f in d["feedback"]:
            if f["id"] in ids:
                f["status"] = "applied"
                f.setdefault("appliedIn", []).append({"ref": run_ref, "at": _now()})
        _save(d)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
