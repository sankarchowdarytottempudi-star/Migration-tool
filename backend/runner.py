"""Run commands and inspect files where the real toolchain lives.

The control plane itself carries no Ballerina/Docker/kubectl. It executes the migration
work on the VM host, reusing the proven toolchain. Two backends:

  LocalRunner  - runs on the same machine (dev on a box that has the toolchain)
  SSHRunner    - runs over SSH on the VM host (how the in-cluster pod works)

Selected by env:
  MT_RUNNER = "ssh" | "local"      (default "local")
  MT_SSH_TARGET = "root@13.140.56.229"
  MT_SSH_KEY = "/secrets/id_ed25519"
  MT_PLATFORM_DIR = path to the api-migration-platform checkout on the target host
"""
from __future__ import annotations

import os
import shlex
import subprocess

PLATFORM_DIR = os.environ.get("MT_PLATFORM_DIR", os.path.expanduser("~/api-migration-platform"))


class Result:
    def __init__(self, rc, out, err):
        self.rc, self.out, self.err = rc, out, err
    @property
    def ok(self):
        return self.rc == 0


class LocalRunner:
    def run(self, cmd: str, cwd: str | None = None, timeout: int = 1800) -> Result:
        try:
            p = subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout,
                               capture_output=True, text=True)
            return Result(p.returncode, p.stdout, p.stderr)
        except subprocess.TimeoutExpired as e:
            return Result(124, e.stdout or "", f"timeout after {timeout}s")

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def listdir(self, path: str) -> list:
        return sorted(os.listdir(path)) if os.path.isdir(path) else []

    def read(self, path: str, limit: int = 20000) -> str:
        if not os.path.exists(path):
            return ""
        with open(path, errors="replace") as f:
            return f.read(limit)

    def put(self, local_path: str, remote_path: str):
        if os.path.abspath(local_path) != os.path.abspath(remote_path):
            import shutil
            os.makedirs(os.path.dirname(remote_path), exist_ok=True)
            shutil.copy(local_path, remote_path)
        return remote_path


class SSHRunner:
    def __init__(self):
        self.target = os.environ["MT_SSH_TARGET"]
        self.key = os.environ.get("MT_SSH_KEY", "")
        self._base = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                      "-o", "ConnectTimeout=15"]
        if self.key:
            self._base += ["-i", self.key]

    def run(self, cmd: str, cwd: str | None = None, timeout: int = 1800) -> Result:
        full = cmd if not cwd else f"cd {shlex.quote(cwd)} && {cmd}"
        full = "export PATH=$PATH:/usr/bin:/usr/local/bin; export KUBECONFIG=/root/.kube/config; " + full
        try:
            p = subprocess.run(self._base + [self.target, full], timeout=timeout,
                               capture_output=True, text=True)
            return Result(p.returncode, p.stdout, p.stderr)
        except subprocess.TimeoutExpired as e:
            return Result(124, e.stdout or "", f"timeout after {timeout}s")

    def exists(self, path: str) -> bool:
        return self.run(f"test -e {shlex.quote(path)}").rc == 0

    def listdir(self, path: str) -> list:
        r = self.run(f"ls -1 {shlex.quote(path)} 2>/dev/null")
        return [x for x in r.out.splitlines() if x] if r.ok else []

    def read(self, path: str, limit: int = 20000) -> str:
        r = self.run(f"head -c {limit} {shlex.quote(path)} 2>/dev/null")
        return r.out if r.ok else ""

    def put(self, local_path: str, remote_path: str):
        self.run(f"mkdir -p {shlex.quote(os.path.dirname(remote_path))}")
        scp = ["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
        if self.key:
            scp += ["-i", self.key]
        subprocess.run(scp + [local_path, f"{self.target}:{remote_path}"],
                       capture_output=True, text=True)
        return remote_path


def get_runner():
    if os.environ.get("MT_RUNNER", "local") == "ssh":
        return SSHRunner()
    return LocalRunner()
