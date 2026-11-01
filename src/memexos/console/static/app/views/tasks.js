"use strict";
// Tasks: "How is the pipeline doing? Which batch is stuck, and why?"
// Layout (a run page with a waterfall timeline): the nine stages in a row on top; runs on the left; on the right the selected run's
// timeline, the problems its review found, and its events. A running step is a moving stripe that grows live; a newly finished run drops in at the top.
const TK = {runs: [], seen: new Set(), sel: null, stage: null, tab: "runs", pipe: null, hl: null, now: Date.now() / 1000};
// which steps (or marks) of a run belong to each stage of the ring
const STAGE_STEPS = {intake: ["collect", "admit", "capture", "visual", "research", "investigate"], classify: ["classify"], compile: ["write", "rework"],
  gate1: [], selfcheck: ["selfcheck"], gate2: ["gate2"], publish: [], retrieve: [], feedback: ["usage", "verify", "triage"]};
const RING = ["intake", "classify", "compile", "gate1", "selfcheck", "gate2", "publish", "retrieve", "feedback"];
const passesStage = (u, id) => id === "gate1" ? u.marks.some(m => m.type === "gate1.result") : id === "publish" ? u.marks.some(m => m.type === "publish") :
  u.segs.some(s => STAGE_STEPS[id].includes(s.step));
VIEWS.tasks = {title: T("nav.tasks"), fit: true,
  init(el) {
    el.innerHTML = `<div class="tasks">
      <div class="ph"><h1>${esc(T("nav.tasks"))}<small>PIPELINE</small></h1><div class="q">${esc(T("tk.q"))}</div>
        <div class="r ctrl"><button class="btn ghost" id="tk-pause">${esc(T("act.pause"))}</button><button class="btn lens" id="tk-ingest">${esc(T("act.ingest"))}</button><button class="btn lens" id="tk-intake">${esc(T("act.intake"))}</button></div></div>
      <div class="stages" id="tk-stages" style="view-transition-name:vt-tasks"></div>
      <div class="today" id="tk-today"></div>
      <div class="body">
        <div class="glass clip" style="display:flex;flex-direction:column;min-height:0"><div style="padding:10px 10px 6px;display:flex;align-items:center;gap:8px"><div class="seg" id="tk-tabs"><button data-v="runs" class="on">${esc(T("tk.tab_runs"))}<span class="n" id="tk-n-runs">0</span></button><button data-v="pending">${esc(T("tk.tab_pending"))}<span class="n" id="tk-n-pending">0</span></button><button data-v="held">${esc(T("tk.tab_held"))}<span class="n" id="tk-n-held">0</span></button></div>
          <span class="muted small" id="tk-filter"></span></div><div class="runs fade-end" id="tk-list"></div></div>
        <div class="rdetail glass" id="tk-detail"></div></div></div>`;
    seg($("#tk-tabs", el), v => { TK.tab = v; renderTkList(true); });
    $("#tk-stages", el).addEventListener("click", e => { const s = e.target.closest(".stg"); if (!s) return; TK.stage = TK.stage === s.dataset.id ? null : s.dataset.id; renderStages(); renderTkList(true); });
    $("#tk-list", el).addEventListener("click", e => {
      const ab = e.target.closest("[data-abandon]"); if (ab) { e.stopPropagation(); doAction(e, "abandon", {branch: ab.dataset.abandon}, {title: T("tk.abandon_title"), confirm: esc(T("tk.abandon_confirm", {b: ab.dataset.abandon}))}).then(loadPipe); return; }
      const r = e.target.closest(".rrow[data-id]"); if (r) selectRun(r.dataset.id); });
    TK.hl = hlList($("#tk-list", el), ".rrow");
    $("#tk-pause", el).onclick = e => LIVE.data && LIVE.data.paused ? doAction(e, "resume", {}, {title: T("act.resume")}) : doAction(e, "pause", {}, {title: T("act.pause"), confirm: esc(T("act.pause_confirm"))});
    $("#tk-ingest", el).onclick = e => doAction(e, "ingest", {}, {title: T("act.ingest"), confirm: esc(T("act.ingest_confirm"))});
    $("#tk-intake", el).onclick = e => doAction(e, "intake", {}, {title: T("act.intake"), confirm: esc(T("act.intake_confirm"))});
    onLive(L => { if (el.hidden) return; renderStages(); renderToday(L); $("#tk-pause").textContent = L.paused ? T("act.resume") : T("act.pause"); });
    setInterval(() => { if (!el.hidden) { loadRuns7(); const r = TK.runs.find(x => x.id === TK.sel); if (r && r.status === "running") loadDetail(TK.sel, true); } }, 10000);
  },
  async enter(el, params) {
    playIn(el); await Promise.all([loadRuns7(true), loadPipe()]);
    this.update(el, params);
  },
  update(el, params) {
    if (params.stage) { TK.stage = params.stage; renderStages(); renderTkList(true); const c = $(`.stg[data-id="${params.stage}"]`); if (c) { c.classList.remove("flash"); void c.offsetWidth; c.classList.add("flash"); } }
    if (params.run) { if (TK.tab !== "runs") { TK.tab = "runs"; renderTkList(true); } selectRun(params.run); }
    if (params.stage || params.run) history.replaceState(null, "", "#/tasks");
    if (!TK.sel && TK.runs[0]) selectRun((TK.runs.find(r => r.lane === "write") || TK.runs[0]).id);
  }
};
async function loadRuns7(first) {
  const d = await api("/api/runs", {hours: 24 * 7}); const fresh = TK.runs.length && d.runs.filter(r => !TK.seen.has(r.id + r.status));
  TK.runs = d.runs.filter(r => r.lane !== "other" || r.segs.length); TK.now = d.now; TK.runs.forEach(r => TK.seen.add(r.id + r.status));
  tick($("#tk-n-runs"), TK.runs.length); renderStages();
  if (TK.tab === "runs") renderTkList(first, fresh ? new Set(fresh.map(r => r.id)) : null);
}
async function loadPipe() { TK.pipe = await api("/api/pipeline"); tick($("#tk-n-pending"), TK.pipe.pending.length); tick($("#tk-n-held"), TK.pipe.held.length); if (TK.tab !== "runs") renderTkList(false); if (LIVE.data) renderToday(LIVE.data); }
function renderStages() {
  const L = LIVE.data; if (!L || !$("#tk-stages")) return;
  const day0 = new Date(); day0.setHours(0, 0, 0, 0); const t0 = day0 / 1000, today = TK.runs.filter(r => (r.t1 || 1e12) >= t0);
  const cnt = id => {
    if (id === "retrieve") return [L.metrics.usage_24h, T("tk.u_24h")];
    if (id === "gate1") { const m = today.flatMap(r => r.marks.filter(x => x.type === "gate1.result" && x.t >= t0)); return [m.filter(x => x.ok).length, T("tk.u_passed")]; }
    if (id === "publish") return [today.reduce((a, r) => a + r.marks.filter(x => x.type === "publish" && x.t >= t0).length, 0), T("tk.u_times")];
    if (id === "gate2") { const g = today.flatMap(r => r.segs.filter(s => s.step === "gate2" && s.t1 && s.t1 >= t0)); return [g.length, T("tk.u_g2", {p: g.filter(s => s.data && s.data.result === "passed").length})]; }
    return [today.reduce((a, r) => a + r.segs.filter(s => STAGE_STEPS[id].includes(s.step) && (s.t1 || 1e12) >= t0).length, 0), T("tk.u_times")]; };
  const box = $("#tk-stages"), fresh = !box.childElementCount;
  box.innerHTML = L.stages.map((s, i) => { const [c, u] = cnt(s.id), det = stageDetail(s);
    return `<div class="stg${s.active ? " act" : ""}${TK.stage === s.id ? " on" : ""}" data-id="${s.id}" data-in style="--i:${i}" data-tip="<b>${esc(stageName(s.id))}</b> ${STAGE_EN[s.id]}<br>${s.active ? `<span class=h>● ${esc(T("c.running"))}</span> ` + esc(det) : "○ " + esc(T("c.idle"))}<br><span class=m>${esc(T("tk.stage_click"))}</span>">
      <div class="no"><span>${STAGE_NO[s.id]}</span><span>${s.active ? '<i class="dot run" style="width:6px;height:6px"></i>' : ""}</span></div><div class="nm">${esc(stageName(s.id))}</div>
      <div class="c">${c == null ? "—" : c}<small>${esc(u)}</small></div><div class="st">${s.active ? esc(det || T("c.running")) : esc(T("c.idle"))}</div></div>`; }).join("");
  if (fresh) playIn(box); else $$("[data-in]", box).forEach(e => e.removeAttribute("data-in"));
}
function renderToday(L) {
  $("#tk-today").innerHTML = (L.done || []).map(x => `<span>${esc(x)}</span>`).join("") +
    (L.schedule ? `<span><b>${esc(T("tk.schedule"))}</b> ${esc(T("tk.schedule_line", {i: L.schedule.intake_at || "—", l: L.schedule.lint_at || "—"}))}</span>` : "");
}
function runRow(r, i, fresh) {
  const st = runStatus(r), end = r.t1 || TK.now, span = (end - r.t0) || 1;
  const pb = r.segs.map(s => `<i style="width:${Math.max(1.5, ((s.t1 || TK.now) - s.t0) / span * 100)}%;background:${segCol(s)}" class="${s.t1 ? "" : "stripes"}"></i>`).join("");
  const g2 = g2Summary(r);
  return `<div class="rrow${fresh && fresh.has(r.id) ? " fresh" : ""}" data-id="${esc(r.id)}" data-key="${esc(r.id)}" data-in="left" style="--i:${Math.min(i, 14)}">
    <div class="ic ${st[1]}" data-tip="${esc(st[2])}${r.note ? ": " + esc(r.note) : ""}">${st[0]}</div>
    <div style="min-width:0"><div class="tt">${esc(runTitle(r))}</div><div class="m"><span class="badge" style="padding:0 6px">${esc(jobName(r.job))}</span>${mdhm(r.t0)}${g2 ? ` · ${esc(g2)}` : ""}</div></div>
    <div class="r">${dur(end - r.t0)}<br><span class="muted">${r.cost ? money(r.cost) : ""}</span></div><div class="pb">${pb}</div></div>`;
}
function renderTkList(anim, fresh) {
  const box = $("#tk-list"); if (!box) return; let html = "";
  $("#tk-filter").innerHTML = TK.stage && TK.tab === "runs" ? `${esc(T("tk.filter", {s: stageName(TK.stage)}))} <span class="link" id="tk-clear">${esc(T("c.clear"))}</span>` : "";
  if (TK.tab === "runs") {
    const xs = TK.runs.filter(r => !TK.stage || passesStage(r, TK.stage));
    html = xs.map((r, i) => runRow(r, i, fresh)).join("") || `<div class="empty">${esc(T("tk.no_runs"))}${TK.stage === "retrieve" ? "<br>" + esc(T("tk.retrieve_note")) : ""}</div>`;
  } else if (TK.tab === "pending") {
    const p = TK.pipe ? TK.pipe.pending : [];
    html = p.map((x, i) => `<div class="rrow" data-path="${esc(x)}" data-in="left" style="--i:${Math.min(i, 14)};grid-template-columns:24px 1fr"><div class="ic">◇</div><div style="min-width:0"><div class="tt">${esc(shortRaw(x))}</div><div class="m mono">${esc(x)}</div></div></div>`).join("") || `<div class="empty">${esc(T("tk.no_pending"))}</div>`;
  } else {
    const h = TK.pipe ? TK.pipe.held : [];
    html = h.map((x, i) => `<div class="rrow" data-in="left" style="--i:${i};grid-template-columns:24px 1fr auto"><div class="ic warn">‖</div><div style="min-width:0"><div class="tt">${esc(x.reason || x.subject)}</div><div class="m mono">${esc(x.branch)} · ${esc(x.when)}${x.raws.length ? " · " + esc(x.raws.map(shortRaw).join(T("u.sep"))) : ""}</div></div><div><button class="btn xs danger" data-abandon="${esc(x.branch)}">${esc(T("tk.abandon"))}</button></div></div>`).join("") || `<div class="empty">${esc(T("tk.no_held"))}<br><span class="small">${esc(T("tk.held_note"))}</span></div>`;
  }
  const doIt = () => { box.querySelectorAll(":scope > :not(.hl-bar):not(.hl-sel)").forEach(n => n.remove()); box.insertAdjacentHTML("beforeend", anim ? html : html.replace(/ data-in="left"/g, "")); };
  if (!anim && TK.tab === "runs") flip(box, ".rrow", doIt); else doIt();
  if (anim) { box.scrollTop = 0; playIn(box); }
  const c = $("#tk-clear"); if (c) c.onclick = () => { TK.stage = null; renderStages(); renderTkList(true); };
  const sel = TK.sel && $(`#tk-list .rrow[data-id="${CSS.escape(TK.sel)}"]`); TK.hl.select(sel || null);
}
function selectRun(id) { TK.sel = id; const r = $(`#tk-list .rrow[data-id="${CSS.escape(id)}"]`); TK.hl.select(r); if (r) r.scrollIntoView({block: "nearest", behavior: "smooth"}); loadDetail(id); }
async function loadDetail(id, quiet) {
  const D = $("#tk-detail");
  if (!quiet) D.innerHTML = `<div class="shimmer skel" style="width:40%;height:22px"></div><div class="shimmer skel" style="width:70%"></div>${[0, 1, 2, 3, 4].map(i => `<div class="shimmer skel" style="width:${30 + i * 12}%;margin-left:${132 + i * 30}px"></div>`).join("")}`;
  const d = await api("/api/run", {id}); if (TK.sel !== id) return;
  if (d.error) { D.innerHTML = `<div class="empty">${esc(T("tk.not_found"))}</div>`; return; }
  renderDetailRun(d, quiet);
}
const VERDICT_COL = {SUPPORTED: "var(--ok)", MARKED: "#8fb0e8", PARTIAL: "var(--warn)", UNSUPPORTED: "var(--crit)", CONTRADICTED: "#ff3d6e"};
function renderDetailRun(d, quiet) {
  const r = d.run, D = $("#tk-detail"), now = Date.now() / 1000, end = r.t1 || now, span = Math.max(30, end - r.t0), st = runStatus(r);
  const X = t => ((t - r.t0) / span * 100).toFixed(2) + "%";
  const g2cost = r.gate2.reduce((a, g) => a + (g.cost_usd || 0), 0);
  // time axis: a tick spacing that suits the total length
  const stepM = [1, 2, 5, 10, 15, 30, 60, 120].find(m => span / 60 / m <= 6) || 240;
  let axis = ""; for (let m = 0; m * 60 <= span; m += stepM) axis += `<span style="left:${X(r.t0 + m * 60)}">${m ? T("tk.plus_min", {n: m}) : hm(r.t0)}</span>`;
  const rows = r.segs.map((s, i) => {
    const w = ((s.t1 || now) - s.t0) / span * 100, res = s.data && s.data.result, ok = res === "passed", bad = res === "failed" || res === "incomplete" || res === "error";
    const bits = [dur((s.t1 || now) - s.t0)];
    if (s.data && s.data.cost_usd) bits.push(money(s.data.cost_usd));
    if (s.data && s.data.model) bits.push(s.data.model);
    if (res) bits.push(T("tk.res_" + res) === "tk.res_" + res ? res : T("tk.res_" + res));
    if (!s.t1) bits.push(T("run.ongoing"));
    const marks = r.marks.filter(m => m.type !== "publish" && m.t >= s.t0 - 1 && m.t <= (s.t1 || now) + 1)
      .map(m => `<div class="wf-mk ${m.ok ? "ok" : m.ok === false ? "warn" : ""}" style="left:${X(m.t)};--i:${i}" data-tip="${hm(m.t)} · ${esc(m.text)}">${m.type === "gate1.result" ? "1" : m.ok ? "✓" : "!"}</div>`).join("");
    const left = parseFloat(X(s.t0)), right = left + w > 72;
    return `<div class="wf-row"><div class="lab"><i style="background:${segCol(s)}"></i>${esc(segName(s))}</div><div class="wf-track">
      <div class="wf-bar${s.t1 ? "" : " open"}" style="--i:${i};left:${X(s.t0)};width:${Math.max(0.5, w)}%;background:${segCol(s)}" data-tip="<b>${esc(segName(s))}</b><br>${hm(s.t0)} → ${s.t1 ? hm(s.t1) : esc(T("run.ongoing"))} · ${dur((s.t1 || now) - s.t0)}${s.data && s.data.why ? "<br><span class=m>" + esc(s.data.why) + "</span>" : ""}"></div>
      <div class="wf-txt" style="--i:${i};${right ? `right:calc(${100 - left}% + 14px)` : `left:calc(${left + w}% + 14px)`}">${esc(bits.join(" · "))}</div>
      ${s.step === "gate2" && (ok || bad) && s.t1 ? `<div class="wf-mk ${ok ? "ok" : "crit"}" style="left:${X(s.t1)};--i:${i}" data-tip="${esc(segName(s))}: ${esc(T("tk.res_" + res))}">${ok ? "✓" : "✕"}</div>` : ""}${marks}</div></div>`; }).join("");
  const pub = r.marks.find(m => m.type === "publish");
  const rep = d.gate2.map(g => { const v = g.verdicts || {}, tot = Object.values(v).reduce((a, b) => a + b, 0) || 1;
    const vs = Object.keys(VERDICT_COL).filter(k => v[k]);
    return `<div class="glass card" style="margin-top:12px;padding:14px 16px" data-in><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b style="font-weight:500">${esc(T(g.mode === "ingest" ? "run.first_review" : "run.recheck"))}</b>
      ${g.passed == null ? `<span class="badge">… ${esc(T("tk.res_incomplete"))}</span>` : g.passed ? `<span class="badge ok">✓ ${esc(T("tk.res_passed"))}</span>` : `<span class="badge crit">✕ ${esc(T("tk.res_failed"))}</span>`}
      <span class="muted small">${esc([g.model, g.seconds ? dur(g.seconds) : "", g.cost ? money(g.cost) : "", g.claims ? T("tk.claims", {n: g.claims}) : ""].filter(Boolean).join(" · "))}</span></div>
      ${vs.length ? `<div class="vbar">${vs.map((k2, i) => `<i style="--i:${i};width:${v[k2] / tot * 100}%;background:${VERDICT_COL[k2]}" data-tip="${esc(T("verdict." + k2))}: ${v[k2]}"></i>`).join("")}</div>
        <div class="legend" style="margin-top:6px">${vs.map(k2 => `<span><i style="background:${VERDICT_COL[k2]}"></i>${esc(T("verdict." + k2))} ${v[k2]}</span>`).join("")}</div>` : ""}
      <div class="issues">${g.problems.map(p => `<div class="is ${p.severity === "high" ? "err" : "wrn"}"><b>${esc(p.label)}</b> · ${esc(p.page)}${p.wiki ? `<br>“${esc(p.wiki)}”` : ""}${p.source ? `<br><span class="muted">${esc(T("tk.source_says"))}: ${esc(p.source)}</span>` : ""}<br><span class="muted">${esc(p.why)}</span></div>`).join("") ||
        g.errors.map(e => `<div class="is err">✕ ${esc(e)}</div>`).join("")}</div>
      ${g.warnings.length ? `<details class="fold" style="margin-top:8px"><summary>${esc(T("tk.warnings", {n: g.n_warnings || g.warnings.length}))}</summary><div class="in-fold issues">${g.warnings.map(w => `<div class="is wrn">${esc(w)}</div>`).join("")}</div></details>` : ""}</div>`; }).join("");
  const filesLabel = r.job === "intake" ? T("tk.k_items") : T("tk.k_files");
  const nfiles = r.job === "intake" ? r.items : r.files.length;
  const html = `<div data-in style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="badge">${esc(jobName(r.job))}</span><span class="badge ${st[1]}">${st[0]} ${esc(st[2])}</span><span class="mono tiny muted">${esc(r.branch || r.run)}</span></div>
    <div class="rd-title" data-in>${esc(runTitle(r))}</div>
    ${r.note ? `<div class="idt-why" data-in style="border-color:var(--warn)">${esc(r.note)}</div>` : ""}
    <div class="rd-kpis" data-in><div><span>${esc(T("tk.k_start"))}</span><b>${mdhm(r.t0)}</b></div><div><span>${esc(T("tk.k_time"))}</span><b>${dur(end - r.t0)}</b></div>${g2cost ? `<div><span>${esc(T("tk.k_review_cost"))}</span><b>${money(g2cost)}</b></div>` : ""}<div><span>${esc(T("tk.k_total"))}</span><b class="acc">${money(r.cost)}</b></div>${nfiles ? `<div><span>${esc(filesLabel)}</span><b>${nfiles}</b></div>` : ""}${pub ? `<div><span>${esc(T("tk.k_published"))}</span><b>${hm(pub.t)}</b></div>` : ""}</div>
    <div class="wf" data-in><div class="wf-axis">${axis}</div>${rows || `<div class="muted small" style="padding:8px 0">${esc(T("tk.no_steps"))}</div>`}${!r.t1 ? `<div class="wf-now" style="left:calc(132px + (100% - 132px) * ${((now - r.t0) / span).toFixed(4)})"></div>` : ""}</div>
    ${r.files.length ? `<div data-in class="small" style="margin:8px 0 2px">${r.files.map(x => `<span class="link" data-path="${esc(x)}">${esc(shortRaw(x))}</span>`).join("　")}</div>` : ""}
    ${r.summary ? `<details class="fold" open data-in style="margin-top:12px"><summary>${esc(T("tk.summary"))}</summary><div class="in-fold md" style="font-size:13px">${md(r.summary)}</div></details>` : ""}
    ${rep ? `<h2 class="h" style="margin-top:18px" data-in>${esc(T("tk.review_found"))} <small>GATE-2</small></h2>${rep}` : ""}
    <h2 class="h" style="margin:18px 0 8px" data-in>${esc(T("tk.events"))} <small>EVENTS</small></h2><div class="logbox" data-in>${d.log.map(l => `<div class="${l.kind}"><b>${hm(l.t)}</b>${esc(l.text)}</div>`).join("") || `<span class="muted">${esc(T("c.none"))}</span>`}</div>`;
  // timed refreshes do not replay the entrance; only the running step keeps moving
  D.classList.toggle("still", !!quiet); D.innerHTML = quiet ? html.replace(/ data-in(="\w+")?/g, "") : html;
  if (!quiet) { D.scrollTop = 0; playIn(D); }
}
