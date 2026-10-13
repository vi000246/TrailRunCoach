/* SP-472: 「COROS 帳號的體重是 X kg，要更新嗎？」 — on 總覽 and 設定 › 基本資料. The COROS account weight
 * (stored at every COROS sign-in, athlete_settings.weight_kg) is never written into the weight entries by
 * itself; when it differs from the newest entry by ≥ 1 kg (engine/weight_sync.coros_hint) this asks.
 *
 *   <div id="weighthint" hidden></div><script src="/api/v1/static/weight_hint.js" defer></script>
 *
 *   GET  /api/v1/weight/coros-hint          {hint: {kg, date, current_kg, current_date, diff} | null}
 *   「更新」 POST /api/v1/weight/coros/accept {kg}   「不用」 POST /api/v1/weight/coros/dismiss {kg}
 *
 * After 更新 it fires "weight:changed" (the settings page reloads its weight block). No hint, an error
 * or the demo (no such route): nothing shown. i18n: common.weighthint.*.
 */
(() => {
  if (window.WeightHint) return;
  const API = "/api/v1/weight";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const T = (k, p) => (window.I18N ? window.I18N.t("common.weighthint." + k, p) : k);
  const CSS = `
  .weighthint { --wh-c: var(--accent, #2563eb); background: color-mix(in srgb, var(--wh-c) 6%, var(--panel, #fff));
    border: 1px solid color-mix(in srgb, var(--wh-c) 40%, var(--line, #e1e5ea)); border-left: 5px solid var(--wh-c);
    border-radius: 12px; padding: 10px 14px 12px; display: grid; gap: 6px; color: var(--text, #111); margin: 8px 0; }
  .weighthint[hidden] { display: none; }
  .weighthint .wh-h { margin: 0; font-size: 14.5px; font-weight: 700; }
  .weighthint .wh-t { margin: 0; font-size: 13.5px; font-variant-numeric: tabular-nums; }
  .weighthint .wh-help { font-size: 12.5px; color: var(--muted, #667); }
  .weighthint .wh-act { display: flex; flex-wrap: wrap; gap: 6px; }
  .weighthint button { font: inherit; font-size: 13px; padding: 5px 13px; border-radius: 6px; cursor: pointer;
    border: 1px solid var(--line, #e1e5ea); background: var(--panel, #fff); color: inherit; }
  .weighthint button.pri { background: var(--wh-c); border-color: var(--wh-c); color: #fff; font-weight: 650; }
  .weighthint button:disabled { opacity: .55; cursor: default; }
  .weighthint .wh-ok { color: var(--good, #1a7f37); font-size: 13px; }
  .weighthint .wh-err { color: var(--bad, #c62828); font-size: 12.5px; }
  @media print { .weighthint { display: none; } }
  `;
  let hint = null, done = "";
  const els = () => [...document.querySelectorAll("#weighthint")];

  function render() {
    for (const el of els()) {
      el.hidden = !hint && !done;
      if (el.hidden) { el.innerHTML = ""; continue; }
      el.classList.add("weighthint");
      el.setAttribute("role", "region");
      el.setAttribute("aria-label", T("title"));
      el.innerHTML = done ? `<div class="wh-ok">${esc(done)}</div>` : `<h2 class="wh-h">${esc(T("title"))}</h2>
        <p class="wh-t">${esc(T("text", { kg: hint.kg, date: hint.date || "–", cur: hint.current_kg, cdate: hint.current_date,
          diff: (hint.diff > 0 ? "+" : "") + hint.diff }))}</p>
        <div class="wh-act"><button type="button" class="pri" data-wh="accept">${esc(T("accept", { kg: hint.kg }))}</button>
          <button type="button" data-wh="dismiss">${esc(T("dismiss"))}</button></div>
        <div class="wh-help">${esc(T("help"))}</div>`;
    }
  }
  async function load() {
    try {
      const r = await fetch(`${API}/coros-hint`, { cache: "no-store" });
      hint = r.ok ? (await r.json()).hint : null;
    } catch { hint = null; }
    render();
  }
  document.addEventListener("click", async (e) => {
    const b = e.target.closest(".weighthint [data-wh]");
    if (!b || !hint) return;
    const box = b.closest(".weighthint");
    box.querySelectorAll("button").forEach((x) => { x.disabled = true; });
    const kg = hint.kg;
    try {
      const r = await fetch(`${API}/coros/${b.dataset.wh}`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kg }) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      hint = null;
      if (b.dataset.wh === "accept") {
        done = T("done", { kg });
        document.dispatchEvent(new CustomEvent("weight:changed", { detail: { kg, from: "coros" } }));
        setTimeout(() => { done = ""; render(); }, 6000);
      }
      render();
    } catch (err) {
      box.querySelectorAll("button").forEach((x) => { x.disabled = false; });
      box.querySelector(".wh-err")?.remove();
      box.insertAdjacentHTML("beforeend", `<div class="wh-err">${esc(T("failed", { msg: err.message }))}</div>`);
    }
  });
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);
  window.WeightHint = { load };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  else load();
})();
