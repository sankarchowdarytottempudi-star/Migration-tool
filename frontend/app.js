const $ = (s) => document.querySelector(s);
let TOKEN = localStorage.getItem("mt_token") || "";
let selected = null;
let pollTimer = null;
const docsCache = {};   // id -> [{name}]
const fbCache = {};     // id -> [feedback]
let ENVS = [];          // registered environments
const vfState = {};     // id -> {resp, envId} (kept across polls so the UI doesn't reset)
const pState = {};      // id -> performance/parity output text

async function loadEnvs() {
  try { ENVS = (await (await api("/api/environments")).json()).environments || []; }
  catch { ENVS = []; }
  const el = $("#plEnv"); if (el) el.innerHTML = envOptions(el.value);
}
function envOptions(sel) {
  return '<option value="">VM cluster (test double)</option>' +
    ENVS.map(e => `<option value="${e.id}" ${e.id === sel ? "selected" : ""}>${esc(e.name)} — ${esc(e.baseUrl)}</option>`).join("");
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts, headers: { "X-Admin-Token": TOKEN, ...(opts.headers || {}) },
  });
  // Never tear the whole app down to the login screen while a modal is open (that closed the
  // Playground and reset the page on any transient 401). Only redirect to login otherwise.
  if (r.status === 401) { if (!anyModalOpen()) showLogin("Session expired — sign in again."); throw new Error("unauthorized"); }
  return r;
}
function anyModalOpen() { return [...document.querySelectorAll(".modal")].some(m => !m.classList.contains("hidden")); }
function openModal(sel) { $(sel).classList.remove("hidden"); if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
function closeModal(sel) {
  $(sel).classList.add("hidden");
  if (!anyModalOpen() && selected) { if (pollTimer) clearInterval(pollTimer); pollTimer = setInterval(refreshDetail, 1500); }
}

// ---------- auth ----------
function showLogin(msg) {
  $("#app").classList.add("hidden"); $("#login").classList.remove("hidden");
  $("#loginError").textContent = msg || "";
}
function showApp() { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); loadEnvs(); loadList(); }
$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  TOKEN = $("#tokenInput").value.trim();
  const r = await fetch("/api/migrations", { headers: { "X-Admin-Token": TOKEN } });
  if (r.ok) { localStorage.setItem("mt_token", TOKEN); showApp(); }
  else $("#loginError").textContent = "Invalid token.";
});
$("#logoutBtn").addEventListener("click", () => {
  localStorage.removeItem("mt_token"); TOKEN = ""; showLogin("");
});

// ---------- list (grouped by workspace) ----------
async function loadList() {
  const { migrations } = await (await api("/api/migrations")).json();
  const total = migrations.length;
  const deployed = migrations.filter((m) => m.progress.outcome === "Deployed").length;
  const avg = total ? Math.round(migrations.reduce((a, m) => a + m.progress.overallPercent, 0) / total) : 0;
  $("#headStats").innerHTML =
    `<div class="s"><b>${total}</b> APIs</div><div class="s"><b>${deployed}</b> deployed</div><div class="s"><b>${avg}%</b> avg</div>`;

  const groups = {};
  migrations.forEach((m) => { (groups[m.workspace || "Unassigned"] ||= []).push(m); });
  $("#list").innerHTML = Object.entries(groups).map(([ws, items]) => `
    <div class="ws">
      <div class="ws-head">${esc(ws)} <span class="muted">· ${items.length} API${items.length > 1 ? "s" : ""}</span></div>
      ${items.map(cardHTML).join("")}
    </div>`).join("") || `<div class="muted">No migrations yet.</div>`;
  migrations.forEach((m) => $(`#card-${m.id}`).addEventListener("click", () => select(m.id)));
  if (!selected && migrations[0]) select(migrations[0].id);
}

function outcomeClass(o){return "oc-"+o.toLowerCase().replace(/\s+/g,"-");}
function cardHTML(m) {
  const p = m.progress;
  return `<div class="mcard ${m.id === selected ? "sel" : ""}" id="card-${m.id}">
    <div class="top"><span class="name">${esc(m.name)}</span>
      <span class="badge ${m.layer || ""}">${m.layer || m.technology || "?"}</span></div>
    <div class="bar"><i style="width:${p.overallPercent}%"></i></div>
    <div class="meta"><span class="oc ${outcomeClass(p.outcome)}">${p.outcome}</span>
      <span>${p.overallPercent}%</span></div>
  </div>`;
}

// ---------- detail ----------
async function select(id) {
  selected = id;
  document.querySelectorAll(".mcard").forEach((e) => e.classList.remove("sel"));
  const c = $(`#card-${id}`); if (c) c.classList.add("sel");
  await Promise.all([loadDocs(id), loadFeedback(id)]);
  await refreshDetail();
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(refreshDetail, 1500);
}
async function loadDocs(id){ try{ docsCache[id]=(await (await api(`/api/migrations/${id}/docs`)).json()).docs||[] }catch{ docsCache[id]=[] } }
async function loadFeedback(id){ try{ fbCache[id]=(await (await api(`/api/migrations/${id}/feedback`)).json()).feedback||[] }catch{ fbCache[id]=[] } }

async function refreshDetail() {
  if (!selected) return;
  const r = await api(`/api/migrations/${selected}`);
  if (r.ok) renderDetail(await r.json());
}

function renderDetail(m) {
  const p = m.progress, busy = m.busy;
  const docs = docsCache[m.id] || [], fb = fbCache[m.id] || [];
  $("#detail").innerHTML = `
    <div class="d-head">
      <div>
        <div class="d-title">${esc(m.name)} <span class="oc ${outcomeClass(p.outcome)}">${p.outcome}</span></div>
        <div class="d-sub">${esc(m.workspace || "")} · ${m.apiName ? esc(m.apiName) + " · " : ""}${m.layer || "—"} layer ·
          ${m.technology}${m.estimateDays ? " · ~" + m.estimateDays + " AI-days" : ""}${m.lessonsApplied ? ' · <span class="lf">★ ' + m.lessonsApplied + " lesson(s) applied</span>" : ""}</div>
      </div>
      <div class="ring" style="--p:${p.overallPercent}"><div><b>${p.overallPercent}%</b><span>${p.outcome}</span></div></div>
    </div>
    <div class="nownext">
      <div class="nn"><div class="l">Current step</div><div class="v">${p.currentStep ? esc(p.currentStep.step) : "—"}
        <small>${p.currentStep ? "· " + esc(p.currentStep.phase) : ""}</small></div></div>
      <div class="nn"><div class="l">Next step</div><div class="v">${p.nextStep ? esc(p.nextStep.step) : "—"}
        <small>${p.nextStep ? "· " + esc(p.nextStep.phase) : ""}</small></div></div>
    </div>
    <div class="actions">
      <button class="primary" ${busy ? "disabled" : ""} onclick="run('${m.id}','autorun')">${busy ? "Running…" : "▶ Run documents + scaffold"}</button>
      <button ${busy ? "disabled" : ""} onclick="run('${m.id}','test')">Run tests</button>
      <button ${busy ? "disabled" : ""} onclick="run('${m.id}','deploy')">Deploy</button>
      <button class="ghost" onclick="delMig('${m.id}')">Delete</button>
    </div>

    ${m.testNodePort ? `<div class="testurl">✓ Live test endpoint:
      <a href="http://${location.hostname}:${m.testNodePort}/health/ready" target="_blank">http://${location.hostname}:${m.testNodePort}/health/ready</a>
      <span class="muted">· base path ${esc(m.testBasePath || "")}</span></div>` : ""}

    ${docs.length ? `<div class="section"><h3>Documents</h3><div class="docs">
      ${docs.map((d) => `<button class="doc" onclick="dlDoc('${m.id}','${d.name}')">⬇ ${d.name}</button>`).join("")}
    </div></div>` : ""}

    <div class="section"><h3>Phases</h3>
      ${m.phases.map((ph) => phaseHTML(m.id, ph)).join("")}
    </div>

    <div class="section"><h3>Environment verification</h3>
      ${m.verifiedEnv ? `<div class="verified">✓ Verified against <b>${esc(m.verifiedEnv)}</b> on ${(m.verifiedAt||"").slice(0,19).replace("T"," ")} — migration complete</div>`
        : '<div class="muted">Choose the environment to connect to, fetch its data, and complete the migration. Add environments in the API Playground.</div>'}
      <div class="pl-row">
        <select id="vfEnv">${envOptions(vfState[m.id] ? vfState[m.id].envId : "")}</select>
        <button class="primary" onclick="verifyMig('${m.id}')">Fetch env data &amp; complete</button>
      </div>
      <pre id="vfResp" class="resp">${esc(vfState[m.id] ? vfState[m.id].resp : "—")}</pre>
      ${(m.discoveredEnvs && m.discoveredEnvs.length) ? `<div class="disc">
        <div class="muted sm">Discovered from the uploaded source — click to register (no re-entry):</div>
        ${m.discoveredEnvs.map(e => e.candidates.map(c =>
          `<button class="doc" onclick="regEnv('${m.id}','${esc(e.name)}','${esc(c)}')">${esc(e.name)}: ${esc(c)}</button>`).join("")).join("")}
      </div>` : ""}
    </div>

    <div class="section"><h3>Performance &amp; parity</h3>
      <div class="pl-row">
        <button onclick="optimizeMig('${m.id}')">⚡ Optimize performance</button>
        <button onclick="parityMig('${m.id}')">⇄ Parity vs legacy (uses the env selected above)</button>
      </div>
      ${m.optimization ? `<div class="muted sm">Last optimize: ${m.optimization.beforeMs}ms → ${m.optimization.afterMs}ms
        (${(m.optimization.actions||[]).join(", ")}) — target ${m.optimization.meetsTarget ? "met ✓" : "missed"}</div>` : ""}
      <pre id="pResp" class="resp">${esc(pState[m.id] || "Targets: ≤250 ms ideal, ≤1 s max.")}</pre>
    </div>

    <div class="section"><h3>Reviewer feedback</h3>
      ${feedbackFormHTML(m)}
      ${fb.length ? fb.map(fbHTML).join("") : '<div class="muted">No feedback yet.</div>'}
    </div>

    <div class="section"><h3>Activity</h3>
      <div class="log">${(m.log || []).slice(-50).map(logHTML).join("") || '<div class="ln">No activity.</div>'}</div>
    </div>`;
  const lg = $("#detail .log"); if (lg) lg.scrollTop = lg.scrollHeight;
}

function phaseHTML(mid, ph) {
  return `<div class="phase"><div class="ph"><span class="stat ${ph.status}"></span>
      <span class="pt">${ph.title}</span><span class="pp">${ph.percent}%${ph.mode === "manual" ? " · manual" : ""}</span></div>
    <div class="steps">${ph.steps.map((s) => stepHTML(mid, ph, s)).join("")}</div></div>`;
}
function stepHTML(mid, ph, s) {
  const act = s.status === "action_required"
    ? `<button onclick="advance('${mid}','${ph.key}','${s.key}')">Mark done</button>` : "";
  return `<div class="step"><span class="stat ${s.status}"></span><span>${s.title}</span>
    <span class="st ${s.status}">${s.status.replace("_", " ")}</span>
    ${s.detail ? `<span class="sd" title="${esc(s.detail)}">${esc(s.detail)}</span>` : ""}${act}</div>`;
}
function logHTML(l){return `<div class="ln ${l.level}">${l.t.slice(11,19)}  ${esc(l.line)}</div>`;}

function feedbackFormHTML(m) {
  return `<form class="fbform" onsubmit="submitFeedback(event,'${m.id}')">
    <select id="fbScope">
      <option value="functional">Functional / approach gap — share to ALL workspaces (same technology)</option>
      <option value="workspace">Workspace-specific — keep private to ${esc(m.workspace || "this workspace")}</option>
      <option value="api" selected>API-specific detail — keep private to this API</option>
    </select>
    <input id="fbSummary" placeholder="What went wrong? (e.g. pulled the wrong API details / mapping)">
    <input id="fbMistakes" placeholder="Specific mistakes, comma-separated (optional)">
    <button class="primary" type="submit">Submit feedback</button>
  </form>`;
}
function fbHTML(f) {
  const sc = { functional: "shared · functional", workspace: "private · workspace", api: "private · API" }[f.scope] || f.scope;
  return `<div class="fb">
    <div class="fb-top"><span class="fb-scope ${f.scope}">${sc}</span>
      <span class="fb-status ${f.status}">${f.status}${f.status === "applied" ? " ✓" : ""}</span></div>
    <div class="fb-sum">${esc(f.summary)}</div>
    ${(f.mistakes || []).length ? `<ul>${f.mistakes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    ${f.technology ? `<div class="muted">applies to: ${esc(f.technology)} migrations</div>` : ""}
  </div>`;
}

// ---------- actions ----------
async function run(id, phase){ await api(`/api/migrations/${id}/run/${phase}`,{method:"POST"}); setTimeout(()=>loadDocs(id),500); refreshDetail(); }
async function advance(id,phase,step){ await api(`/api/migrations/${id}/advance`,{method:"POST",
  headers:{"Content-Type":"application/json"},body:JSON.stringify({phase,step,status:"done"})}); refreshDetail(); }
async function delMig(id){ if(!confirm("Delete this migration record?"))return;
  await api(`/api/migrations/${id}`,{method:"DELETE"}); selected=null;
  $("#detail").innerHTML='<div class="empty">Select a migration.</div>'; loadList(); }
async function dlDoc(id,name){
  const r=await api(`/api/migrations/${id}/docs/${name}`);
  const blob=await r.blob(); const url=URL.createObjectURL(blob);
  const a=document.createElement("a"); a.href=url; a.download=name; a.click(); URL.revokeObjectURL(url);
}
async function submitFeedback(e,id){
  e.preventDefault();
  const scope=$("#fbScope").value, summary=$("#fbSummary").value.trim();
  const mistakes=$("#fbMistakes").value.split(",").map((s)=>s.trim()).filter(Boolean);
  if(!summary)return;
  await api(`/api/migrations/${id}/feedback`,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({scope,summary,mistakes})});
  await loadFeedback(id); refreshDetail();
}
function presetForMigration(m){
  if(m.layer==="experience")return PRESETS.exp;
  if(m.layer==="process")return PRESETS.proc;
  if(m.apiName&&m.apiName.indexOf("seaware-sys")>=0)return PRESETS.sys;
  if(m.layer==="system")return {method:"GET",path:"/health/ready",headers:{},body:""};
  return PRESETS.exp;
}
async function verifyMig(id){
  const envId=$("#vfEnv").value;
  if(!envId){ vfState[id]={resp:"Select an environment first (add one in the API Playground).",envId:""}; return refreshDetail(); }
  const m=await (await api(`/api/migrations/${id}`)).json();
  const pr=presetForMigration(m);
  const headers={...pr.headers}; delete headers.Authorization;   // use the environment's own token
  vfState[id]={resp:"…fetching from environment",envId};
  refreshDetail();
  const r=await api(`/api/migrations/${id}/verify`,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({envId,method:pr.method,path:pr.path,headers,body:pr.body})});
  const d=await r.json();
  let pretty=d.body; try{pretty=JSON.stringify(JSON.parse(d.body),null,2);}catch{}
  const perf=" · "+d.ms+" ms ("+d.slo+")"+(d.slo==="slow"?" — exceeds 1s target, needs optimization":"");
  vfState[id]={resp:(d.ok?"✓ VERIFIED — HTTP ":"✗ not verified — HTTP ")+d.status+perf+"\n\n"+pretty,envId};
  await loadList(); refreshDetail();
}
async function regEnv(id,name,cand){
  const baseUrl=/^https?:\/\//.test(cand)?cand:("https://"+cand);
  await api("/api/environments",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name,baseUrl})});
  await loadEnvs(); refreshDetail();
}
window.regEnv=regEnv;
async function parityMig(id){
  const envId=$("#vfEnv")?$("#vfEnv").value:"";
  if(!envId){ pState[id]="Select an environment in Environment verification first."; return refreshDetail(); }
  const m=await (await api(`/api/migrations/${id}`)).json(); const pr=presetForMigration(m);
  const headers={...pr.headers}; delete headers.Authorization;
  pState[id]="…comparing legacy vs migrated"; refreshDetail();
  const d=await (await api(`/api/migrations/${id}/parity`,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({envId,method:pr.method,path:pr.path,headers,body:pr.body})})).json();
  let s=(d.match?"✓ MATCH — migrated response equals legacy":"✗ DIFFERENCES: "+(d.diff||[]).length)+"\n";
  s+="legacy:   HTTP "+d.legacy.status+" · "+d.legacy.ms+" ms ("+d.legacy.slo+")\n";
  s+="migrated: HTTP "+d.migrated.status+" · "+d.migrated.ms+" ms ("+d.migrated.slo+")\n";
  if((d.diff||[]).length) s+="\n"+d.diff.map(x=>" "+x.path+": legacy="+x.legacy+" | migrated="+x.migrated).join("\n");
  pState[id]=s; refreshDetail();
}
async function optimizeMig(id){
  pState[id]="…measuring and optimizing (adds a replica, re-measures)"; refreshDetail();
  const d=await (await api(`/api/migrations/${id}/optimize`,{method:"POST"})).json();
  pState[id]=d.error?("error: "+d.error):("Before "+d.beforeMs+" ms → After "+d.afterMs+" ms\n"
    +"Actions: "+(d.actions||[]).join(", ")+"\nTarget ≤"+d.maxMs+" ms: "+(d.meetsTarget?"met ✓":"still missed"));
  await loadList(); refreshDetail();
}
window.run=run;window.advance=advance;window.delMig=delMig;window.dlDoc=dlDoc;window.submitFeedback=submitFeedback;window.verifyMig=verifyMig;window.parityMig=parityMig;window.optimizeMig=optimizeMig;

// ---------- knowledge base modal ----------
$("#kbBtn").addEventListener("click", async () => {
  openModal("#kbModal");
  const { feedback } = await (await api("/api/knowledge")).json();
  const grp = { functional: [], workspace: [], api: [] };
  feedback.forEach((f) => (grp[f.scope] || grp.api).push(f));
  const sec = (title, items, note) => `<h3>${title} <span class="muted">· ${items.length}</span></h3>
    <p class="muted sm">${note}</p>${items.length ? items.map(fbHTML).join("") : '<div class="muted">None.</div>'}`;
  $("#kbBody").innerHTML =
    sec("Functional lessons (shared to all workspaces)", grp.functional,
        "Auto-applied to the same technology migration in any workspace.") +
    sec("Workspace lessons (private)", grp.workspace, "Only applied within the originating workspace.") +
    sec("API lessons (private)", grp.api, "Only applied to the same API.");
});
$("#kbClose").addEventListener("click", () => closeModal("#kbModal"));

// ---------- workspaces & target servers ----------
const WS_TIERS = [["dev","Dev"],["qa","QA"],["uat","UAT"],["prod","Prod (optional)"]];
function wsServersHTML(){
  return WS_TIERS.map(([k,label])=>`<div class="wsrv">
    <div class="wsrv-h">${label}</div>
    <div class="wsrv-row">
      <input id="ws_${k}_host" placeholder="host / IP">
      <input id="ws_${k}_port" placeholder="port" value="22" style="max-width:80px">
      <input id="ws_${k}_user" placeholder="username">
      <input id="ws_${k}_pass" type="password" placeholder="password">
    </div></div>`).join("");
}
$("#wsBtn").addEventListener("click",async()=>{
  $("#wsServers").innerHTML=wsServersHTML(); $("#wsError").textContent=""; $("#wsName").value="";
  openModal("#wsModal"); loadWsConfigs();
});
$("#wsClose").addEventListener("click",()=>closeModal("#wsModal"));
$("#wsSave").addEventListener("click",async()=>{
  const servers={};
  WS_TIERS.forEach(([k])=>{
    const host=$(`#ws_${k}_host`).value.trim();
    if(host) servers[k]={host,port:$(`#ws_${k}_port`).value.trim()||"22",
      username:$(`#ws_${k}_user`).value.trim(),password:$(`#ws_${k}_pass`).value};
  });
  const r=await api("/api/workspace-configs",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name:$("#wsName").value.trim(),servers})});
  const d=await r.json();
  if(!r.ok){ $("#wsError").textContent=d.error||"failed"; return; }
  $("#wsError").textContent=""; $("#wsName").value=""; $("#wsServers").innerHTML=wsServersHTML(); loadWsConfigs();
});
async function loadWsConfigs(){
  const d=await (await api("/api/workspace-configs")).json();
  $("#wsList").innerHTML=(d.workspaces||[]).map(w=>`<div class="wsitem">
    <div class="row-between"><b>${esc(w.name)}</b><button class="ghost" onclick="delWs('${w.id}')">Delete</button></div>
    ${Object.entries(w.servers).map(([t,s])=>`<div class="srvrow">
      <span class="muted sm">${t.toUpperCase()}: ${esc(s.username)}@${esc(s.host)}:${s.port} ${s.provisioned?"✓ provisioned":""}</span>
      <button class="ghost" onclick="provisionSrv('${w.id}','${t}')">Provision</button></div>`).join("")||'<div class="muted sm">no servers</div>'}
  </div>`).join("")||'<div class="muted">None yet.</div>';
}
async function provisionSrv(id,tier){
  const d=await (await api(`/api/workspace-configs/${id}/provision/${tier}`,{method:"POST"})).json();
  $("#wsError").textContent=(d.note||d.error||"")+" — preflight started; see Activity/logs. Full install needs ?confirm=true.";
}
window.provisionSrv=provisionSrv;
async function delWs(id){ if(!confirm("Delete this workspace config?"))return;
  await api(`/api/workspace-configs/${id}`,{method:"DELETE"}); loadWsConfigs(); }
window.delWs=delWs;

// ---------- new migration ----------
$("#newBtn").addEventListener("click", () => openModal("#modal"));
$("#cancelNew").addEventListener("click", () => closeModal("#modal"));
$("#newForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#mName").value.trim(), ws = $("#mWorkspace").value.trim(), file = $("#mFile").files[0];
  if (!name || !file) { $("#newError").textContent = "Name and file required."; return; }
  const hdrs = { "X-Name": name, "X-Filename": file.name, "X-Workspace": ws };
  if ($("#mTarget")) hdrs["X-Target-Tech"] = $("#mTarget").value;
  if ($("#mSpec") && $("#mSpec").checked) hdrs["X-Source-Type"] = "spec";
  const r = await api("/api/migrations", { method: "POST", headers: hdrs, body: file });
  if (r.ok) { const m = await r.json(); closeModal("#modal");
    $("#mName").value = ""; $("#mFile").value = ""; await loadList(); select(m.id); }
  else $("#newError").textContent = "Upload failed.";
});

function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,(c)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}

// ---------- API playground ----------
const GQL = '{"query":"query AvailableVoyages { availableVoyages { pkg { key } } }"}';
let APIS = [];   // migrated APIs, from /api/migrations

async function loadApis() {
  try {
    const { migrations } = await (await api("/api/migrations")).json();
    const seen = new Set(); APIS = [];
    migrations.forEach(m => {
      if (m.apiName && !seen.has(m.apiName)) {
        seen.add(m.apiName);
        APIS.push({ apiName: m.apiName, layer: m.layer, outcome: m.progress.outcome, id: m.id });
      }
    });
  } catch { APIS = []; }
  const el = $("#plSvc");
  if (el) el.innerHTML = APIS.map(a =>
    `<option value="${esc(a.apiName)}">${esc(a.apiName)} · ${a.layer || "?"} · ${a.outcome}</option>`).join("")
    || '<option value="">no migrated APIs yet</option>';
}
// Known GraphQL-passthrough contracts (exact names). Everything else is driven by the API's
// own generated endpoints (inventory.json), so each API shows its real operations.
function knownPreset(name) {
  const n = (name || "").toLowerCase();
  if (n === "reservation-mgr-exp-api")
    return { method: "POST", path: "/reservation-mgr-exp-api/v1/integration-tasks",
             headers: { "client_channel": "web", "Content-Type": "application/json" }, body: GQL };
  if (n === "seaware-sys-api")
    return { method: "POST", path: "/seaware-sys-api/v1/integration-tasks",
             headers: { "Content-Type": "application/json" }, body: GQL };
  if (n === "seaware-cache-proc-api")
    return { method: "POST", path: "/seaware-cache-proc-api/v1/cache",
             headers: { "operationName": "availableVoyages", "Content-Type": "application/json" }, body: GQL };
  return null;
}
async function loadOps() {
  const a = APIS.find(x => x.apiName === $("#plSvc").value);
  if (a && a.id && !a.endpoints) {
    try {
      const inv = await (await api(`/api/migrations/${a.id}/docs/inventory.json`)).json();
      a.endpoints = (inv.endpoints || []).filter(e => e.method && e.method !== "ANY");
    } catch { a.endpoints = []; }
  }
  const sel = $("#plOp");
  if (sel) {
    const known = knownPreset(a && a.apiName), eps = (a && a.endpoints) || [];
    sel.innerHTML = eps.map((e, i) => `<option value="${i}">${e.method} ${esc((e.path||"").replace(/\$\{app\.name\}/g, a.apiName))}</option>`).join("")
      || '<option value="">health check (no operations generated)</option>';
    sel.parentElement.style.display = known ? "none" : "";   // known APIs use their fixed contract
  }
  plFill();
}
const PRESETS = {
  exp:  { method:"POST", path:"/reservation-mgr-exp-api/v1/integration-tasks",
    headers:{Authorization:"Bearer e2e-secret-token-123","client_channel":"web","Content-Type":"application/json"},
    body:'{"query":"query AvailableVoyages { availableVoyages { pkg { key } } }"}' },
  sys:  { method:"POST", path:"/seaware-sys-api/v1/integration-tasks",
    headers:{Authorization:"Bearer e2e-secret-token-123","Content-Type":"application/json"},
    body:'{"query":"query AvailableVoyages { availableVoyages { pkg { key } } }"}' },
  proc: { method:"POST", path:"/seaware-cache-proc-api/v1/cache",
    headers:{"operationName":"availableVoyages","Content-Type":"application/json"},
    body:'{"query":"query AvailableVoyages { availableVoyages { pkg { key } } }"}' },
  zoho: { method:"GET", path:"/health/ready", headers:{}, body:"" },
};
function plFill(){
  const a=APIS.find(x=>x.apiName===$("#plSvc").value)||{};
  let p=knownPreset(a.apiName);
  if(!p){
    const eps=a.endpoints||[]; const idx=parseInt(($("#plOp")&&$("#plOp").value)||"0",10)||0; const ep=eps[idx];
    if(ep) p={method:ep.method, path:(ep.path||"").replace(/\$\{app\.name\}/g, a.apiName), headers:{"Content-Type":"application/json"}, body:ep.method==="GET"?"":"{\n  \n}"};
    else p={method:"GET", path:"/health/ready", headers:{}, body:""};
  }
  $("#plMethod").value=p.method; $("#plPath").value=p.path;
  $("#plHeaders").value=JSON.stringify(p.headers,null,2); $("#plBody").value=p.body; $("#plResp").textContent="—";
}
$("#playBtn").addEventListener("click",async()=>{await loadApis();await loadEnvs();openModal("#playModal");await loadOps();});
$("#playClose").addEventListener("click",()=>closeModal("#playModal"));
$("#plSvc").addEventListener("change",loadOps);
$("#plOp").addEventListener("change",plFill);
$("#plAddEnv").addEventListener("click",()=>$("#plEnvForm").classList.toggle("hidden"));
$("#envSave").addEventListener("click",async()=>{
  const name=$("#envName").value.trim(), baseUrl=$("#envUrl").value.trim(), token=$("#envToken").value.trim();
  if(!name||!baseUrl)return;
  await api("/api/environments",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({name,baseUrl,token})});
  $("#envName").value="";$("#envUrl").value="";$("#envToken").value="";
  $("#plEnvForm").classList.add("hidden"); await loadEnvs();
});
$("#plSend").addEventListener("click",async()=>{
  let headers={}; try{headers=JSON.parse($("#plHeaders").value||"{}");}
  catch{ $("#plResp").textContent="Headers must be valid JSON"; return; }
  $("#plResp").textContent="…sending";
  const r=await api("/api/proxy",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({envId:$("#plEnv").value,service:$("#plSvc").value,method:$("#plMethod").value,
      path:$("#plPath").value,headers,body:$("#plBody").value})});
  const d=await r.json();
  let pretty=d.body; try{pretty=JSON.stringify(JSON.parse(d.body),null,2);}catch{}
  $("#plResp").textContent="HTTP "+d.status+"  ·  "+d.ms+" ms ("+d.slo+", target ≤250ms / max 1000ms)\n\n"+pretty;
});

// ---------- boot ----------
(async function(){
  if(!TOKEN) return showLogin("");
  const r=await fetch("/api/migrations",{headers:{"X-Admin-Token":TOKEN}});
  if(r.ok) showApp(); else showLogin("");
})();
