"use strict";
// Inbox: "What do I have to decide? Which one first?"
// Layout like a mail inbox: the list on the left (grouped by impact, highest first), the item on the right. J/K move, number keys pick an option,
// E = no need, U = undo. After a decision the row slides out, the count rolls down and the next item moves up; the toast offers undo.
const IB = {data: null, b: null, tab: "decide", sel: null, q: "", seg: null, hl: null};
const impLv = x => !x.impact ? "none" : x.impact.score >= 0.75 ? "high" : x.impact.score >= 0.4 ? "mid" : "low";
const IMP_COL = {high: "var(--accent)", mid: "color-mix(in srgb, var(--accent) 50%, var(--mute))", low: "var(--mute)", none: "rgba(123,134,151,.35)"};
const IB_TABS = ["decide", "waiting", "results", "stuck", "agent", "system", "archived", "untriaged"];
function ibBuckets(items) {
  const by = f => items.filter(f), newest = (a, b) => (b.date || "").localeCompare(a.date || ""), imp = (a, b) => ((b.impact || {}).score || 0) - ((a.impact || {}).score || 0);
  const dupN = {}; items.forEach(x => { if (x.dup_of && x.status === "open") dupN[x.dup_of] = (dupN[x.dup_of] || 0) + 1; });
  const stuck = x => x.kind === "agent" && x.status === "open" && x.research && !(x.research.raws || []).length;
  return {dupN,
    decide: by(x => x.kind === "decide" && x.status === "open" && !x.dup_of).sort((a, b) => imp(a, b) || newest(a, b)),
    waiting: by(x => ["decided", "queued"].includes(x.status)),
    results: by(x => ["applied", "skipped", "failed"].includes(x.status)).sort((a, b) => ((b.result || {}).ts || "").localeCompare((a.result || {}).ts || "")),
    stuck: by(stuck),
    agent: by(x => x.kind === "agent" && x.status === "open" && !stuck(x)).sort((a, b) => imp(a, b) || newest(a, b)),
    system: by(x => x.kind === "system" && x.status === "open").sort(newest),
    archived: by(x => (["fyi", "done"].includes(x.kind) && !x.decision) || ["dismissed", "resolved"].includes(x.status)).sort(newest),
    untriaged: by(x => !x.kind && x.status === "open").sort(newest)};
}
const ibTitle = x => { const t = x.triage || {}; return t.question || t.agent_task || x.title; };
const ibSrc = x => `${esc(T("ib.src_" + x.src))} · ${esc(x.date || "")}`;
const ibPages = t => (t.pages || []).map(p => `<a class="wl" data-name="${esc(p)}">${esc(p)}</a>`).join(" ");
VIEWS.inbox = {title: T("nav.inbox"), fit: true,
  init(el) {
    el.innerHTML = `<div class="ibx">
      <div class="ph"><h1>${esc(T("nav.inbox"))}<small>INBOX</small></h1><div class="q">${esc(T("ib.q"))}</div>
        <div class="r"><div class="chip warm" style="view-transition-name:vt-inbox;height:36px;font-size:13px">⚑ <b class="mono" id="ib-big" style="font-size:16px">0</b> ${esc(T("top.inbox_waiting"))}</div>
          <button class="btn lens" id="ib-apply">${esc(T("act.inbox_apply"))}</button></div></div>
      <div class="tb"><div class="seg" id="ib-tabs">${IB_TABS.map(k => `<button data-v="${k}"${k === "decide" ? ' class="on"' : ""}>${esc(T("ib.tab_" + k))}<span class="n" data-n="${k}">0</span></button>`).join("")}</div>
        <span class="grow"></span><input class="inp" id="ib-q" placeholder="${esc(T("ib.filter"))}" style="width:240px"></div>
      <div class="body"><div class="ilist glass" id="ib-list"></div><div class="idetail glass" id="ib-detail"></div></div></div>`;
    IB.seg = seg($("#ib-tabs", el), v => { IB.tab = v; IB.sel = null; renderList(true); });
    $("#ib-q", el).oninput = e => { IB.q = e.target.value.trim().toLowerCase(); renderList(true); };
    $("#ib-apply", el).onclick = e => doAction(e, "inbox-apply", {}, {title: T("act.inbox_apply"), confirm: esc(T("act.inbox_apply_confirm"))}).then(() => loadInbox());
    $("#ib-list", el).addEventListener("click", e => {
      const acc = e.target.closest("[data-accept]"); if (acc) { e.stopPropagation(); acceptGroup(e, acc.dataset.accept); return; }
      const r = e.target.closest(".irow"); if (r) selectItem(r.dataset.id); });
    IB.hl = hlList($("#ib-list", el), ".irow");
    $("#ib-detail", el).addEventListener("click", e => {
      const o = e.target.closest(".opt"); if (o) return choose(+o.dataset.i, o, e);
      const a = e.target.closest("[data-act]"); if (!a) return;
      ({text: () => submitText(e), dismiss: () => dismiss(e), undo: () => undo()})[a.dataset.act]?.();
    });
    $("#ib-detail", el).addEventListener("toggle", e => { const d = e.target; if (d.dataset.body && d.open && !d.dataset.done) { d.dataset.done = 1; const x = IB.data.items.find(i => i.id === IB.sel); $(".md", d).innerHTML = md((x && x.body) || T("ib.no_body")); } }, true);
    document.addEventListener("keydown", e => {
      if (el.hidden || Cmdk.open || $("#drawer").classList.contains("on") || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || e.metaKey || e.ctrlKey) return;
      const k = e.key.toLowerCase();
      if (k === "j" || e.key === "ArrowDown") { e.preventDefault(); moveSel(1); }
      else if (k === "k" || e.key === "ArrowUp") { e.preventDefault(); moveSel(-1); }
      else if (/^[1-9]$/.test(e.key) && IB.tab === "decide") { const o = $(`#ib-detail .opt[data-i="${+e.key - 1}"]`); if (o) { const r = o.getBoundingClientRect(); choose(+e.key - 1, o, {clientX: r.left + r.width / 2, clientY: r.top + r.height / 2}); } }
      else if (k === "e") { const r = $("#ib-detail").getBoundingClientRect(); dismiss({clientX: r.left + r.width / 2, clientY: r.top + 120}); }
      else if (k === "u") undo();
      else if (e.key === "/") { e.preventDefault(); $("#ib-q").focus(); }
    });
    onLive(L => { if (!el.hidden) tick($("#ib-big"), (L.inbox || {}).decide || 0); });
  },
  enter(el) { playIn(el); loadInbox(true); }
};
async function loadInbox(first) {
  const d = await api("/api/inbox"); IB.data = d; IB.b = ibBuckets(d.items);
  for (const k of IB_TABS) tick($(`#ib-tabs [data-n="${k}"]`), IB.b[k].length);
  tick($("#ib-big"), IB.b.decide.length);
  const R = d.running || {}; $("#ib-apply").disabled = !!R.apply; $("#ib-apply").textContent = R.apply ? T("ib.applying") : T("act.inbox_apply");
  renderList(first);
}
function visibleItems() {
  const xs = IB.b ? IB.b[IB.tab] : [];
  return IB.q ? xs.filter(x => (ibTitle(x) + " " + ((x.triage || {}).context || "") + " " + ((x.triage || {}).pages || []).join(" ")).toLowerCase().includes(IB.q)) : xs;
}
const RESULT_BADGE = {applied: ["ok", "✓"], skipped: ["", "↷"], failed: ["crit", "✕"]};
function renderList(anim) {
  const list = $("#ib-list"), xs = visibleItems();
  const row = (x, i) => { const t = x.triage || {}, lv = impLv(x), dups = IB.b.dupN[x.id] || 0;
    const st = IB.tab === "results" ? `<span class="${(RESULT_BADGE[x.status] || [""])[0]}c">${(RESULT_BADGE[x.status] || ["", ""])[1]} ${esc(T("ib.st_" + x.status))}</span> · ` :
      IB.tab === "waiting" ? (x.status === "queued" ? `<span class="blue">${esc(T("ib.st_queued"))}</span> · ` : esc(T("ib.st_decided")) + " · ") :
      IB.tab === "agent" ? (x.research ? esc(T("ib.researched", {n: (x.research.raws || []).length})) + " · " : t.needs_web ? esc(T("ib.needs_web")) + " · " : t.needs_local ? esc(T("ib.needs_local")) + " · " : esc(T("ib.queued_agent")) + " · ") : "";
    return `<div class="irow" data-id="${esc(x.id)}" data-key="${esc(x.id)}" data-in="left" style="--i:${Math.min(i, 12)}"><i class="imp" style="background:${IMP_COL[lv]}" data-tip="${esc(T("ib.imp_" + lv))}: ${esc(T("ib.imp_" + lv + "_tip"))}"></i>
      <div style="min-width:0"><div class="q">${esc(ibTitle(x))}</div><div class="m">${st}${ibSrc(x)}${dups ? " · " + esc(T("ib.dups", {n: dups})) : ""}</div></div></div>`; };
  let html = "";
  if (IB.tab === "decide") {
    for (const lv of ["high", "mid", "low", "none"]) {
      const g = xs.filter(x => impLv(x) === lv); if (!g.length) continue;
      const rec = g.filter(x => ((x.triage || {}).recommend ?? -1) >= 0).map(x => x.id);
      html += `<div class="igrp"><span><b>${esc(T("ib.imp_" + lv))}</b> · ${g.length}</span>${lv !== "high" && rec.length ? `<button class="btn xs ghost" data-accept="${esc(rec.join(","))}" data-tip="${esc(T("ib.accept_tip", {n: rec.length}))}">${esc(T("ib.accept", {n: rec.length}))}</button>` : ""}</div>` + g.map(row).join("");
    }
  } else html = xs.map(row).join("");
  list.querySelectorAll(":scope > :not(.hl-bar):not(.hl-sel)").forEach(n => n.remove());
  if (!anim) html = html.replace(/ data-in="left"/g, "");
  list.insertAdjacentHTML("beforeend", html || `<div class="empty" data-in>${esc(IB.q ? T("ib.no_match") : T("ib.empty"))}<br><span class="small">${esc(T("ib.tab_" + IB.tab + "_tip"))}</span></div>`);
  if (anim) { list.scrollTop = 0; playIn(list); } else $$("[data-in]", list).forEach(e => e.classList.add("in"));
  if (!IB.sel || !xs.some(x => x.id === IB.sel)) IB.sel = xs[0] ? xs[0].id : null;
  markSel(); renderDetail();
}
function markSel() { const r = IB.sel && $(`#ib-list .irow[data-id="${CSS.escape(IB.sel)}"]`); IB.hl.select(r); if (r) r.scrollIntoView({block: "nearest", behavior: "smooth"}); }
function selectItem(id) { if (id === IB.sel) return; IB.sel = id; markSel(); renderDetail(); }
function moveSel(d) { const xs = visibleItems(), i = xs.findIndex(x => x.id === IB.sel); const n = xs[Math.max(0, Math.min(xs.length - 1, i + d))]; if (n) selectItem(n.id); }
function renderDetail() {
  const D = $("#ib-detail"), x = IB.data && IB.data.items.find(i => i.id === IB.sel);
  if (!x) { D.innerHTML = `<div class="empty" style="padding-top:120px"><div style="font:400 30px var(--f-disp);color:var(--ink2)">${esc(IB.tab === "decide" ? T("ib.nothing_to_decide") : T("ib.empty"))}</div><div class="small" style="margin-top:6px">${esc(T("ib.tab_" + IB.tab + "_tip"))}</div></div>`; return; }
  const t = x.triage || {}, lv = impLv(x), rec = t.recommend ?? -1, dups = IB.b.dupN[x.id] || 0;
  const meta = `<div class="idt-meta"><span class="badge" style="color:${IMP_COL[lv]}">● ${esc(T("ib.imp_" + lv))}</span><span>${ibSrc(x)}</span>${x.src === "report" ? `<span class="mono tiny">inbox/${esc(x.file)}</span>` : ""}
    ${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(T("ib.open_link"))} ↗</a>` : ""}${dups ? `<span class="acc">${esc(T("ib.dups_together", {n: dups}))}</span>` : ""}</div>
    ${(t.pages || []).length ? `<div class="idt-meta" style="margin-top:6px">${esc(T("ib.pages"))} ${ibPages(t)}</div>` : ""}`;
  const report = `<details class="fold" data-body="1"><summary>${esc(T("ib.see_report"))}</summary><div class="in-fold"><div class="md"></div></div></details>`;
  const textBox = (label, ph) => `<details class="fold"${IB.tab === "stuck" ? " open" : ""}><summary>${esc(label)}</summary><div class="in-fold"><textarea class="inp" id="ib-text" placeholder="${esc(ph)}"></textarea>
    <div style="display:flex;gap:8px;margin-top:8px;align-items:center"><button class="btn sm primary lens" data-act="text">${esc(T("c.submit"))}</button><span class="muted small">${esc(T("ib.cmd_enter"))}</span></div></div></details>`;
  const resBadge = s => RESULT_BADGE[s] ? `<span class="badge ${RESULT_BADGE[s][0]}">${RESULT_BADGE[s][1]} ${esc(T("ib.st_" + s))}</span>` : "";
  const commit = x.result && x.result.commit ? `<span class="mono small muted" style="margin-left:8px">${esc(T("ib.commit", {c: x.result.commit}))}</span>` : "";
  let body = "";
  if (IB.tab === "decide") body = `${t.context ? `<div class="idt-ctx" data-in>${esc(t.context)}</div>` : ""}
    <div class="opts">${(t.options || []).map((o, i) => `<button class="opt${i === rec ? " rec" : ""}" data-i="${i}" data-in="pop" style="--i:${i + 2}"><div class="ol"><span class="kb">${i + 1}</span>${esc(o.label)}${i === rec ? `<span class="tag">${esc(T("ib.recommended"))}</span>` : ""}</div><div class="oe">${esc(o.effect)}</div></button>`).join("")}</div>
    ${rec >= 0 && t.recommend_why ? `<div class="idt-why" data-in>${esc(T("ib.why_rec"))}: ${esc(t.recommend_why)}</div>` : ""}
    <div class="idt-more" data-in>${textBox(T("ib.other_decision"), T("ib.other_ph"))}${report}<div><span class="link small" data-act="dismiss">${esc(T("ib.no_need"))}</span></div></div>
    <div class="kh"><span><kbd>1</kbd>–<kbd>${Math.max(1, (t.options || []).length)}</kbd> ${esc(T("ib.k_pick"))}</span><span><kbd>J</kbd><kbd>K</kbd> ${esc(T("ib.k_move"))}</span><span><kbd>E</kbd> ${esc(T("ib.no_need"))}</span><span><kbd>U</kbd> ${esc(T("ib.k_undo"))}</span></div>`;
  else if (IB.tab === "waiting") body = `<div class="idt-why" data-in>${esc(T("ib.your_decision"))}: ${esc((x.decision || {}).label || "")}${(x.decision || {}).text ? "　" + esc(x.decision.text) : ""}</div>
    <div class="idt-more" data-in>${x.status === "decided" ? `<div><button class="btn sm ghost" data-act="undo">${esc(T("ib.undo_decision"))}</button></div>` : `<div class="badge run">${esc(T("ib.st_queued"))}</div>`}${report}</div>`;
  else if (IB.tab === "results") body = `<div data-in>${resBadge(x.status)}${commit}</div><div class="idt-ctx" style="margin-top:10px" data-in>${esc((x.result || {}).note || "")}</div>
    ${x.decision ? `<div class="idt-why" style="margin-top:12px" data-in>${esc(T("ib.your_decision"))}: ${esc(x.decision.label || x.decision.text || "")}</div>` : ""}<div class="idt-more" data-in>${report}</div>`;
  else if (IB.tab === "stuck") body = `<div class="idt-ctx" data-in>${esc(T("ib.research_result"))}: ${esc((x.research || {}).note || "")}</div><div class="idt-more" data-in>${textBox(T("ib.add_info"), T("ib.add_info_ph"))}${report}<div><span class="link small" data-act="dismiss">${esc(T("ib.no_need"))}</span></div></div>`;
  else if (IB.tab === "agent") body = `<div class="idt-ctx" data-in>${x.research ? esc(T("ib.researched_note", {n: (x.research.raws || []).length, note: x.research.note || ""})) : t.needs_web ? esc(T("ib.needs_web_long")) : t.needs_local ? esc(T("ib.needs_local_long")) : esc(T("ib.queued_long"))}</div>
    ${t.why ? `<div class="idt-why" style="margin-top:12px" data-in>${esc(T("ib.why_sorted"))}: ${esc(t.why)}</div>` : ""}<div class="idt-more" data-in>${report}</div>`;
  else if (IB.tab === "untriaged") body = `<div class="idt-ctx" data-in>${esc(T("ib.untriaged_note"))}</div><div class="idt-more" data-in>${report}<div><span class="link small" data-act="dismiss">${esc(T("ib.no_need"))}</span></div></div>`;
  else if (IB.tab === "system") body = `<div class="idt-ctx" data-in>${esc(t.why || "")}</div><div class="idt-more" data-in>${report}<div><span class="link small" data-act="dismiss">${esc(T("ib.mark_handled"))}</span></div></div>`;
  else if (x.status === "resolved") body = `<div data-in><span class="badge ok">✓ ${esc(T("ib.st_resolved"))}</span>${commit}</div>
    <div class="idt-ctx" style="margin-top:10px" data-in>${esc((x.result || {}).note || "")}</div><div class="idt-more" data-in>${report}</div>`;
  else body = `<div class="idt-ctx" data-in>${esc(t.why || (x.status === "dismissed" ? T("ib.you_dismissed") : ""))}</div><div class="idt-more" data-in>${report}</div>`;
  D.innerHTML = `<div class="idt">${meta}<div class="idt-q" data-in>${esc(ibTitle(x))}</div>${body}</div>`;
  D.scrollTop = 0; playIn(D, 60);
  const ta = $("#ib-text"); if (ta) ta.onkeydown = e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submitText(e); } };
}
// one item done: its row slides out, the next is selected, data refreshes in the background; the toast offers undo
async function finish(id, msg, undoable, status = "decided") {
  const xs = visibleItems(), i = xs.findIndex(x => x.id === id), next = xs[i + 1] || xs[i - 1];
  const r = $(`#ib-list .irow[data-id="${CSS.escape(id)}"]`); if (r) r.classList.add("out");
  const it = IB.data.items.find(x => x.id === id); if (it) it.status = status;
  IB.b = ibBuckets(IB.data.items);
  for (const k of IB_TABS) tick($(`#ib-tabs [data-n="${k}"]`), IB.b[k].length);
  tick($("#ib-big"), IB.b.decide.length);
  IB.sel = next ? next.id : null;
  setTimeout(() => flip($("#ib-list"), ".irow", () => renderList(false)), 430);
  toast(msg, "", undoable ? {action: {label: T("c.undo"), fn: async () => { const u = await post("inbox-undo", {id}); toast(T("ib.undone"), u.message); loadInbox(); pollLive(); }}} : {});
  pollLive(); setTimeout(() => loadInbox(), 1500);
}
async function choose(i, el) {
  const x = IB.data.items.find(i2 => i2.id === IB.sel); if (!x || IB.tab !== "decide") return;
  el.classList.remove("chosen"); void el.offsetWidth; el.classList.add("chosen");
  const label = ((x.triage || {}).options || [])[i]?.label || "";
  const r = await post("inbox-decide", {id: x.id, choice: i});
  if (!r.ok) return toast(T("c.failed"), r.message);
  finish(x.id, T("ib.recorded", {label}), true);
}
async function submitText() {
  const ta = $("#ib-text"), t = ta && ta.value.trim(); if (!t) { toast(T("ib.write_first")); ta && ta.focus(); return; }
  const x = IB.data.items.find(i => i.id === IB.sel); if (!x) return;
  const r = await post("inbox-decide", {id: x.id, text: t});
  if (!r.ok) return toast(T("c.failed"), r.message);
  finish(x.id, T("ib.recorded_text"), true);
}
async function dismiss() {
  const x = IB.data.items.find(i => i.id === IB.sel); if (!x || !["decide", "stuck", "system", "untriaged"].includes(IB.tab)) return;
  const r = await post("inbox-dismiss", {id: x.id});
  if (!r.ok) return toast(T("c.failed"), r.message);
  finish(x.id, IB.tab === "system" ? T("ib.handled") : T("ib.dismissed"), true, "dismissed");
}
async function undo() {
  const x = IB.data.items.find(i => i.id === IB.sel);
  const target = x && x.status === "decided" ? x : IB.data.items.filter(i => i.status === "decided" || i.status === "dismissed").sort((a, b) => ((b.decision || {}).ts || "").localeCompare((a.decision || {}).ts || ""))[0];
  if (!target) return toast(T("ib.nothing_to_undo"));
  const r = await post("inbox-undo", {id: target.id}); toast(T("ib.undone"), r.message); loadInbox(); pollLive();
}
async function acceptGroup(e, ids) {
  const xs = ids.split(",");
  if (!(await confirmBox(e, T("ib.accept_title", {n: xs.length}), esc(T("ib.accept_body")), T("ib.accept_ok")))) return;
  const r = await post("inbox-accept", {ids: xs}); toast(T("ib.recorded_text"), r.message); loadInbox(); pollLive();
}
