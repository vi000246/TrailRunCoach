/*
 * 登入已過期 banner — small, non-blocking (overview, schedule).
 *
 *     <script src="/api/v1/static/session_banner.js" defer></script>
 *
 * GET /api/v1/auth/session-alerts lists the logins in use (資料來源, 課表推送)
 * that have expired (the server checks the stored token, cached a few
 * minutes: sync/session_check.py). Each one gets a line at the top of <main>
 * linking to 設定 → 資料同步. Nothing is shown when every login is fine or
 * the request fails.
 */
(function () {
  "use strict";
  async function run() {
    let r;
    try {
      const res = await fetch("/api/v1/auth/session-alerts");
      if (!res.ok) return;
      r = await res.json();
    } catch (_) { return; }
    if (!r || !r.expired || !r.expired.length) return;
    const host = document.querySelector("main") || document.body;
    const box = document.createElement("div");
    box.id = "session-banner";
    box.setAttribute("role", "status");
    box.style.cssText = "display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center;margin:0 0 8px;"
      + "padding:6px 10px;border-radius:8px;font-size:13px;"
      + "background:rgba(217,119,6,.12);border:1px solid rgba(217,119,6,.45);color:inherit";
    for (const a of r.expired) {
      const line = document.createElement("span");
      line.textContent = a.message + " ";
      const link = document.createElement("a");
      link.href = r.settings_url || "/api/v1/wko5/settings#sync";
      link.textContent = "去重新登入";
      line.appendChild(link);
      box.appendChild(line);
    }
    host.insertBefore(box, host.firstChild);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
