const $ = (s) => document.querySelector(s);
let TOKEN = localStorage.getItem("mt_token") || "";
let selected = null;
let pollTimer = null;

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: { "X-Admin-Token": TOKEN, ...(opts.headers || {}) },
  });
  if (r.status === 401) { showLogin("Token rejected."); throw new Error("unauthorized"); }
  return r;
}

// ---------- auth ----------
function showLogin(msg) {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#loginError").textContent = msg || "";
}
function showApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  loadList();
}
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

// ---------- list ----------
async function loadList() {
  const r = await api("/api/migrations");
  const { migrations } = await r.json();
  const total = migrations.length;
  const done = migrations.filter((m) => m.progress.done).length;
  const avg = total ? Math.round(migrations.reduce((a, m) => a + m.progress.overallPercent, 0) / total) : 0;
  $("#headStats").innerHTML =
    `<div class="s"><b>${total}</b> APIs</div>
     <div class="s"><b>${done}</b> deployed</div>
     <div class="s"><b>${avg}%</b> avg progress</div>`;
  $("#list").innerHTML = migrations.map(cardHTML).join("") ||
    `<div class="muted">No migrations yet. Click “New migration”.</div>`;
  migrations.forEach((m) => {
    $(`#card-${m.id}`).addEventListener("click", () => select(m.id));
  });
  if (!selected && migrations[0]) select(migrations[0].id);
}

function cardHTML(m) {
  const p = m.progress;
  return `<div class="mcard ${m.id === selected ? "sel" : ""}" id="card-${m.id}">
    <div class="top"><span class="name">${esc(m.name)}</span>
      <span class="badge ${m.layer || ""}">${m.layer || m.technology || "?"}</span></div>
    <div class="bar"><i style="width:${p.overallPercent}%"></i></div>
    <div class="meta"><span>${p.majorPhase}</span><span>${p.overallPercent}%</span></div>
  </div>`;
}

// ---------- detail ----------
function select(id) {
  selected = id;
  document.querySelectorAll(".mcard").forEach((e) => e.classList.remove("sel"));
  const c = $(`#card-${id}`); if (c) c.classList.add("sel");
  refreshDetail();
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(refreshDetail, 1500);
}

async function refreshDetail() {
  if (!selected) return;
  const r = await api(`/api/migrations/${selected}`);
  if (!r.ok) return;
  renderDetail(await r.json());
}

function renderDetail(m) {
  const p = m.progress;
  const busy = m.busy;
  const canRun = (ph) => !busy;
  const d = $("#detail");
  d.innerHTML = `
    <div class="d-head">
      <div>
        <div class="d-title">${esc(m.name)}</div>
        <div class="d-sub">${m.apiName ? esc(m.apiName) + " · " : ""}${m.layer || "—"} layer ·
          ${m.technology} ${m.estimateDays ? "· ~" + m.estimateDays + " AI-days" : ""}</div>
      </div>
      <div class="ring" style="--p:${p.overallPercent}">
        <div><b>${p.overallPercent}%</b><span>${p.done ? "complete" : "in progress"}</span></div>
      </div>
    </div>
    <div class="nownext">
      <div class="nn"><div class="l">Current step</div>
        <div class="v">${p.currentStep ? esc(p.currentStep.step) : "—"}
          <small>${p.currentStep ? "· " + esc(p.currentStep.phase) : ""}</small></div></div>
      <div class="nn"><div class="l">Next step</div>
        <div class="v">${p.nextStep ? esc(p.nextStep.step) : "—"}
          <small>${p.nextStep ? "· " + esc(p.nextStep.phase) : ""}</small></div></div>
    </div>
    <div class="actions">
      <button class="primary" ${canRun() ? "" : "disabled"} onclick="run('${m.id}','autorun')">
        ${busy ? "Running…" : "▶ Run documents + scaffold"}</button>
      <button ${canRun() ? "" : "disabled"} onclick="run('${m.id}','test')">Run tests</button>
      <button ${canRun() ? "" : "disabled"} onclick="run('${m.id}','deploy')">Deploy</button>
      <button class="ghost" onclick="delMig('${m.id}')">Delete</button>
    </div>
    ${m.phases.map((ph) => phaseHTML(m.id, ph)).join("")}
    <div class="log">${(m.log || []).slice(-60).map(logHTML).join("") || '<div class="ln">No activity yet.</div>'}</div>
  `;
  const lg = d.querySelector(".log"); if (lg) lg.scrollTop = lg.scrollHeight;
}

function phaseHTML(mid, ph) {
  return `<div class="phase">
    <div class="ph"><span class="stat ${ph.status}"></span>
      <span class="pt">${ph.title}</span>
      <span class="pp">${ph.percent}%${ph.mode === "manual" ? " · manual" : ""}</span></div>
    <div class="steps">
      ${ph.steps.map((s) => stepHTML(mid, ph, s)).join("")}
    </div></div>`;
}

function stepHTML(mid, ph, s) {
  const act = s.status === "action_required"
    ? `<button onclick="advance('${mid}','${ph.key}','${s.key}')">Mark done</button>` : "";
  return `<div class="step"><span class="stat ${s.status}"></span>
    <span>${s.title}</span>
    <span class="st ${s.status}">${s.status.replace("_", " ")}</span>
    ${s.detail ? `<span class="sd" title="${esc(s.detail)}">${esc(s.detail)}</span>` : ""}
    ${act}</div>`;
}

function logHTML(l) {
  return `<div class="ln ${l.level}">${l.t.slice(11, 19)}  ${esc(l.line)}</div>`;
}

// ---------- actions ----------
async function run(id, phase) {
  await api(`/api/migrations/${id}/run/${phase}`, { method: "POST" });
  refreshDetail();
}
async function advance(id, phase, step) {
  await api(`/api/migrations/${id}/advance`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phase, step, status: "done" }),
  });
  refreshDetail();
}
async function delMig(id) {
  if (!confirm("Delete this migration record?")) return;
  await api(`/api/migrations/${id}`, { method: "DELETE" });
  selected = null; $("#detail").innerHTML = '<div class="empty">Select a migration.</div>';
  loadList();
}
window.run = run; window.advance = advance; window.delMig = delMig;

// ---------- new migration ----------
$("#newBtn").addEventListener("click", () => $("#modal").classList.remove("hidden"));
$("#cancelNew").addEventListener("click", () => $("#modal").classList.add("hidden"));
$("#newForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#mName").value.trim();
  const file = $("#mFile").files[0];
  if (!name || !file) { $("#newError").textContent = "Name and file required."; return; }
  const r = await api("/api/migrations", {
    method: "POST",
    headers: { "X-Name": name, "X-Filename": file.name },
    body: file,
  });
  if (r.ok) {
    const m = await r.json();
    $("#modal").classList.add("hidden");
    $("#mName").value = ""; $("#mFile").value = "";
    await loadList(); select(m.id);
  } else $("#newError").textContent = "Upload failed.";
});

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// ---------- boot ----------
(async function () {
  if (!TOKEN) return showLogin("");
  const r = await fetch("/api/migrations", { headers: { "X-Admin-Token": TOKEN } });
  if (r.ok) showApp(); else showLogin("");
})();
