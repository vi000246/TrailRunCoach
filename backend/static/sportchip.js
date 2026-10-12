/*
 * 專項 chip (SP-381): which sport the charts are about — 主要訓練項目 (engine/primary_sport.py,
 * the setting athlete.primary_sport: auto | trail | road), the same switch as 設定 › 主要訓練項目.
 * 圖表分析 hides the charts of the other sport (wko5views: the viewer's catalog), so changing it
 * saves the setting (PUT /api/v1/sync/settings) and reloads the page. It replaced the 資料來源
 * chip there (the data source stays in 設定 › 資料同步).
 *
 *     <span id="sport-chip"></span><script src="/api/v1/static/sportchip.js" defer></script>
 *
 * SPORTS lists the choices: a new sport (三鐵) is one more entry here and in primary_sport.py.
 * The demo can't change settings: the chip shows the sport in effect, disabled.
 */
(function () {
  "use strict";
  const SPORTS = ["auto", "trail", "road"];
  const T = (k, p) => (window.I18N ? window.I18N.t("common.sportchip." + k, p) : k);

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
    lab.style.cssText = "display:inline-flex;gap:6px;align-items:center;font-size:12.5px;padding:2px 8px;"
      + "border:1px solid currentColor;border-radius:12px;opacity:.85";
    lab.title = T("tip");
    const name = document.createElement("span");
    name.textContent = T("label");
    const sel = document.createElement("select");
    sel.id = "sport-sel";
    sel.setAttribute("aria-label", T("label"));
    sel.style.cssText = "font:inherit;border:0;background:transparent;color:inherit";
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
