/* Single-activity view: the activity's metadata — 活動類型 and 努力度, auto-
 * filled and editable, plus a note (like WKO5's workout metadata;
 * engine/activity_tags.py). Loaded by wko5_viewer.html with one script tag,
 * the same way as segments_card.js: it watches #grid and puts the card first
 * whenever a workout is shown. Reads the viewer's global `S` (S.mode, S.workout).
 *
 * GET / PATCH /api/v1/wko5/workouts/{idx}/activity. Choosing 「自動」 sends
 * null (back to the auto value); a user value shows the 「手動」 badge.
 */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const URL_ = (i) => `/api/v1/wko5/workouts/${i}/activity`;
  let pending = null;

  function state() {
    try { return typeof S !== "undefined" ? S : null; } catch { return null; }
  }

  const badge = (manual) => manual
    ? `<span title="你設定的值" style="font-size:11px;padding:1px 6px;border-radius:9px;background:var(--accent);color:#fff;margin-left:6px">手動</span>`
    : `<span title="依規則自動判定" style="font-size:11px;padding:1px 6px;border-radius:9px;border:1px solid var(--line);color:var(--muted, #888);margin-left:6px">自動</span>`;

  function select(name, opts, cur, manual, autoLabel) {
    const o = [`<option value="" ${manual ? "" : "selected"}>自動（${esc(autoLabel || "—")}）</option>`]
      .concat(Object.entries(opts).map(([k, v]) => `<option value="${k}" ${manual && cur === k ? "selected" : ""}>${esc(v)}</option>`));
    return `<select data-f="${name}" style="font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:6px;background:transparent;color:inherit">${o.join("")}</select>`;
  }

  // bad activity files (engine/bad_activity.py): this workout is in the
  // dataset, so it is not excluded; offer 手動排除, and when the rule flags it
  // but the user kept it, say so with 恢復自動判定
  function exclusionRow(x) {
    if (!x) return "";
    const btn = (v, t) => `<button data-ex="${v}" style="font:inherit;font-size:11.5px;padding:1px 8px;border:1px solid var(--line);border-radius:6px;background:transparent;color:inherit;cursor:pointer;margin-left:6px">${t}</button>`;
    const kept = x.override === "keep";
    return `<div class="meta" style="font-size:11.5px;margin-top:6px">排除：` +
      (kept ? `你標成正常（這筆是正常的，不要排除）${x.flagged ? "；規則判定：" + esc(x.flagged) : ""}${btn("auto", "恢復自動判定")}`
            : `沒有排除${btn("exclude", "手動排除")}`) + `</div>`;
  }

  function render(card, r) {
    const body = card.querySelector(".body");
    const row = (lab, ctl, manual, why) => `<div style="display:flex;align-items:center;gap:8px;margin:6px 0;flex-wrap:wrap">
        <span style="min-width:4.5em;color:var(--muted, #888);font-size:13px">${lab}</span>${ctl}${badge(manual)}</div>
        ${why ? `<div class="meta" style="font-size:11.5px;margin:-2px 0 6px 5.3em">${esc(why)}</div>` : ""}`;
    const ed = r.effort_detail || {};
    const nums = ed.hr_frac != null ? `移動心率 ${(ed.hr_frac * 100).toFixed(0)}% LTHR` +
      (ed.above_aet != null ? `・AeT 以上 ${(ed.above_aet * 100).toFixed(0)}%` : "") +
      (ed.rest_share != null ? `・長休息（≥ 5 分）${(ed.rest_share * 100).toFixed(0)}%` : "") : "";
    body.innerHTML =
      row("活動類型", select("activity_type", r.types, r.activity_type, r.activity_type_overridden, r.activity_type_auto_label),
          r.activity_type_overridden, r.activity_type_overridden ? `自動判定：${r.activity_type_auto_label}（${r.activity_type_reason || ""}）` : r.activity_type_reason) +
      row("努力度", select("effort", r.efforts, r.effort, r.effort_overridden, r.effort_auto_label),
          r.effort_overridden, r.effort_overridden ? `自動判定：${r.effort_auto_label}（${r.effort_reason || ""}）` : r.effort_reason) +
      (nums ? `<div class="meta" style="font-size:11.5px;margin:0 0 6px 5.3em">${esc(nums)}</div>` : "") +
      `<div style="display:flex;gap:8px;margin-top:6px"><span style="min-width:4.5em;color:var(--muted, #888);font-size:13px">備註</span>
        <textarea data-f="note" rows="2" style="flex:1;font:inherit;padding:4px 6px;border:1px solid var(--line);border-radius:6px;background:transparent;color:inherit">${esc(r.note || "")}</textarea></div>` +
      (r.power && r.power.source && r.power.source !== "none"
        ? `<div class="meta" style="font-size:11.5px;margin-top:6px">功率來源：${esc(r.power.label || r.power.source)}` +
          (r.power.used ? "" : "（功率模型、功率 TSS 不採用；心率／配速照常使用）") + `</div>` : "") +
      (r.capacity ? `<div class="meta" style="font-size:11.5px;margin-top:6px">比賽能力樣本：${r.capacity.ok ? "是" : "否"}（${esc(r.capacity.reason || "")}）</div>` : "") +
      exclusionRow(r.exclusion_state) +
      `<div class="meta" data-msg style="font-size:11.5px;margin-top:4px;min-height:1em"></div>`;
    const msg = body.querySelector("[data-msg]");
    const save = async (patch) => {
      msg.textContent = "儲存中…";
      try {
        const res = await fetch(URL_(card.dataset.w), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
        if (!res.ok) throw new Error((await res.json()).detail || res.status);
        render(card, await res.json());
        card.querySelector("[data-msg]").textContent = "已儲存";
      } catch (e) { msg.textContent = "儲存失敗：" + e.message; }
    };
    body.querySelectorAll("button[data-ex]").forEach((b) => b.addEventListener("click", async () => {
      const v = b.dataset.ex === "auto" ? null : b.dataset.ex;
      if (v === "exclude" && !confirm("排除這筆活動？它會留在活動清單（標「已排除」），但不再算進 PMC、功率曲線、比賽功率和圖表。之後可以在清單或「設定 → 資料校正」取消。")) return;
      msg.textContent = "儲存中…";
      try {
        const res = await fetch(URL_(card.dataset.w), { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ exclusion: v }) });
        if (!res.ok) throw new Error((await res.json()).detail || res.status);
        // the dataset is rebuilt (the file may leave it): workout indices shift,
        // so drop the selection and reload the list
        const s = state();
        if (s) { s.workout = null; s.workoutLabel = null; }
        if (typeof loadActs === "function") loadActs();
        if (typeof load === "function") load();
      } catch (e) { msg.textContent = "儲存失敗：" + e.message; }
    }));
    body.querySelectorAll("select[data-f]").forEach((el) => el.addEventListener("change", () => save({ [el.dataset.f]: el.value || null })));
    const note = body.querySelector("textarea[data-f=note]");
    note.addEventListener("change", () => save({ note: note.value }));
  }

  async function fill(card, idx) {
    try {
      const r = await fetch(URL_(idx));
      if (!r.ok) throw new Error(r.status);
      render(card, await r.json());
    } catch { card.querySelector(".body").textContent = "讀取失敗"; }
  }

  function ensure() {
    const s = state(), grid = document.getElementById("grid");
    if (!s || !grid || s.mode !== "workout" || s.workout == null) return;
    if (!grid.querySelector("section.card:not(#tagcard):not(#segcard)")) return;   // viewer still starting its cards
    const cur = grid.querySelector("#tagcard");
    if (cur && +cur.dataset.w === s.workout) return;
    cur?.remove();
    const card = document.createElement("section");
    card.className = "card";
    card.id = "tagcard";
    card.dataset.w = s.workout;
    card.innerHTML = `<h2>活動資訊</h2><div class="body"><div class="loading">讀取中…</div></div>`;
    grid.prepend(card);
    fill(card, s.workout);
  }

  function watch() {
    const grid = document.getElementById("grid");
    if (!grid) return;
    new MutationObserver(() => {
      if (pending) return;
      pending = setTimeout(() => { pending = null; ensure(); }, 120);
    }).observe(grid, { childList: true });
    ensure();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watch); else watch();
})();
