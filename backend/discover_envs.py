#!/usr/bin/env python3
"""Extract per-environment endpoints from an uploaded source bundle.

Middleware projects keep their environment config in files named by environment
(dev/test/uat/prod/...). This reads them and prints {env: [candidate urls/hosts]} as JSON,
so the tool can pre-fill environments instead of asking the customer to type them again.

Reads an untrusted archive, so run isolated:  python3 -I discover_envs.py <zip|dir>
"""
import glob
import json
import os
import re
import sys
import tempfile
import zipfile

ENV_NAMES = ["local", "dev", "development", "test", "sit", "qa", "uat",
             "stage", "staging", "preprod", "prod", "production"]
CONFIG_EXT = (".yaml", ".yml", ".properties", ".json", ".xml", ".config", ".env")
SKIP_HOSTS = {"0.0.0.0", "127.0.0.1", "localhost", "::1"}
URL_RE = re.compile(r'https?://[^\s"\'<>)]+')
HOST_RE = re.compile(r'(?:host|hostname|server|baseurl|base_url|endpoint|uri|address)'
                     r'\s*[:=]\s*["\']?'
                     r'([A-Za-z0-9.\-]+\.[A-Za-z]{2,}|[0-9]{1,3}(?:\.[0-9]{1,3}){3})', re.I)


def env_of(path, base):
    name = os.path.splitext(base)[0].lower()
    low = path.lower()
    for e in ENV_NAMES:
        if name == e or base.lower().startswith(e + ".") or ("/" + e + "/") in low or ("." + e + ".") in base.lower():
            return e
    return None


def scan(root):
    found = {}
    for path in glob.glob(root + "/**/*", recursive=True):
        if not os.path.isfile(path):
            continue
        base = os.path.basename(path)
        if os.path.splitext(base)[1].lower() not in CONFIG_EXT:
            continue
        env = env_of(path, base)
        if not env:
            continue
        try:
            text = open(path, errors="replace").read()[:300000]
        except OSError:
            continue
        cands = set(URL_RE.findall(text))
        cands.update(h for h in HOST_RE.findall(text) if h not in SKIP_HOSTS)
        if cands:
            found.setdefault(env, set()).update(cands)
    return {k: sorted(v)[:8] for k, v in found.items()}


def main():
    if len(sys.argv) < 2:
        print("{}")
        return
    src = sys.argv[1]
    root = src
    if os.path.isfile(src) and zipfile.is_zipfile(src):
        root = tempfile.mkdtemp()
        try:
            with zipfile.ZipFile(src) as z:
                z.extractall(root)
        except Exception:
            print("{}")
            return
    print(json.dumps(scan(root)))


main()
