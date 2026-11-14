#!/usr/bin/env python3
"""Publish a migrated API onto the WSO2 API Manager gateway (the single front door).

Reproduces, under version control, the gateway setup done by the control plane so that
TraceIT and the infra team can re-run it instead of hand-crafted curl. stdlib only.

Why this exists: each migrated API also has its own k8s NodePort, but those are NOT a
shared gateway -- calling API-B's path on API-A's NodePort returns Ballerina 404
"no matching service found for path". The ONE address for every API is the APIM gateway
(HTTP 30280 / HTTPS 30243). Rate limiting, throttling and IP whitelisting are then managed
from the APIM publisher/admin portal (9443 / 30443).

Env (all optional, these are the POC defaults):
  APIM_HOST   default 13.140.56.229
  APIM_PORT   default 30443           (publisher/admin REST + token + DCR)
  APIM_USER   default admin
  APIM_PASS   default admin           # CHANGE for anything beyond the POC

Usage:
  python3 scripts/apim_publish.py list
  python3 scripts/apim_publish.py publish --name cms-proc-api --context /cms --version v1 \
      --backend http://cms-proc-api.wso2.svc/cms-proc-api/v1 \
      --op GET:/descriptions/{type} --cache 600
  python3 scripts/apim_publish.py publish --name reservation-mgr-exp-api \
      --context /reservation-manager --version v1 \
      --backend http://reservation-mgr-exp-api.wso2.svc/reservation-mgr-exp-api/v1 \
      --op POST:/integration-tasks

Operations are published with authType None (open) to match the current
"deployed APIs allowed to all for now" policy. Lock down payment/financial APIs with a
subscription or IP whitelist in the portal BEFORE production.
"""
from __future__ import annotations
import argparse, base64, json, os, ssl, urllib.parse, urllib.request

HOST = os.environ.get("APIM_HOST", "13.140.56.229")
PORT = os.environ.get("APIM_PORT", "30443")
USER = os.environ.get("APIM_USER", "admin")
PASS = os.environ.get("APIM_PASS", "admin")
BASE = f"https://{HOST}:{PORT}"
PUB = f"{BASE}/api/am/publisher/v4"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _req(method, url, headers=None, data=None):
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
    elif isinstance(data, str):
        data = data.encode()
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(r, context=_CTX, timeout=60) as resp:
        body = resp.read().decode()
    return json.loads(body) if body.strip().startswith(("{", "[")) else body


def token():
    dcr = _req("POST", f"{BASE}/client-registration/v0.17/register",
               {"Authorization": "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode(),
                "Content-Type": "application/json"},
               {"clientName": "mt-publisher", "owner": USER,
                "grantType": "password refresh_token", "saasApp": True,
                "callbackUrl": "https://localhost"})
    cid, csec = dcr["clientId"], dcr["clientSecret"]
    scope = "apim:api_view apim:api_create apim:api_publish apim:api_manage"
    form = urllib.parse.urlencode({"grant_type": "password", "username": USER,
                                   "password": PASS, "scope": scope}).encode()
    auth = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    tok = _req("POST", f"{BASE}/oauth2/token",
               {"Authorization": "Basic " + auth,
                "Content-Type": "application/x-www-form-urlencoded"}, form)
    return tok["access_token"]


def _H(at):
    return {"Authorization": "Bearer " + at, "Content-Type": "application/json"}


def list_apis():
    at = token()
    data = _req("GET", f"{PUB}/apis?limit=200", _H(at))
    for a in data.get("list", []):
        print(f"{a['name']:<40} context={a['context']:<28} "
              f"version={a['version']:<6} state={a.get('lifeCycleStatus')}")


def publish(name, context, version, backend, ops, cache):
    at = token()
    operations = []
    for o in ops:
        verb, _, target = o.partition(":")
        operations.append({"target": target, "verb": verb.upper(),
                           "authType": "None", "throttlingPolicy": "Unlimited"})
    body = {"name": name, "context": context, "version": version, "provider": USER,
            "type": "HTTP", "transport": ["http", "https"],
            "endpointConfig": {"endpoint_type": "http",
                               "production_endpoints": {"url": backend},
                               "sandbox_endpoints": {"url": backend}},
            "policies": ["Unlimited"], "operations": operations}
    if cache:
        body["responseCachingEnabled"] = True
        body["cacheTimeout"] = int(cache)
    created = _req("POST", f"{PUB}/apis", _H(at), body)
    api_id = created["id"]
    print("created apiId=", api_id)
    rev = _req("POST", f"{PUB}/apis/{api_id}/revisions", _H(at), {"description": "initial"})["id"]
    _req("POST", f"{PUB}/apis/{api_id}/deploy-revision?revisionId={rev}", _H(at),
         [{"name": "Default", "vhost": "localhost", "displayOnDevportal": True}])
    _req("POST", f"{PUB}/apis/change-lifecycle?apiId={api_id}&action=Publish", _H(at))
    print(f"published {name} at gateway {context}/{version}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("publish")
    p.add_argument("--name", required=True)
    p.add_argument("--context", required=True)
    p.add_argument("--version", default="v1")
    p.add_argument("--backend", required=True)
    p.add_argument("--op", action="append", required=True, help="VERB:/path, repeatable")
    p.add_argument("--cache", default="", help="cacheTimeout seconds; empty = off")
    a = ap.parse_args()
    if a.cmd == "list":
        list_apis()
    else:
        publish(a.name, a.context, a.version, a.backend, a.op, a.cache)


if __name__ == "__main__":
    main()
