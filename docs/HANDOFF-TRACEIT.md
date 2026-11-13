# Handoff: TraceIT · Migration Factory

Audience: the TraceIT IDE / engineering agent that will (1) implement the new product UI and
(2) add the Migration Factory + old-vs-new monitoring and self-healing into TraceIT. This is
the single source of truth for what exists, what is planned, and the contracts to build against.
Nothing here requires changing the existing Migration Tool backend's endpoints or responses.

Date: 2026-11-13.

---

## 1. What exists today (POC)

A working platform that migrates integration APIs (MuleSoft, webMethods, .NET, Ballerina, and now
IBM, Oracle SOA/OSB, Boomi, Workato, Java/Spring, OpenAPI) to **WSO2 Integrator (Ballerina) on
Kubernetes**, documents them, and runs them with AI agents behind human approval.

- **Platform repo** (local git, no remote yet): `api-migration-platform/` — the reusable Ballerina
  framework, migrated services, the Python **migration-factory**, the `ai-ops` sentinel, Helm chart,
  and the single-VM k3s kit. Commit attribution for that repo: `Co-Authored-By: Claude Opus 5.5`.
- **Migration Tool repo** (GitHub `sankarchowdarytottempudi-star/Migration-tool`): the control-plane
  UI + backend described in section 3. This handoff lives in it.
- **POC server** `13.140.56.229` (k3s). This is a POC box only; real workspaces deploy to their own
  servers (section 6). Deployed there now: `seaware-sys-api` (NodePort 30081), `reservation-mgr-exp-api`
  (30082), `seaware-cache-proc-api` + loader (30090), `zoho-sys-api` (30083), the sentinel monitoring
  API (30880), and the Migration Tool itself (30900). All run against in-cluster test doubles, never
  production.

The three layers (System / Experience / Process) are deployed and labelled
`integration.aroya/layer`. Everything else in this document extends from here.

---

## 1a. Deployment topology — ONLY the UI moves to TraceIT

Important: the test/deploy environment does **not** move. The POC server `13.140.56.229` stays the
place where WSO2 on Kubernetes (k3s) runs — the Migration Tool backend, the migrated APIs, the test
doubles, Prometheus and the sentinel all continue to live there (and later, each workspace's own
servers via provisioning in §6). **Only the UI is reimplemented inside TraceIT.** The TraceIT UI is a
thin client that calls the existing backend over the network; it builds and deploys nothing itself.

Base URLs the TraceIT UI must call (set via env, do not hard-code):
- Migration Tool backend / API (§3): `http://13.140.56.229:30900` — header `X-Admin-Token`.
- Playground/verify/parity/optimize all go through that backend (server-side proxy; no CORS).
- Sentinel monitoring API (§5): `http://13.140.56.229:30880` — `Authorization: Bearer <MONITOR_API_TOKEN>`.

So: TraceIT hosts the UI only; all build/test/deploy and monitoring stay on the POC server (then on
the provisioned workspace servers). Do not re-implement the backend in TraceIT.

## 2. Two things to build in TraceIT

1. **The product UI** (section 4): implement the approved design in TraceIT's web app.
2. **Migration Factory + monitoring inside TraceIT** (section 5): old-vs-new production monitoring
   (health, performance, CPU, memory and other indices), a comparison that shows how much better the
   migrated API is, and a self-healing agent that opens a PR for human approval, then redeploys and
   resumes monitoring.

---

## 3. The Migration Tool backend — API the UI binds to

Stdlib-only Python server. Every `/api/*` call needs header `X-Admin-Token` except `/api/health`.
No response shapes may change. The current single-page UI in `frontend/` is the reference for shapes.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | liveness (no auth); `{status, runner}` |
| GET | `/api/phases` | phase/step definitions |
| GET | `/api/migrations` | list; each item has `progress{overallPercent,outcome,majorPhase,currentStep,nextStep}`, `phases[]`, `workspace`, `layer`, `technology`, `targetTech`, `busy` |
| POST | `/api/migrations` | create; body = raw file; headers `X-Name`, `X-Filename`, `X-Workspace`, `X-Target-Tech` (wso2\|mule\|springboot\|dotnet\|webmethods\|boomi\|openapi), `X-Source-Type` (`spec` for doc-only) |
| GET | `/api/migrations/{id}` | full migration incl. `discoveredEnvs`, `verifiedEnv`, `optimization`, `testNodePort`, `log[]` |
| POST | `/api/migrations/{id}/run/{phase}` | phase ∈ `autorun\|document\|scaffold\|test\|deploy\|spec` (async; poll detail) |
| POST | `/api/migrations/{id}/advance` | `{phase, step, status}` — advance a manual step |
| GET/POST | `/api/migrations/{id}/feedback` | knowledge-base feedback; POST `{scope:functional\|workspace\|api, summary, mistakes[]}` |
| POST | `/api/migrations/{id}/verify` | `{envId, path, method, headers, body}` → fetch env data; marks migration **Verified** on 200+data |
| POST | `/api/migrations/{id}/parity` | `{envId, ...}` → legacy vs migrated response diff + both latencies |
| POST | `/api/migrations/{id}/optimize` | measure, apply a real optimization (scale +1 replica), re-measure vs 250ms/1s |
| GET | `/api/migrations/{id}/docs` / `/docs/{name}` | list / download generated documents |
| DELETE | `/api/migrations/{id}` | remove record (UI must require typed-name confirm) |
| GET/POST/DELETE | `/api/environments` | registered target environments; tokens never returned (`hasToken`) |
| GET/POST/DELETE | `/api/workspace-configs` | workspaces + Dev/QA/UAT/Prod servers (passwords never returned) |
| POST | `/api/workspace-configs/{id}/provision/{tier}` | provision WSO2+k3s on that server (preflight; `?confirm=true` installs) |
| GET | `/api/knowledge` | all feedback/lessons grouped by scope |
| POST | `/api/proxy` | `{service\|envId, path, method, headers, body}` → calls the API server-side (no CORS); returns `{status, body, ms, slo}` |

Lifecycle phases per migration: **Document → Mapping → Scaffold → Implement → Test → Deploy**, each
with steps, an overall percent, current/next step, and an outcome
(`Verified > Deployed > Complete > In progress > Failed`). Status derivation lives in `backend/model.py`
and must be reproduced as pure, unit-tested functions in the UI (the prompt requires this).

Security already in place: admin-token auth on all `/api/*`; the downstream bearer is injected
server-side (`MT_DEFAULT_BEARER`), so the UI never holds a token — the Playground shows `Bearer ••••`.

---

## 4. The product UI (implement in TraceIT)

- **Design source of truth** (committed in this repo): `design/migration-factory/` — `Main.dc.html`
  (overview + pipeline), `Playground.dc.html`, `Knowledge.dc.html`, `NewMigration.dc.html`,
  `SignIn.dc.html`, `canvas.json`. Port colours, spacing, type, copy, keyframes and easings exactly.
- **Accent** `#B45309`; layer colours sys `#1E5A9E`, proc `#74399F`, exp `#0F6A62`; full token set
  (light + dark) is in the `.mf` / `.mf:not(.t-light)` blocks of `Main.dc.html`.
- **Build prompt** (verbatim, authoritative) is in `docs/UI-BUILD-PROMPT.md` in this repo. It covers
  mode detection (TraceIT route group `app/(app)/migrations/*` vs standalone `apps/web`), tokens,
  theming (light/dark/auto with view-transition reveal), self-hosted fonts (no Google CDN — shipboard
  networks), motion, TanStack Query against the section-3 endpoints, status derivation, security
  (no hard-coded tokens), accessibility, responsiveness, the five screens, and the acceptance criteria
  (Vitest + Playwright + axe, visual parity, zero console errors). Follow it as written.

Note: the current standalone UI in `frontend/` (single-page, no build step) is a working reference
for data shapes and flows, not the visual target. The design files are the visual target.

---

## 5. Old-vs-new monitoring + self-healing (the new capability)

Goal: monitor the **legacy/old API in production** and the **migrated API** side by side, prove the
improvement, and auto-remediate production issues through human-approved PRs.

### 5.1 What to monitor (both old and new)
Per API and environment: **health** (up/degraded/down), **latency** (p50/p95/p99 vs the SLO: ≤250ms
ideal, ≤1000ms max), **throughput** (req/s), **error rate** (by type), **CPU** and **memory**
(request/limit/usage), restarts, and saturation. Show an **old-vs-new comparison**: same operations,
legacy vs migrated, with deltas (e.g. "p95 620ms → 48ms, −92%; memory 6GB → 760MB").

### 5.2 Data sources (already present, extend them)
- The `ai-ops` **sentinel** already exposes a monitoring REST API the handoff calls Traceit's feed:
  `GET /api/v1/health`, `/services`, `/services/{name}`, `/incidents`, Bearer `MONITOR_API_TOKEN`
  (on the POC: NodePort 30880, token in `/root/.monitor-api-token`). It reads Prometheus metrics
  (`requests_total`, `response_errors_total`, `response_time_seconds_bucket`).
- **Extend** the sentinel sources with: (a) CPU/memory from the k8s **metrics-server** / cAdbvisor
  (`container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`); (b) a **legacy adapter**
  that scrapes or probes the old prod API (the legacy endpoints are auto-discovered per environment —
  see `discoveredEnvs` on each migration, and the Migration Tool `/parity` already calls legacy vs
  migrated); (c) an **old-vs-new comparison** endpoint the TraceIT UI renders.
- TraceIT consumes these over the same Bearer-token REST contract. Do not couple TraceIT to the
  cluster directly; go through the sentinel API so it works on closed shipboard networks.

### 5.3 Self-healing loop (human-approved)
Already designed in `ai-ops` (`sentinel` + `.github/workflows/ai-fix.yml`); surface and complete it:
1. Sentinel **detects** a recurring production issue (fingerprint + recurrence ≥3 in 24h, confidence
   ≥0.7 — `ai-ops/policies/guardrails.yaml`).
2. **Triage** (Claude structured output) classifies it and proposes a fix.
3. The **ai-fix agent** runs Claude Code on branch `ai-fix/<fingerprint>`, constrained by guardrails
   (allowed/forbidden paths, ≤8 files, ≤200 lines, **never merge, deploy, tag or push to main**),
   runs the gates (build, tests, scan), and opens a **PR**.
4. A **human reviews and approves** the PR (TraceIT shows the incident, the diff, the test results,
   and an Approve/Reject action). Approval + the prod environment gate deploy it.
5. After deploy, **monitoring resumes** and the incident is marked resolved if metrics recover;
   otherwise it re-opens. Agents never self-approve or self-deploy.

TraceIT work: an **Incidents** view (open/triaged/PR-open/approved/resolved), the old-vs-new
**comparison dashboard**, per-API health/perf/CPU/memory tiles, and the PR-approval UX wired to
GitHub. The guardrail policy and the approval requirement are non-negotiable.

---

## 6. Provisioning to the workspace's own servers (ready)

- A workspace (`/api/workspace-configs`) holds **Dev/QA/UAT/Prod** servers: host, port, username,
  password. Validation: **at least one of Dev/QA/UAT is mandatory; Prod optional**. Passwords are
  stored server-side and never returned.
- `POST /api/workspace-configs/{id}/provision/{tier}` is wired (`backend/provisioner.py`): it reaches
  the target over SSH (password auth via `sshpass`), runs a **connectivity preflight**, and with
  `?confirm=true` runs the platform's own single-VM kit on the target —
  `deploy/vm/bootstrap.sh` (clears old container/k8s, installs Docker + k3s + Helm), installs
  Ballerina, `bal build`, then `deploy/vm/deploy.sh`. It marks the server `provisioned` on success.
- **Status: ready, not yet run against a real server** (needs real credentials; the install is
  destructive, so it runs only on explicit `confirm`). This replaces using the POC box as the target.

---

## 7. Any-source → any-target migration

- Sources (10, auto-detected): mule4, webmethods, dotnet, ballerina, **ibm** (ACE/IIB/MQ), **oracle**
  (SOA/OSB), **boomi**, **workato**, **java** (Spring/JAX-RS), **openapi** (microservices).
- Targets (`factory/targets.py`): **wso2** is the production generator; mule, springboot, dotnet,
  webmethods, boomi, openapi emit a labelled skeleton + the technology-neutral `ApiModel`
  (`api-model.json`). Because every parser produces the same neutral model, the matrix is
  **sources × targets**, not N×M bespoke code: completing a target = writing one generator module
  shaped like `scaffold.py`. The UI's New migration dialog already lets the user pick the target.
- Remaining per-target engineering: production generators for the non-wso2 targets (each ~one module).
  This is the planned work; the framework, selection and skeletons are in place.

---

## 8. Status summary

Built and deployed: the Migration Tool (phases, workspaces, environments + verification, knowledge
base learning loop, playground, parity, optimizer, doc-only generation, 10 source parsers, any-target
selection), provisioning endpoint (ready), server-side token injection.

For TraceIT to build: the product UI (section 4 + `docs/UI-BUILD-PROMPT.md`), the old-vs-new
monitoring + comparison, and the incident/self-healing/PR-approval experience (section 5). Not yet
done anywhere: production generators for non-wso2 targets; running provisioning against a real server.
