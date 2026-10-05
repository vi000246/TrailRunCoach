/*
 * 開啟網站時自動同步 — standalone, non-blocking.
 *
 * Include on any page (shell.js can add it with one line, see
 * docs/spec/wko5-coros-sync.spec.md):
 *     <script src="/api/v1/static/autosync.js" defer></script>
 * or from shell.js:
 *     import("/api/v1/static/autosync.js")   // or append a <script> tag
 *
 * It calls POST /api/v1/sync/auto at most once per 10 minutes per browser
 * (the server decides per source: enabled, logged in, idle, older than the
 * configured N hours), then shows a small status in the nav bar while
 * background syncs run. If the page has an element with
 * id="nav-sync-status" the text goes there, otherwise a small badge is
 * added to the top-right corner. It never blocks rendering.
 */
(function () {
  "use strict";
  const KEY = "wko5coach.autosync.last";
  const MIN_GAP_MS = 10 * 60 * 1000;
  const SRC = { coros: "COROS", tp: "TP" };
  const T = (k, p) => (window.I18N && window.I18N.t ? window.I18N.t("common." + k, p) : k);

  function el() {
    let e = document.getElementById("nav-sync-status");
    if (!e) {
      e = document.createElement("span");
      e.id = "nav-sync-status";
      e.style.cssText = "position:fixed;top:8px;right:12px;z-index:50;font:12px system-ui,sans-serif;"
        + "padding:2px 8px;border-radius:10px;background:rgba(37,99,235,.12);color:#2563eb;display:none";
      document.body.appendChild(e);
    }
    return e;
  }
  function show(text, ok, bad) {
    const e = el();
    e.textContent = text;
    e.style.display = text ? "" : "none";
    if (bad) {
      e.style.background = "rgba(220,38,38,.12)";
      e.style.color = "#dc2626";
    }
    if (ok) setTimeout(() => { e.style.display = "none"; }, 6000);
  }
  function recent() {
    try { return Date.now() - Number(localStorage.getItem(KEY) || 0) < MIN_GAP_MS; } catch (_) { return false; }
  }
  function mark() { try { localStorage.setItem(KEY, String(Date.now())); } catch (_) { /* private mode */ } }

  async function watch(sources) {
    for (let i = 0; i < 120; i++) {                  // up to ~10 min
      await new Promise((r) => setTimeout(r, 5000));
      let st;
      try { st = await (await fetch("/api/v1/sync/sources")).json(); } catch (_) { return; }
      const busy = sources.filter((s) => st[s] && st[s].busy);
      if (!busy.length) {
        // SP-88: a failed run used to show as 「已同步 COROS +0」 for 6 s
        const failed = sources.filter((s) => st[s] && st[s].last_result && st[s].last_result.status === "failed");
        const done = sources.filter((s) => !failed.includes(s));
        if (failed.length) {
          show(T("autosync.failed", { src: failed.map((s) => SRC[s]).join("、") }), false, true);
        } else {
          const res = done.map((s) => {
            const r = st[s] && st[s].last_result;
            return `${SRC[s]} ${r ? `+${r.downloaded}` : ""}`;
          }).join("、");
          show(`已同步 ${res}`, true);
        }
        // the 登入已過期／同步失敗 banner (session_banner.js) reads the new state
        if (window.WKO5SessionBanner) window.WKO5SessionBanner.refresh();
        return;
      }
      show(`同步中：${busy.map((s) => SRC[s]).join("、")}…`);
    }
  }

  async function run() {
    if (recent()) return;
    mark();
    let r;
    try {
      r = await (await fetch("/api/v1/sync/auto", { method: "POST" })).json();
    } catch (_) { return; }
    if (r && r.started && r.started.length) {
      show(`同步中：${r.started.map((s) => SRC[s]).join("、")}…`);
      watch(r.started);
    }
  }

  window.WKO5AutoSync = { run };
  const go = () => setTimeout(run, 1500);            // after the page has painted
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go);
  else go();
})();
