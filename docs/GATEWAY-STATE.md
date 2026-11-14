# API gateway state and cross-API migration audit

As of 2026-11-14. This file version-controls what was done to the running WSO2 API
Manager gateway (which is otherwise runtime-only state on the VM) so TraceIT and the
infra team can see and reproduce it. Re-run with `scripts/apim_publish.py`.

## The single front door

WSO2 API Manager 4.5.0 runs on the POC VM `13.140.56.229` in namespace `apim`:

| Purpose | Port |
|---|---|
| Publisher / admin / REST / token / DCR | 30443 (container 9443) |
| HTTPS gateway (API traffic) | 30243 (container 8243) |
| HTTP gateway (API traffic) | 30280 (container 8280) |

Each migrated API ALSO has its own k8s NodePort (cms 30084, reservation-mgr 30082,
financial-transactions 30163, telr 30136, ...). Those per-API NodePorts are **not** a
shared gateway: calling one API's path on another API's NodePort returns the Ballerina
404 `no matching service found for path`. Consume everything through the APIM gateway,
not the raw NodePorts. Rate limiting, throttling and IP whitelisting are managed from the
publisher/admin portal.

## Published so far

| API | Gateway context | Backend | Ops | Caching |
|---|---|---|---|---|
| cms-proc-api | `/cms/v1` | `cms-proc-api.wso2.svc/cms-proc-api/v1` | `GET /descriptions/{type}` | on, 600s |
| reservation-mgr-exp-api | `/reservation-manager/v1` | `reservation-mgr-exp-api.wso2.svc/reservation-mgr-exp-api/v1` | `POST /integration-tasks` | off |

All operations published with `authType: None` (open), matching the current
"deployed APIs allowed to all for now" policy. Verified working:

```
http://13.140.56.229:30280/cms/v1/descriptions/shorex-descriptions   -> 200, 1.36 MB real Jease
http://13.140.56.229:30280/cms/v1/descriptions/ctgs-descriptions     -> 200, 94 KB cabin categories
http://13.140.56.229:30280/reservation-manager/v1/integration-tasks  -> reaches backend
```

Not yet fronted: reservation-manager v2 / asyncstatus (version/path mismatch, add on request).

## Performance note (cms)

The CMS pass-through is correct and byte-identical to the current source. It is slower
from the connector's test server, and gateway caching does not fix that: first byte
dropped from ~0.9s to ~0.4s with caching (Jease round-trip removed), but the ~220 KB
gzipped payload download is ~1.6s over the test-server-to-VM link vs ~0.46s from Jease.
That gap is network/hosting (VM location), not a CMS problem.

## Cross-API migration audit (the "is the fix applied to every API?" question)

Per-API code fixes do NOT auto-propagate to already-deployed APIs. The knowledge base
captures functional lessons so FUTURE migrations don't repeat them, but APIs deployed
before a lesson was learned keep their gaps until re-migrated or patched.

Scan of deployed service source on the VM (`/root/api-migration-platform/services`):

| API | Complete | Unmigrated ops (501 catch-all) |
|---|---|---|
| cms-proc-api | yes | - |
| reservation-mgr-exp-api | yes | - |
| seaware-sys-api | yes | - |
| reservation-common-proc-api | mostly | process-payment (intentional: payments off WSO2) |
| financial-transactions-proc-api | partial | has 501 stubs (get-all-checks etc.) |
| loyalty-exp-api | partial | has 501 stubs |
| otalio-sys-api-new | partial | has 501 stubs |
| reservation-common-services-process-api | partial | has 501 stubs |
| telr-pay-gw-sys-api | partial | sessions/status (501 stubs) |
| zoho-sys-api | partial | has 501 stubs |

No synthetic/hardcoded data remains in any service source (earlier lesson held).
Next step to fully close the customer's concern: a standing audit gate in the control
plane that re-applies every recorded lesson across all deployed APIs, not just the one
being worked on.

## Pending decisions (payment / financial)

- **Telr sessions** and **get-all-checks** are 501 scaffolds. Publishing them now would
  put a 501 behind a nicer URL, so they are deliberately not on the gateway.
- Both collide with the standing "payments stay off WSO2 (internal systems)" decision.
  Implementing Telr on WSO2 requires an explicit reversal of that decision plus the real
  backend address (held in server secrets; infra to share).
- Telr contract supplied by the connector team (to implement only after the payments
  decision is confirmed):
  - `POST /sessions`, headers: IAM bearer, Seaware `client_auth`, `client_channel: web`
    (or `web-manage` for manage-booking).
    Body: `{ session: { locale, order: { cartid, amount, currency, description,
    trantype, return_url, reservationId }, customer: { ref, email, phone, name } } }`.
  - `POST /status` with `{ orderRef }`.
- **get-all-checks**: no equivalent exists on the current site, so no contract. Its
  business purpose must be defined (replace the post-payment status check, or a separate
  reconciliation job) before it can be migrated.
