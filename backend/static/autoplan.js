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
  const STATUS = { applied: "已套用", pending: "待確認", approved: "已同意", rejected: "已拒絕", superseded: "已被新提案取代",
    undone: "已復原", restore: "復原", failed: "失敗" };
  const ACTION = { added: "新增", removed: "取消", changed: "調整", done: "完成", missed: "沒跑", note: "備註", dropped: "不補" };
  const PUSH = { ok: "已推送到手錶", partial: "部分推送失敗", failed: "推送失敗", unchanged: "手錶上已是最新", off: "未推送（自動推送已關）",
    held: "等你確認，手錶維持原本的課", notice_removed: "已移除手錶上的提醒" };

  const T = (k, p) => (window.I18N ? window.I18N.t(k, p) : k);
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
    return `<ul>${xs.slice(0, 8).map((i) => `<li>${md(i.day)} ${esc(ACTION[i.action] || i.action)}「${esc(i.title)}」${i.reason ? "：" + esc(i.reason) : ""}</li>`).join("")}${xs.length > 8 ? `<li class="meta">…還有 ${xs.length - 8} 項</li>` : ""}</ul>`;
  }

  function pushText(p) {
    if (!p) return "";
    // "pushing": the run saved the plan and is sending it to the watch (SP-362: outside the plan lock)
    const t = p.status === "pushing" ? T("common.autoplan.pushing") : PUSH[p.status] || p.status || "";
    const err = [p.error, ...(p.errors || [])].filter(Boolean).join("；");
    return `<span class="meta">${esc(t)}${p.sent ? `（${p.sent} 堂）` : ""}</span>${err ? ` <span class="ap-err">${esc(err)}</span>` : ""}`;
  }

  async function render() {
    let b;
    try { b = await sj("GET", `${API}?limit=${root.dataset.limit || 5}`); } catch (e) { root.innerHTML = ""; return; }
    const out = [];
    const p = b.pending;
    if (p) {
      out.push(`<div class="ap-box ap-pend" role="alert"><h3>⚠ 課表待確認 <span class="meta">${when(p.at)}</span></h3>
        <div>${(p.big || []).map((x) => esc(x.text)).join("；")}</div>${itemsHtml(p.items, true)}
        <div class="meta">${p.notice_uid ? "手錶上有一堂「⚠ 課表待確認」提醒，按下同意／拒絕後會自動移除。" : ""}在你決定之前，手錶維持原本的課。</div>
        <div style="margin-top:6px;display:flex;gap:8px"><button class="pri" type="button" data-approve="${p.id}">同意，套用並推送</button>
        <button type="button" data-reject="${p.id}">拒絕，維持原本的課</button></div></div>`);
    }
    const rows = (b.log || []).filter((e) => e.status !== "pending");
    // data-log="none": no change log (總覽 shows only the pending proposal);
    // data-log="collapsible": a <details>, collapsed by default, remembered per browser (課表)
    const logMode = root.dataset.log || "";
    if (rows.length && logMode !== "none") {
      const list = rows.map((e) => `<div class="ap-row">
        <span class="ap-at">${when(e.at)}</span><span class="ap-st">${esc(STATUS[e.status] || e.status)}</span>
        <span class="ap-sum">${esc(e.summary)}${itemsHtml(e.items, true)}${pushText(e.push)}</span>
        ${e.can_undo ? `<button type="button" data-undo="${e.id}" title="把這次動到的課還原成調整前，並重新推送（還原的課會變成你的課，之後自動調整不再動它們）">復原</button>` : ""}</div>`).join("");
      if (logMode === "collapsible") {
        out.push(`<details class="ap-box ap-log"${logOpen() ? " open" : ""}><summary>${esc(T("common.autoplan.log_title"))} <span class="meta">${esc(T("common.autoplan.log_count", { n: rows.length }))}</span></summary>${list}</details>`);
      } else {
        out.push(`<div class="ap-box"><div class="meta" style="margin-bottom:4px">${esc(T("common.autoplan.log_title"))}</div>${list}</div>`);
      }
    }
    root.innerHTML = out.join("");
  }

  async function act(fn, label) {
    try { await fn(); } catch (e) { alert(`${label}失敗：${e.message}`); }
    await render();
    if (typeof window.onAutoPlanChange === "function") window.onAutoPlanChange();
  }

  root.addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.approve) act(() => sj("POST", `${API}/proposal/${t.dataset.approve}/approve`), "同意");
    else if (t.dataset.reject) act(() => sj("POST", `${API}/proposal/${t.dataset.reject}/reject`), "拒絕");
    else if (t.dataset.undo && confirm("把這次自動調整動到的課還原，並重新推送到手錶？")) act(() => sj("POST", `${API}/log/${t.dataset.undo}/undo`), "復原");
  });
  window.autoPlanRefresh = render;
  render();
})();
