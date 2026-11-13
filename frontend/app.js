const $ = (s) => document.querySelector(s);
let TOKEN = localStorage.getItem("mt_token") || "";
let selected = null;
let pollTimer = null;
const docsCache = {};   // id -> [{name}]
const fbCache = {};     // id -> [feedback]

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts, headers: { "X-Admin-Token": TOKEN, ...(opts.headers || {}) },
  });
  if (r.status === 401) { showLogin("Token rejected."); throw new Error("unauthorized"); }
  return r;
}

// ---------- auth ----------
function showLogin(msg) {
  $("#app").classList.add("hidden"); $("#login").classList.remove("hidden");
  $("#loginError").textContent = msg || "";
}
function showApp() { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); loadList(); }
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
window.run=run;window.advance=advance;window.delMig=delMig;window.dlDoc=dlDoc;window.submitFeedback=submitFeedback;

// ---------- knowledge base modal ----------
$("#kbBtn").addEventListener("click", async () => {
  $("#kbModal").classList.remove("hidden");
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
$("#kbClose").addEventListener("click", () => $("#kbModal").classList.add("hidden"));

// ---------- new migration ----------
$("#newBtn").addEventListener("click", () => $("#modal").classList.remove("hidden"));
$("#cancelNew").addEventListener("click", () => $("#modal").classList.add("hidden"));
$("#newForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#mName").value.trim(), ws = $("#mWorkspace").value.trim(), file = $("#mFile").files[0];
  if (!name || !file) { $("#newError").textContent = "Name and file required."; return; }
  const r = await api("/api/migrations", { method: "POST",
    headers: { "X-Name": name, "X-Filename": file.name, "X-Workspace": ws }, body: file });
  if (r.ok) { const m = await r.json(); $("#modal").classList.add("hidden");
    $("#mName").value = ""; $("#mFile").value = ""; await loadList(); select(m.id); }
  else $("#newError").textContent = "Upload failed.";
});

function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,(c)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}

// ---------- boot ----------
(async function(){
  if(!TOKEN) return showLogin("");
  const r=await fetch("/api/migrations",{headers:{"X-Admin-Token":TOKEN}});
  if(r.ok) showApp(); else showLogin("");
})();
