/* 疼痛 one-tap picker (傷病紀錄, engine/injuries.py), shared by 活動編輯 and the
 * single-activity chart page.
 *
 *   const meta = await PainPicker.meta();          // null = feature hidden (demo mode) or unavailable
 *   el.innerHTML = PainPicker.html(state, meta);   // state = {pain, pain_area, injury, reentry}
 *   PainPicker.wire(el, state, meta, async (patch) => newState);
 *
 * patch = {pain} | {pain, pain_area, pain_side}. Tapping the lit button again
 * clears the mark (沒填). Styles come from the page tokens (--panel, --line,
 * --accent, --err, --warn, --muted, --text), so dark mode follows the page.
 */
(() => {
  const API = "/api/v1/wko5/injuries";
  const PAGE = `${API}/page`;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const LEVELS = [[0, "沒痛"], [1, "痠"], [2, "痛"], [3, "中斷"]];
  const HELP = "這次跑步有沒有痛。\n「痠」是練完正常的痠，不算受傷；「痛」是會影響跑姿、配速，或跑完還在痛；「中斷」是痛到停下來。\n" +
    "選「痛」或「中斷」會自動開一筆傷病紀錄（同部位 28 天內接到同一筆，推估），細節可以之後補。\n再點一次亮著的按鈕 = 清掉（沒填）。\n這不是診斷，持續痛請看醫師或物理治療師。";
  const CSS = `
  .pp { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .pp .pp-seg { display: inline-flex; border: 1px solid var(--line, #e1e5ea); border-radius: 8px; overflow: hidden; width: max-content; max-width: 100%; }
  .pp .pp-seg button { border: 0; border-right: 1px solid var(--line, #e1e5ea); border-radius: 0; background: var(--panel, #fff);
    color: var(--text, #111); padding: 4px 12px; font: inherit; font-size: 13px; cursor: pointer; min-height: 32px; }
  .pp .pp-seg button:last-child { border-right: 0; }
  .pp .pp-seg button[aria-pressed="true"] { background: var(--accent, #2563eb); color: #fff; font-weight: 600; }
  .pp .pp-seg button[data-p="1"][aria-pressed="true"] { background: var(--warn, #b45309); }
  .pp .pp-seg button[data-p="2"][aria-pressed="true"], .pp .pp-seg button[data-p="3"][aria-pressed="true"] { background: var(--err, #b91c1c); }
  .pp .pp-chips { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .pp .pp-chips button { font: inherit; font-size: 12px; padding: 2px 9px; border-radius: 12px; border: 1px solid var(--line, #e1e5ea);
    background: var(--bg, transparent); color: var(--text, #111); cursor: pointer; min-height: 26px; }
  .pp .pp-chips button[aria-pressed="true"] { border-color: var(--err, #b91c1c); color: var(--err, #b91c1c); font-weight: 600;
    background: color-mix(in srgb, var(--err, #b91c1c) 10%, transparent); }
  .pp .pp-chips .pp-sep { width: 1px; height: 16px; background: var(--line, #e1e5ea); margin: 0 2px; }
  .pp .pp-chips input { font: inherit; font-size: 12px; width: 6.5em; padding: 2px 6px; border-radius: 12px; border: 1px dashed var(--line, #e1e5ea);
    background: none; color: var(--text, #111); }
  .pp .pp-link { font-size: 12px; text-decoration: none; color: var(--err, #b91c1c); border: 1px solid currentColor; border-radius: 12px;
    padding: 1px 9px; width: max-content; max-width: 100%; }
  .pp .pp-link.draft { color: var(--warn, #b45309); }
  .pp .pp-link.ok { color: var(--muted, #667); }
  .pp .pp-re { font-size: 12px; color: var(--warn, #b45309); }
  .pp .pp-mon { font-size: 12px; color: var(--muted, #667); white-space: pre-line; max-width: 46em; }
  .pp.pp-hl { outline: 2px solid color-mix(in srgb, var(--warn, #b45309) 55%, transparent); outline-offset: 4px; border-radius: 6px; }
  .pp .pp-msg { font-size: 12px; color: var(--muted, #667); min-height: 1em; }
  .pp .pp-msg.err { color: var(--err, #b91c1c); }
  `;
  let metaP = null;

  function css() {
    if (document.getElementById("painpicker-css")) return;
    const st = document.createElement("style");
    st.id = "painpicker-css"; st.textContent = CSS;
    document.head.appendChild(st);
  }

  async function meta(force) {
    if (!metaP || force) {
      metaP = fetch(`${API}/meta`, { cache: "no-store" }).then((r) => r.ok ? r.json() : null).catch(() => null);
    }
    return metaP;
  }

  function linkChip(inj) {
    if (!inj) return "";
    const cls = inj.status === "draft" ? "draft" : inj.open ? "" : "ok";
    const txt = `傷病紀錄 #${inj.id}・${inj.label}（${inj.status_label}${inj.open ? `，第 ${inj.day_n} 天` : ""}）`;
    return `<a class="pp-link ${cls}" href="${PAGE}?id=${inj.id}" title="打開傷病紀錄">${esc(txt)} ›</a>`;
  }

  function html(state, m, opts = {}) {
    css();
    const s = state || {};
    const p = s.pain;
    const areas = (m && m.areas) || [];
    const seg = LEVELS.map(([v, t]) => `<button type="button" data-p="${v}" aria-pressed="${p === v}">${t}</button>`).join("");
    const showAreas = p != null && p >= 1;
    const area = s.pain_area;
    const noSide = (m && m.no_side) || [];
    const chips = showAreas ? `<div class="pp-chips" role="group" aria-label="部位">
        ${areas.map((a) => `<button type="button" data-area="${esc(a.key)}" aria-pressed="${area === a.key}">${esc(a.label)}</button>`).join("")}
        <input type="text" data-add-area maxlength="12" placeholder="＋其他部位" aria-label="新增部位，Enter">
        ${p >= 2 && area && !noSide.includes(area) ? `<span class="pp-sep"></span>
          ${Object.entries((m && m.sides) || {}).map(([k, t]) => `<button type="button" data-side="${k}" aria-pressed="${(s.side ?? s.injury?.side) === k}">${esc(t)}</button>`).join("")}` : ""}
      </div>` : "";
    const re = s.reentry ? `<div class="pp-re">恢復期：跑完記一下有沒有痛${opts.qtip ? opts.qtip(s.reentry.monitor || "") : ""}</div>` : "";
    // 傷別 (SP-269): the open event's pain-monitoring text (server-translated), with the disclaimer
    const mon = s.injury && s.injury.open && s.injury.monitor
      ? `<div class="pp-mon">${esc(s.injury.monitor + (m && m.disclaimer ? "\n" + m.disclaimer : ""))}</div>` : "";
    return `<div class="pp ${s.reentry && p == null ? "pp-hl" : ""}">
      <div class="pp-seg" role="group" aria-label="疼痛">${seg}</div>${chips}${linkChip(s.injury)}${mon}${re}
      <div class="pp-msg" aria-live="polite"></div></div>`;
  }

  function wire(root, state, m, save) {
    const s = state || {};
    const msg = (t, err) => { const el = root.querySelector(".pp-msg"); if (el) { el.textContent = t || ""; el.className = "pp-msg" + (err ? " err" : ""); } };
    const go = async (patch) => {
      msg("儲存中…");
      root.querySelectorAll(".pp button").forEach((b) => { b.disabled = true; });
      try {
        await save(patch);
      } catch (e) {
        msg("儲存失敗：" + e.message, true);
        root.querySelectorAll(".pp button").forEach((b) => { b.disabled = false; });
      }
    };
    root.querySelectorAll(".pp-seg button[data-p]").forEach((b) => b.addEventListener("click", () => {
      const v = +b.dataset.p;
      go({ pain: s.pain === v ? null : v });
    }));
    root.querySelectorAll(".pp-chips button[data-area]").forEach((b) => b.addEventListener("click", () => {
      const a = b.dataset.area;
      go({ pain: s.pain, pain_area: s.pain_area === a ? null : a });
    }));
    root.querySelectorAll(".pp-chips button[data-side]").forEach((b) => b.addEventListener("click", () => {
      s.side = b.dataset.side;
      go({ pain: s.pain, pain_area: s.pain_area, pain_side: b.dataset.side });
    }));
    const add = root.querySelector("input[data-add-area]");
    add?.addEventListener("keydown", async (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      const v = add.value.trim();
      if (!v) return;
      if (v.length > 12) { msg("部位名稱最多 12 字", true); return; }
      await go({ pain: s.pain, pain_area: v });
      meta(true);
    });
  }

  window.PainPicker = { meta, html, wire, linkChip, PAGE };
})();
