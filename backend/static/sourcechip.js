/*
 * Chart data-source chip: 資料來源 (the one synced source in use, COROS or
 * TrainingPeaks — chosen in 設定 → 資料同步) or the WKO5 folder (cross-check).
 *
 * One line in a page (e.g. wko5_viewer.html, next to the other header chips):
 *     <span id="source-chip"></span><script src="/api/v1/static/sourcechip.js" defer></script>
 * Without a #source-chip element it adds itself to the first <header>.
 * Changing it saves charts.data_source (PUT /api/v1/sync/settings) and reloads,
 * so every chart, the overview and race power read that source.
 */
(function () {
  "use strict";

  async function mount() {
    // the demo has one fixed source and no WKO5 cross-check: no chip
    if ((window.TRC_SESSION || {}).mode === "demo") return;
    let host = document.getElementById("source-chip");
    if (!host) {
      host = document.createElement("span");
      host.id = "source-chip";
      (document.querySelector("header") || document.body).appendChild(host);
    }
    let ss = {};
    try { ss = await (await fetch("/api/v1/sync/settings")).json(); } catch (_) {}
    const cur = ss.chart_data_source === "wko5" ? "wko5" : "source";
    const LABEL = { source: ss.primary_label || "COROS", wko5: "WKO5 資料夾（比對用）" };
    host.innerHTML = "";
    const lab = document.createElement("label");
    lab.style.cssText = "display:inline-flex;gap:6px;align-items:center;font-size:12.5px;padding:2px 8px;"
      + "border:1px solid currentColor;border-radius:12px;opacity:.85";
    lab.title = "圖表、總覽和功率計算機讀取的資料來源。COROS／TrainingPeaks 一次只用一個，在 設定 → 資料同步 切換";
    const sel = document.createElement("select");
    sel.style.cssText = "font:inherit;border:0;background:transparent;color:inherit";
    for (const k of Object.keys(LABEL)) {
      const o = document.createElement("option");
      o.value = k; o.textContent = LABEL[k]; if (k === cur) o.selected = true;
      sel.appendChild(o);
    }
    sel.onchange = async () => {
      sel.disabled = true;
      try {
        const r = await fetch("/api/v1/sync/settings", { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chart_data_source: sel.value }) });
        if (!r.ok) throw new Error(String(r.status));
        location.reload();
      } catch (e) { sel.disabled = false; alert("切換資料來源失敗：" + e.message); }
    };
    lab.append("資料來源", sel);
    host.appendChild(lab);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
