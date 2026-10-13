/*
 * 專項 chip (SP-381): which sport the charts are about — 主要訓練項目 (engine/primary_sport.py,
 * the setting athlete.primary_sport: auto | trail | road), the same switch as 設定 › 主要訓練項目.
 * 圖表分析 hides the charts of the other sport (wko5views: the viewer's catalog), so changing it
 * saves the setting (PUT /api/v1/sync/settings) and reloads the page. It replaced the 資料來源
 * chip there (the data source stays in 設定 › 資料同步).
 *
 *     <span id="sport-chip"></span><script src="/api/v1/static/sportchip.js" defer></script>
 *
 * SP-450: 賽事計算機 and 課表 mount it too (their top row): the calculator's default race type and
 * the 自動課表 follow 主要訓練項目, and both pages read it when they load, so the reload applies it.
 *
 * SPORTS lists the choices: a new sport (三鐵) is one more entry here and in primary_sport.py.
 * The demo can't change settings: the chip shows the sport in effect, disabled.
 *
 * Colours (SP-447): the select and its options take the page's --panel / --text (every page that
 * mounts it has them; Canvas / CanvasText otherwise) and the dark color-scheme on a dark page. A
 * transparent select with an inherited text colour left the option list on the browser's own white
 * with the page's white text (Chrome / Edge on Windows, Firefox), and the native list light on macOS.
 * The chosen option keeps a mark (--line), which the option background would otherwise hide.
 */
(function () {
  "use strict";
  const SPORTS = ["auto", "trail", "road"];
  const T = (k, p) => (window.I18N ? window.I18N.t("common.sportchip." + k, p) : k);
  const CSS = `
  .sport-chip { display: inline-flex; gap: 6px; align-items: center; font-size: 12.5px; padding: 1px 3px 1px 10px;
    border: 1px solid var(--line, currentColor); border-radius: 12px; background: var(--panel, Canvas);
    color: var(--text, CanvasText); white-space: nowrap; }
  .sport-chip > span { color: var(--muted, inherit); }
  .sport-chip select, .sport-chip option { background-color: var(--panel, Canvas); color: var(--text, CanvasText); }
  .sport-chip option:checked { background-color: var(--line, Highlight); }
  .sport-chip select { font: inherit; border: 0; border-radius: 10px; padding: 1px 2px; margin: 0; cursor: pointer; }
  .sport-chip select:disabled { cursor: default; opacity: 1; }
  .sport-chip select:focus-visible { outline: 2px solid var(--accent, Highlight); outline-offset: 1px; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .sport-chip select { color-scheme: dark; } }
  :root[data-theme="dark"] .sport-chip select { color-scheme: dark; }`;
  const st = document.createElement("style");
  st.textContent = CSS;
  (document.head || document.documentElement).appendChild(st);

  async function mount() {
    const host = document.getElementById("sport-chip");
    if (!host) return;
    let ps = null;
    try {
      const r = await fetch("/api/v1/wko5/primary-sport");
      if (r.ok) ps = await r.json();
    } catch (_) { /* no data yet: no chip */ }
    if (!ps) return;
    const demo = (window.TRC_SESSION || {}).mode === "demo";
    const lab = document.createElement("label");
    lab.className = "sport-chip";
    lab.title = T("tip");
    const name = document.createElement("span");
    name.textContent = T("label");
    const sel = document.createElement("select");
    sel.id = "sport-sel";
    sel.setAttribute("aria-label", T("label"));
    for (const k of SPORTS) {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = k === "auto" ? T("auto", { sport: T(ps.sport === "road" ? "road" : "trail") }) : T(k);
      if (k === (ps.setting || "auto")) o.selected = true;
      sel.appendChild(o);
    }
    sel.disabled = demo;
    if (demo) lab.title = T("demo");
    sel.onchange = async () => {
      sel.disabled = true;
      try {
        const r = await fetch("/api/v1/sync/settings", { method: "PUT", headers: { "Content-Type": "application/json" },
                                                          body: JSON.stringify({ primary_sport: sel.value }) });
        if (!r.ok) throw new Error(r.statusText);
        location.reload();
      } catch (e) {
        sel.disabled = false;
        alert(T("failed", { msg: String(e.message || e).slice(0, 80) }));
      }
    };
    lab.append(name, sel);
    host.replaceChildren(lab);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
