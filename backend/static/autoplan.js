// 自動調整課表 (engine/plan_auto.py): the pending proposal banner (同意／拒絕),
// the last change-log entries (each with 復原). Mounts into the element with id="autoplan".
// The plan.auto.* settings live in 課表 › 課表偏好 › 自動調整 (schedule.html), not here.
//   <div id="autoplan" data-log="collapsible"></div>
// data-log: "" = the change log as a box, "none" = no log (總覽), "collapsible" = a <details>,
// collapsed by default, open state in localStorage autoplan.log.open (課表).
//   <script src="/api/v1/static/autoplan.js"></script>
// After a decision it calls window.onAutoPlanChange() when the page defines it.
(function () {
  const API = "/api/v1/overview/plan/auto";
  const root = document.getElementById("autoplan");
  if (!root) return;
  const css = `
  #autoplan { display: grid; gap: 8px; }
  #autoplan .ap-box { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; font-size: 13px; }
  #autoplan .ap-pend { border-color: #d98b00; background: color-mix(in srgb, #d98b00 9%, var(--panel)); }
  #autoplan .ap-pend h3 { margin: 0 0 4px; font-size: 14px; }
  #autoplan ul { margin: 4px 0; padding-left: 18px; }
  #autoplan li { margin: 2px 0; }
  #autoplan .ap-row { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; padding: 4px 0; border-top: 1px solid var(--line); }
  #autoplan .ap-row:first-of-type { border-top: 0; }
  #autoplan .ap-at { color: var(--muted); font-size: 12px; min-width: 74px; }
  #autoplan .ap-sum { flex: 1; min-width: 160px; }
  #autoplan .ap-st { font-size: 11px; border: 1px solid var(--line); border-radius: 8px; padding: 0 6px; color: var(--muted); }
  #autoplan .ap-err { color: #c0392b; font-size: 12px; }
  #autoplan button { font: inherit; font-size: 12.5px; border: 1px solid var(--line); background: var(--panel); color: var(--text);
    border-radius: 6px; padding: 3px 10px; cursor: pointer; }
  #autoplan button.pri { background: var(--accent); color: #fff; border-color: var(--accent); }
  #autoplan button:disabled { opacity: .55; cursor: progress; }
  #autoplan .ap-busy { margin-top: 6px; }
  #autoplan details summary { cursor: pointer; color: var(--muted); font-size: 12.5px; }
  #autoplan details.ap-log > summary { padding: 1px 0; }
  #autoplan details.ap-log[open] > summary { margin-bottom: 4px; }
  #autoplan .meta { color: var(--muted); font-size: 12px; }`;
  const st = document.createElement("style");
  st.textContent = css;
  document.head.appendChild(st);

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const md = (d) => { if (!d) return "—"; const [, m, x] = d.split("-"); return `${+m}/${+x}`; };
  const when = (iso) => { if (!iso) return ""; const t = new Date(iso); return `${t.getMonth() + 1}/${t.getDate()} ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`; };
  const T = (k, p) => (window.I18N ? window.I18N.t(k, p) : k);
  // SP-909: the status / action / push words through common.autoplan.* (a code the catalog lacks: as sent)
  const STATUS = ["applied", "pending", "approved", "rejected", "superseded", "undone", "restore", "failed"];
  const ACTION = ["added", "removed", "changed", "done", "missed", "note", "dropped"];
  const PUSH = ["ok", "partial", "failed", "unchanged", "off", "held", "notice_removed"];
  const word = (list, ns, k) => (list.includes(k) ? T(`common.autoplan.${ns}.${k}`) : k);
  const LOG_KEY = "autoplan.log.open";
  const logOpen = () => { try { return localStorage.getItem(LOG_KEY) === "1"; } catch (_) { return false; } };
  root.addEventListener("toggle", (e) => {
    if (!e.target.classList || !e.target.classList.contains("ap-log")) return;
    try { localStorage.setItem(LOG_KEY, e.target.open ? "1" : "0"); } catch (_) { /* storage blocked */ }
  }, true);

  async function sj(method, url, body) {
    const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((j && (j.detail && (j.detail.detail || j.detail))) || r.statusText);
    return j;
  }

  function itemsHtml(items, onlyForward) {
    const xs = (items || []).filter((i) => !onlyForward || ["added", "removed", "changed", "dropped", "note"].includes(i.action));
    if (!xs.length) return "";
    return `<ul>${xs.slice(0, 8).map((i) => `<li>${esc(T("common.autoplan.item", { day: md(i.day), act: word(ACTION, "act", i.action), title: i.title || "" }))}${i.reason ? esc(T("common.autoplan.item_why", { why: i.reason })) : ""}</li>`).join("")}${xs.length > 8 ? `<li class="meta">${esc(T("common.autoplan.more", { n: xs.length - 8 }))}</li>` : ""}</ul>`;
  }

  function pushText(p) {
    if (!p) return "";
    // "pushing": the run saved the plan and is sending it to the watch (SP-362: outside the plan lock)
    // "interrupted": the push was cut off (shutdown / cancelled); the next push sends what is missing
    const t = p.status === "pushing" ? T("common.autoplan.pushing")
      : p.status === "interrupted" ? T("common.autoplan.interrupted") : p.status ? word(PUSH, "push", p.status) : "";
    const err = [p.error, ...(p.errors || [])].filter(Boolean).join(T("common.autoplan.sep"));
    return ` <span class="meta">${esc(t)}${p.sent ? esc(T("common.autoplan.sent", { n: p.sent })) : ""}</span>${err ? ` <span class="ap-err">${esc(err)}</span>` : ""}`;
  }

  async function render() {
    let b;
    try { b = await sj("GET", `${API}?limit=${root.dataset.limit || 5}`); } catch (e) { root.innerHTML = ""; return; }
    const out = [];
    const p = b.pending;
    if (p) {
      out.push(`<div class="ap-box ap-pend" role="alert"><h3>${esc(T("common.autoplan.pending_title"))} <span class="meta">${when(p.at)}</span></h3>
        <div>${(p.big || []).map((x) => esc(x.text)).join(esc(T("common.autoplan.sep")))}</div>${itemsHtml(p.items, true)}
        <div class="meta">${p.notice_uid ? esc(T("common.autoplan.pending_notice")) : ""}${esc(T("common.autoplan.pending_hold"))}</div>
        <div style="margin-top:6px;display:flex;gap:8px"><button class="pri" type="button" data-approve="${p.id}">${esc(T("common.autoplan.approve_btn"))}</button>
        <button type="button" data-reject="${p.id}">${esc(T("common.autoplan.reject_btn"))}</button></div></div>`);
    }
    const rows = (b.log || []).filter((e) => e.status !== "pending");
    // data-log="none": no change log (總覽 shows only the pending proposal);
    // data-log="collapsible": a <details>, collapsed by default, remembered per browser (課表)
    const logMode = root.dataset.log || "";
    if (rows.length && logMode !== "none") {
      const list = rows.map((e) => `<div class="ap-row">
        <span class="ap-at">${when(e.at)}</span><span class="ap-st">${esc(word(STATUS, "st", e.status))}</span>
        <span class="ap-sum">${esc(e.summary)}${itemsHtml(e.items, true)}${pushText(e.push)}</span>
        ${e.can_undo ? `<button type="button" data-undo="${e.id}" title="${esc(T("common.autoplan.undo_tip"))}">${esc(T("common.autoplan.undo_btn"))}</button>` : ""}</div>`).join("");
      if (logMode === "collapsible") {
        out.push(`<details class="ap-box ap-log"${logOpen() ? " open" : ""}><summary>${esc(T("common.autoplan.log_title"))} <span class="meta">${esc(T("common.autoplan.log_count", { n: rows.length }))}</span></summary>${list}</details>`);
      } else {
        out.push(`<div class="ap-box"><div class="meta" style="margin-bottom:4px">${esc(T("common.autoplan.log_title"))}</div>${list}</div>`);
      }
    }
    root.innerHTML = out.join("");
  }

  // SP-377: 同意／拒絕／復原 wait for the push to the watch (20–30 s): while one runs every button
  // in the box is disabled and the box says what is happening, so a second click can't send it again
  let busy = false;
  async function act(fn, label, t, msg) {
    if (busy) return;
    busy = true;
    const box = t.closest(".ap-box") || root;
    box.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    box.setAttribute("aria-busy", "true");
    const note = document.createElement("div");
    note.className = "meta ap-busy";
    note.setAttribute("role", "status");
    note.textContent = msg;
    t.parentElement.after(note);
    try {
      const r = await fn();
      if (r && r.status === "already_approved") note.textContent = T("common.autoplan.already_approved");
    } catch (e) { alert(T("common.autoplan.failed", { what: label, why: e.message })); }
    busy = false;
    await render();
    if (typeof window.onAutoPlanChange === "function") window.onAutoPlanChange();
  }

  root.addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t || t.disabled || busy) return;
    if (t.dataset.approve) act(() => sj("POST", `${API}/proposal/${t.dataset.approve}/approve`), T("common.autoplan.label_approve"), t, T("common.autoplan.approving"));
    else if (t.dataset.reject) act(() => sj("POST", `${API}/proposal/${t.dataset.reject}/reject`), T("common.autoplan.label_reject"), t, T("common.autoplan.rejecting"));
    else if (t.dataset.undo && confirm(T("common.autoplan.undo_confirm"))) act(() => sj("POST", `${API}/log/${t.dataset.undo}/undo`), T("common.autoplan.undo_btn"), t, T("common.autoplan.undoing"));
  });
  window.autoPlanRefresh = render;
  render();
})();
