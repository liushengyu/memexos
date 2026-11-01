"use strict";
// Knowledge: "What is in the knowledge base? How much do we know about a topic?" (domain knowledge; what agents remember about you is on the Profile page)
// Layout (faceted search with list and graph side by side): filters on the left, results in the middle, a preview on the right; search and view switch on top.
// Views: cards / list / graph (the 3D globe moved to the centre) / activity (daily growth and recent commits). Cards spring to their new place when filters change.
const MEM = {o: null, f: {type: "", domain: "", q: "", sort: "in"}, view: "cards", pages: [], total: 0, search: null, sel: null, hoverT: 0};
const KTYPES = ["", "topic", "concept", "entity", "source"];
const LAYER_OF = {topic: 0, concept: 1, entity: 1, source: 2, raw: 3};
VIEWS.knowledge = {title: T("nav.knowledge"), fit: true,
  init(el) {
    el.innerHTML = `<div class="mem">
      <div class="ph"><h1><svg class="pi kn" viewBox="0 0 14 14"><path d="M7 1.6 12.4 4.5 7 7.4 1.6 4.5Z" fill="none"/><path d="M1.6 7 7 9.9 12.4 7 M1.6 9.5 7 12.4 12.4 9.5" fill="none"/></svg>${esc(T("nav.knowledge"))}<small>KNOWLEDGE</small></h1><div class="q">${esc(T("kn.q"))}</div>
        ${BOOT.profile ? `<span class="other" data-go-other="profile" data-tip="${esc(T("kn.other_tip"))}">${esc(T("kn.other"))} <b>${esc(T("nav.profile"))}</b> ›</span>` : ""}
        <div class="r"><div class="seg" id="mm-view"><button data-v="cards" class="on">${esc(T("kn.v_cards"))}</button><button data-v="list">${esc(T("kn.v_list"))}</button><button data-v="graph">${esc(T("kn.v_graph"))}</button><button data-v="feed">${esc(T("kn.v_feed"))}</button></div></div></div>
      <div class="tb"><input class="inp" id="mm-q" placeholder="${esc(T("kn.filter"))}" style="width:300px">
        <input class="inp" id="mm-s" placeholder="${esc(T("kn.search"))}" style="width:360px"><span class="grow"></span>
        <span class="muted small">${esc(T("kn.sort"))}</span><div class="seg" id="mm-sort"><button data-v="in" class="on">${esc(T("kn.s_in"))}</button><button data-v="used">${esc(T("kn.s_used"))}</button><button data-v="new">${esc(T("kn.s_new"))}</button><button data-v="name">${esc(T("kn.s_name"))}</button></div></div>
      <div class="body"><div class="facets glass" id="mm-fac" style="view-transition-name:vt-knowledge"></div><div class="results" id="mm-res"></div><div class="preview glass" id="mm-pv"></div></div></div>`;
    seg($("#mm-view", el), v => setView(v));
    MEM.sortSeg = seg($("#mm-sort", el), v => { MEM.f.sort = v; loadPages(); });
    let tq; $("#mm-q", el).oninput = e => { clearTimeout(tq); tq = setTimeout(() => { MEM.f.q = e.target.value.trim(); MEM.search = null; loadPages(); }, 180); };
    $("#mm-s", el).onkeydown = e => { if (e.key === "Enter") semantic(e.target.value.trim()); };
    $("#mm-fac", el).addEventListener("click", e => {
      const t = e.target.closest("[data-type]"); if (t) { MEM.f.type = t.dataset.type; MEM.search = null; renderFacets(); loadPages(); return; }
      const d = e.target.closest("[data-dom]"); if (d) { MEM.f.domain = MEM.f.domain === d.dataset.dom ? "" : d.dataset.dom; MEM.search = null; renderFacets(); loadPages(); } });
    const res = $("#mm-res", el);
    res.addEventListener("pointerover", e => { const c = e.target.closest("[data-pv]"); if (!c) return; clearTimeout(MEM.hoverT); MEM.hoverT = setTimeout(() => preview(c.dataset.pv), 120); });
    res.addEventListener("pointermove", e => { const c = e.target.closest(".pcard"); if (!c) return; const r = c.getBoundingClientRect(); c.style.setProperty("--mx", (e.clientX - r.left) + "px"); c.style.setProperty("--my", (e.clientY - r.top) + "px"); });
    $("#mm-pv", el).addEventListener("click", e => { const a = e.target.closest("[data-act]"); if (!a) return;
      if (a.dataset.act === "open") openPage(MEM.sel); else if (a.dataset.act === "globe") go("overview", {focus: MEM.sel}); else if (a.dataset.act === "ego") preview(a.dataset.id); });
  },
  async enter(el, params) {
    playIn(el);
    if (!MEM.o) { MEM.o = await api("/api/overview"); }
    this.update(el, params, true);
  },
  update(el, params, first) {
    let changed = false;
    if (params.domain != null && params.domain !== MEM.f.domain) { MEM.f.domain = params.domain; changed = true; }
    if (params.search) { $("#mm-s").value = params.search; semantic(params.search); history.replaceState(null, "", "#/knowledge"); renderFacets(); return; }
    if (params.domain != null) history.replaceState(null, "", "#/knowledge");
    renderFacets(); if (changed || first || !MEM.pages.length) loadPages();
    if (MEM.view === "graph") setView("graph", true);
  }
};
function renderFacets() {
  const o = MEM.o; if (!o) return;
  const from = sinceDays(7), week = o.growth.filter(g => g.day >= from).reduce((a, g) => a + g.wiki, 0);
  const tcount = Object.fromEntries(o.wiki_types), d1 = o.domains1, mx = Math.max(1, ...d1.map(d => d[1]));
  const F = $("#mm-fac"), fresh = !F.dataset.done; F.dataset.done = 1;
  F.innerHTML = `<div class="stats4">${[[fmt(o.raw), T("kn.st_raw")], [fmt(o.wiki), T("kn.st_wiki")], [o.domains2.filter(([x]) => !x.endsWith("*")).length, T("kn.st_domains")], ["+" + week, T("kn.st_week")]].map(([v, l], i) => `<div class="st" data-in style="--i:${i}"><b>${v}</b><span>${esc(l)}</span></div>`).join("")}</div>
    <h4>${esc(T("kn.f_type"))}<span>TYPE</span></h4><div class="fchips">${KTYPES.map(t => `<button class="fchip${MEM.f.type === t ? " on" : ""}" data-type="${t}">${t ? `<i class="tdot" style="background:${TYPE_COL[t]}"></i>` : ""}${esc(t ? typesName(t) : T("c.all"))}${t ? `<span class="n">${tcount[t] || 0}</span>` : ""}</button>`).join("")}</div>
    <h4>${esc(T("kn.f_domain"))}<span>DOMAIN</span></h4><div class="hbars fdom">${d1.map(([d, n], i) => `<div class="hb click${MEM.f.domain === d ? " on" : ""}" data-dom="${esc(d)}" data-tip="<b>${esc(d)}</b> · ${esc(T("kn.dom_raw", {n}))}<br><span class=m>${esc(list(o.domains2.filter(([x]) => x.startsWith(d + "/")).map(([x, m]) => `${x.split("/")[1]} ${m}`)))}</span>"><div class="l">${esc(d)}</div><div class="tr"><div class="f" style="--i:${i};width:${(n / mx * 100).toFixed(1)}%"></div></div><div class="n">${n}</div></div>`).join("") || `<div class="muted small">${esc(T("kn.no_catalog"))}</div>`}</div>
    <h4>${esc(T("kn.f_evidence"))}<span>EVIDENCE</span></h4><div id="mm-ev"></div>
    <h4>${esc(T("kn.f_content"))}<span>CONTENT</span></h4><div id="mm-ct"></div>`;
  $("#mm-ev").appendChild(hbars(o.evidence, {color: "linear-gradient(90deg, rgba(210,168,104,.45), #d2a868)"}));
  $("#mm-ct").appendChild(hbars(o.content_types, {color: "linear-gradient(90deg, rgba(141,151,168,.45), #8d97a8)"}));
  if (fresh) playIn(F);
}
async function loadPages() {
  const r = await api("/api/pages", MEM.f); MEM.pages = r.pages; MEM.total = r.total; renderResults(true);
}
async function semantic(q) {
  if (!q) { MEM.search = null; return renderResults(true); }
  if (MEM.view !== "list" && MEM.view !== "cards") setView("cards");
  $("#mm-res").innerHTML = `<div class="rh">${esc(T("kn.searching", {q}))}</div>${[90, 70, 80, 60].map(w => `<div class="shimmer skel" style="width:${w}%;height:16px;margin:14px 0"></div>`).join("")}`;
  const r = await api("/api/search", {q}); MEM.search = {q, hits: r.hits || [], degraded: r.degraded || [], seconds: r.seconds}; renderResults(true);
}
function pcard(p, i) {
  const col = TYPE_COL[p.type] || "#7b8697";
  return `<div class="pcard" data-key="${esc(p.path)}" data-pv="${esc(p.path)}" data-path="${esc(p.path)}" style="--tc:${col};--i:${Math.min(i, 14)}" data-in>
    <div class="n">${esc(p.name)}</div><div class="m">${esc(typeName(p.type))} · ${esc(p.created || "")}</div><div class="s">${esc(plain(p.summary))}</div>
    <div class="f"><span data-tip="${esc(T("kn.c_in"))}">↘ <b>${p.in}</b></span><span data-tip="${esc(T("kn.c_raws"))}">◇ <b>${p.raws}</b></span>${p.used ? `<span data-tip="${esc(T("kn.c_used"))}" class="blue">◉ <b>${p.used}</b></span>` : ""}</div></div>`;
}
function renderResults(anim) {
  const R = $("#mm-res"); if (MEM.view === "graph" || MEM.view === "feed") return;
  const head = MEM.search ? `<div class="rh">${esc(T("kn.results", {q: MEM.search.q, n: MEM.search.hits.length}))}${MEM.search.degraded.map(x => " · ⚠️ " + esc(x)).join("")}<span class="link" data-clear>${esc(T("kn.clear_search"))}</span></div>` :
    `<div class="rh"><span class="mono">${fmt(MEM.total)}</span> ${esc(T("kn.pages"))}${MEM.total > 500 ? " " + esc(T("kn.first500")) : ""}${MEM.f.domain ? ` · ${esc(T("kn.f_domain"))} <span class="badge acc">${esc(MEM.f.domain)} ✕</span>` : ""}${MEM.f.type ? ` · ${esc(typesName(MEM.f.type))}` : ""}${MEM.f.q ? ` · ${esc(T("kn.containing", {q: MEM.f.q}))}` : ""}</div>`;
  let body;
  if (MEM.search) body = MEM.search.hits.length ? `<table class="t">${MEM.search.hits.map((h, i) => `<tr class="click" data-path="${esc(h.path)}" data-pv="${esc(h.path)}" data-in style="--i:${i}"><td class="num muted" style="width:30px">${i + 1}</td><td><b>${esc(h.title)}</b> <span class="muted small">${esc(h.kind)}</span>${h.compiled_page ? ` <span class="small blue">→ ${esc(h.compiled_page)}</span>` : ""}<br><span class="small ink2">${esc((h.snippet || "").replace(/@@[^@]*@@/g, "").slice(0, 200))}</span>${h.classification ? `<br><span class="small muted">${esc(h.classification)}</span>` : ""}</td></tr>`).join("")}</table>` :
    `<div class="empty">${esc(T("kn.nothing"))}</div>`;
  else if (MEM.view === "list") body = `<table class="t"><tr><th>${esc(T("kn.h_page"))}</th><th>${esc(T("kn.f_type"))}</th><th class="r">${esc(T("kn.c_raws"))}</th><th class="r">${esc(T("kn.s_in"))}</th><th class="r">${esc(T("kn.s_used"))}</th><th>${esc(T("kn.h_created"))}</th></tr>${MEM.pages.map((p, i) => `<tr class="click" data-path="${esc(p.path)}" data-pv="${esc(p.path)}" data-in style="--i:${i}"><td><span class="tdot" style="background:${TYPE_COL[p.type]}"></span> <b>${esc(p.name)}</b><br><span class="small ink2">${esc(plain(p.summary).slice(0, 120))}</span></td><td class="muted">${esc(typeName(p.type))}</td><td class="r mono">${p.raws}</td><td class="r mono">${p.in}</td><td class="r mono">${p.used || ""}</td><td class="muted small">${esc(p.created)}</td></tr>`).join("")}</table>`;
  else body = MEM.pages.length ? `<div class="cards">${MEM.pages.map(pcard).join("")}</div>` : `<div class="empty">${esc(T("kn.empty"))}</div>`;
  const grid = R.querySelector(".cards");
  if (!anim && grid && MEM.view === "cards" && !MEM.search) { flip(R, ".pcard", () => { R.innerHTML = head + body; $$("[data-in]", R).forEach(e => e.classList.add("in")); }); }
  else { R.innerHTML = head + body; R.scrollTop = 0; playIn(R); }
  const c = R.querySelector("[data-clear]"); if (c) c.onclick = () => { MEM.search = null; $("#mm-s").value = ""; renderResults(true); };
  const b = R.querySelector(".rh .badge.acc"); if (b) { b.style.cursor = "pointer"; b.onclick = () => { MEM.f.domain = ""; renderFacets(); loadPages(); }; }
  if (!MEM.sel && MEM.pages[0]) preview(MEM.pages[0].path);
}
// preview: summary, relation counts and a small graph centred on this page (click a dot to move there)
async function preview(path) {
  if (MEM.sel === path && $("#mm-pv").dataset.p === path) return; MEM.sel = path;
  $$("#mm-res .pcard.sel").forEach(c => c.classList.remove("sel")); const c = $(`#mm-res .pcard[data-key="${CSS.escape(path)}"]`); if (c) c.classList.add("sel");
  await GRAPH.init(); const n = GRAPH.byId.get(path), P = $("#mm-pv"); P.dataset.p = path;
  if (!n) { const p = MEM.pages.find(x => x.path === path); P.innerHTML = `<div data-in>${typeBadge(p ? p.type : "page")}</div><div class="pv-name" data-in>${esc(p ? p.name : pageTitle(path))}</div><div class="pv-sum" data-in>${esc(plain(p ? p.summary : ""))}</div><div data-in style="margin-top:14px"><button class="btn sm primary lens" data-act="open">${esc(T("c.open_full"))}</button></div>`; playIn(P); return; }
  const nb = neighbours(n.i), cnt = {}; nb.forEach(x => { const t = core.nodes[x.j].type; cnt[t] = (cnt[t] || 0) + 1; });
  P.innerHTML = `<div data-in>${typeBadge(n.type)} <span class="muted small">${esc(T("graph.layer", {name: LAYER_NAME()[n.layer]}))} · ${esc(n.domain || T("graph.unclassified"))}</span></div>
    <div class="pv-name" data-in>${esc(n.name)}</div><div class="pv-sum" data-in>${esc(plain(n.summary || ""))}</div>
    <div class="pv-stats" data-in><div><b>${n.in}</b><span>${esc(T("kn.s_in"))}</span></div><div><b>${n.out}</b><span>${esc(T("kn.out"))}</span></div><div><b>${nb.length}</b><span>${esc(T("kn.direct"))}</span></div></div>
    <div data-in style="display:flex;gap:8px"><button class="btn sm primary lens" data-act="open">${esc(T("c.open_full"))}</button><button class="btn sm ghost" data-act="globe">◎ ${esc(T("drawer.on_globe"))}</button></div>
    <div data-in style="margin-top:12px">${egoSvg(n, nb)}</div>
    <div class="legend" data-in>${Object.entries(cnt).map(([t, c2]) => `<span><i style="background:${TYPE_COL[t]}"></i>${esc(typeName(t))} ${c2}</span>`).join("")}</div>`;
  playIn(P);
}
function egoSvg(n, nb) {
  const W = 300, H = 240, cx = W / 2, cy = H / 2, show = nb.slice().sort((a, b) => core.adj[b.j].length - core.adj[a.j].length).slice(0, 26);
  let s = `<svg class="ego" viewBox="0 0 ${W} ${H}">`;
  show.forEach((x, i) => { const m = core.nodes[x.j], a = -Math.PI / 2 + i / show.length * Math.PI * 2, r = 70 + (i % 2) * 26, px = cx + Math.cos(a) * r, py = cy + Math.sin(a) * r * 0.86;
    s += `<line x1="${cx}" y1="${cy}" x2="${px}" y2="${py}" stroke="${REL_COL[x.rel] || "#5b8cff"}" stroke-opacity=".5" pathLength="1" style="animation-delay:${i * 25}ms"/>`;
    s += `<circle cx="${px}" cy="${py}" r="${3 + Math.min(4, Math.sqrt(core.adj[x.j].length) * .5)}" fill="${TYPE_COL[m.type]}" data-act="ego" data-id="${esc(m.id)}" data-tip="${esc(m.name)} · ${esc(relName(x.rel))}<br><span class=h>${esc(T("kn.ego_click"))}</span>" style="cursor:pointer;animation-delay:${200 + i * 25}ms"/>`;
    if (i < 10) s += `<text x="${px + (Math.cos(a) >= 0 ? 7 : -7)}" y="${py + 3.5}" text-anchor="${Math.cos(a) >= 0 ? "start" : "end"}" style="animation-delay:${300 + i * 25}ms">${esc(m.name.slice(0, LANG === "zh" ? 8 : 14))}</text>`; });
  s += `<circle cx="${cx}" cy="${cy}" r="9" fill="${TYPE_COL[n.type]}" style="filter:drop-shadow(0 0 8px ${TYPE_COL[n.type]})"/></svg>`;
  return show.length ? s : `<div class="muted small">${esc(T("graph.no_relations"))}</div>`;
}
function setView(v, again) {
  const prev = MEM.view; MEM.view = v; const R = $("#mm-res"), M = $(".mem");
  M.classList.toggle("wide", v === "graph" || v === "feed");
  if (v === "graph") {
    R.innerHTML = `<div class="gstage glass" data-in></div>`; playIn(R);
    GRAPH.init().then(() => GRAPH.mount($(".gstage", R), {insetL: 0, insetR: 0, expL: 0, expR: 0, onExplore: () => {}}));
    if (MEM.sel) GRAPH.whenReady().then(() => { if (MEM.view === "graph") GRAPH.selectById(MEM.sel); });
    return;
  }
  if (v === "feed") return renderFeed();
  if (prev !== v || again) renderResults(true);
}
async function renderFeed() {
  const o = MEM.o, R = $("#mm-res");
  const colOf = s => /^auto-ingest|^Merge|^correct/.test(s) ? "var(--blue)" : /^fix/.test(s) ? "var(--warn)" : "var(--mute)";
  R.innerHTML = `<div class="glass card" data-in><h2 class="h">${esc(T("kn.growth"))} <small>GROWTH · 30D</small></h2><div class="sub">${esc(T("kn.growth_sub"))}</div><div id="mm-growth"></div></div>
    <div class="grid3" style="grid-template-columns:1fr 1fr;margin-top:14px">
      <div class="glass card" data-in><h2 class="h">${esc(T("type.topics"))} <small>${o.topics.length}</small></h2><div class="sub">${esc(T("kn.topics_sub"))}</div>${o.topics.map(t => `<div class="tl-it" style="padding-left:0;cursor:pointer" data-path="${esc(t.path)}"><b>${esc(t.name)}</b> <span class="muted small">${esc(T("kn.linked_by", {n: t.in}))}</span><br><span class="ink2 small">${esc(plain(t.summary).slice(0, 120))}</span></div>`).join("") || `<div class="muted small">${esc(T("c.none"))}</div>`}</div>
      <div class="glass card" data-in><h2 class="h">${esc(T("kn.recent"))} <small>COMMITS</small></h2><div class="sub">${esc(T("kn.recent_sub"))}</div><div class="tline">${o.recent.slice(0, 40).map(r => `<div class="tl-it" style="--c:${colOf(r.subject)}"><div class="w">${esc(r.when)}</div>${esc(r.subject.slice(0, 160))}</div>`).join("")}</div></div></div>`;
  playIn(R);
  columns($("#mm-growth"), fillDays(o.growth.map(g => ({label: g.day, raw: g.raw, wiki: g.wiki})), 30, ["raw", "wiki"]),
    [{key: "raw", name: T("kn.st_raw"), color: "#8d97a8"}, {key: "wiki", name: T("kn.st_wiki"), color: "#4f84ff"}], {stacked: false, h: 200});
}
