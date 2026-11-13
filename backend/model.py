"""Phase model for a migration and the progress maths the UI shows.

A migration moves through ordered phases; each phase has ordered steps. The UI reads
the derived fields: overall percent, per-phase percent, the current step and the next
step. Everything here is plain dicts so the whole record serialises to JSON as-is.
"""
from __future__ import annotations

import time
import uuid

# status values a step or phase can hold
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"
ACTION = "action_required"  # a human must do something (mapping review, implementation)

# mode: "auto" phases are run by the orchestrator; "manual" phases are advanced by a person.
PHASE_DEFS = [
    {"key": "upload", "title": "Source intake", "weight": 5, "mode": "auto", "steps": [
        {"key": "receive", "title": "Receive source archive"},
        {"key": "detect", "title": "Detect technology"},
    ]},
    {"key": "document", "title": "Document generation", "weight": 22, "mode": "auto", "steps": [
        {"key": "ingest", "title": "Ingest & unpack"},
        {"key": "parse", "title": "Parse source"},
        {"key": "model", "title": "Build API model"},
        {"key": "technical", "title": "Technical document"},
        {"key": "mapping_doc", "title": "Mapping document"},
        {"key": "functional", "title": "Functional document"},
        {"key": "estimate", "title": "Effort estimate"},
        {"key": "portfolio", "title": "Portfolio update"},
    ]},
    {"key": "mapping", "title": "Mapping review", "weight": 10, "mode": "manual", "steps": [
        {"key": "spec", "title": "Curate mapping spec"},
        {"key": "decisions", "title": "Resolve open questions"},
    ]},
    {"key": "scaffold", "title": "Service scaffold", "weight": 13, "mode": "auto", "steps": [
        {"key": "generate", "title": "Generate WSO2 service"},
        {"key": "compile_check", "title": "Confirm package layout"},
    ]},
    {"key": "implement", "title": "Implementation", "weight": 20, "mode": "manual", "steps": [
        {"key": "core", "title": "Core logic (modules/core)"},
        {"key": "wiring", "title": "HTTP & config wiring"},
        {"key": "unit", "title": "Unit tests"},
    ]},
    {"key": "test", "title": "Testing", "weight": 15, "mode": "auto", "steps": [
        {"key": "build", "title": "bal build"},
        {"key": "unit_run", "title": "Unit tests"},
        {"key": "e2e", "title": "End-to-end tests"},
        {"key": "coverage", "title": "Coverage gate (>=80%)"},
    ]},
    {"key": "deploy", "title": "Deployment", "weight": 15, "mode": "auto", "steps": [
        {"key": "image", "title": "Build image"},
        {"key": "import", "title": "Import to k3s"},
        {"key": "helm", "title": "Helm deploy"},
        {"key": "verify", "title": "Verify rollout"},
    ]},
]

PHASE_ORDER = [p["key"] for p in PHASE_DEFS]
_DEF_BY_KEY = {p["key"]: p for p in PHASE_DEFS}


DEFAULT_WORKSPACE = "Cruise Saudi Workspace"


def new_migration(name: str, source_file: str, technology: str = "unknown",
                  workspace: str = DEFAULT_WORKSPACE) -> dict:
    now = _now()
    phases = []
    for pdef in PHASE_DEFS:
        phases.append({
            "key": pdef["key"], "title": pdef["title"], "weight": pdef["weight"],
            "mode": pdef["mode"], "status": PENDING, "detail": "",
            "startedAt": None, "finishedAt": None,
            "steps": [{"key": s["key"], "title": s["title"], "status": PENDING, "detail": ""}
                      for s in pdef["steps"]],
        })
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "workspace": workspace,
        "technology": technology,
        "targetTech": "wso2",
        "sourceFile": source_file,
        "apiName": None,
        "lessonsApplied": 0,
        "layer": None,
        "estimateDays": None,
        "createdAt": now,
        "updatedAt": now,
        "phases": phases,
        "log": [],
    }


# ----- mutation helpers (callers hold the store lock) -----

def phase(mig: dict, key: str) -> dict:
    for p in mig["phases"]:
        if p["key"] == key:
            return p
    raise KeyError(key)


def step(mig: dict, phase_key: str, step_key: str) -> dict:
    for s in phase(mig, phase_key)["steps"]:
        if s["key"] == step_key:
            return s
    raise KeyError(step_key)


def set_step(mig: dict, phase_key: str, step_key: str, status: str, detail: str = ""):
    s = step(mig, phase_key, step_key)
    s["status"] = status
    if detail:
        s["detail"] = detail
    _reconcile_phase(mig, phase_key)
    mig["updatedAt"] = _now()


def set_phase(mig: dict, phase_key: str, status: str, detail: str = ""):
    p = phase(mig, phase_key)
    p["status"] = status
    if detail:
        p["detail"] = detail
    if status == RUNNING and not p["startedAt"]:
        p["startedAt"] = _now()
    if status in (DONE, FAILED, SKIPPED):
        p["finishedAt"] = _now()
    mig["updatedAt"] = _now()


def log(mig: dict, line: str, level: str = "info"):
    mig["log"].append({"t": _now(), "level": level, "line": line})
    mig["log"] = mig["log"][-400:]  # keep the tail
    mig["updatedAt"] = _now()


# ----- derived fields for the UI -----

def _reconcile_phase(mig: dict, phase_key: str):
    p = phase(mig, phase_key)
    steps = p["steps"]
    if any(s["status"] == FAILED for s in steps):
        p["status"] = FAILED
    elif steps and all(s["status"] in (DONE, SKIPPED) for s in steps):
        p["status"] = DONE
        if not p["finishedAt"]:
            p["finishedAt"] = _now()
    elif any(s["status"] == RUNNING for s in steps):
        p["status"] = RUNNING
        if not p["startedAt"]:
            p["startedAt"] = _now()
    elif any(s["status"] == ACTION for s in steps):
        p["status"] = ACTION
    else:
        p["status"] = PENDING


def _phase_percent(p: dict) -> float:
    steps = p["steps"]
    if not steps:
        return 100.0 if p["status"] == DONE else 0.0
    val = 0.0
    for s in steps:
        if s["status"] in (DONE, SKIPPED):
            val += 1.0
        elif s["status"] == RUNNING:
            val += 0.5
    return round(100.0 * val / len(steps), 1)


def summarize(mig: dict) -> dict:
    """Return the migration with derived progress fields for the UI."""
    total_w = sum(p["weight"] for p in mig["phases"]) or 1
    overall = 0.0
    phase_views = []
    for p in mig["phases"]:
        pct = _phase_percent(p)
        overall += p["weight"] * pct / 100.0
        pv = dict(p)
        pv["percent"] = pct
        phase_views.append(pv)
    overall_pct = round(100.0 * overall / total_w, 1)

    current = _find_step(mig, RUNNING) or _find_step(mig, ACTION) or _find_step(mig, PENDING)
    nxt = _next_after(mig, current) if current else None
    major = _current_phase(mig)

    out = dict(mig)
    out["phases"] = phase_views
    out["progress"] = {
        "overallPercent": overall_pct,
        "majorPhase": major["title"] if major else "Complete",
        "majorPhaseKey": major["key"] if major else None,
        "currentStep": _label(current),
        "nextStep": _label(nxt),
        "done": overall_pct >= 99.9,
        "outcome": _outcome(mig, overall_pct),
    }
    return out


def _outcome(mig, overall_pct):
    if any(p["status"] == FAILED for p in mig["phases"]):
        return "Failed"
    if mig.get("verifiedEnv"):
        return "Verified"
    if phase(mig, "deploy")["status"] == DONE:
        return "Deployed"
    if overall_pct >= 99.9:
        return "Complete"
    if overall_pct <= 0:
        return "Not started"
    return "In progress"


def _find_step(mig, status):
    for p in mig["phases"]:
        for s in p["steps"]:
            if s["status"] == status:
                return (p, s)
    return None


def _current_phase(mig):
    for p in mig["phases"]:
        if p["status"] in (RUNNING, ACTION, PENDING, FAILED):
            return p
    return None


def _next_after(mig, current):
    seen = False
    cp, cs = current
    for p in mig["phases"]:
        for s in p["steps"]:
            if seen and s["status"] in (PENDING, ACTION, RUNNING):
                return (p, s)
            if p["key"] == cp["key"] and s["key"] == cs["key"]:
                seen = True
    return None


def _label(ref):
    if not ref:
        return None
    p, s = ref
    return {"phase": p["title"], "phaseKey": p["key"], "step": s["title"],
            "stepKey": s["key"], "status": s["status"]}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
