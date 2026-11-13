"""Provision a workspace's own server with WSO2 + Kubernetes (not the POC box).

Chain: this pod -> the POC host (existing SSH runner) -> the target server (sshpass + the
stored password). The target install reuses the platform's own single-VM kit
(deploy/vm/bootstrap.sh + deploy.sh). Ready to run once real server credentials are entered;
a connectivity preflight runs first, and the full install only runs with confirm=True.
"""
from __future__ import annotations

import shlex
import threading

from runner import get_runner, PLATFORM_DIR

_RUNNING = set()
_LOCK = threading.Lock()


def plan(server: dict) -> list:
    host, port, user = server["host"], server.get("port", 22), server.get("username", "root")
    t = f"{user}@{host}"
    return [
        f"preflight: ssh -p {port} {t} (password auth)",
        f"copy platform kit to {t}:~/api-migration-platform",
        f"{t}: bash deploy/vm/bootstrap.sh  (clears old container/k8s, installs Docker + k3s + Helm)",
        f"{t}: install Ballerina 2201.13.4, bal build",
        f"{t}: bash deploy/vm/deploy.sh  (Redis, test doubles, Prometheus, services, sentinel)",
    ]


def _ssh(server, inner):
    host, port, user, pw = server["host"], server.get("port", 22), server.get("username", "root"), server.get("password", "")
    base = (f"sshpass -p {shlex.quote(pw)} ssh -p {port} -o StrictHostKeyChecking=accept-new "
            f"-o ConnectTimeout=15 {shlex.quote(user + '@' + host)}")
    return f"{base} {shlex.quote(inner)}"


def preflight(server: dict):
    """True/err — can we reach the target with these credentials?"""
    r = get_runner()
    r.run("command -v sshpass >/dev/null || (apt-get update -y && apt-get install -y sshpass)", timeout=120)
    res = r.run(_ssh(server, "echo mt-ok && uname -a"), timeout=40)
    ok = res.ok and "mt-ok" in (res.out or "")
    return ok, (res.out or res.err)[-300:]


def provision(server: dict, confirm: bool = False):
    """Run the full install on the target. Returns (started, message)."""
    key = f"{server['host']}:{server.get('port', 22)}"
    with _LOCK:
        if key in _RUNNING:
            return False, "already provisioning this server"
        _RUNNING.add(key)
    try:
        ok, msg = preflight(server)
        if not ok:
            return False, f"preflight failed: {msg}"
        if not confirm:
            return False, "preflight ok; call with confirm=true to install (destructive: clears the target)"
        r = get_runner()
        host, port, user, pw = server["host"], server.get("port", 22), server.get("username", "root"), server.get("password", "")
        t = shlex.quote(f"{user}@{host}")
        pwq = shlex.quote(pw)
        # copy the platform kit from the POC host to the target, then bootstrap + build + deploy
        steps = [
            f"sshpass -p {pwq} scp -P {port} -r -o StrictHostKeyChecking=accept-new "
            f"{PLATFORM_DIR} {t}:~/api-migration-platform",
            _ssh(server, "cd ~/api-migration-platform && bash deploy/vm/bootstrap.sh"),
            _ssh(server, "cd /tmp && curl -sSfLO https://dist.ballerina.io/downloads/2201.13.4/"
                         "ballerina-2201.13.4-swan-lake-linux-x64.deb && dpkg -i ballerina-*.deb"),
            _ssh(server, "cd ~/api-migration-platform && export PATH=$PATH:/usr/bin && bal build && "
                         "mkdir -p jars && cp services/*/target/bin/*.jar jars/ && bash deploy/vm/deploy.sh"),
        ]
        for cmd in steps:
            res = r.run(cmd, timeout=1800)
            if not res.ok:
                return True, f"provision step failed: {(res.err or res.out)[-300:]}"
        return True, "provisioned"
    finally:
        with _LOCK:
            _RUNNING.discard(key)
