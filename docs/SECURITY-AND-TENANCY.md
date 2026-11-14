# Security and multi-tenant provisioning plan

As of 2026-11-14. Target: every migrated API is OAuth-protected, each customer self-serves
keys from the Migration-tool UI, customers are isolated by login and role in one portal, and
deploy targets are firewalled.

## Done (live on the POC gateway)

All consumer-facing APIs are published on the WSO2 APIM gateway requiring OAuth2, with a
6000 req/min API-level rate limit (`AroyaApiLimit`), subscribed to application `aroya-connector`:

| API | Gateway context |
|---|---|
| cms-proc-api | `/cms/v1` |
| reservation-mgr-exp-api | `/reservation-manager/v1` |
| loyalty-exp-api | `/loyalty/v1` |
| telr-pay-gw-sys-api | `/telr/v1` |
| financial-transactions-proc-api | `/financial-transactions/v1` |

Verified: no token = 401, valid token = 200/backend. Token: `POST https://13.140.56.229:30443/oauth2/token` (client_credentials).

Internal-only APIs (seaware-sys, seaware-cache-proc, reservation-common-proc,
reservation-common-services-process, zoho-sys, otalio-sys) are NOT on the public gateway by
design. They are reached service-to-service inside the cluster and must be protected by network
isolation (below), not a public OAuth route.

## 1. OAuth for every future migrated API (automate at deploy)

Move the manual publish+secure into the orchestrator deploy step so no API is ever exposed open:

- On deploy, publish the API to the gateway with `securityScheme: oauth2`, operations
  `authType: Application & Application User`, and `apiThrottlingPolicy` set to the workspace's limit.
- Reusable logic already exists in `scripts/apim_publish.py`; lift it into `backend/apim.py`
  and call it from `backend/orchestrator.py` `run_deploy`.
- Internal APIs get a NetworkPolicy instead of a public route.

## 2. Customers get keys from the Migration-tool UI (provisioning)

- On a workspace's first deploy, the backend ensures one APIM application exists for that
  workspace, subscribes the workspace's APIs to it, and stores the key reference.
- New backend endpoints: `POST /api/gateway/app` (ensure app), `POST /api/gateway/keys`
  (generate/regenerate), `GET /api/gateway/keys` (show).
- New UI "Security" panel per workspace: consumer key + secret, token URL, rate limit,
  per-API secured status, and regenerate/revoke buttons.
- The WSO2 Dev Portal (`https://13.140.56.229:30443/devportal`) remains available as the
  native self-service fallback.

## 3. One portal, isolated by login and role

Model: a single control-plane UI (this Migration-tool), with user accounts and roles; each
user sees only their own workspaces, APIs and keys.

- Add authentication + a user store to the backend (start with the APIM/IS identity server
  already running, via OpenID Connect, so there is one credential set).
- Roles: `admin` (all workspaces, policies, portal), `customer` (own workspaces only),
  `viewer` (read-only). Enforce row-level scoping in `ws_store`/`store` by owner.
- Each customer maps to an APIM subscriber user, which also fixes the throttle-tier
  restriction below.

## 4. Fixes for scale

- **Subscriber roles.** The admin user can only subscribe to the Unlimited tier
  (error 902002). Create a real subscriber user/role per customer so Gold/Silver/custom
  tiers and per-customer quotas work. Rate limiting already works today via the API-level
  `AroyaApiLimit` policy, so this is about per-customer tiers, and it lands with step 3.
- **Close the direct ports.** The raw per-API NodePorts bypass the gateway and its auth.
  Lock them down once the connector is confirmed on the gateway with a key. Safest order:
  (a) connector switches to the gateway, (b) convert internal services to ClusterIP and
  restrict the external NodePorts at the firewall. This is a breaking change, so do it in a
  maintenance window.

## 5. Firewall on the deploy target (robust, no malware/intrusion)

Do NOT enable a host firewall (ufw/iptables) blindly on a live k3s node: it can conflict with
the cluster's own iptables rules and can lock out SSH. The robust, safe layering is:

1. **Cloud provider security group** (needs the cloud console): default-deny inbound, allow
   only SSH from admin IPs, the gateway ports (30280/30243/30443), and monitoring. This is
   the strongest layer and cannot be undone from inside the VM.
2. **k8s NetworkPolicies**: restrict internal APIs so only the gateway and the experience
   APIs can reach them; deny all other pod ingress. Pod-level, does not risk SSH.
3. Keep the base image patched and run the image-scan gate already in CI.

Per-customer deploy targets (Dev/QA/UAT/Prod from the workspace) get the same security-group
+ NetworkPolicy treatment applied as a deploy step, once the target's cloud/API access is
provided.

## Inputs needed

- Cloud console or security-group API access for the target servers (for step 5.1).
- A maintenance window to close the direct NodePorts (step 4b), after the connector moves to
  the gateway.
