/*
 * 手動同步一個來源（COROS／TP → 這裡）：POST the SSE start endpoint and read the
 * progress stream. Shared by 設定 › 立即同步 (settings.html) and 課表 › 從 COROS 抓活動
 * (schedule.html); the UI text stays in each page.
 *     <script src="/api/v1/static/syncrun.js"></script>
 *     const st = await TRCSync.run("coros", { since, onEvent(ev, st) { … } });
 * src: "coros" | "tp" (the /sync/<src> folder name, GET /api/v1/sync/primary → source).
 * st (also handed to onEvent after each event, counters already updated):
 *   busy      409 SYNC_BUSY — that source is already syncing (nothing ran)
 *   total     activities to look at (range_list / changed_list), 0 = unknown
 *   checked / downloaded / errors
 *   fatal     run-level failure (no workout id): { code, detail } — COROS_AUTH_REQUIRED /
 *             TP_AUTH_REQUIRED = log in again, SYNC_DISABLED, SYNC_FAILED, …
 *   finished  the "complete" event arrived (false + no fatal = the stream was cut)
 * Throws on a non-2xx other than 409 (Error "<status> <body>") and on network errors.
 */
(function () {
  "use strict";
  const API = "/api/v1/sync";
  const AUTH = new Set(["COROS_AUTH_REQUIRED", "TP_AUTH_REQUIRED"]);

  async function run(src, opts = {}) {
    const { since, onEvent } = opts;
    const st = { busy: false, total: 0, checked: 0, downloaded: 0, errors: 0, fatal: null, finished: false };
    const url = `${API}/${src === "coros" ? "coros/start" : "start"}` + (since ? `?since=${encodeURIComponent(since)}` : "");
    const r = await fetch(url, { method: "POST" });
    if (r.status === 409) { st.busy = true; return st; }
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0 || (i = buf.indexOf("\r\n\r\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + (buf[i] === "\r" ? 4 : 2));
        const data = chunk.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
        if (!data) continue;
        let ev; try { ev = JSON.parse(data); } catch { continue; }
        if (ev.status === "range_list") st.total += ev.count || 0;
        if (ev.status === "changed_list") st.total += ev.modified || 0;
        if (ev.status === "checking") st.checked++;
        if (ev.status === "downloaded") st.downloaded++;
        else if (ev.status === "error" || ev.error) {
          // the lock was taken between the 409 check and the run (runner.stream)
          if (ev.error === "SYNC_BUSY") st.busy = true;
          st.errors++;
          if (!(ev.workout_id || ev.activity_id)) st.fatal = { code: ev.error || "", detail: ev.detail || "", auth: AUTH.has(ev.error) };
        }
        else if (ev.status === "complete") st.finished = true;
        if (onEvent) onEvent(ev, st);
      }
    }
    return st;
  }

  // GET /sync/primary: { source: coros | tp, label, logged_in, enabled, busy }
  async function primary() {
    const r = await fetch(`${API}/primary`);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }

  window.TRCSync = { run, primary };
})();
