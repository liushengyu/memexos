"use strict";
// Usage: "What do agents look up in the knowledge base? Did it help?"
// Layout (key numbers → trends → details, with small multiples): three key numbers on top, trend, ranking and per-project small charts in the middle,
// every question in detail at the bottom. Only real use counts, not the evaluations' own questions.
const US = {days: 30, d: null, tab: "events"};
const VERD = {helped: ["var(--ok)", "ok"], partly: ["#8fb0e8", "run"], honest_gap: ["#d2a868", "acc"], not_helped: ["var(--crit)", "crit"], unclear: ["#5d6878", ""]};
const FB_BADGE = {rejected: "ok", confirmed: "crit", to_fix: "warn", unsure: "warn", pending: ""};
VIEWS.usage = {title: T("nav.usage"),
  init(el) {
    el.innerHTML = `<div class="ph"><h1>${esc(T("nav.usage"))}<small>USAGE</small></h1><div class="q">${esc(T("us.q"))}</div>
      <div class="r"><div class="seg" id="us-days"><button data-v="7">${esc(T("us.d7"))}</button><button data-v="30" class="on">${esc(T("us.d30"))}</button><button data-v="90">${esc(T("us.d90"))}</button></div>
        <button class="btn ghost" id="us-refresh" data-tip="${esc(T("us.refresh_tip"))}">${esc(T("us.refresh"))}</button></div></div>
      <div id="us-body"></div>`;
    seg($("#us-days", el), v => { US.days = +v; loadUsage(); });
    $("#us-refresh", el).onclick = () => loadUsage(true);
    el.addEventListener("click", e => { const c = e.target.closest(".chain"); if (c && !e.target.closest("[data-path],a,.link")) { c.classList.toggle("open"); } });
  },
  enter() { loadUsage(); }
};
async function loadUsage(refresh) {
  const B = $("#us-body");
  if (!US.d || refresh) B.innerHTML = `<div class="grid3">${[0, 1, 2].map(() => `<div class="glass kpi"><div class="shimmer skel" style="width:40%"></div><div class="shimmer skel" style="width:60%;height:34px"></div><div class="shimmer skel" style="width:80%"></div></div>`).join("")}</div>`;
  const u = US.d = await api("/api/usage", {days: US.days, refresh: refresh ? 1 : 0});
  if (u.error) { B.innerHTML = `<div class="empty">${esc(u.error)}</div>`; return; }
  const vv = Object.fromEntries(u.verdicts), vtot = u.verdicts.reduce((a, [, n]) => a + n, 0) || 1;
  // small multiples: one small line per project on the same scale (questions per day)
  const days = Math.min(US.days, 45), byP = {};
  u.events.forEach(e => { const p = (e.cwd || "").split("/").filter(Boolean).pop() || "?", d = (e.session_last || "").slice(0, 10); ((byP[p] ||= {})[d] = (byP[p][d] || 0) + 1); });
  const projs = u.projects.slice(0, 6), pmax = Math.max(1, ...projs.flatMap(([p]) => Object.values(byP[p] || {})));
  const vs = Object.keys(VERD).filter(k => vv[k]);
  B.innerHTML = `<div class="grid3">
    <div class="glass kpi" data-in><div class="lb"><span>${esc(T("us.k_questions"))}</span><span class="kicker">QUERIES</span></div><div class="v"><span id="us-n"></span><small>${esc(T("us.times"))}</small></div>
      <div class="sb">${esc(list(u.by_agent.map(([k, n]) => `${k} ${n}`)))} · ${esc(T("us.k_q_sb", {s: fmt(u.n_search), o: fmt(u.n_open)}))}</div></div>
    <div class="glass kpi hero warm" data-in style="view-transition-name:vt-usage"><div class="lb"><span>${esc(T("us.k_helped"))}</span><span class="kicker">HELPED</span></div><div class="v"><span id="us-h"></span></div>
      <div class="vbar">${vs.map((k, i) => `<i style="--i:${i};width:${vv[k] / vtot * 100}%;background:${VERD[k][0]}" data-tip="${esc(T("us.v_" + k))}: ${vv[k]}"></i>`).join("")}</div>
      <div class="legend" style="margin-top:8px">${vs.map(k => `<span><i style="background:${VERD[k][0]}"></i>${esc(T("us.v_" + k))} ${vv[k]}</span>`).join("")}</div>
      <div class="sb">${esc(T("us.k_h_sb", {n: u.judged - (vv.unclear || 0)}))}</div></div>
    <div class="glass kpi" data-in><div class="lb"><span>${esc(T("us.k_gaps"))}</span><span class="kicker">GAPS</span></div><div class="v"><span id="us-g"></span><small>${esc(T("us.times"))} · ${pct(u.gaps.length / Math.max(1, u.n_recent))}</small></div>
      <div class="sb">${esc(T("us.k_g_sb", {n: fmt(u.n_no_open), t: fmt(u.touched_30d), w: fmt(u.wiki_total)}))}</div></div></div>
  <div class="grid3" style="margin-top:14px">
    <div class="glass card" data-in><h2 class="h">${esc(T("us.per_day"))} <small>PER DAY</small></h2><div class="sub">${esc(T("us.per_day_sub"))}</div><div id="us-day"></div></div>
    <div class="glass card" data-in><h2 class="h">${esc(T("us.top"))} <small>TOP PAGES</small></h2><div class="sub">${esc(T("us.top_sub"))}</div><div id="us-top"></div></div>
    <div class="glass card" data-in><h2 class="h">${esc(T("us.projects"))} <small>SMALL MULTIPLES</small></h2><div class="sub">${esc(T("us.projects_sub"))}</div>
      <div class="mults">${projs.map(([p, n]) => `<div class="mult"><div class="t"><span>${esc(p)}</span><b>${n}</b></div>${spark(fillDays(Object.entries(byP[p] || {}).map(([d, c]) => ({label: d, c})), days, ["c"]).map(x => x.c), {w: 160, h: 34, color: "#4cc7b6", dot: false, max: pmax})}</div>`).join("") || `<div class="muted small">${esc(T("c.none"))}</div>`}</div></div></div>
  <div class="glass card" style="margin-top:14px" data-in><div style="display:flex;align-items:center;gap:12px;margin-bottom:12px"><div class="seg" id="us-tabs">
      <button data-v="events" class="${US.tab === "events" ? "on" : ""}">${esc(T("us.tab_events"))}<span class="n">${u.events.length}</span></button><button data-v="gaps" class="${US.tab === "gaps" ? "on" : ""}">${esc(T("us.tab_gaps"))}<span class="n">${u.gaps.length}</span></button><button data-v="feedback" class="${US.tab === "feedback" ? "on" : ""}">${esc(T("us.tab_feedback"))}<span class="n">${u.feedback.length}</span></button></div>
      <span class="muted small" id="us-tabnote"></span></div><div id="us-list"></div></div>`;
  playIn(B);
  tick($("#us-n"), u.n_recent); tick($("#us-h"), pct(u.helped_rate)); tick($("#us-g"), u.gaps.length);
  area($("#us-day"), fillDays(u.per_day.map(([d, n]) => ({label: d, n})), days, ["n"]), {key: "n", color: "#4cc7b6", h: 190, tipX: r => { const qs = u.events.filter(e => (e.session_last || "").startsWith(r.label)).slice(0, 4); return qs.length ? "<br>" + qs.map(e => `<span class=m>· ${esc((e.question || "").slice(0, 40))}</span>`).join("<br>") : ""; }});
  const top = u.opened.slice(0, 11), nm = p => p.startsWith("raw/") ? T("type.raw") + " · " + shortRaw(p) : pageTitle(p);
  $("#us-top").appendChild(hbars(top.map(([p, n]) => [nm(p), n, p]), {onClick: (l, i) => openPage(top[i][0]), color: "linear-gradient(90deg, rgba(76,199,182,.4), #4cc7b6)"}));
  seg($("#us-tabs"), v => { US.tab = v; renderUsList(); });
  renderUsList();
}
function renderUsList() {
  const u = US.d, L = $("#us-list");
  if (US.tab === "events") {
    $("#us-tabnote").textContent = T("us.events_note");
    L.innerHTML = u.events.slice(0, 80).map((e, i) => { const v = VERD[e.verdict] || ["var(--mute)", ""];
      const calls = e.calls || [], opened = calls.filter(c => c.op === "get" || c.op === "read").map(c => c.opened || c.page).filter(Boolean), searches = calls.filter(c => c.op === "search");
      return `<div class="chain" data-in style="--i:${Math.min(i, 12)}"><div class="top"><div style="flex:1;min-width:0"><div class="qq">${esc(e.question)}</div>
        <div class="mm">${esc((e.session_last || "").slice(5, 16).replace("T", " "))} · ${esc((e.cwd || "").split("/").filter(Boolean).pop() || "")} · ${esc(e.agent)} · ${esc(T("us.chain_counts", {s: searches.length, o: opened.length}))}</div></div>
        ${e.verdict ? `<span class="badge ${v[1]}">${esc(T("us.v_" + e.verdict))}</span>` : `<span class="badge">${esc(T("us.unjudged"))}</span>`}</div>
        <div class="steps"><div class="step"><span class="k">${esc(T("us.searched"))}</span><span>${esc(searches.map(c => c.query).join("; ") || T("us.none_searched"))}</span></div>
          <div class="step"><span class="k">${esc(T("us.opened"))}</span><span>${opened.map(p => `<span class="link" data-path="${esc(p)}">${esc(pageTitle(p))}</span>`).join(T("u.sep")) || esc(T("c.none"))}</span></div>
          <div class="step"><span class="k">${esc(T("us.answered"))}</span><span>${esc((e.answer || "").slice(0, 700))}</span></div>
          ${e.verdict_reason ? `<div class="step"><span class="k">${esc(T("us.judgment"))}</span><span>${esc(e.verdict_reason)}</span></div>` : ""}</div></div>`; }).join("") || `<div class="empty">${esc(T("us.no_events"))}</div>`;
  } else if (US.tab === "gaps") {
    $("#us-tabnote").textContent = T("us.gaps_note");
    L.innerHTML = u.gaps.map((g, i) => `<div class="chain" data-in style="--i:${i}"><div class="qq">${esc(g.question)}</div><div class="mm">${esc(g.agent)}</div></div>`).join("") || `<div class="empty">${esc(T("c.none"))}</div>`;
  } else {
    $("#us-tabnote").textContent = T("us.feedback_note");
    L.innerHTML = u.feedback.slice().reverse().map((f, i) => `<div class="chain" data-in style="--i:${Math.min(i, 12)}"><div class="top"><div style="flex:1;min-width:0"><span class="link" data-path="${esc(f.page)}">${esc(pageTitle(f.page))}</span>
      <div class="qq" style="margin-top:4px">${esc(f.why)}</div>${f.summary ? `<div class="mm" style="font-family:var(--f-ui)">${esc(T("us.verified"))}: ${esc(f.summary.slice(0, 260))}</div>` : ""}</div>
      <span class="badge ${FB_BADGE[f.status] ?? "warn"}">${esc(T("us.fb_" + (f.status || "pending")))}</span></div></div>`).join("") || `<div class="empty">${esc(T("c.none"))}</div>`;
  }
  playIn(L);
}
