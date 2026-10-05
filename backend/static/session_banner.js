/*
 * 登入已過期／無法同步 banner — non-blocking (overview, schedule).
 *
 *     <script src="/api/v1/static/session_banner.js" defer></script>
 *
 * GET /api/v1/auth/session-alerts:
 *   sync     — can the 資料來源 sync? problem = expired | logged_out | failed
 *              (with the last successful sync time); shown as a card with a
 *              「重新登入」 link to 設定 → 資料同步.
 *   expired  — the other logins in use (課表推送) that have expired (the server
 *              checks the stored token, cached a few minutes: sync/session_check.py).
 * Nothing is shown when every login is fine or the request fails.
 *
 * SP-88: the check used to run once, at page load, before the auto-sync
 * (autosync.js) found the dead token, and nothing read it again. Now the
 * banner is re-read when the auto-sync ends (WKO5SessionBanner.refresh) and
 * when the tab becomes visible again (a page left open overnight).
 */
(function () {
  "use strict";
  // the common catalog (static/i18n/{zh-TW,en}/common.json) is inlined on every page: keys only, no fallback
  const T = (k, p) => (window.I18N && window.I18N.t ? window.I18N.t("common." + k, p) : k);
  const MIN_GAP_MS = 60 * 1000;            // visibility re-checks at most once a minute
  let lastRun = 0, running = false;

  const pad = (n) => String(n).padStart(2, "0");
  function when(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function link(href, text, strong) {
    const a = document.createElement("a");
    a.href = href;
    a.textContent = text;
    if (strong) a.style.cssText = "font-weight:600;white-space:nowrap";
    return a;
  }

  function syncCard(s, expired, url) {
    const name = s.name;
    const push = expired.some((a) => a.source === s.source && (a.needs || []).includes("push"));
    const title = s.problem === "expired"
      ? (push ? T("session.expired_push", { name })
              : T("session.expired_sync", { name }))
      : s.problem === "logged_out"
        ? T("session.logged_out", { name })
        : T("session.sync_failed", { name });
    const box = document.createElement("div");
    box.style.cssText = "display:grid;gap:2px";
    const head = document.createElement("div");
    head.style.cssText = "display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline";
    const b = document.createElement("b");
    b.textContent = title;
    head.appendChild(b);
    head.appendChild(s.problem === "failed"
      ? link(url, T("session.see_settings"), true)
      : link(url, T("session.relogin"), true));
    box.appendChild(head);
    const meta = [];
    meta.push(s.last_ok_at
      ? T("session.last_ok", { when: when(s.last_ok_at) })
      : T("session.never_ok"));
    const r = s.last_run;
    if (r && r.status === "failed") {
      meta.push(T("session.last_failed", { when: when(r.at), error: r.error || "?" }));
    }
    const sub = document.createElement("div");
    sub.style.cssText = "font-size:12px;opacity:.85";
    sub.textContent = meta.join(" · ");
    box.appendChild(sub);
    return box;
  }

  async function run() {
    if (running) return;
    running = true;
    lastRun = Date.now();
    let r;
    try {
      const res = await fetch("/api/v1/auth/session-alerts");
      if (!res.ok) return;
      r = await res.json();
    } catch (_) { return; } finally { running = false; }
    const old = document.getElementById("session-banner");
    if (old) old.remove();
    if (!r) return;
    const url = r.settings_url || "/api/v1/wko5/settings#sync";
    const expired = r.expired || [];
    const s = r.sync && r.sync.problem ? r.sync : null;
    // the 資料來源's line is the card; the rest (課表推送) a short line each
    const others = expired.filter((a) => !s || a.source !== s.source);
    if (!s && !others.length) return;
    const host = document.querySelector("main") || document.body;
    const box = document.createElement("div");
    box.id = "session-banner";
    box.setAttribute("role", "alert");
    box.style.cssText = "display:grid;gap:6px;margin:0 0 8px;padding:10px 12px;border-radius:8px;font-size:13.5px;"
      + "background:rgba(220,38,38,.10);border:1px solid rgba(220,38,38,.55);border-left-width:4px;color:inherit";
    if (s) box.appendChild(syncCard(s, expired, url));
    for (const a of others) {
      const line = document.createElement("div");
      const needs = a.needs || [];
      line.textContent = (needs.includes("sync")
        ? T("session.expired_sync", { name: a.name })
        : T("session.expired_push_only", { name: a.name })) + " ";
      line.appendChild(link(url, T("session.relogin"), true));
      box.appendChild(line);
    }
    host.insertBefore(box, host.firstChild);
  }

  window.WKO5SessionBanner = { refresh: run };
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - lastRun > MIN_GAP_MS) run();
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
