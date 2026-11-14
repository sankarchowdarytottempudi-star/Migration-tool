"""Default migration quality gates: catch integration defects WITHOUT a technical user.

The target users are functional people. They can tell us the business symptom
("booking fails on WSO2 but works on Mule"), not the technical cause. These checks turn
a symptom into an actionable diagnosis automatically, and run by default on every
migration and inside the self-healing loop:

  1. source_contract_diff  - STATIC: headers / credentials the source forwards downstream
     that the generated target drops (the Seaware-login / client_id class of defect).
  2. parity                - DYNAMIC: the same request against source vs target; flags any
     operation that works on the source but fails on the target.
  3. classify_error        - maps a backend error signature to root cause + fix, so a
     functional user's "it failed" becomes an actionable diagnosis.

Every finding carries a `lesson` suitable for the knowledge base (kb.add_feedback with
scope="functional"), so the agent learns the pattern and applies it to future migrations.

Seeded from real defects found migrating AROYA from Mule to WSO2 (Nov 2026):
auth/session pass-through, gateway content wrapping, timeout/circuit, synthetic data,
and route-not-published.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass


@dataclass
class Finding:
    check: str          # "source-contract" | "parity" | "error-signature"
    severity: str       # "blocker" | "warning"
    klass: str          # short slug, e.g. "auth-passthrough"
    summary: str
    root_cause: str = ""
    fix: str = ""
    lesson: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --- helpers -----------------------------------------------------------------

_AUTHISH = ("authorization", "auth", "token", "session", "client_id", "client_secret",
            "client_auth", "x-seaware-authorization", "apikey", "api-key")


def _is_auth_header(name: str) -> bool:
    n = name.lower()
    return any(a in n for a in _AUTHISH)


def _as_text(body) -> str:
    if body is None:
        return ""
    if isinstance(body, (dict, list)):
        return json.dumps(body)
    return str(body)


def _has_graphql_errors(body) -> bool:
    """GraphQL returns business errors inside an HTTP 200 body."""
    if isinstance(body, dict):
        errs = body.get("errors")
        return bool(errs)
    t = _as_text(body)
    return bool(re.search(r'"errors"\s*:\s*\[\s*{', t))


# --- 1. static source-contract diff ------------------------------------------

def source_contract_diff(source_forwarded_headers, target_forwarded_headers) -> list:
    """Headers the SOURCE forwards downstream that the generated TARGET drops.

    Caught the AROYA booking bug: Mule forwarded the Seaware session header; the generated
    WSO2 service forwarded only Authorization, so writes were anonymous.
    """
    src = {h.lower(): h for h in source_forwarded_headers}
    tgt = {h.lower() for h in target_forwarded_headers}
    findings = []
    for low, original in sorted(src.items()):
        if low in tgt:
            continue
        auth = _is_auth_header(low)
        findings.append(Finding(
            check="source-contract",
            severity="blocker" if auth else "warning",
            klass="auth-passthrough" if auth else "header-passthrough",
            summary=f"Source forwards header '{original}' downstream but the generated service does not.",
            root_cause=("An auth/session header the backend needs is dropped, so the backend "
                        "treats calls as anonymous.") if auth else
                       "A header the source relied on is dropped; downstream behaviour may differ.",
            fix=(f"Forward '{original}' through EVERY hop to the backend; if the gateway token "
                 "occupies Authorization, carry the backend token in this header and set it as "
                 "Authorization: Bearer at the last hop.") if auth else
                f"Forward '{original}' through every hop to the backend.",
            lesson=("Preserve the source's full downstream header/credential set end-to-end across "
                    "every hop, especially auth/session tokens; keep gateway auth separate from "
                    "backend auth."),
        ))
    return findings


# --- 2. dynamic parity -------------------------------------------------------

def parity(operation, source_status, source_body, target_status, target_body,
           normalize=None) -> list:
    """Same request, source vs target. 'Works on source, fails on target' is the signal."""
    norm = normalize or (lambda b: _as_text(b))
    src_ok = int(source_status) < 400 and not _has_graphql_errors(source_body)
    tgt_ok = int(target_status) < 400 and not _has_graphql_errors(target_body)
    if src_ok and not tgt_ok:
        return [Finding(
            check="parity", severity="blocker", klass="source-ok-target-fail",
            summary=(f"Operation '{operation}' succeeds on the source but fails on the target "
                     f"(source {source_status}, target {target_status})."),
            root_cause="The migrated API does not behave like the original for this operation.",
            fix="Diff what the target sends to the backend vs the source; usually a dropped "
                "header, missing auth, or altered payload.",
            lesson="An API is not 'migrated' until every operation reaches parity with the source.",
        )]
    if src_ok and tgt_ok and norm(source_body) != norm(target_body):
        return [Finding(
            check="parity", severity="warning", klass="body-mismatch",
            summary=f"Operation '{operation}' returns a different body than the source.",
            root_cause="Response shape, content or content-type differs after migration.",
            fix="Compare field by field; check transformations and content-type pass-through.",
            lesson="Preserve response shape, status codes, content type and source field names.",
        )]
    return []


# --- 3. error-signature map --------------------------------------------------

ERROR_SIGNATURES = [
    dict(pattern=r"not permitted in the current security context|permission error",
         klass="auth-passthrough",
         root_cause="The backend's own auth/session token is not reaching it; the call is anonymous.",
         fix="Forward the backend session token in a dedicated header and set it as "
             "Authorization: Bearer at the last hop; do not rely on the gateway OAuth token.",
         lesson="Keep gateway auth separate from backend auth; forward the backend session token end-to-end."),
    dict(pattern=r"ns:binary|ws\.apache\.org/commons/ns/payload",
         klass="content-wrapping",
         root_cause="The gateway wrapped non-JSON content as base64 binary, usually from response "
                    "caching or a missing message formatter.",
         fix="Disable response caching for this API, or configure a pass-through formatter for its "
             "content type (e.g. text/html).",
         lesson="Pass non-JSON content types through the gateway unchanged; caching must not binarise text/html."),
    dict(pattern=r"circuit.*open|gateway timeout|read timed out|\b504\b",
         klass="timeout-circuit",
         root_cause="A downstream call is slower than the configured timeout, tripping the breaker.",
         fix="Raise the hop timeout to cover real latency, or serve heavy lists from cache.",
         lesson="Set timeouts across the whole chain to cover real downstream latency; serve heavy queries from cache."),
    dict(pattern=r"package not found|voyage.*not.*found|not recognis",
         klass="synthetic-data",
         root_cause="The API returned synthetic / placeholder data the backend does not recognise.",
         fix="Serve real source data; never invent codes or ids.",
         lesson="Never return synthetic or hardcoded data; always serve the real source."),
    dict(pattern=r"no matching (service|resource) found for path",
         klass="route-not-published",
         root_cause="The path is not registered where it was called (wrong port, or not on the gateway).",
         fix="Call the API on its gateway path, or publish/route the API where it is being called.",
         lesson="Front every API on the shared gateway; a per-service NodePort is not a shared gateway."),
]


def classify_error(text) -> Finding | None:
    """Map a backend error (string or JSON) to a known root cause + fix."""
    t = _as_text(text)
    for sig in ERROR_SIGNATURES:
        if re.search(sig["pattern"], t, re.I):
            return Finding(
                check="error-signature", severity="blocker", klass=sig["klass"],
                summary=f"Recognised failure class: {sig['klass']}.",
                root_cause=sig["root_cause"], fix=sig["fix"], lesson=sig["lesson"],
            )
    return None


# --- default gate: run all three and aggregate -------------------------------

def run_default_gate(source_headers=None, target_headers=None, operations=None,
                     error_texts=None) -> dict:
    """Run every default check. `operations` is a list of dicts with keys:
    operation, source_status, source_body, target_status, target_body.
    Returns {findings, blockers, passed, lessons}."""
    findings: list[Finding] = []
    if source_headers is not None and target_headers is not None:
        findings += source_contract_diff(source_headers, target_headers)
    for op in operations or []:
        findings += parity(op.get("operation", "?"), op.get("source_status", 200),
                           op.get("source_body"), op.get("target_status", 200),
                           op.get("target_body"))
    for txt in error_texts or []:
        f = classify_error(txt)
        if f:
            findings.append(f)
    blockers = [f for f in findings if f.severity == "blocker"]
    return {
        "findings": [f.to_dict() for f in findings],
        "blockers": [f.to_dict() for f in blockers],
        "passed": len(blockers) == 0,
        "lessons": sorted({f.lesson for f in findings if f.lesson}),
    }
