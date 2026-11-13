"""Seed the three APIs already migrated and deployed on the VM, so the dashboard shows
real history on first start. Runs only when the store is empty.
"""
from __future__ import annotations

import model as M
import store

_SEED = [
    {"name": "Seaware System API", "api": "seaware-sys-api", "layer": "system",
     "tech": "mule4", "est": 5.0, "src": "cs-repo-seaware-sys-api-prod.zip"},
    {"name": "Reservation Manager Experience API", "api": "reservation-mgr-exp-api",
     "layer": "experience", "tech": "mule4", "est": 18.8,
     "src": "cs-repo-reservation-mgr-exp-api-prod.zip"},
    {"name": "Seaware Cache Process API", "api": "seaware-cache-proc-api", "layer": "process",
     "tech": "ballerina", "est": 6.0,
     "src": "cs-seaware-cache-proc-api-wso2-feat-initial-import.zip"},
]


def ensure_seed():
    if store.list_all():
        return
    for s in _SEED:
        mig = M.new_migration(s["name"], s["src"], s["tech"])
        mig["apiName"] = s["api"]
        mig["layer"] = s["layer"]
        mig["estimateDays"] = s["est"]
        mig["serviceDir"] = f"services/{s['api']}"
        for p in mig["phases"]:
            for st in p["steps"]:
                st["status"] = M.DONE
            M.set_phase(mig, p["key"], M.DONE)
        M.log(mig, f"{s['api']} migrated and deployed on the VM (k3s), layer={s['layer']}")
        store.save(mig)
