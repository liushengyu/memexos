"use strict";
// The knowledge graph as a layered 3D globe that can be mounted on different pages.
// Every dot is a real page; every line is a real [[link]] or a citation of a raw source.
// Shells from the inside out follow the distillation layers: topic overviews (core) → concepts / entities → source pages → raw sources.
// The outer ring is the nine pipeline stages: particles flow along it, and running stages glow and send beams into the globe.
// Click a dot = select it (the globe stops and shows its relations and a card); click empty space = deselect; double-click = expand around it.
const GC = {blue: "#5b8cff", blueHi: "#9db8ff", silver: "#dfe7f1", accent: "#e7c792", ink: "#eef2f7", ink2: "#b9c3d1", muted: "#7b8697", warn: "#f0a35e", crit: "#ff6b6b", ok: "#5fd4a2"};
const MONO = cssv("--f-num") || "monospace", UIF = cssv("--f-ui") || "sans-serif", DISP = cssv("--f-disp") || "serif";
const monoFont = (px, w = 400) => `${w} ${px}px ${MONO}`;
const uiFont = (px, w = 400) => `${w} ${px}px ${UIF}`;
function glowSprite(color, size = 64) {
  const c = document.createElement("canvas"); c.width = c.height = size; const g = c.getContext("2d");
  const r = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  r.addColorStop(0, rgba(color, 1)); r.addColorStop(0.18, rgba(color, 0.55)); r.addColorStop(0.5, rgba(color, 0.12)); r.addColorStop(1, rgba(color, 0));
  g.fillStyle = r; g.fillRect(0, 0, size, size); return c;
}
const SPR = {blue: glowSprite(GC.blue), accent: glowSprite(GC.accent), white: glowSprite("#f6f9ff"), silver: glowSprite(GC.silver)};
const REL_COL = {contains: "#f2f5f9", related: "#4f7dff", mentions: "#4cc7b6", basis: "#d2a868", cites: "#8d97a8"};
const relName = r => T("rel." + r);
const SHELL = [0.34, 0.62, 0.84, 1.0];
const LAYER_NAME = () => [T("layer.0"), T("layer.1"), T("layer.2"), T("layer.3")];
// clockwise: the upper half holds the more abstract (entities, topics, concepts), the lower half the evidence (source pages, raw sources)
const GROUPS = ["entity", "topic", "concept", "source", "raw"];
const UPPER = new Set(["entity", "topic", "concept"]);
const TSPR = Object.fromEntries(Object.entries(TYPE_COL).map(([k, v]) => [k, glowSprite(v)]));
const STAGE_NO = {intake: "01", classify: "02", compile: "03", gate1: "04", selfcheck: "05", gate2: "06", publish: "07", retrieve: "08", feedback: "09"};
const STAGE_EN = {intake: "INTAKE", classify: "CLASSIFY", compile: "COMPILE", gate1: "GATE-1", selfcheck: "SELF-CHECK", gate2: "GATE-2", publish: "PUBLISH", retrieve: "RETRIEVE", feedback: "FEEDBACK"};
const STAGE_DEFAULT = Object.keys(STAGE_NO).map(id => ({id, active: false, jobs: []}));
const HUD = {drag: null, rotY: 0, rotX: 0.38, spin: 0.0016, hover: null, hoverStage: null, hoverDom: null, spinHold: false, sel: null, t0: performance.now()};
const core = {wrap: null, cv: null, g: null, w: 0, h: 0, nodes: [], edges: [], adj: [], pulses: [], bursts: [], particles: [], stageXY: [], domArcs: [], geo: null,
  mode: "globe", focus: null, trail: [], anim: null, iters: 0, groups: [], shown: null, inner: [], explore: false, autoExplore: false,
  opts: {insetL: 0, insetR: 0}, inL: 0, inR: 0, tL: 0, tR: 0, stageHov: {}, domHov: {}, X: null, C: null, D: null};
const LAYOUT_ITERS = 200;
const fullLabel = on => on ? "⤡ " + T("graph.collapse") : "⤢ " + T("graph.expand");
const GRAPH = {ready: false, byId: new Map(), _wait: [],
  whenReady() { return this.ready ? Promise.resolve() : new Promise(r => this._wait.push(r)); },
  async init() {
    if (core.wrap) return this.whenReady();
    core.wrap = document.createElement("div"); core.wrap.id = "gwrap";
    core.wrap.innerHTML = `<canvas id="core"></canvas>
      <div id="gsearch" class="glass"><span class="mono tiny muted">⌕</span><input id="gq" placeholder="${esc(T("graph.find"))}" autocomplete="off"><div id="gsug"></div></div>
      <button class="btn sm ghost lens" id="gfull">${esc(fullLabel(false))}</button>
      <div id="glegend"></div><div id="focuspanel" class="glass"></div><div id="selcard" class="glass sweep"></div>`;
    core.cv = $("canvas", core.wrap); core.g = core.cv.getContext("2d");
    const G = await getGraph(); initCore(G); this.ready = true; this._wait.forEach(r => r()); this._wait = [];
  },
  // mount into a page's container; insetL / insetR: widths hidden by side panels, the globe is drawn in the space between
  mount(container, opts = {}) {
    container.appendChild(core.wrap); core.opts = {insetL: 0, insetR: 0, ...opts};
    retarget(); core.inL = core.tL; core.inR = core.tR; resizeCore();
  },
  focusById(id) { const n = this.byId.get(id); if (n) focusNode(n.i); },
  selectById(id) { const n = this.byId.get(id); if (n) selectNode(n.i); },
  setExplore(on) { setExplore(on); }
};
function retarget() {
  const o = core.opts, exp = core.explore || core.mode === "focus";
  core.tL = exp ? (o.expL ?? 0) : o.insetL; core.tR = (exp ? (o.expR ?? 0) : o.insetR) + (core.mode === "focus" ? 400 : 0);
  core.wrap && core.wrap.style.setProperty("--gl", (exp ? (o.expL ?? 0) : o.insetL) + "px");
  core.wrap && core.wrap.style.setProperty("--gr", (exp ? (o.expR ?? 0) : o.insetR) + "px");
}
function setExplore(on) {
  core.explore = on; retarget(); core.opts.onExplore && core.opts.onExplore(on || core.mode === "focus");
  $("#gfull", core.wrap).textContent = fullLabel(on);
}

function initCore(G) {
  core.graph = G;
  // starting point: each domain owns a patch of the sphere (golden-angle spacing); pages start near their domain, then organise by links
  const doms = G.domains.map(d => d[0]), golden = Math.PI * (3 - Math.sqrt(5));
  const centers = Object.fromEntries(doms.map((d, i) => { const y = 1 - (i + 0.5) / doms.length * 2, r = Math.sqrt(1 - y * y), th = golden * i;
    return [d, [Math.cos(th) * r, y, Math.sin(th) * r]]; }));
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const N = G.nodes.length; core.X = new Float64Array(N * 3); core.C = new Float64Array(N * 3); core.D = new Float64Array(N * 3);
  core.nodes = G.nodes.map((n, i) => { const c = centers[n.domain] || [rnd() - 0.5, rnd() - 0.5, rnd() - 0.5];
    const v = [c[0] + (rnd() - 0.5) * 0.8, c[1] + (rnd() - 0.5) * 0.8, c[2] + (rnd() - 0.5) * 0.8], L = Math.hypot(...v) || 1;
    core.X.set(v.map(x => x / L), i * 3); core.C.set(centers[n.domain] || [0, 0, 0], i * 3);
    return {...n, i, sx: 0, sy: 0, gx: 0, gy: 0, d: 0, p: 1, alpha: 1}; });
  core.nodes.forEach(n => GRAPH.byId.set(n.id, n));
  core.edges = G.edges;
  core.adj = core.nodes.map(() => []);
  for (const [a, b, rel] of G.edges) { core.adj[a].push([b, rel, "out"]); core.adj[b].push([a, rel, "in"]); }
  core.particles = Array.from({length: 160}, (_, i) => ({u: i / 160 * 9, jitter: Math.random()}));
  buildGraphUI(G);
  new ResizeObserver(() => resizeCore()).observe(core.wrap);
  core.cv.addEventListener("pointermove", onCoreMove);
  core.cv.addEventListener("pointerleave", () => { core.mouse = null; HUD.hover = HUD.hoverStage = HUD.hoverDom = null; Tip.hide(); });
  core.cv.addEventListener("pointerdown", e => HUD.drag = {x: e.clientX, y: e.clientY, ry: HUD.rotY, rx: HUD.rotX, moved: false});
  addEventListener("pointerup", () => setTimeout(() => HUD.drag = null, 0));
  core.cv.addEventListener("click", () => {
    if (HUD.drag && HUD.drag.moved) return;
    if (core.mode === "focus") { if (HUD.hover) focusNode(HUD.hover.i); return; }
    if (HUD.hover) return selectNode(HUD.hover.i);
    if (HUD.sel != null) return selectNode(null);
    if (HUD.hoverStage) go("tasks", {stage: HUD.hoverStage.s.id});
    else if (HUD.hoverDom) go("knowledge", {domain: HUD.hoverDom.name});
  });
  core.cv.addEventListener("dblclick", () => { if (core.mode !== "focus" && HUD.hover) focusNode(HUD.hover.i); });
  addEventListener("keydown", e => { if (e.key !== "Escape" || !visible()) return;
    if (core.mode === "focus") exitFocus(); else if (HUD.sel != null) selectNode(null); else if (core.explore) setExplore(false); });
  requestAnimationFrame(frame);
}
const visible = () => core.wrap && core.wrap.isConnected && core.wrap.offsetParent !== null && !document.hidden;
function resizeCore() { if (!core.cv) return; const dpr = devicePixelRatio || 1, r = core.wrap.getBoundingClientRect(); if (!r.width) return; core.w = r.width; core.h = r.height;
  core.cv.width = r.width * dpr; core.cv.height = r.height * dpr; core.cv.style.width = r.width + "px"; core.cv.style.height = r.height + "px"; core.g.setTransform(dpr, 0, 0, dpr, 0, 0); }
function geom() {
  // the global view leaves room for the nine stage labels: about 66px above, 76px below, 150px each side; focus leaves room for the list panel on the right
  const x0 = core.inL, fw = Math.max(200, core.w - core.inL - core.inR), h = core.h;
  if (core.mode === "focus") { const R = Math.min(fw, h) / 2 - 8;
    return {cx: x0 + fw / 2, cy: h / 2, R, rs: R * 0.42, r1: R * 0.50, r2: R * 0.555, r3: R * 0.60, ro: R * 0.82, rf: R * 0.40}; }
  const top = 10, bottom = 18;
  const ro = Math.max(90, Math.min((h - top - bottom - 142) / 1.94, (fw / 2 - 150) / 0.985)), R = ro / 0.82;
  const slack = Math.max(0, h - top - bottom - (1.94 * ro + 142)), cy = top + 66 + ro + slack / 2;
  return {cx: x0 + fw / 2, cy, R, rs: R * 0.42, r1: R * 0.50, r2: R * 0.555, r3: R * 0.60, ro, rf: R * 0.40};
}

// ---- self-organising layout (force-directed on the sphere): every pair repels, linked pages attract, pages drift to their domain's centre; the step shrinks over time (annealing)
function layoutStep() {
  const X = core.X, D = core.D, C = core.C, N = core.nodes.length, KR = 0.0001, KS = 0.01, KD = 0.002;
  D.fill(0);
  for (let i = 0; i < N; i++) { const xi = X[i * 3], yi = X[i * 3 + 1], zi = X[i * 3 + 2];
    for (let j = i + 1; j < N; j++) { const dx = xi - X[j * 3], dy = yi - X[j * 3 + 1], dz = zi - X[j * 3 + 2], f = KR / (dx * dx + dy * dy + dz * dz + 1e-4);
      D[i * 3] += dx * f; D[i * 3 + 1] += dy * f; D[i * 3 + 2] += dz * f; D[j * 3] -= dx * f; D[j * 3 + 1] -= dy * f; D[j * 3 + 2] -= dz * f; } }
  for (const [a, b, rel] of core.edges) { const s = rel === "cites" ? KS * 0.6 : KS;
    for (let q = 0; q < 3; q++) { const d = (X[b * 3 + q] - X[a * 3 + q]) * s; D[a * 3 + q] += d; D[b * 3 + q] -= d; } }
  const temp = 0.08 * (1 - core.iters / LAYOUT_ITERS) + 0.005;
  for (let i = 0; i < N; i++) { const o = i * 3;
    for (let q = 0; q < 3; q++) D[o + q] += (C[o + q] - X[o + q]) * KD;
    const dot = D[o] * X[o] + D[o + 1] * X[o + 1] + D[o + 2] * X[o + 2];
    let tx = D[o] - dot * X[o], ty = D[o + 1] - dot * X[o + 1], tz = D[o + 2] - dot * X[o + 2]; const m = Math.hypot(tx, ty, tz);
    if (m > temp) { tx *= temp / m; ty *= temp / m; tz *= temp / m; }
    const x = X[o] + tx, y = X[o + 1] + ty, z = X[o + 2] + tz, L = Math.hypot(x, y, z); X[o] = x / L; X[o + 1] = y / L; X[o + 2] = z / L; }
  core.iters++;
}
function project(cx, cy, rs) {
  const cyR = Math.cos(HUD.rotY), syR = Math.sin(HUD.rotY), cxR = Math.cos(HUD.rotX), sxR = Math.sin(HUD.rotX);
  for (const n of core.nodes) {
    const r = SHELL[n.layer], o = n.i * 3, x = core.X[o] * r, y = core.X[o + 1] * r, z = core.X[o + 2] * r;
    const x1 = x * cyR + z * syR, z1 = -x * syR + z * cyR, y2 = y * cxR - z1 * sxR, z2 = y * sxR + z1 * cxR;
    const p = 1 / (1 - z2 * 0.3); n.gx = cx + x1 * rs * p; n.gy = cy + y2 * rs * p; n.d = (z2 / r + 1) / 2; n.p = p;
  }
}

// ---- focus: the clicked page moves to the centre and its direct relations form a ring, one sector per type; what does not fit is listed on the right
function neighbours(i) {
  const seen = new Map();
  for (const [j, rel, dir] of core.adj[i]) { const prev = seen.get(j); seen.set(j, prev ? {...prev, dir: prev.dir === dir ? dir : "both"} : {j, rel, dir}); }
  return [...seen.values()];
}
function focusNode(i) {
  if (core.mode === "focus" && core.focus === i) return;
  if (core.mode === "focus") core.trail.push(core.focus); else core.autoExplore = !core.explore;
  if (core.trail.length > 8) core.trail.shift();
  core.mode = "focus"; core.focus = i; HUD.spinHold = true; HUD.hover = null; Tip.hide(); selectNode(null, true);
  retarget(); core.opts.onExplore && core.opts.onExplore(true);
  const nb = neighbours(i);
  core.groups = GROUPS.map(type => ({type, label: T("group." + type), items: nb.filter(x => core.nodes[x.j].type === type)
    .sort((a, b) => core.adj[b.j].length - core.adj[a.j].length)})).filter(g => g.items.length);
  // upper half: the more abstract (entities, topics, concepts, left to right); lower half: evidence (source pages, raw sources, right to left), 180° each.
  // How many fit in a half follows the label spacing (about 15px of arc), shared by √count with at least 2 per group; the rest is listed on the right
  const rfEst = Math.min(core.w - (core.opts.expL ?? 0) - (core.opts.expR ?? 0) - 400, core.h) / 2 * 0.40, target = new Map([[i, null]]);
  const up = core.groups.filter(g => UPPER.has(g.type)), down = core.groups.filter(g => !UPPER.has(g.type)).reverse();
  const halves = up.length && down.length ? [[up, Math.PI], [down, 0]] : [[up.length ? up : down, -Math.PI / 2 - Math.PI]];
  for (const [gs, start] of halves) {
    const span = halves.length === 2 ? Math.PI : Math.PI * 2, cap = Math.max(12, Math.floor(span * rfEst / 15));
    const quota = gs.map(g => Math.min(g.items.length, 2));
    for (let left = cap - quota.reduce((a, b) => a + b, 0); left > 0; left--) {
      let best = -1, bv = 0;
      gs.forEach((g, k) => { if (quota[k] < g.items.length) { const v = Math.sqrt(g.items.length) / (quota[k] + 1); if (v > bv) { bv = v; best = k; } } });
      if (best < 0) break; quota[best]++;
    }
    const slots = quota.reduce((a, b) => a + b, 0) + gs.length * 1.4 + 0.6, step = span / Math.max(slots, 8);
    let a = start + (span - slots * step) / 2 + step * 0.3;
    gs.forEach((g, k) => {
      g.a0 = a; a += step * 0.7;
      g.items.slice(0, quota[k]).forEach(x => { target.set(x.j, a + step / 2); a += step; });
      a += step * 0.7; g.a1 = a; g.onGraph = quota[k];
    });
  }
  core.shown = target;
  core.inner = core.edges.filter(([p, q]) => p !== i && q !== i && target.has(p) && target.has(q));
  core.anim = {t0: performance.now(), dur: 820, target, from: new Map(core.nodes.map(m => [m.i, [m.sx, m.sy]]))};
  core.bursts.push({center: true, t0: performance.now(), col: TYPE_COL[core.nodes[i].type]});
  renderFocusPanel();
}
function exitFocus() {
  core.mode = "globe"; HUD.spinHold = false; HUD.hover = null;
  if (core.autoExplore) core.explore = false;
  retarget(); core.opts.onExplore && core.opts.onExplore(core.explore); $("#gfull", core.wrap).textContent = fullLabel(core.explore);
  core.anim = {t0: performance.now(), dur: 700, target: null, from: new Map(core.nodes.map(m => [m.i, [m.sx, m.sy]]))};
  core.focus = null; core.trail = []; core.groups = []; core.shown = null; core.inner = [];
  const P = $("#focuspanel", core.wrap); P.classList.remove("on");
}
function hoverNode(j) { HUD.hover = j == null ? null : core.nodes[j]; }
const domText = n => esc(n.domain || T("graph.unclassified")) + (n.inferred && n.type !== "raw" ? " " + esc(T("graph.inferred")) : "");
const linkText = n => n.type === "raw" ? T("graph.cited_by", {n: n.in}) : T("graph.links", {i: n.in, o: n.out});
function selectNode(i, quiet) {
  HUD.sel = i; const card = $("#selcard", core.wrap);
  if (i == null) { card.classList.remove("on"); if (!quiet) updateHover(true); return; }
  const n = core.nodes[i], nb = neighbours(i);
  const groups = GROUPS.map(type => [type, T("group." + type), nb.filter(x => core.nodes[x.j].type === type).length]).filter(x => x[2]);
  card.innerHTML = `<div class="sc-top"><span>${typeBadge(n.type)} <span class="muted small">${esc(T("graph.layer", {name: LAYER_NAME()[n.layer]}))}</span></span><span class="sc-x" data-tip="${esc(T("graph.deselect"))}">✕</span></div>
    <div class="sc-name">${esc(n.name)}</div>
    <div class="sc-meta">${esc(T("graph.domain"))}: ${domText(n)} · ${esc(linkText(n))}</div>
    ${n.summary ? `<div class="sc-sum">${esc(plain(n.summary))}</div>` : ""}
    <div class="sc-rel">${groups.map(([t, l, c]) => `<span><i style="background:${TYPE_COL[t]}"></i>${esc(l)} <b class="mono">${c}</b></span>`).join("") || `<span class="muted">${esc(T("graph.no_relations"))}</span>`}</div>
    <div class="sc-btns"><button class="btn sm lens" data-act="focus">◎ ${esc(T("graph.focus"))}</button>${n.exists === false ? "" : `<button class="btn sm primary lens" data-act="open">${esc(T("c.open_full"))}</button>`}</div>`;
  card.querySelector(".sc-x").onclick = () => selectNode(null);
  card.querySelector('[data-act="focus"]').onclick = () => focusNode(i);
  const o = card.querySelector('[data-act="open"]'); if (o) o.onclick = () => openPage(n.id);
  card.classList.remove("on"); void card.offsetWidth; card.classList.add("on");
  core.cardW = card.offsetWidth; core.cardH = card.offsetHeight;
  core.bursts.push({n, t0: performance.now(), col: TYPE_COL[n.type]});
  playIn(card, 80); updateHover(true);
}
function renderFocusPanel() {
  const n = core.nodes[core.focus], P = $("#focuspanel", core.wrap);
  const arrow = d => d === "out" ? "→" : d === "in" ? "←" : "↔";
  const dirTip = d => T("graph.dir_" + d);
  P.innerHTML = `
    <div class="fp-top">${typeBadge(n.type)}<span class="muted small">${esc(T("graph.layer", {name: LAYER_NAME()[n.layer]}))}</span><button class="btn xs ghost" data-act="back">${esc(T("graph.back"))} <span class="k">ESC</span></button></div>
    ${core.trail.length ? `<div class="fp-trail">${core.trail.map(t => `<span class="link" data-j="${t}">${esc(core.nodes[t].name.slice(0, 10))}</span>`).join(" › ")} › <b>${esc(n.name.slice(0, 12))}</b></div>` : ""}
    <div class="fp-name" data-in>${esc(n.name)}</div>
    <div class="fp-meta" data-in>${esc(T("graph.domain"))}: ${domText(n)}<br>${esc(linkText(n))}${n.exists === false ? " · " + esc(T("drawer.missing")) : ""}</div>
    ${n.summary ? `<div class="fp-sum" data-in>${esc(plain(n.summary))}</div>` : ""}
    ${n.exists === false ? "" : `<div data-in><button class="btn sm primary lens" data-act="open">${esc(T("c.open_full"))}</button></div>`}
    <div class="fp-groups">${core.groups.map(g => `<div class="fp-g" data-in><div class="fp-gh"><i style="background:${TYPE_COL[g.type]}"></i>${esc(g.label)} <span class="mono">${g.items.length}${g.onGraph < g.items.length ? ` · ${esc(T("graph.on_graph", {n: g.onGraph}))}` : ""}</span></div>
      <div class="fp-list">${g.items.slice(0, 80).map(x => `<div class="fp-it" data-j="${x.j}" data-tip="${esc(core.nodes[x.j].name)} · ${esc(dirTip(x.dir))} · ${esc(relName(x.rel))}"><span class="ar">${arrow(x.dir)}</span>${esc(core.nodes[x.j].name)}</div>`).join("")}</div>
      ${g.items.length > 80 ? `<div class="muted small">${esc(T("graph.more", {n: g.items.length - 80}))}</div>` : ""}</div>`).join("") || `<div class="muted small">${esc(T("graph.no_relations"))}</div>`}</div>
    <div class="fp-note">${esc(T("graph.focus_note"))}</div>`;
  P.querySelector('[data-act="back"]').onclick = exitFocus;
  const o = P.querySelector('[data-act="open"]'); if (o) o.onclick = () => openPage(n.id);
  P.onclick = e => { const it = e.target.closest("[data-j]"); if (it) focusNode(+it.dataset.j); };
  P.onpointerover = e => { const it = e.target.closest(".fp-it"); hoverNode(it ? +it.dataset.j : null); };
  P.onpointerleave = () => hoverNode(null);
  $$(".fp-list", P).forEach(l => hlList(l, ".fp-it"));
  P.classList.add("on"); P.querySelector(".fp-groups").scrollTop = 0; playIn(P, 120);
}

// ---- controls on the graph: search, legend, expand
function buildGraphUI(G) {
  // a narrow legend (bottom left, clear of the stage labels): types on two rows; relation colours and totals in its tooltip
  const sw = t => `<span><i style="background:${TYPE_COL[t]};box-shadow:0 0 8px ${TYPE_COL[t]}"></i>${esc(typeName(t))}</span>`;
  $("#glegend", core.wrap).innerHTML = `<div class="lg-row">${["topic", "concept", "entity"].map(sw).join("")}</div>
    <div class="lg-row">${["source", "raw"].map(sw).join("")}</div>
    <div class="lg-row"><span><i style="background:${GC.accent};box-shadow:0 0 8px ${GC.accent}"></i>${esc(T("graph.just_written"))}</span><span><i style="background:${GC.blue};box-shadow:0 0 8px ${GC.blue}"></i>${esc(T("graph.just_read"))}</span><span class="link">${esc(T("graph.rel_colours"))} ⓘ</span></div>`;
  $("#glegend", core.wrap).dataset.tip = `<b>${esc(T("graph.totals", {w: fmt(G.totals.wiki), r: fmt(G.totals.cited_raw), l: fmt(G.totals.links)}))}</b><br>` +
    Object.entries(REL_COL).map(([r, c]) => `<span class="tdot" style="background:${c}"></span> ${esc(relName(r))}: ${esc(T("rel_tip." + r))}`).join("<br>") + `<br><span class="m">${esc(T("graph.rel_note"))}</span>`;
  const q = $("#gq", core.wrap), sug = $("#gsug", core.wrap);
  q.oninput = () => { const t = q.value.trim().toLowerCase(); if (!t) { sug.innerHTML = ""; sug.classList.remove("on"); return; }
    const hits = core.nodes.filter(n => n.name.toLowerCase().includes(t)).sort((a, b) => a.layer - b.layer || core.adj[b.i].length - core.adj[a.i].length).slice(0, 12);
    sug.innerHTML = hits.map(n => `<div data-j="${n.i}"><i style="background:${TYPE_COL[n.type]}"></i>${esc(n.name)}<span>${esc(typeName(n.type))}</span></div>`).join("") || `<div class="muted">${esc(T("c.no_match"))}</div>`;
    sug.classList.add("on"); playIn(sug); };
  sug.onclick = e => { const it = e.target.closest("[data-j]"); if (!it) return; focusNode(+it.dataset.j); sug.innerHTML = ""; sug.classList.remove("on"); q.value = ""; };
  q.onkeydown = e => { if (e.key === "Enter") { const f = sug.querySelector("[data-j]"); if (f) f.click(); } if (e.key === "Escape") { q.value = ""; sug.innerHTML = ""; sug.classList.remove("on"); e.stopPropagation(); } };
  $("#gfull", core.wrap).onclick = () => { if (core.mode === "focus") { core.autoExplore = true; return exitFocus(); } setExplore(!core.explore); };
}

function onCoreMove(e) {
  const r = core.cv.getBoundingClientRect(), focus = core.mode === "focus";
  core.mouse = {x: e.clientX - r.left, y: e.clientY - r.top, cx: e.clientX, cy: e.clientY};
  if (HUD.drag && !focus) { const dx = e.clientX - HUD.drag.x, dy = e.clientY - HUD.drag.y; if (Math.abs(dx) + Math.abs(dy) > 3) HUD.drag.moved = true;
    if (HUD.drag.moved) { HUD.rotY = HUD.drag.ry + dx * 0.008; HUD.rotX = Math.max(-1.3, Math.min(1.3, HUD.drag.rx + dy * 0.006)); Tip.hide(); return; } }
  updateHover(true);
}
function updateHover(force) {
  const m = core.mouse, focus = core.mode === "focus"; if (!m) return;
  const mx = m.x, my = m.y;
  let best = null, bd = focus ? 12 : 8;
  for (const n of core.nodes) { if (focus ? !core.shown.has(n.i) : n.d < 0.4) continue;
    const d = Math.hypot(n.sx - mx, n.sy - my); if (d < bd) { bd = d; best = n; } }
  let stage = null, dom = null;
  if (!best && !focus) {
    core.stageXY.forEach(s => { if (Math.hypot(s.x - mx, s.y - my) < 24) stage = s; });
    if (!stage && core.geo) { const {cx, cy, r1} = core.geo, dist = Math.hypot(mx - cx, my - cy);
      if (Math.abs(dist - r1) < 8) { const TAU = Math.PI * 2, x = ((Math.atan2(my - cy, mx - cx) % TAU) + TAU) % TAU;
        dom = core.domArcs.find(d => { const a0 = ((d.a0 % TAU) + TAU) % TAU, a1 = a0 + (d.a1 - d.a0); return (x >= a0 && x <= a1) || (x + TAU >= a0 && x + TAU <= a1); }) || null; } }
  }
  const same = best === HUD.hover && (stage && stage.s.id) === (HUD.hoverStage && HUD.hoverStage.s.id) && (dom && dom.name) === (HUD.hoverDom && HUD.hoverDom.name);
  HUD.hover = best; HUD.hoverStage = stage; HUD.hoverDom = dom;
  if (!same || force) {
    let html = "";
    if (best && !focus && best.i === HUD.sel) { Tip.hide(); core.cv.style.cursor = "default"; return; }
    if (best) { const cnt = {}; for (const [, rel] of core.adj[best.i]) cnt[rel] = (cnt[rel] || 0) + 1;
      html = `<b>${esc(best.name)}</b><br><span class="m">${esc(typeName(best.type))} · ${esc(best.domain || T("graph.unclassified"))}</span><br>${Object.entries(cnt).map(([k, v]) => `${esc(relName(k))} ${v}`).join(" · ") || esc(T("graph.no_relations"))}${best.summary ? `<br><span class="m">${esc(plain(best.summary).slice(0, 90))}</span>` : ""}<br><span class="h">${esc(T(focus ? "graph.hint_focus" : "graph.hint_globe"))}</span>`; }
    else if (stage) { const s = stage.s; html = `<b>${esc(stageName(s.id))}</b> <span class="m">${STAGE_EN[s.id]}</span><br>${s.active ? `<span class="h">● ${esc(T("c.running"))}</span>` : "○ " + esc(T("c.idle"))}${stageDetail(s) ? "<br>" + esc(stageDetail(s)) : ""}<br><span class="m">${esc(T("graph.hint_stage"))}</span>`; }
    else if (dom) html = `<b>${esc(dom.name)}</b><br><span class="m">${esc(T("graph.hint_domain", {n: dom.n}))}</span>`;
    if (!html) { Tip.hide(); core.cv.style.cursor = focus ? "default" : "grab"; return; }
    core.cv.style.cursor = "pointer"; Tip.show(html, m.cx, m.cy, core.cv); return;
  }
  if (Tip.owner === core.cv) Tip.move(m.cx, m.cy);
}

const ease = x => 1 - Math.pow(1 - x, 3);
const easeSpring = x => x >= 1 ? 1 : 1 - Math.exp(-6.5 * x) * Math.cos(5.2 * x);   // a spring with a little overshoot
function arrowOn(g, x0, y0, x1, y1, at, col) {
  const an = Math.atan2(y1 - y0, x1 - x0), px = x0 + (x1 - x0) * at, py = y0 + (y1 - y0) * at;
  g.fillStyle = col; g.beginPath(); g.moveTo(px + Math.cos(an) * 6, py + Math.sin(an) * 6);
  g.lineTo(px + Math.cos(an + 2.6) * 5, py + Math.sin(an + 2.6) * 5); g.lineTo(px + Math.cos(an - 2.6) * 5, py + Math.sin(an - 2.6) * 5); g.fill();
}
const lightOf = t => TYPE_COL[t] === "#6c7482" ? "#a3acbb" : TYPE_COL[t];
function frame(now) {
  requestAnimationFrame(frame);
  if (!core.g || !visible()) return;
  if (!core.w) resizeCore();
  const g = core.g, w = core.w, h = core.h, t = (now - HUD.t0) / 1000, focus = core.mode === "focus";
  core.inL += (core.tL - core.inL) * 0.11; core.inR += (core.tR - core.inR) * 0.11;
  const geo = core.geo = geom(), {cx, cy, R, rs, r1, r2, r3, ro, rf} = geo;
  if (core.iters < LAYOUT_ITERS) for (let k = 0; k < 2; k++) layoutStep();
  g.clearRect(0, 0, w, h);
  if (!HUD.drag && !HUD.spinHold && HUD.sel == null) HUD.rotY += HUD.spin;
  // ground glow and sweep
  const bg = g.createRadialGradient(cx, cy, 0, cx, cy, R); bg.addColorStop(0, "rgba(70,110,200,0.16)"); bg.addColorStop(0.45, "rgba(40,70,140,0.07)"); bg.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = bg; g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.fill();
  if (g.createConicGradient && !focus) { const cg = g.createConicGradient((t * 0.45) % (Math.PI * 2), cx, cy); cg.addColorStop(0, "rgba(140,170,240,0.08)"); cg.addColorStop(0.08, "rgba(140,170,240,0)"); cg.addColorStop(1, "rgba(140,170,240,0)");
    g.fillStyle = cg; g.beginPath(); g.arc(cx, cy, ro, 0, Math.PI * 2); g.fill(); }
  // ---- node positions: sphere projection or the focus layout, with a spring between the two
  project(cx, cy, rs);
  const A = core.anim, k = A ? (A.target ? easeSpring : ease)(Math.min(1, (now - A.t0) / A.dur)) : 1;
  if (A && now - A.t0 >= A.dur && !A.target) core.anim = null;
  for (const n of core.nodes) {
    let tx = n.gx, ty = n.gy, ta = 1;
    if (focus && core.shown) { if (core.shown.has(n.i)) { const a = core.shown.get(n.i); if (a == null) { tx = cx; ty = cy; } else { tx = cx + Math.cos(a) * rf; ty = cy + Math.sin(a) * rf; } } else ta = 0.06; }
    if (A) { const f = A.from.get(n.i) || [tx, ty]; n.sx = f[0] + (tx - f[0]) * k; n.sy = f[1] + (ty - f[1]) * k; } else { n.sx = tx; n.sy = ty; }
    n.alpha += (ta - n.alpha) * 0.12;
  }
  // ---- the four dashed shells are the four distillation layers
  if (!focus) { g.setLineDash([1, 5]); g.textAlign = "left"; g.font = uiFont(10); const names = LAYER_NAME();
    SHELL.forEach((s, i) => { g.strokeStyle = rgba(GC.silver, 0.16); g.lineWidth = 1; g.beginPath(); g.arc(cx, cy, rs * s, 0, Math.PI * 2); g.stroke();
      g.fillStyle = rgba(GC.silver, 0.42); g.fillText(names[i], cx + rs * s * 0.72 + 3, cy - rs * s * 0.7); });
    g.setLineDash([]); }
  if (!focus && !HUD.drag && core.mouse && core.iters >= LAYOUT_ITERS) updateHover(false);
  const hov = HUD.hover ? HUD.hover.i : null, sel = focus ? null : HUD.sel, act = sel != null ? sel : hov;
  // ---- lines
  if (!focus) {
    // links between pages in three brightness bands by depth; citations of raw sources in their own, dimmer band
    const buckets = [[], [], [], []];
    for (const [a, b, rel] of core.edges) { const d = (core.nodes[a].d + core.nodes[b].d) / 2; buckets[rel === "cites" ? 3 : d < 0.4 ? 0 : d < 0.65 ? 1 : 2].push(a, b); }
    buckets.forEach((bk, bi) => { g.strokeStyle = bi === 3 ? "rgba(141,151,168,0.035)" : `rgba(170,195,240,${[0.018, 0.04, 0.075][bi]})`; g.lineWidth = 0.6; g.beginPath();
      for (let q = 0; q < bk.length; q += 2) { const P = core.nodes[bk[q]], Q = core.nodes[bk[q + 1]]; g.moveTo(P.sx, P.sy); g.lineTo(Q.sx, Q.sy); } g.stroke(); });
    // the selected page (else the hovered one): all its relations coloured by type; hovering another page while one is selected overlays its relations faintly
    for (const [who, al, lw] of [[act, 0.8, 1.1], [sel != null && hov != null && hov !== sel ? hov : null, 0.4, 0.8]]) { if (who == null) continue; const H = core.nodes[who];
      for (const [j, rel] of core.adj[who]) { const m = core.nodes[j]; g.strokeStyle = rgba(REL_COL[rel] || GC.blue, al); g.lineWidth = lw; g.beginPath(); g.moveTo(H.sx, H.sy); g.lineTo(m.sx, m.sy); g.stroke(); } }
  } else if (core.shown) {
    const F = core.nodes[core.focus];
    // neighbours linked to each other get a faint line: you can see who belongs together in the ring
    g.lineWidth = 0.8; g.strokeStyle = rgba(GC.silver, 0.09 * Math.min(1, k)); g.beginPath();
    for (const [a, b] of core.inner) { g.moveTo(core.nodes[a].sx, core.nodes[a].sy); g.lineTo(core.nodes[b].sx, core.nodes[b].sy); } g.stroke();
    if (hov != null && hov !== core.focus) { const H = core.nodes[hov]; g.strokeStyle = rgba(GC.blueHi, 0.55); g.beginPath();
      for (const [a, b] of core.inner) if (a === hov || b === hov) { const o = core.nodes[a === hov ? b : a]; g.moveTo(H.sx, H.sy); g.lineTo(o.sx, o.sy); } g.stroke(); }
    const kk = Math.min(1, k);
    for (const grp of core.groups) for (const x of grp.items.slice(0, grp.onGraph)) { const m = core.nodes[x.j], col = REL_COL[x.rel] || GC.blue, hot = hov === x.j;
      const al = (hov == null || hot ? 0.6 : 0.22) * kk;
      g.strokeStyle = rgba(col, al); g.lineWidth = hot ? 1.8 : 1; g.beginPath(); g.moveTo(F.sx, F.sy); g.lineTo(m.sx, m.sy); g.stroke();
      if (x.dir === "out" || x.dir === "both") arrowOn(g, F.sx, F.sy, m.sx, m.sy, 0.8, rgba(col, al + 0.15));
      if (x.dir === "in" || x.dir === "both") arrowOn(g, m.sx, m.sy, F.sx, F.sy, 0.62, rgba(col, al + 0.15)); }
  }
  // ---- nodes: just written glow champagne, just read by an agent glow cobalt
  g.globalCompositeOperation = "lighter";
  const live = LIVE.data, changed = new Set(live ? live.pulse.changed : []), used = new Set(live ? live.pulse.used : []);
  const hset = act != null && !focus ? new Set(core.adj[act].map(x => x[0])) : null;
  for (const n of core.nodes) {
    if (n.alpha < 0.02) continue;
    const deg = core.adj[n.i].length;
    let s = n.type === "topic" ? 24 : n.type === "raw" ? 4 : 4.5 + Math.sqrt(deg) * 0.9, al = n.alpha;
    if (focus) { s = n.i === core.focus ? 46 : core.shown.has(n.i) ? Math.max(11, s * 1.1) : s * 0.6; if (n.i === hov) s *= 1.35; }
    else { s *= n.p * (0.55 + n.d * 0.6); al *= 0.18 + n.d * 0.82;
      if (hset) { if (n.i === act || hset.has(n.i)) { al = 1; s *= n.i === act ? 1.6 : 1.3; } else al *= sel != null ? 0.18 : 0.3; } }
    const spr = changed.has(n.id) ? SPR.accent : used.has(n.id) ? SPR.blue : (TSPR[n.type] || SPR.silver);
    g.globalAlpha = Math.min(1, al); g.drawImage(spr, n.sx - s / 2, n.sy - s / 2, s, s);
  }
  g.globalAlpha = 1;
  // ripples: recently changed (champagne) and recently read (cobalt) pages flash a ring now and then
  if (live && !focus && Math.random() < 0.05) { const pool = core.nodes.filter(n => (changed.has(n.id) || used.has(n.id)) && n.d > 0.5);
    if (pool.length) { const n = pool[(Math.random() * pool.length) | 0]; core.pulses.push({n, t0: t, c: changed.has(n.id) ? GC.accent : GC.blue}); } }
  core.pulses = core.pulses.filter(p => t - p.t0 < 1.6);
  for (const p of core.pulses) { const q = (t - p.t0) / 1.6; g.strokeStyle = rgba(p.c, 0.8 * (1 - q)); g.lineWidth = 1.2; g.beginPath(); g.arc(p.n.sx, p.n.sy, 4 + q * 26, 0, Math.PI * 2); g.stroke(); }
  // click feedback: two rings burst from the selected or expanded point
  core.bursts = core.bursts.filter(b => now - b.t0 < 900);
  for (const b of core.bursts) { const q = (now - b.t0) / 900, x = b.center ? cx : b.n.sx, y = b.center ? cy : b.n.sy;
    for (const [d, sc] of [[0, 1], [0.18, 0.6]]) { const qq = Math.max(0, Math.min(1, (q - d) / (1 - d))); if (qq <= 0) continue;
      g.strokeStyle = rgba(b.col, 0.85 * (1 - qq)); g.lineWidth = 2 * (1 - qq) + 0.5; g.beginPath(); g.arc(x, y, 6 + ease(qq) * (b.center ? 120 : 46) * sc, 0, Math.PI * 2); g.stroke(); } }
  g.globalCompositeOperation = "source-over";
  // ---- text
  if (!focus) {
    g.textAlign = "left"; const placed = [];
    for (const n of core.nodes.filter(n => n.type === "topic" && n.d > 0.5).sort((a, b) => b.d - a.d)) {
      if (placed.some(([x, y]) => Math.abs(x - n.sx) < 96 && Math.abs(y - n.sy) < 15)) continue;
      placed.push([n.sx, n.sy]); g.font = uiFont(11); g.fillStyle = `rgba(242,245,249,${Math.min(0.95, (n.d - 0.45) * 2)})`; g.fillText(n.name.slice(0, 18), n.sx + 9, n.sy + 4); }
    if (core.iters < LAYOUT_ITERS) { g.font = monoFont(10); g.fillStyle = GC.blueHi; g.textAlign = "center";
      g.fillText(T("graph.layout", {p: Math.round(core.iters / LAYOUT_ITERS * 100)}), cx, cy - r3 - 10); }
    if (sel != null) {
      const S = core.nodes[sel], placed2 = [[S.sx, S.sy]];
      g.font = uiFont(11); g.textAlign = "left";
      for (const x of neighbours(sel).map(x => core.nodes[x.j]).filter(m => m.d > 0.35).sort((a, b) => core.adj[b.i].length - core.adj[a.i].length)) {
        if (placed2.length > 22) break;
        if (placed2.some(([px, py]) => Math.abs(px - x.sx) < 84 && Math.abs(py - x.sy) < 14)) continue;
        placed2.push([x.sx, x.sy]); g.fillStyle = rgba(lightOf(x.type), 0.92); g.fillText(x.name.slice(0, 14), x.sx + 7, x.sy + 4); }
      g.strokeStyle = lightOf(S.type); g.lineWidth = 1.4;
      for (const [rr, sp, n2] of [[13, 0.9, 3], [19, -0.6, 4]]) for (let q = 0; q < n2; q++) { const a = t * sp + q / n2 * Math.PI * 2; g.beginPath(); g.arc(S.sx, S.sy, rr, a, a + Math.PI / n2 * 0.7); g.stroke(); }
      // the card sits outside the globe on the same side as the selected point (so it hides no dots), with a leader line
      const card = $("#selcard", core.wrap), cw = core.cardW || 280, ch = core.cardH || 190, right = S.sx >= cx;
      const left = Math.max(core.inL + 8, Math.min(w - core.inR - cw - 8, right ? cx + rs + 34 : cx - rs - 34 - cw)), top = Math.max(50, Math.min(h - ch - 8, S.sy - ch / 2));
      card.style.left = left + "px"; card.style.top = top + "px";
      const ex = right ? left : left + cw, ey = Math.max(top + 12, Math.min(top + ch - 12, S.sy));
      g.strokeStyle = rgba(GC.silver, 0.7); g.lineWidth = 1; g.beginPath(); g.moveTo(S.sx + (right ? 19 : -19), S.sy); g.lineTo(ex + (right ? -14 : 14), ey); g.lineTo(ex, ey); g.stroke();
      g.fillStyle = GC.accent; g.beginPath(); g.arc(ex, ey, 2.5, 0, Math.PI * 2); g.fill();
    }
    if (HUD.hover && HUD.hover.i !== sel) { const n = HUD.hover; g.strokeStyle = GC.silver; g.lineWidth = 1; g.beginPath(); g.arc(n.sx, n.sy, 9 + Math.sin(t * 6) * 1.2, 0, Math.PI * 2); g.stroke();
      g.beginPath(); g.moveTo(n.sx + 9, n.sy - 9); g.lineTo(n.sx + 24, n.sy - 24); g.lineTo(n.sx + 120, n.sy - 24); g.stroke();
      g.fillStyle = "#f6f9ff"; g.font = uiFont(12); g.textAlign = "left"; g.fillText(n.name.slice(0, 24), n.sx + 28, n.sy - 28); }
  } else if (core.shown) {
    const F = core.nodes[core.focus], kk = Math.min(1, k), fc = lightOf(F.type);
    g.strokeStyle = rgba(fc, 0.8); g.lineWidth = 1.2;
    for (const [rr, sp, n2] of [[30, 0.8, 3], [38, -0.5, 4]]) for (let q = 0; q < n2; q++) { const a = t * sp + q / n2 * Math.PI * 2; g.beginPath(); g.arc(F.sx, F.sy, rr, a, a + Math.PI / n2 * 0.7); g.stroke(); }
    g.textAlign = "center"; g.font = uiFont(15, 600); const fn = F.name.slice(0, 32), fw = Math.max(g.measureText(fn).width, 120);
    g.fillStyle = "rgba(8,11,17,0.86)"; g.beginPath(); g.roundRect ? g.roundRect(F.sx - fw / 2 - 10, F.sy + 42, fw + 20, 40, 10) : g.rect(F.sx - fw / 2 - 10, F.sy + 42, fw + 20, 40); g.fill();
    g.strokeStyle = rgba(fc, 0.5); g.lineWidth = 1; g.stroke();
    g.fillStyle = "#f6f9ff"; g.fillText(fn, F.sx, F.sy + 61);
    g.font = monoFont(10); g.fillStyle = fc; g.fillText(`${typeName(F.type)} · ${T("graph.direct", {n: neighbours(F.i).length})}`, F.sx, F.sy + 75);
    // direction hint: more abstract above, evidence below
    g.font = uiFont(11); g.fillStyle = rgba(GC.silver, 0.5);
    g.fillText(T("graph.up"), cx, cy - R * 0.93); g.fillText(T("graph.down"), cx, cy + R * 0.97);
    // sectors: an inner coloured arc and the group name
    for (const gr of core.groups) { const am = (gr.a0 + gr.a1) / 2, col = lightOf(gr.type);
      g.strokeStyle = rgba(col, 0.5 * kk); g.lineWidth = 2; g.beginPath(); g.arc(cx, cy, rf - 16, gr.a0 + 0.03, gr.a0 + 0.03 + (gr.a1 - gr.a0 - 0.06) * kk); g.stroke();
      const lr = rf - 34, lx = cx + Math.cos(am) * lr, ly = cy + Math.sin(am) * lr * 0.95;
      g.font = uiFont(11, 500); g.textAlign = "center"; const txt = `${gr.label} ${gr.items.length}`, tw = g.measureText(txt).width;
      g.fillStyle = "rgba(8,11,17,0.85)"; g.fillRect(lx - tw / 2 - 5, ly - 10, tw + 10, 16); g.fillStyle = rgba(col, kk); g.fillText(txt, lx, ly + 2); }
    // neighbour names point outwards along the radius
    const maxCh = Math.max(6, Math.floor((R * 0.40) / (LANG === "zh" ? 11.5 : 6.8)));
    g.font = uiFont(11);
    core.shown.forEach((_, j) => { if (j === core.focus) return; const m = core.nodes[j], ang = Math.atan2(m.sy - cy, m.sx - cx), out = Math.cos(ang) >= 0;
      g.save(); g.translate(m.sx, m.sy); g.rotate(out ? ang : ang + Math.PI); g.textAlign = out ? "left" : "right";
      g.fillStyle = j === hov ? "#ffffff" : rgba(GC.ink, 0.82 * kk);
      const nm = m.name.length > maxCh ? m.name.slice(0, maxCh - 1) + "…" : m.name; g.fillText(nm, out ? 9 : -9, 4); g.restore(); });
  }
  // ---- decoration: domain ring, tick ring, text ring (faded in focus)
  const G = core.graph, deco = focus ? 0.2 : 1, dimOuter = sel != null ? 0.35 : 1;
  core.domArcs = [];
  if (!focus && G.domains.length) { const doms = G.domains.slice(0, 8), total = doms.reduce((a, d) => a + d[1], 0), gap = 0.035; let a0 = t * 0.06 - Math.PI / 2;
    doms.forEach(([name, n], i) => { const span = (n / total) * (Math.PI * 2 - gap * doms.length), hv = core.domHov[name] = (core.domHov[name] || 0) + (((HUD.hoverDom && HUD.hoverDom.name === name) ? 1 : 0) - (core.domHov[name] || 0)) * 0.18;
      g.strokeStyle = hv > 0.05 ? `rgba(${[231, 199, 146].map((c, k) => Math.round(c * hv + [223, 231, 241][k] * (1 - hv))).join(",")},${0.85 - i * 0.07 + hv * 0.3})` : rgba(GC.silver, 0.8 - i * 0.07);
      g.lineWidth = 5 + hv * 4; g.shadowColor = hv > 0.05 ? GC.accent : GC.silver; g.shadowBlur = 6 + hv * 10; g.beginPath(); g.arc(cx, cy, r1, a0, a0 + span); g.stroke(); g.shadowBlur = 0;
      core.domArcs.push({name, n, a0, a1: a0 + span}); a0 += span + gap; }); }
  for (let i = 0; i < 180; i++) { const a = -t * 0.11 + i / 180 * Math.PI * 2, long = i % 15 === 0, l = long ? 9 : 4, rr = focus ? R * 0.95 : r2;
    g.strokeStyle = rgba(GC.silver, (long ? 0.6 : 0.22) * deco); g.lineWidth = long ? 1.4 : 1; g.beginPath();
    g.moveTo(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr); g.lineTo(cx + Math.cos(a) * (rr + l), cy + Math.sin(a) * (rr + l)); g.stroke(); }
  if (!focus) { const label = `KNOWLEDGE GRAPH · ${G.totals.wiki} PAGES · ${G.totals.cited_raw} CITED SOURCES · ${G.totals.links} RELATIONS · MEMEX//OS · `;
    g.font = monoFont(9.5); g.fillStyle = rgba(GC.silver, 0.5); g.textAlign = "center";
    for (let i = 0; i < label.length; i++) { const a = t * 0.03 + i * 0.0215 * (r3 > 200 ? 200 / r3 * 1.15 : 1.15) - Math.PI / 2; g.save();
      g.translate(cx + Math.cos(a) * r3, cy + Math.sin(a) * r3); g.rotate(a + Math.PI / 2); g.fillText(label[i], 0, 0); g.restore(); }
    g.lineWidth = 1.5; g.strokeStyle = rgba(GC.silver, 0.3);
    for (const [rr, sp, segs] of [[R * 0.68, 0.18, [[0, 0.9], [1.4, 2.2], [3.3, 3.6]]], [R * 0.72, -0.12, [[0.3, 1.5], [2.6, 3.0], [4.1, 5.2]]]])
      for (const [s0, s1] of segs) { g.beginPath(); g.arc(cx, cy, rr, s0 + t * sp, s1 + t * sp); g.stroke(); }
    drawOrbit(g, t, cx, cy, ro, dimOuter);
    if (ro - r2 > 60) { g.textAlign = "center"; g.font = `400 30px ${DISP}`; g.fillStyle = "#f6f9ff"; g.shadowColor = GC.blue; g.shadowBlur = 14;
      g.fillText(fmt(G.totals.wiki), cx, cy + r2 + 38); g.shadowBlur = 0; g.font = monoFont(9); g.fillStyle = rgba(GC.silver, 0.6); g.fillText("WIKI PAGES", cx, cy + r2 + 52); }
  }
}

// ---- the pipeline orbit: nine stages in a ring, particles flowing along it, running stages glowing
function drawOrbit(g, t, cx, cy, ro, dim = 1) {
  const live = LIVE.data, stages = live ? live.stages : STAGE_DEFAULT;
  g.globalAlpha = dim;
  g.setLineDash([2, 6]); g.strokeStyle = rgba(GC.silver, 0.22); g.lineWidth = 1; g.beginPath(); g.arc(cx, cy, ro, 0, Math.PI * 2); g.stroke(); g.setLineDash([]);
  const ang = i => -Math.PI / 2 + i / stages.length * Math.PI * 2;
  core.stageXY = stages.map((s, i) => ({s, x: cx + Math.cos(ang(i)) * ro, y: cy + Math.sin(ang(i)) * ro, a: ang(i)}));
  stages.forEach((s, i) => { if (!s.active) return; g.strokeStyle = rgba(GC.blue, 0.65); g.lineWidth = 2; g.shadowColor = GC.blue; g.shadowBlur = 12;
    g.beginPath(); g.arc(cx, cy, ro, ang(i), ang(i + 1)); g.stroke(); g.shadowBlur = 0; });
  g.globalCompositeOperation = "lighter";
  for (const p of core.particles) { const seg = Math.floor(p.u) % stages.length, act = stages[seg].active;
    p.u = (p.u + (act ? 0.016 : 0.0035) * (0.7 + p.jitter * 0.6)) % stages.length;
    const a = -Math.PI / 2 + p.u / stages.length * Math.PI * 2, rr = ro + (p.jitter - 0.5) * 6, s = act ? 11 : 6;
    g.globalAlpha = (act ? 0.95 : 0.35) * dim; g.drawImage(act ? SPR.blue : SPR.silver, cx + Math.cos(a) * rr - s / 2, cy + Math.sin(a) * rr - s / 2, s, s); }
  g.globalAlpha = dim; g.globalCompositeOperation = "source-over";
  // a running search sends a cobalt beam from "retrieve" into the graph (reading); writing and publishing send champagne beams (writing)
  if (core.nodes.length) for (const st of core.stageXY) { if (!st.s.active || !["retrieve", "compile", "publish", "selfcheck", "gate2"].includes(st.s.id)) continue;
    const tgt = core.nodes[Math.floor((t * 3 + st.x) % core.nodes.length)], col = ["compile", "publish"].includes(st.s.id) ? GC.accent : GC.blue;
    const lg = g.createLinearGradient(st.x, st.y, tgt.sx, tgt.sy); lg.addColorStop(0, rgba(col, 0.75)); lg.addColorStop(1, rgba(col, 0));
    g.strokeStyle = lg; g.lineWidth = 1.2; g.beginPath(); g.moveTo(st.x, st.y); g.lineTo(tgt.sx, tgt.sy); g.stroke(); }
  for (const {s, x, y, a} of core.stageXY) {
    const hv = core.stageHov[s.id] = (core.stageHov[s.id] || 0) + (((HUD.hoverStage && HUD.hoverStage.s.id === s.id) ? 1 : 0) - (core.stageHov[s.id] || 0)) * 0.2;
    const pulse = s.active ? 0.5 + 0.5 * Math.sin(t * 4) : 0, rad = 17 + hv * 4;
    g.beginPath(); for (let q = 0; q < 6; q++) { const b = q / 6 * Math.PI * 2 + Math.PI / 6 + hv * 0.5; g[q ? "lineTo" : "moveTo"](x + Math.cos(b) * rad, y + Math.sin(b) * rad); } g.closePath();
    g.fillStyle = s.active ? rgba(GC.blue, 0.2 + pulse * 0.14) : `rgba(10,14,22,${0.88 - hv * 0.2})`; g.fill();
    g.strokeStyle = s.active ? GC.blueHi : rgba(GC.silver, 0.32 + hv * 0.5); g.lineWidth = s.active ? 1.6 : 1 + hv * 0.6; if (s.active || hv > 0.05) { g.shadowColor = s.active ? GC.blue : GC.silver; g.shadowBlur = s.active ? 14 : hv * 12; } g.stroke(); g.shadowBlur = 0;
    if (s.active) { g.strokeStyle = rgba(GC.blue, 0.55 * (1 - pulse)); g.beginPath(); g.arc(x, y, rad + 6 + pulse * 10, 0, Math.PI * 2); g.stroke(); }
    g.fillStyle = s.active ? "#f6f9ff" : rgba(GC.silver, 0.45 + hv * 0.5); g.font = monoFont(10, 500); g.textAlign = "center"; g.fillText(String(STAGE_NO[s.id] || ""), x, y + 3.5);
    const lines = [[stageName(s.id), uiFont(13, 500), s.active ? "#f6f9ff" : `rgba(223,231,241,${0.6 + hv * 0.4})`], [`${STAGE_EN[s.id] || ""}${s.active ? " · ACTIVE" : ""}`, monoFont(9), s.active ? GC.blueHi : GC.muted]];
    const det = s.active ? stageDetail(s) : "";
    if (det) { const side = Math.abs(Math.cos(a)) > 0.25, max = side ? 18 : 28; lines.push([det.length > max ? det.slice(0, max - 1) + "…" : det, uiFont(11), "rgba(223,231,241,0.82)"]); }
    const blockH = 14 * lines.length, up = Math.sin(a) < -0.35, down = Math.sin(a) > 0.35;
    const lx = x + Math.cos(a) * 30, al = Math.cos(a) > 0.25 ? "left" : Math.cos(a) < -0.25 ? "right" : "center";
    let ly = up ? y - 24 - blockH + 12 : down ? y + 34 : y - blockH / 2 + 10;
    g.textAlign = al; for (const [txt, font, col] of lines) { g.font = font; g.fillStyle = col; g.fillText(txt, lx, ly); ly += 14; }
  }
  g.globalAlpha = 1;
}
