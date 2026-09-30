/*
 * Chart data-source chip: 資料來源 WKO5 / COROS / TP.
 *
 * One line in a page (e.g. wko5_viewer.html, next to the other header chips):
 *     <span id="source-chip"></span><script src="/api/v1/static/sourcechip.js" defer></script>
 * Without a #source-chip element it adds itself to the first <header>.
 * Changing it saves charts.data_source (PUT /api/v1/sync/settings) and reloads,
 * so every chart, the overview and race power read that source.
 */
(function () {
  "use strict";
  const LABEL = { wko5: "WKO5 資料夾", coros: "COROS", tp: "TrainingPeaks" };

  async function mount() {
    let host = document.getElementById("source-chip");
    if (!host) {
      host = document.createElement("span");
      host.id = "source-chip";
      (document.querySelector("header") || document.body).appendChild(host);
    }
    let cur = "wko5";
    try { cur = (await (await fetch("/api/v1/sync/settings")).json()).chart_data_source || "wko5"; } catch (_) {}
    host.innerHTML = "";
    const lab = document.createElement("label");
    lab.style.cssText = "display:inline-flex;gap:6px;align-items:center;font-size:12.5px;padding:2px 8px;"
      + "border:1px solid currentColor;border-radius:12px;opacity:.85";
    lab.title = "圖表、總覽和功率計算機讀取的資料來源";
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
