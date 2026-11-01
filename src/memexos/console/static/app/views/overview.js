"use strict";
// Overview: "Is the system all right? What is it doing? What is waiting for me?"
// Layout (a mission-control main screen with ambient screens): a top line for now and next; the 3D knowledge globe in the middle;
// six vital signs on the left (number + two-week trend), live events and recent questions on the right; a band of recent runs at the bottom.
const OV = {growth: [], quality: null, usage: null, runs: null, hours: 24, seen: new Set(), firstLog: true};
const costOf = d => Object.entries(d).filter(([k]) => k !== "day").reduce((a, [, v]) => a + (v || 0), 0);
const VITALS = [
  {k: "wiki", go: "knowledge", vt: "vt-knowledge", col: "#4f84ff",
    v: L => L.metrics.wiki, spark: () => ovDays("growth", "wiki"),
    sb: () => { const d = ovDays("growth", "wiki"); return T("ov.v_wiki_sb", {d: d[d.length - 1] || 0, w: d.slice(-7).reduce((a, b) => a + b, 0)}); }},
  {k: "pending", go: "tasks", vt: "vt-tasks", col: "#8d97a8", unit: true,
    v: L => L.metrics.pending, spark: () => ovDays("growth", "raw"),
    sb: L => L.metrics.pending ? T("ov.v_pending_sb", {n: L.metrics.pending}) : T("ov.v_pending_empty")},
  {k: "gate2", go: "quality", vt: "vt-quality", col: "#d5deea",
    v: L => { const n = L.metrics.gate2_pass + L.metrics.gate2_fail; return n ? pct(L.metrics.gate2_pass / n) : "—"; },
    cls: L => { const n = L.metrics.gate2_pass + L.metrics.gate2_fail; return n && L.metrics.gate2_pass / n < 0.5 ? "warn" : ""; },
    spark: () => (OV.quality ? fillDays(OV.quality.gate2_days.map(d => ({label: d.day, p: d.pass, f: d.fail})), 14, ["p", "f"]).map(d => d.p + d.f ? d.p / (d.p + d.f) : null).filter(v => v != null) : []),
    sb: L => T("ov.v_gate2_sb", {p: L.metrics.gate2_pass, n: L.metrics.gate2_pass + L.metrics.gate2_fail})},
  {k: "models", go: "quality", col: "#f0a35e",
    v: L => { const b = L.backends.filter(x => x.available); return b.length ? `${b.filter(x => !x.exhausted).length}/${b.length}` : "—"; },
    cls: L => { const b = L.backends.filter(x => x.available); return !b.length || b.every(x => x.exhausted) ? "crit" : b.some(x => x.exhausted) ? "warn" : ""; },
    bullet: L => { const b = L.backends.filter(x => x.available), ok = b.filter(x => !x.exhausted).length; return bullet(ok, Math.max(1, b.length), {color: ok < b.length ? "var(--warn)" : "var(--blue-mark)"}); },
    sb: L => { const ex = L.backends.filter(x => x.available && x.exhausted); return ex.length ? T("ov.v_models_out", {names: list(ex.map(x => x.name))}) : T("ov.v_models_ok"); }},
  {k: "cost", go: "quality", col: "#e7c792",
    v: L => money(L.metrics.cost_today), spark: () => (OV.quality ? fillDays(OV.quality.cost_days.map(d => ({label: d.day, c: costOf(d)})), 14, ["c"]).map(d => d.c) : []),
    sb: () => { if (!OV.quality) return ""; const d = fillDays(OV.quality.cost_days.map(d => ({label: d.day, c: costOf(d)})), 7, ["c"]); return T("ov.v_cost_sb", {c: money(d.reduce((a, x) => a + x.c, 0) / 7)}); }},
  {k: "usage", go: "usage", vt: "vt-usage", col: "#4cc7b6", unit: true,
    v: L => L.metrics.usage_24h, spark: () => (OV.usage ? fillDays(OV.usage.per_day.map(([d, n]) => ({label: d, n})), 14, ["n"]).map(d => d.n) : []),
    sb: L => T("ov.v_usage_sb", {h: pct(L.metrics.helped), n: L.metrics.usage_30d})},
];
function ovDays(src, key) { return fillDays((OV[src] || []).map(g => ({label: g.day, [key]: g[key]})), 14, [key]).map(d => d[key]); }
VIEWS.overview = {title: T("nav.overview"), fit: true,
  init(el) {
    el.innerHTML = `<div class="ov">
      <div class="mission glass" data-in>
        <div class="m-now" data-tip=""><i class="dot" id="m-dot"></i><div style="min-width:0"><div class="kicker">${kick("NOW", T("ov.now"))}</div><div class="m-head" id="m-head">${esc(T("c.loading"))}</div></div></div>
        <div class="m-sep"></div>
        <div class="m-next"><div class="kicker">${kick("NEXT", T("ov.next"))}</div><div class="x" id="m-next">—</div></div>
        <div class="m-sep"></div>
        <div class="m-today"><div class="kicker">${kick("TODAY", T("ov.today"))}</div><div class="x" id="m-today">—</div></div>
        <button class="btn primary lens mag" id="m-ibx" style="view-transition-name:vt-inbox">⚑ <b id="m-ibx-n">0</b> ${esc(T("top.inbox_waiting"))} →</button>
      </div>
      <div class="mid" id="ov-mid">
        <aside class="wing wl" id="ov-vitals">${VITALS.map((x, i) => `<div class="vital glass hov" data-in="left" style="--i:${i}${x.vt ? `;view-transition-name:${x.vt}` : ""}" data-go="${x.go}" data-k="${x.k}" data-tip="${esc(T("ov.v_" + x.k + "_tip"))}">
          <div class="lb"><span>${esc(T("ov.v_" + x.k))}</span><span class="kicker">${esc(T("ov.v_" + x.k + "_en"))}</span></div><div class="v"><span class="vv">—</span>${x.unit ? `<small>${esc(T("ov.v_" + x.k + "_unit"))}</small>` : ""}</div><div class="sp"></div><div class="sb"></div></div>`).join("")}</aside>
        <aside class="wing wr">
          <div class="feed glass" data-in="right"><div class="hd"><h2 class="h">${esc(T("ov.live"))} <small>LIVE</small></h2><span class="dot run" data-tip="${esc(T("ov.live_tip"))}"></span></div><div class="list" id="ov-feed"></div></div>
          <div class="asks glass" data-in="right"><div class="hd"><h2 class="h">${esc(T("ov.asks"))} <small>QUERIES</small></h2><span class="link small" data-go="usage">${esc(T("c.all"))} ›</span></div><div class="list" id="ov-asks"></div></div>
        </aside>
      </div>
      <div class="band glass" data-in>
        <div class="bh"><h2 class="h">${esc(T("ov.band"))} <small>RUNS</small></h2><div class="seg" id="ov-range"><button data-v="6">${esc(T("ov.h6"))}</button><button data-v="24" class="on">${esc(T("ov.h24"))}</button><button data-v="72">${esc(T("ov.h72"))}</button></div>
          <div class="legend">${[["collect", "--s-intake"], ["classify", "--s-classify"], ["write", "--s-write"], ["selfcheck", "--s-check"], ["gate2", "--s-gate2"], ["rework", "--s-rework"]].map(([n, c]) => `<span><i style="background:var(${c})"></i>${esc(stepName(n))}</span>`).join("")}
            <span><b class="okc">✓</b> ${esc(T("ov.l_pass"))}</span><span><b class="critc">✕</b> ${esc(T("ov.l_fail"))}</span><span><b class="acc">◆</b> ${esc(T("ov.l_publish"))}</span></div></div>
        <div class="tl" id="ov-tl"></div></div></div>`;
    el.addEventListener("click", e => { const g = e.target.closest("[data-go]"); if (g && !e.target.closest("[data-path]")) go(g.dataset.go); });
    $("#m-ibx", el).onclick = () => go("inbox");
    seg($("#ov-range", el), v => { OV.hours = +v; loadBand(); });
    const tl = $("#ov-tl", el);
    tl.addEventListener("click", e => { const r = e.target.closest(".run"); if (r && r.dataset.id) go("tasks", {run: r.dataset.id}); });
    tl.addEventListener("pointerover", e => tl.classList.toggle("hovering", !!e.target.closest(".run")));
    tl.addEventListener("pointerleave", () => tl.classList.remove("hovering"));
    new ResizeObserver(() => drawBand()).observe(tl);
    onLive(L => { if (!el.hidden) renderOverview(L); });
    const slow = async () => {
      const [o, q, u] = await Promise.all([api("/api/overview"), api("/api/quality"), api("/api/usage", {days: 30})]);
      OV.growth = o.growth || []; OV.quality = q; OV.usage = u; if (LIVE.data) renderOverview(LIVE.data, true);
    };
    slow(); setInterval(() => !el.hidden && slow(), 120000);
    loadBand(); setInterval(() => !el.hidden && loadBand(), 20000);
    GRAPH.init();
  },
  enter(el, params) {
    GRAPH.mount($("#ov-mid", el), {insetL: 284 + 16, insetR: 322 + 16, expL: 0, expR: 0, onExplore: on => $(".ov", el).classList.toggle("explore", on)});
    playIn(el);
    if (LIVE.data) renderOverview(LIVE.data, true);
    this.update(el, params);
  },
  update(el, params) {
    if (params.focus) { GRAPH.whenReady().then(() => GRAPH.focusById(params.focus)); history.replaceState(null, "", "#/overview"); }
  }
};
async function loadBand() { OV.runs = await api("/api/runs", {hours: OV.hours}); drawBand(); }
function renderOverview(L, all) {
  const s = sysState(L);
  $("#m-dot").className = "dot " + s.cls; $("#m-head").textContent = L.state + (L.paused ? " · " + T("top.paused") : "");
  $(".m-now").dataset.tip = (L.doing || []).map(esc).join("<br>") || esc(T("ov.nothing"));
  $("#m-next").textContent = L.metrics.pending ? T("ov.next_pending", {n: L.metrics.pending}) : T("ov.next_idle", {at: (L.schedule || {}).intake_at || "—"});
  $("#m-today").innerHTML = esc(T("ov.today_line", {n: L.metrics.published_today, c: money(L.metrics.cost_today)})) +
    (L.metrics.gate2_pass + L.metrics.gate2_fail ? " · " + esc(T("ov.today_g2", {p: L.metrics.gate2_pass, n: L.metrics.gate2_pass + L.metrics.gate2_fail})) : "");
  const n = (L.inbox || {}).decide || 0; tick($("#m-ibx-n"), n);
  // vital signs
  for (const x of VITALS) {
    const c = $(`.vital[data-k="${x.k}"]`); if (!c) continue;
    tick($(".vv", c), x.v(L)); c.classList.remove("warn", "crit"); const cl = x.cls && x.cls(L); if (cl) c.classList.add(cl);
    $(".sb", c).innerHTML = esc(x.sb(L)).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    if (x.bullet) { const b = x.bullet(L); if (c.dataset.b !== b) { c.dataset.b = b; $(".sp", c).innerHTML = b; } }
    else if (all || !c.dataset.sp) { const v = x.spark(); if (v.length) { c.dataset.sp = 1; $(".sp", c).innerHTML = spark(v, {w: 104, h: 30, color: x.col}); } }
  }
  // live events: a new one drops in at the top and flashes champagne
  const feed = $("#ov-feed"), rows = L.log.slice().reverse();
  feed.innerHTML = rows.slice(0, 16).map(l => { const key = l.t + l.type + l.text, fresh = !OV.firstLog && !OV.seen.has(key); OV.seen.add(key);
    const ic = {ok: "✓", warn: "!", alert: "✕"}[l.kind] || "·";
    return `<div class="ev ${l.kind}${fresh ? " new" : ""}" data-tip="${esc(mdhm(l.t))} · ${esc(jobName(l.job))}<br>${esc(l.text)}"><span class="t">${hm(l.t)}</span><span class="ic">${ic}</span><span class="x">${esc(l.text)}</span></div>`; }).join("") ||
    `<div class="muted small">${esc(T("ov.no_events"))}</div>`;
  if (OV.firstLog) { OV.firstLog = false; $$(".ev", feed).forEach((e, i) => { e.style.setProperty("--i", i); e.setAttribute("data-in", "right"); }); playIn(feed, 200); }
  $("#ov-asks").innerHTML = (L.queries || []).slice(0, 7).map(q => `<div class="ask" data-go="usage" data-tip="${esc(q.q)}"><div class="m"><b>${esc(q.project)}</b><span>${esc(q.agent)}</span><span style="margin-left:auto">${esc(q.when)}</span></div><div class="x">${esc(q.q)}</div></div>`).join("") || `<div class="muted small">${esc(T("ov.no_asks"))}</div>`;
  if (!$("#ov-asks").dataset.hl) { $("#ov-asks").dataset.hl = 1; hlList($("#ov-asks"), ".ask"); hlList($("#ov-feed"), ".ev"); }
}
// the band: time runs left to right (now at the right end); two lanes: collecting, and writing (compile / fix / inbox / health check); each run coloured by step
function drawBand() {
  const box = $("#ov-tl"); if (!box || !OV.runs || !box.clientWidth) return;
  const W = box.clientWidth, H = Math.max(60, box.clientHeight), now = OV.runs.now, span = OV.hours * 3600, t0 = now - span, padL = 58, padR = 40;
  const X = t => padL + (t - t0) / span * (W - padL - padR);
  const axisY = H - 13, lanes = {collect: {name: T("ov.lane_collect"), y: 10, h: 14}, write: {name: T("ov.lane_write"), y: Math.max(40, axisY - 30), h: 20}};
  let s = `<svg viewBox="0 0 ${W} ${H}">`;
  const step = OV.hours <= 6 ? 3600 : OV.hours <= 24 ? 3 * 3600 : 12 * 3600, first = Math.ceil(t0 / step) * step;
  for (let t = first; t < now; t += step) { const x = X(t), d = new Date(t * 1000);
    s += `<line x1="${x}" x2="${x}" y1="2" y2="${axisY}" stroke="var(--grid)" stroke-dasharray="2 4"/><text x="${x}" y="${H - 1}" text-anchor="middle">${OV.hours > 24 ? `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}h` : pad2(d.getHours()) + ":00"}</text>`; }
  Object.values(lanes).forEach(l => { s += `<text class="lane-l" x="0" y="${l.y + l.h / 2 + 4}">${esc(l.name)}</text><line x1="${padL}" x2="${W - padR}" y1="${l.y + l.h / 2}" y2="${l.y + l.h / 2}" stroke="var(--line)"/>`; });
  let k = 0;
  for (const u of OV.runs.runs) {
    const lane = lanes[u.lane]; if (!lane || (u.t1 || now) < t0) continue;
    let g = `<g class="run" data-id="${esc(u.id)}" data-tip="${esc(runTip(u))}">`;
    const rows = [];
    for (const sg of u.segs) {
      const a = Math.max(sg.t0, t0), b = sg.t1 || now; if (b < t0) continue;
      let y = lane.y, h = lane.h;
      if (sg.step === "admit") { let ri = rows.findIndex(e => e <= sg.t0); if (ri < 0) { ri = rows.length; rows.push(0); } rows[ri] = b; h = 5; y = lane.y + Math.min(ri, 2) * 6; }
      const x0 = X(a), w = Math.max(2, X(b) - x0);
      g += `<rect class="seg" style="animation-delay:${k * 40}ms" x="${x0}" y="${y}" width="${w}" height="${h}" rx="${Math.min(3, w / 2)}" fill="${segCol(sg)}"${sg.t1 ? "" : ' opacity=".9"'}/>`;
      if (!sg.t1) g += `<rect x="${x0}" y="${y}" width="${w}" height="${h}" rx="3" fill="url(#ovstripe)"/>`;
      const res = sg.data && sg.data.result;
      if (sg.step === "gate2" && sg.t1 && (res === "passed" || res === "failed")) { const ok = res === "passed", x = X(b);
        g += `<g class="mk" style="animation-delay:${k * 40 + 360}ms"><circle cx="${x}" cy="${lane.y - 4}" r="6" fill="${ok ? "var(--ok)" : "var(--crit)"}"/><text x="${x}" y="${lane.y - 0.6}" text-anchor="middle" style="fill:#06080c;font-weight:700;font-size:8.5px">${ok ? "✓" : "✕"}</text></g>`; }
    }
    for (const m of u.marks.filter(m => m.type === "publish")) { const x = X(m.t); if (x >= padL) g += `<path class="mk" style="animation-delay:${k * 40 + 300}ms" d="M${x},${lane.y - 7} l4,4 l-4,4 l-4,-4 Z" fill="var(--accent)"/>`; }
    s += g + "</g>"; k++;
  }
  const xn = X(now);
  s += `<defs><pattern id="ovstripe" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="10" fill="rgba(255,255,255,.28)"><animate attributeName="x" from="0" to="10" dur=".8s" repeatCount="indefinite"/></rect></pattern></defs>`;
  s += `<line class="now" x1="${xn}" x2="${xn}" y1="0" y2="${axisY}"/><circle class="nowdot" cx="${xn}" cy="${axisY}" r="3.5"/><text x="${xn + 6}" y="${axisY - 2}" style="fill:var(--accent)">${esc(T("ov.now_mark"))}</text>`;
  box.classList.toggle("still", !!OV.drawn && OV.drawnH === OV.hours); OV.drawn = true; OV.drawnH = OV.hours;
  box.innerHTML = s + "</svg>";
}
