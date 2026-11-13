"""Runs migration phases against the real toolchain and records live progress.

Document and Scaffold are fully automated through the migration factory. Test and Deploy
run the Ballerina / Docker / k3s toolchain on the host; for a brand-new API that has not
been implemented yet they stop at a clear "action required" rather than failing.
"""
from __future__ import annotations

import os
import threading

from runner import get_runner, PLATFORM_DIR
import kb
import model as M
import store

_RUNNING = set()          # mig ids with a phase in flight
_RUNNING_LOCK = threading.Lock()


def _update(mig_id, fn):
    with store.lock():
        mig = store.get(mig_id)
        if mig is None:
            return None
        fn(mig)
        store.save(mig)
        return mig


def is_busy(mig_id) -> bool:
    with _RUNNING_LOCK:
        return mig_id in _RUNNING


def trigger(mig_id, phase_key):
    """Start a phase in a background thread. Returns (ok, message)."""
    with _RUNNING_LOCK:
        if mig_id in _RUNNING:
            return False, "a phase is already running for this migration"
        _RUNNING.add(mig_id)
    t = threading.Thread(target=_run_guarded, args=(mig_id, phase_key), daemon=True)
    t.start()
    return True, "started"


def _run_guarded(mig_id, phase_key):
    try:
        fn = {"document": run_document, "scaffold": run_scaffold,
              "test": run_test, "deploy": run_deploy, "autorun": run_auto}.get(phase_key)
        if fn:
            fn(mig_id)
    except Exception as e:  # never let a worker die silently
        _update(mig_id, lambda m: (M.log(m, f"orchestrator error: {e}", "error"),
                                   M.set_phase(m, phase_key, M.FAILED, str(e))))
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(mig_id)


# ------------------------------------------------------------- Auto pipeline

def run_auto(mig_id):
    """One-click: run the fully automated phases (document, then scaffold)."""
    run_document(mig_id)
    mig = store.get(mig_id)
    if M.phase(mig, "document")["status"] == M.DONE:
        run_scaffold(mig_id)
    _update(mig_id, lambda m: M.log(m, "auto pipeline finished; mapping review & "
                                       "implementation are the next human steps"))


# ---------------------------------------------------------------- Document

def run_document(mig_id):
    r = get_runner()
    mig = store.get(mig_id)
    local_src = mig["sourceFile"]
    fname = os.path.basename(local_src)

    _update(mig_id, lambda m: (M.set_phase(m, "upload", M.RUNNING),
                               M.set_step(m, "upload", "receive", M.RUNNING)))
    # stage the source where the toolchain can read it
    host_src = f"{PLATFORM_DIR}/.mt-uploads/{fname}"
    r.put(local_src, host_src)
    _update(mig_id, lambda m: M.set_step(m, "upload", "receive", M.DONE, f"staged {fname}"))

    tech = _detect_tech(fname)
    def _det(m):
        m["technology"] = tech
        M.set_step(m, "upload", "detect", M.DONE, tech)
    _update(mig_id, _det)

    gen_root = f"{PLATFORM_DIR}/docs/generated"
    before = set(r.listdir(gen_root))

    _update(mig_id, lambda m: (M.set_phase(m, "document", M.RUNNING),
                               M.set_step(m, "document", "ingest", M.RUNNING),
                               M.log(m, "factory analyze: unpacking and parsing the source")))

    lessons_env = _apply_lessons(mig_id, r, mig.get("apiName"), mig.get("workspace"),
                                 mig.get("technology"), f"{mig_id}:document")
    cmd = (f"{lessons_env}python3 -m factory analyze {_q(host_src)} --out {_q(gen_root)}")
    res = r.run(cmd, cwd=f"{PLATFORM_DIR}/migration-factory", timeout=1200)

    if not res.ok:
        _update(mig_id, lambda m: (M.set_step(m, "document", "ingest", M.FAILED,
                                              (res.err or res.out)[-300:]),
                                   M.log(m, "factory analyze failed", "error"),
                                   M.log(m, (res.err or res.out)[-800:], "error")))
        return

    after = set(r.listdir(gen_root))
    new_dirs = [d for d in (after - before)]
    api_name = new_dirs[0] if new_dirs else _guess_api(r, gen_root, before)
    gen_dir = f"{gen_root}/{api_name}" if api_name else None

    _dets = {"ingest": "unpacked", "parse": "parsed source", "model": "API model built"}
    for stepk in ("ingest", "parse", "model"):
        _update(mig_id, lambda m, s=stepk: M.set_step(m, "document", s, M.DONE, _dets[s]))

    files = {"technical": "TECHNICAL.md", "mapping_doc": "MAPPING.md",
             "functional": "FUNCTIONAL.md", "estimate": "ESTIMATE.md"}
    for stepk, fn in files.items():
        present = gen_dir and r.exists(f"{gen_dir}/{fn}")
        _update(mig_id, lambda m, s=stepk, ok=present, f=fn:
                M.set_step(m, "document", s, M.DONE if ok else M.SKIPPED,
                           f if ok else "not produced"))

    portfolio = r.exists(f"{gen_root}/PORTFOLIO.md")
    _update(mig_id, lambda m: M.set_step(m, "document", "portfolio",
                                         M.DONE if portfolio else M.SKIPPED))

    layer, est = _read_meta(r, gen_dir, gen_root, api_name)
    def _meta(m):
        m["apiName"] = api_name
        m["layer"] = layer
        m["estimateDays"] = est
        M.set_phase(m, "upload", M.DONE)
        M.log(m, f"documents generated for '{api_name}'"
                 + (f" ({layer} layer)" if layer else "")
                 + (f", ~{est} AI-assisted days" if est else ""))
        # mapping review becomes the next human action
        M.set_step(m, "mapping", "spec", M.ACTION, "curate migration-factory/mappings/%s.json" % (api_name or "<source>"))
        M.set_step(m, "mapping", "decisions", M.ACTION, "resolve open questions from the mapping doc")
    _update(mig_id, _meta)


# ---------------------------------------------------------------- Scaffold

def run_scaffold(mig_id):
    r = get_runner()
    mig = store.get(mig_id)
    fname = os.path.basename(mig["sourceFile"])
    host_src = f"{PLATFORM_DIR}/.mt-uploads/{fname}"
    if not r.exists(host_src):
        r.put(mig["sourceFile"], host_src)

    services = f"{PLATFORM_DIR}/services"
    before = set(r.listdir(services))
    _update(mig_id, lambda m: (M.set_phase(m, "scaffold", M.RUNNING),
                               M.set_step(m, "scaffold", "generate", M.RUNNING),
                               M.log(m, "factory scaffold: generating WSO2 service skeleton")))

    lessons_env = _apply_lessons(mig_id, r, mig.get("apiName"), mig.get("workspace"),
                                 mig.get("technology"), f"{mig_id}:scaffold")
    res = r.run(f"{lessons_env}python3 -m factory scaffold {_q(host_src)} --out {_q(services)}",
                cwd=f"{PLATFORM_DIR}/migration-factory", timeout=900)
    if not res.ok:
        _update(mig_id, lambda m: (M.set_step(m, "scaffold", "generate", M.FAILED,
                                              (res.err or res.out)[-300:]),
                                   M.log(m, (res.err or res.out)[-800:], "error")))
        return

    after = set(r.listdir(services))
    new = [d for d in (after - before)]
    svc = new[0] if new else (mig.get("apiName") or "")
    _update(mig_id, lambda m: M.set_step(m, "scaffold", "generate", M.DONE,
                                         f"services/{svc}" if svc else "generated"))

    ok_layout = svc and r.exists(f"{services}/{svc}/Ballerina.toml")
    def _fin(m):
        M.set_step(m, "scaffold", "compile_check", M.DONE if ok_layout else M.SKIPPED,
                   "package layout present" if ok_layout else "check manually")
        m["serviceDir"] = f"services/{svc}" if svc else None
        M.log(m, f"scaffolded {svc}; implement modules/core next" if svc else "scaffold done")
        M.set_step(m, "implement", "core", M.ACTION, "write business logic in modules/core")
        M.set_step(m, "implement", "wiring", M.ACTION, "wire HTTP & config")
        M.set_step(m, "implement", "unit", M.ACTION, "add unit tests to >=80% coverage")
    _update(mig_id, _fin)


# ---------------------------------------------------------------- Test

def run_test(mig_id):
    r = get_runner()
    mig = store.get(mig_id)
    svc = mig.get("serviceDir")
    if not svc:
        _update(mig_id, lambda m: (M.set_step(m, "test", "build", M.ACTION,
                                              "scaffold a service first"),
                                   M.log(m, "test skipped: no service directory yet", "warn")))
        return
    pkg = f"{PLATFORM_DIR}/{svc}"

    _update(mig_id, lambda m: (M.set_phase(m, "test", M.RUNNING),
                               M.set_step(m, "test", "build", M.RUNNING),
                               M.log(m, f"bal build {svc}")))
    b = r.run("bal build", cwd=pkg, timeout=1200)
    _update(mig_id, lambda m: M.set_step(m, "test", "build",
                                         M.DONE if b.ok else M.FAILED,
                                         "compiled" if b.ok else (b.err or b.out)[-300:]))
    if not b.ok:
        _update(mig_id, lambda m: M.log(m, (b.out or b.err)[-800:], "error"))
        return

    _update(mig_id, lambda m: M.set_step(m, "test", "unit_run", M.RUNNING))
    t = r.run("bal test --code-coverage", cwd=pkg, timeout=1800)
    passed = t.ok
    _update(mig_id, lambda m: M.set_step(m, "test", "unit_run",
                                         M.DONE if passed else M.FAILED,
                                         "unit tests passed" if passed else (t.err or t.out)[-300:]))

    # e2e is a platform-level suite; run it only when asked for a ready service
    _update(mig_id, lambda m: M.set_step(m, "test", "e2e", M.SKIPPED,
                                         "run platform e2e once implemented"))

    cov = _parse_coverage(t.out)
    def _cov(m):
        if cov is None:
            M.set_step(m, "test", "coverage", M.SKIPPED, "no coverage reported")
        else:
            ok = cov >= 80.0
            M.set_step(m, "test", "coverage", M.DONE if ok else M.FAILED,
                       f"{cov:.1f}% line coverage")
        M.log(m, f"tests done (coverage {cov if cov is not None else 'n/a'})")
    _update(mig_id, _cov)


# ---------------------------------------------------------------- Deploy

def run_deploy(mig_id):
    r = get_runner()
    mig = store.get(mig_id)
    api = mig.get("apiName") or ""
    values = f"{PLATFORM_DIR}/deploy/values/{api}"
    if not api or not r.exists(values):
        _update(mig_id, lambda m: (M.set_phase(m, "deploy", M.ACTION),
                                   M.set_step(m, "deploy", "image", M.ACTION,
                                              f"add deploy/values/{api or '<api>'} and VM values first"),
                                   M.log(m, "deploy needs Helm values for this API; "
                                            "use the existing services as the template", "warn")))
        return
    # A ready service: build image, import to k3s, helm deploy, verify.
    jar = api.replace("-", "_")
    steps = [
        ("image", f"docker build -q -f deploy/docker/Dockerfile --build-arg JAR=services/{api}/target/bin/{jar}.jar --build-arg PORT=8081 -t {api}:vm ."),
        ("import", f"docker save {api}:vm | k3s ctr images import -"),
        ("helm", f"helm upgrade --install {api} deploy/helm/wso2-api -n wso2 --atomic --timeout 5m -f deploy/values/{api}/common.yaml -f deploy/vm/values/{api}.yaml"),
        ("verify", f"kubectl -n wso2 rollout status deployment/{api} --timeout=150s"),
    ]
    _update(mig_id, lambda m: M.set_phase(m, "deploy", M.RUNNING))
    for sk, cmd in steps:
        _update(mig_id, lambda m, s=sk: M.set_step(m, "deploy", s, M.RUNNING))
        res = r.run(cmd, cwd=PLATFORM_DIR, timeout=1200)
        _update(mig_id, lambda m, s=sk, ok=res.ok, msg=(res.err or res.out)[-200:]:
                (M.set_step(m, "deploy", s, M.DONE if ok else M.FAILED, msg),
                 M.log(m, f"deploy {s}: {'ok' if ok else 'FAILED'}", "info" if ok else "error")))
        if not res.ok:
            return


# ---------------------------------------------------------------- helpers

def _apply_lessons(mig_id, r, api_name, workspace, technology, run_ref):
    """Consolidate open reviewer lessons for this run, hand them to the build agent as a
    knowledge file on the host, mark them applied, and record the count. Returns an env
    prefix (e.g. 'MT_LESSONS_FILE=... ') to attach to the factory command."""
    import os
    import tempfile
    lessons = kb.lessons_for(api_name, workspace, technology)
    if not lessons:
        return ""
    md = kb.render_lessons(lessons)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
    tmp.write(md)
    tmp.close()
    dest = f"{PLATFORM_DIR}/migration-factory/.mt-knowledge.md"
    try:
        r.put(tmp.name, dest)
    finally:
        os.unlink(tmp.name)
    ids = [f["id"] for f in lessons]
    kb.mark_applied(ids, run_ref)

    def _mark(m):
        m["lessonsApplied"] = m.get("lessonsApplied", 0) + len(ids)
        M.log(m, f"applied {len(ids)} reviewer lesson(s) from the knowledge base to this run")
    _update(mig_id, _mark)
    return f"MT_LESSONS_FILE={_q(dest)} "


def _q(s):
    import shlex
    return shlex.quote(s)


def _detect_tech(fname: str) -> str:
    low = fname.lower()
    if "wso2" in low or "ballerina" in low:
        return "ballerina"
    if low.endswith(".jar") or "mule" in low:
        return "mule4"
    if "webmethods" in low or low.endswith(".html"):
        return "webmethods"
    if low.endswith(".zip"):
        return "mule4"
    return "unknown"


def _guess_api(r, gen_root, before):
    now = [d for d in r.listdir(gen_root) if d not in ("PORTFOLIO.md",)]
    extra = [d for d in now if d not in before]
    return extra[0] if extra else (now[-1] if now else None)


def _read_meta(r, gen_dir, gen_root, api_name):
    """layer from inventory.json head; AI-assisted days from the PORTFOLIO row."""
    import re
    layer = None
    est = None
    if gen_dir:
        head = r.read(f"{gen_dir}/inventory.json", 4000)
        m = re.search(r'"layer"\s*:\s*"([^"]+)"', head)
        if m:
            layer = m.group(1)
    port = r.read(f"{gen_root}/PORTFOLIO.md", 40000)
    for line in port.splitlines():
        line = line.strip()
        if line.startswith("|") and api_name and api_name in line:
            cols = [c.strip() for c in line.strip("|").split("|")]
            # name | tech | layer | endpoints | flows | crit | high | manual | ai | basis
            if len(cols) >= 3 and not layer:
                layer = cols[2]
            if len(cols) >= 9:
                try:
                    est = float(cols[8])
                except ValueError:
                    pass
            break
    return layer, est


def _parse_coverage(out: str):
    import re
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", out or "")
    return float(m.group(1)) if m else None
