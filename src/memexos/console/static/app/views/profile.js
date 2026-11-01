"use strict";
// Profile (opt-in, console.profile_page): "What do my agents remember about me, and since when?" Read from Claude Code's auto-memory,
// which is about you, not about the domain; that knowledge is on the Knowledge page.
// Layout: a card wall by memory type (good for scanning), a timeline on the right (when it was written or changed).
// Cards flip on hover to show the start of the text and open in full from where they were clicked; on entering, the latest few get a dot flying in from the timeline.
const PF = {d: null, proj: "", q: ""};
const PF_COLS = ["user", "feedback", "project", "reference"];
const PF_COL = {user: "#e7c792", feedback: "#4f84ff", project: "#4cc7b6", reference: "#d2a868"};
VIEWS.profile = {title: T("nav.profile"), fit: true,
  init(el) {
    el.innerHTML = `<div class="ph"><h1><svg class="pi pf" viewBox="0 0 14 14"><circle cx="7" cy="4.6" r="2.3" fill="none"/><path d="M2.4 12.6c.6-3 2.6-4.3 4.6-4.3s4 1.3 4.6 4.3" fill="none"/></svg>${esc(T("nav.profile"))}<small>PROFILE</small></h1><div class="q">${esc(T("pf.q"))}</div>
      <span class="other" data-go-other="knowledge" data-tip="${esc(T("pf.other_tip"))}">${esc(T("pf.other"))} <b>${esc(T("nav.knowledge"))}</b> ›</span>
      <div class="r"><select class="inp" id="pf-proj" style="width:230px"></select><input class="inp" id="pf-q" placeholder="${esc(T("pf.filter"))}" style="width:150px"></div></div>
      <div class="prof" id="pf-body"></div>`;
    $("#pf-proj", el).onchange = e => { PF.proj = e.target.value; renderProfile(true); };
    let t; $("#pf-q", el).oninput = e => { clearTimeout(t); t = setTimeout(() => { PF.q = e.target.value.trim().toLowerCase(); renderProfile(true); }, 160); };
    $("#pf-body", el).addEventListener("click", e => { const c = e.target.closest("[data-mem]"); if (c) openMem(c.dataset.mem, e); });
    $("#pf-body", el).addEventListener("pointerover", e => { const it = e.target.closest(".ptl .tl-it"); if (!it) return; const c = $(`.mcard[data-mem="${CSS.escape(it.dataset.mem)}"]`); if (c) { c.scrollIntoView({block: "nearest", behavior: "smooth"}); anim(c, [{transform: "scale(1)"}, {transform: "scale(1.03)"}, {transform: "scale(1)"}], "bouncy"); } });
  },
  async enter() {
    if (!PF.d) { PF.d = await api("/api/profile"); const ps = {}; PF.d.items.forEach(x => ps[x.project] = (ps[x.project] || 0) + 1);
      $("#pf-proj").innerHTML = `<option value="">${esc(T("pf.all", {n: PF.d.items.length}))}</option>` + Object.entries(ps).sort((a, b) => b[1] - a[1]).map(([p, n]) => `<option value="${esc(p)}">${esc(p)} (${n})</option>`).join(""); }
    if (!PF.d.enabled) { $("#pf-body").innerHTML = `<div class="empty">${esc(T("pf.off"))}</div>`; return; }
    renderProfile(true, true);
  }
};
const pfKey = x => x.project + "/" + x.file;
const pfName = x => x.name.replace(/^(feedback|project|user|reference)[-_]/, "");
function pfItems() { return PF.d.items.filter(x => x.type !== "index" && x.file !== "MEMORY.md" && (!PF.proj || x.project === PF.proj) && (!PF.q || (x.name + x.description + x.body).toLowerCase().includes(PF.q))); }
function renderProfile(animate, fly) {
  const xs = pfItems().sort((a, b) => b.mtime.localeCompare(a.mtime)), B = $("#pf-body");
  const recent = new Set(xs.slice(0, 5).map(pfKey)), col = t => PF_COLS.includes(t) ? t : "project";
  const card = (x, i) => `<div class="mcard${recent.has(pfKey(x)) ? " new" : ""}" data-mem="${esc(pfKey(x))}" data-in style="--i:${Math.min(i, 10)}">
    <div class="fc"><div class="nm">${esc(pfName(x))}</div><div class="ds">${esc(x.description || plain(x.body).slice(0, 120))}</div>
      <div class="ft"><span>${esc(x.mtime.slice(5, 16))}</span><span>${esc(x.project.split("/").pop())}</span></div></div>
    <div class="bk"><div class="tx">${esc(plain(x.body).replace(/\s+/g, " ").slice(0, 240))}</div><span class="go">${esc(T("pf.read"))} →</span></div></div>`;
  const byDay = {}; xs.slice(0, 120).forEach(x => (byDay[x.mtime.slice(0, 10)] ||= []).push(x));
  B.innerHTML = PF_COLS.map(k => { const cs = xs.filter(x => col(x.type) === k);
    return `<div class="pcol"><div class="hd" data-in><b><i class="tdot" style="background:${PF_COL[k]};margin-right:7px"></i>${esc(T("pf.c_" + k))}</b><span>${cs.length} · ${esc(T("pf.c_" + k + "_d"))}</span></div><div class="cl">${cs.map(card).join("") || `<div class="empty">${esc(T("c.none"))}</div>`}</div></div>`; }).join("") +
    `<div class="glass ptl" data-in="right"><h2 class="h" style="margin-bottom:6px">${esc(T("pf.timeline"))} <small>WHEN</small></h2><div class="sub">${esc(T("pf.timeline_sub"))}</div>
      ${Object.entries(byDay).map(([d, xs2]) => `<div class="day">${d}</div><div class="tline">${xs2.map(x => `<div class="tl-it" data-mem="${esc(pfKey(x))}" style="--c:${PF_COL[col(x.type)]}"><div class="w">${esc(x.mtime.slice(11, 16))} · ${esc(x.project.split("/").pop())}</div><span class="nm2">${esc(pfName(x))}</span></div>`).join("")}</div>`).join("")}</div>`;
  if (animate) playIn(B);
  if (fly && !reduce) setTimeout(flyDots, 700);
}
// the latest few: a dot flies from the timeline to the matching card
function flyDots() {
  $$(".ptl .tl-it").slice(0, 5).forEach((it, i) => {
    const c = $(`.mcard[data-mem="${CSS.escape(it.dataset.mem)}"]`); if (!c) return;
    const a = it.getBoundingClientRect(), b = c.getBoundingClientRect(); if (!b.width || b.bottom < 0 || b.top > innerHeight) return;
    const d = document.createElement("div"); d.className = "flydot"; document.body.appendChild(d);
    const x0 = a.left - 12, y0 = a.top + 12, x1 = b.left + b.width / 2, y1 = b.top + b.height / 2, mx = (x0 + x1) / 2, my = Math.min(y0, y1) - 80;
    const k = d.animate([{transform: `translate(${x0}px,${y0}px) scale(.4)`, opacity: 0}, {transform: `translate(${mx}px,${my}px) scale(1.2)`, opacity: 1, offset: .5}, {transform: `translate(${x1}px,${y1}px) scale(.6)`, opacity: .9}],
      {duration: 1100, delay: i * 160, easing: "cubic-bezier(.45,0,.2,1)", fill: "both"});
    k.onfinish = () => { d.remove(); anim(c, [{boxShadow: "0 0 0 2px var(--accent), 0 0 40px -4px var(--accent)"}, {boxShadow: "0 0 0 0 transparent"}], "gentle", {duration: 900}); };
  });
}
function openMem(key, e) {
  const x = PF.d.items.find(m => pfKey(m) === key); if (!x) return;
  const links = [...new Set([...(x.body.matchAll(/\[\[([^\]]+)\]\]/g))].map(m => m[1]))], byName = new Map(PF.d.items.map(m => [m.name, m]));
  modal({title: x.name, origin: e, width: "min(760px, 92vw)", actions: [{label: T("c.close"), primary: true}],
    html: `<div class="muted small" style="margin-bottom:10px"><span class="badge" style="color:${PF_COL[x.type] || "var(--ink2)"}">${esc(x.type || "")}</span> ${esc(x.project)} · ${esc(x.file)} · ${esc(T("pf.changed", {t: x.mtime}))}</div>
      ${x.description ? `<div class="idt-why" style="margin-bottom:10px">${esc(x.description)}</div>` : ""}<div class="md" style="max-height:52vh;overflow:auto">${md(x.body)}</div>
      ${links.length ? `<div class="small" style="margin-top:10px">${esc(T("pf.related"))}: ${links.map(l => byName.has(l) ? `<span class="badge acc">${esc(l)}</span>` : `<span class="badge">${esc(l)} ${esc(T("pf.unwritten"))}</span>`).join(" ")}</div>` : ""}`}).then(() => {});
}
