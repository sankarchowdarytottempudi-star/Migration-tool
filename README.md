# Migration Tool

A control plane and dashboard for the API Migration Platform (MuleSoft / webMethods / .NET →
WSO2 Ballerina on Kubernetes). It runs the migration from a web UI and shows, for every API,
exactly which phase and step it is on, what comes next, and the percent complete — overall and
per phase.

## What it shows

Each migration moves through ordered phases, each with its own percent and steps:

| Phase | Mode | What it does |
|-------|------|--------------|
| Source intake | auto | Receive the source export, detect the technology |
| Document generation | auto | `factory analyze` → Technical, Mapping, Functional, Estimate, Portfolio |
| Mapping review | manual | Curate `migration-factory/mappings/<api>.json`, resolve open questions |
| Service scaffold | auto | `factory scaffold` → a WSO2 Ballerina service that compiles |
| Implementation | manual | Core logic, HTTP/config wiring, unit tests |
| Testing | auto | `bal build`, unit tests, coverage gate |
| Deployment | auto | Build image, import to k3s, Helm deploy, verify rollout |

The dashboard shows overall percent, the current step, the next step, the major phase, and a
live per-step status (pending / running / done / failed / action required).

## Architecture

- **Backend** (`backend/`): Python standard library only — no third-party packages — matching
  the factory. An HTTP server exposes a JSON API and serves the UI. It owns no toolchain of its
  own; it runs `factory`, `bal`, `docker`, and `kubectl` **on the VM host** through a runner
  (`local` for a dev box with the toolchain, `ssh` for the in-cluster pod).
- **Frontend** (`frontend/`): one HTML page plus vanilla JS and CSS. No build step.
- **Deploy** (`deploy/`): a small image and a k3s manifest (NodePort 30900).

## Run locally (dev box with the platform checked out)

```bash
export MT_RUNNER=local
export MT_PLATFORM_DIR=/path/to/api-migration-platform   # needs bal + the factory for real runs
python3 backend/server.py
# open http://localhost:8080  — the admin token prints on startup
```

## Deploy on the VM (k3s)

```bash
# copy this repo to the VM, then on the VM:
cd ~/Migration-tool && bash deploy/deploy-vm.sh
# prints the UI URL (NodePort 30900) and the admin token
```

The pod drives the host over SSH using a dedicated key the script creates and authorizes.

## Security

- The control plane can trigger deploys and run commands on the host, so it is **never open**:
  every `/api/*` call requires the admin token (`X-Admin-Token`). The UI asks for it once and
  keeps it in the browser.
- The migrated business APIs are intentionally open to all for now; IP whitelisting and WSO2
  firewall hardening are the planned next step.
- The pod's SSH key is a Kubernetes Secret mounted read-only; the admin token is a Secret.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | liveness (no auth) |
| GET | `/api/migrations` | list with progress |
| POST | `/api/migrations` | create (raw file body, `X-Name` + `X-Filename` headers) |
| GET | `/api/migrations/{id}` | one migration with derived progress |
| POST | `/api/migrations/{id}/run/{phase}` | run `autorun` / `document` / `scaffold` / `test` / `deploy` |
| POST | `/api/migrations/{id}/advance` | mark a manual step done (`{phase, step, status}`) |
| DELETE | `/api/migrations/{id}` | remove the record |
