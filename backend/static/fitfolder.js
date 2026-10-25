/* 自動匯入 FIT 資料夾 (SP-324): desktop Chrome / Edge remember one FIT folder and, each time the
 * site is opened, import the FIT files in it that were not imported before.
 *
 * The File System Access API (`showDirectoryPicker`, docs/research/fit-on-device.md §4.1) gives a
 * folder handle; it is kept in IndexedDB (db "trc-fitfolder", store "kv") under the signed-in
 * account, so another account on the same browser never uploads it:
 *   dir:<acct>   { handle, name, at }          the folder
 *   seen:<acct>  { keys: { "<path>|<size>|<mtime>": 1 } }   files already imported (or that can
 *                never be: unreadable, not a run, too big); a file written again gets a new key
 *   last:<acct>  the last check's result (shown on the settings page; the 10-minute throttle)
 * Only on a desktop Chromium with the API (Firefox, Safari, iPhone have none; Android Chrome has it
 * but the ticket is the desktop) and an account that may upload FIT files (`upload.fit`); elsewhere
 * nothing shows. The new files go through SP-323's upload (fitimport.js: batches, the server's hash
 * and same-activity dedup, the post-sync steps once at the end). A web page cannot read the folder
 * in the background: it is checked when a page opens (shell.js loads this with data-auto="1"), at
 * most once per 10 minutes. Without a remembered permission (closing the browser drops it unless
 * Chrome's 「每次造訪都允許」 or an installed app keeps it) the browser must ask again, and only a
 * click may make it ask: a small note with 重新授權 shows, nothing is read, nothing throws.
 *
 *   FitFolder.supported(win)        the API on a desktop browser
 *   FitFolder.run({store, acct, upload, ask, limits, gap, now, onProgress})   one check -> result
 *   FitFolder.auto(...)             run() throttled (page open)
 *   FitFolder.pick / forget / status / html / badge / mount / idbStore
 * Pure apart from mount, idbStore and the page-open badge (tested under node:
 * backend/tests/test_fit_folder_sp324.py). Texts: common.fitfolder.* (the common catalog is
 * inlined on every page).
 */
(function (root) {
  "use strict";
  const cs = typeof document !== "undefined" ? document.currentScript : null;

  if (!root.FitFolder) {
    // the server's picks (backend/sync/upload.py MAX_SELECT / EXTS; a test keeps them equal)
    const DEF = { max_select: 3000, exts: [".fit", ".fit.gz", ".tcx", ".tcx.gz", ".gpx", ".gpx.gz", ".pwx", ".pwx.gz"] };
    const GAP = 10 * 60 * 1000;               // one check per 10 minutes of page opens
    const DEPTH = 4;                           // sub-folders read (Garmin / COROS exports nest by year)
    const FAIL_KEEP = 50;                      // failures kept for the settings page
    // a failure that the same bytes will always get: remembered, not sent again
    const PERMANENT = new Set(["not_fit", "too_large", "empty", "unreadable", "not_activity"]);
    const PICK_OPTS = { id: "trc-fit-folder", mode: "read" };

    const T0 = (t, k, p) => t("common.fitfolder." + k, p);
    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const two = (n) => String(n).padStart(2, "0");
    const when = (ms) => {
      const d = new Date(ms);
      return isNaN(d) ? "" : `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
    };

    /** The API on a desktop browser (Chrome / Edge 86+). */
    function supported(win) {
      if (!win || typeof win.showDirectoryPicker !== "function") return false;
      const n = win.navigator || {};
      if (n.userAgentData && n.userAgentData.mobile === true) return false;
      return !/Android|iPhone|iPad|iPod|Mobile/i.test(n.userAgent || "");
    }
    const canUpload = (win) => !(win && win.AppShell && typeof win.AppShell.can === "function") || win.AppShell.can("upload.fit");

    /** Whose folder: the signed-in user, or the one owner of a single-user install. */
    function account(session) {
      const u = session && session.user;
      return u && u.id != null ? "u" + u.id : "owner";
    }

    const fileKey = (path, f) => `${path}|${f.size}|${f.lastModified}`;
    const isFit = (name, exts) => { const n = name.toLowerCase(); return exts.some((e) => n.endsWith(e)); };

    /** Every FIT file in the folder (hidden files and folders skipped: macOS ._ copies, .Trash):
     * [{path, key, mtime, file}]; path starts with the folder's own name, like a folder pick. */
    async function scan(dir, lim) {
      const exts = (lim && lim.exts) || DEF.exts;
      const out = [];
      async function walk(d, prefix, depth) {
        for await (const h of d.values()) {
          if (!h || !h.name || h.name.startsWith(".")) continue;
          const path = prefix + "/" + h.name;
          if (h.kind === "directory") { if (depth < DEPTH) await walk(h, path, depth + 1); continue; }
          if (h.kind !== "file" || !isFit(h.name, exts)) continue;
          const f = await h.getFile();
          out.push({ path, key: fileKey(path, f), mtime: f.lastModified || 0, file: f });
        }
      }
      await walk(dir, dir.name, 0);
      return out;
    }

    /** The folder's read permission: "granted" | "prompt" | "denied". ask: may show the browser's
     * prompt (only inside a click). A query that throws counts as "prompt". */
    async function permission(h, ask) {
      const o = { mode: "read" };
      let p;
      try { p = typeof h.queryPermission === "function" ? await h.queryPermission(o) : "granted"; } catch (_) { p = "prompt"; }
      if (p !== "granted" && ask && typeof h.requestPermission === "function") {
        try { p = await h.requestPermission(o); } catch (_) { p = "prompt"; }
      }
      return p === "granted" || p === "denied" ? p : "prompt";
    }

    // what a scan error means for the person
    function kindOf(e) {
      const n = e && e.name;
      if (n === "NotFoundError" || n === "NotFoundErr" || n === "InvalidStateError") return "missing";
      if (n === "NotAllowedError" || n === "SecurityError") return "permission";
      return "error";
    }

    // the server echoes each name sent; a folder file is sent under its path
    function withPath(f, path) {
      try { Object.defineProperty(f, "webkitRelativePath", { value: path, configurable: true }); } catch (_) { /* name only */ }
      return f;
    }

    /** One check: scan, upload what is new, remember what was imported. */
    async function run(o) {
      const store = o.store, acct = o.acct || "owner";
      const now = typeof o.now === "number" ? o.now : Date.now();
      const lim = { ...DEF, ...(o.limits || {}) };
      const rec = await store.get("dir:" + acct);
      if (!rec || !rec.handle) return { state: "none" };
      const name = rec.name || rec.handle.name || "";
      if ((await permission(rec.handle, !!o.ask)) !== "granted") return { state: "permission", name };
      if (o.gap) {
        const last = await store.get("last:" + acct);
        if (last && last.state === "ok" && !last.retry && typeof last.at === "number" && now >= last.at && now - last.at < o.gap) {
          return { state: "recent", name };
        }
      }
      const out = { state: "ok", name, at: now, found: 0, fresh: 0, sent: 0, pending: 0, added: 0, duplicate: 0,
        failed: 0, retry: 0, failures: [], stopped: null };
      const finish = async (state, extra) => {
        Object.assign(out, extra || {}, { state });
        await store.set("last:" + acct, out);
        return out;
      };
      let found;
      try { found = await scan(rec.handle, lim); } catch (e) {
        const k = kindOf(e);
        if (k === "permission") return { state: "permission", name };
        return finish(k, { error: String((e && e.message) || e) });
      }
      const seenRec = await store.get("seen:" + acct);
      const seen = (seenRec && seenRec.keys) || {};
      const keep = {};                       // files no longer in the folder drop out of the list
      for (const x of found) if (seen[x.key]) keep[x.key] = 1;
      const fresh = found.filter((x) => !seen[x.key]);
      out.found = found.length;
      out.fresh = fresh.length;
      if (fresh.length) {
        // the newest first; above one pick's cap the older ones wait for the next open
        fresh.sort((a, b) => b.mtime - a.mtime || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
        const take = fresh.slice(0, lim.max_select);
        out.sent = take.length;
        out.pending = fresh.length - take.length;
        const byPath = new Map(take.map((x) => [x.path, x]));
        const byBase = new Map();
        for (const x of take) { const b = x.path.split("/").pop(); byBase.set(b, byBase.has(b) ? null : x); }
        let res;
        try {
          res = await o.upload(take.map((x) => withPath(x.file, x.path)), { limits: o.limits, onProgress: o.onProgress });
        } catch (e) {
          await store.set("seen:" + acct, { keys: keep });
          return finish("error", { error: String((e && e.message) || e), retry: take.length });
        }
        for (const it of (res && res.items) || []) {
          const x = byPath.get(it.name) || byBase.get(String(it.name || "").split("/").pop()) || null;
          if (it.status === "added") out.added += 1;
          else if (it.status === "duplicate") out.duplicate += 1;
          else if (it.status === "failed") {
            out.failed += 1;
            if (out.failures.length < FAIL_KEEP) out.failures.push({ name: it.name, reason: it.reason || null });
            if (!PERMANENT.has(it.reason)) out.retry += 1;
          }
          if (x && (it.status !== "failed" || PERMANENT.has(it.reason))) keep[x.key] = 1;
        }
        out.stopped = (res && res.stopped) || null;
      }
      await store.set("seen:" + acct, { keys: keep });
      return finish("ok");
    }

    /** run() at a page open: at most once per 10 minutes (unless the last one left files to retry). */
    const auto = (o) => run({ ...o, gap: o.gap == null ? GAP : o.gap });

    /** The browser's folder picker; the handle is remembered. The same folder picked again keeps
     * the list of imported files, another folder starts a new one. */
    async function pick(o) {
      const win = o.win, store = o.store, acct = o.acct || "owner";
      if (!supported(win)) return { state: "unsupported" };
      let h;
      try { h = await win.showDirectoryPicker(PICK_OPTS); } catch (e) {
        return e && e.name === "AbortError" ? { state: "cancelled" } : { state: "error", error: String((e && e.message) || e) };
      }
      const old = await store.get("dir:" + acct);
      let same = false;
      if (old && old.handle && typeof h.isSameEntry === "function") {
        try { same = !!(await h.isSameEntry(old.handle)); } catch (_) { same = false; }
      }
      if (!same) { await store.del("seen:" + acct); await store.del("last:" + acct); }
      await store.set("dir:" + acct, { handle: h, name: h.name || "", at: Date.now() });
      return { state: "picked", name: h.name || "", same };
    }

    /** 取消授權: the folder is never read again (the activities imported stay). */
    async function forget(store, acct) {
      for (const k of ["dir:", "seen:", "last:"]) await store.del(k + (acct || "owner"));
    }

    /** What the settings page shows: {state: none | granted | permission | missing | error, name, known, last}. */
    async function status(store, acct) {
      const a = acct || "owner";
      const rec = await store.get("dir:" + a);
      if (!rec || !rec.handle) return { state: "none" };
      const last = (await store.get("last:" + a)) || null;
      const seen = await store.get("seen:" + a);
      const known = seen && seen.keys ? Object.keys(seen.keys).length : 0;
      let state = await permission(rec.handle, false);
      state = state === "granted" ? (last && (last.state === "missing" || last.state === "error") ? last.state : "granted") : "permission";
      return { state, name: rec.name || "", known, last };
    }

    function summary(r, t) {
      if (!r.fresh) return T0(t, "nothing_new");
      const TI = (k, p) => t("common.fitimport." + k, p);
      const parts = [TI("sum.added", { n: r.added }), TI("sum.duplicate", { n: r.duplicate }), TI("sum.failed", { n: r.failed })];
      return parts.join(TI("sep"));
    }

    /** The settings block. st: status(); extra: {busy, progress: {done, total}, scanning, msg}. */
    function html(st, t, extra) {
      const x = extra || {};
      const T = (k, p) => esc(T0(t, k, p));
      const dis = x.busy ? " disabled" : "";
      const btn = (k, cls) => `<button type="button" data-ff="${k}"${cls ? ` class="${cls}"` : ""} data-cap="upload.fit"${dis}>${T(k)}</button>`;
      let prog = "";
      if (x.progress) prog = T("running", { done: x.progress.done, total: x.progress.total });
      else if (x.busy) prog = T("scanning");
      const statusEl = `<span class="fi-meta" role="status" aria-live="polite" data-ff="status">${prog}</span>`;
      let out = `<h4 class="ff-title">${T("title")}</h4><p class="fi-meta">${T("intro")}</p>`;
      if (st.state === "none") {
        out += `<div class="fi-row">${btn("pick", "primary")}${statusEl}</div><p class="fi-meta">${T("first_note")}</p>`;
      } else {
        out += `<p class="ff-folder">${T("folder", { name: st.name })} <span class="ff-pill ff-${esc(st.state)}">${T("st." + st.state)}</span></p>`;
        const last = st.last;
        if (st.state === "granted") {
          const line = last && last.state === "ok" && typeof last.at === "number"
            ? T("last", { at: when(last.at), summary: summary(last, t) }) : T("last_none");
          out += `<p class="fi-meta">${line}${st.known ? T("sep") + T("known", { n: st.known }) : ""}</p>`;
          if (last && last.pending) out += `<p class="fi-meta">${T("pending", { n: last.pending })}</p>`;
        } else if (st.state === "permission") {
          out += `<p class="ff-warn" role="alert">${T("permission_note")}</p>`;
        } else if (st.state === "missing") {
          out += `<p class="ff-warn" role="alert">${T("missing_note")}</p>`;
        } else {
          out += `<p class="ff-warn" role="alert">${T("error_note", { error: (last && last.error) || "" })}</p>`;
        }
        const btns = st.state === "granted" ? [btn("check", "primary"), btn("change"), btn("forget")]
          : st.state === "permission" ? [btn("reauth", "primary"), btn("change"), btn("forget")]
            : [btn("change", "primary"), btn("forget")];
        out += `<div class="fi-row">${btns.join("")}${statusEl}</div>`;
        if (st.state === "granted" && last && last.failures && last.failures.length) {
          const TI = (k, p) => esc(t("common.fitimport." + k, p));
          out += `<details class="fi-list"><summary>${TI("list.failed", { n: last.failed })}</summary><ul>` +
            last.failures.map((f) => `<li><span class="fi-name">${esc(f.name)}</span> <span class="fi-why">${
              f.reason && !String(f.reason).startsWith("http_") ? TI("reason." + f.reason) : TI("reason.http", { status: String(f.reason || "").slice(5) })}</span></li>`).join("") +
            "</ul></details>";
        }
        if (st.state === "granted" || st.state === "permission") out += `<p class="fi-meta">${T("persist_tip")}</p>`;
      }
      if (x.msg) out += `<p class="fi-meta" data-ff="msg">${esc(x.msg)}</p>`;
      return out;
    }

    /** The note after a page open: {kind: ok | warn, text, act: reauth | settings | null}, or null
     * when there is nothing to say (no folder, nothing new, checked a moment ago). */
    function badge(r, t) {
      if (!r) return null;
      if (r.state === "permission") return { kind: "warn", text: T0(t, "badge.permission", { name: r.name }), act: "reauth" };
      if (r.state === "missing") return { kind: "warn", text: T0(t, "badge.missing", { name: r.name }), act: "settings" };
      if (r.state === "error") return { kind: "warn", text: T0(t, "badge.error", { name: r.name }), act: "settings" };
      if (r.state !== "ok" || !r.fresh) return null;
      if (r.failed) {
        const text = (r.added ? T0(t, "badge.added", { name: r.name, n: r.added }) + T0(t, "sep") : "") + T0(t, "badge.failed", { n: r.failed });
        return { kind: "warn", text, act: "settings" };
      }
      if (!r.added) return null;
      return { kind: "ok", text: T0(t, "badge.added", { name: r.name, n: r.added }), act: null };
    }

    // ---- the browser side ------------------------------------------------------
    /** IndexedDB as {get, set, del} (a handle is kept as it is: structured clone). */
    function idbStore(idb) {
      let dbp = null;
      const open = () => dbp || (dbp = new Promise((res, rej) => {
        const r = idb.open("trc-fitfolder", 1);
        r.onupgradeneeded = () => r.result.createObjectStore("kv");
        r.onsuccess = () => res(r.result);
        r.onerror = () => { dbp = null; rej(r.error); };
      }));
      const tx = async (mode, f) => {
        const db = await open();
        return new Promise((res, rej) => {
          const x = db.transaction("kv", mode);
          const req = f(x.objectStore("kv"));
          x.oncomplete = () => res(req ? req.result : undefined);
          x.onerror = () => rej(x.error);
          x.onabort = () => rej(x.error);
        });
      };
      return {
        get: (k) => tx("readonly", (s) => s.get(k)),
        set: (k, v) => tx("readwrite", (s) => s.put(v, k)).then(() => undefined),
        del: (k) => tx("readwrite", (s) => s.delete(k)).then(() => undefined),
      };
    }

    const tOf = (win) => (k, p) => (win.I18N ? win.I18N.t(k, p) : k);
    const acctOf = (win) => account(win.AppShell && win.AppShell.session);

    // SP-323's upload, loaded on the pages that do not have it, with the server's caps
    function need(win) {
      if (win.FitImport) return Promise.resolve(win.FitImport);
      return new Promise((res, rej) => {
        const s = win.document.createElement("script");
        s.src = "/api/v1/static/fitimport.js";
        s.onload = () => (win.FitImport ? res(win.FitImport) : rej(new Error("fitimport.js")));
        s.onerror = () => rej(new Error("fitimport.js"));
        win.document.head.appendChild(s);
      });
    }
    async function upload(win, files, o) {
      const FI = await need(win);
      let limits = o.limits || {};
      try {
        const r = await win.fetch(FI.URL, { cache: "no-store" });
        if (r.ok) limits = { ...limits, ...((await r.json()).limits || {}) };
      } catch (_) { /* its defaults */ }
      return FI.upload(files, { ...o, limits });
    }
    // two tabs opened at once: one checks
    async function locked(win, acct, f) {
      const L = win.navigator && win.navigator.locks;
      if (!L || typeof L.request !== "function") return f();
      return L.request("trc-fitfolder-" + acct, { ifAvailable: true }, (lock) => (lock ? f() : { state: "busy" }));
    }

    const CSS = `
    .ff { margin-top: 14px; padding-top: 12px; border-top: 1px dashed var(--line, #e1e5ea); }
    .ff .ff-title { margin: 0 0 4px; font-size: 14px; }
    .ff .ff-folder { margin: 8px 0 0; font-weight: 600; word-break: break-all; }
    .ff .ff-pill { display: inline-block; margin-left: 6px; padding: 1px 8px; border-radius: 10px; font-size: 12px; font-weight: 500;
      background: rgba(22,163,74,.12); color: #15803d; }
    .ff .ff-pill.ff-permission, .ff .ff-pill.ff-missing, .ff .ff-pill.ff-error { background: rgba(217,119,6,.14); color: #b45309; }
    .ff .ff-warn { margin: 6px 0 0; padding: 6px 10px; border-radius: 6px; font-size: 13px;
      background: rgba(217,119,6,.10); color: var(--text, #111); border-left: 3px solid #d97706; }
    .ff-badge { position: fixed; left: 16px; bottom: 16px; z-index: 61; max-width: 380px; display: flex; gap: 8px; align-items: center;
      padding: 8px 10px 8px 12px; border-radius: 8px; font: 13px/1.4 system-ui, sans-serif; box-shadow: 0 4px 16px rgba(0,0,0,.15);
      background: var(--card, #fff); color: var(--text, #111); border: 1px solid var(--line, #e1e5ea); border-left: 4px solid #16a34a; }
    .ff-badge.warn { border-left-color: #d97706; }
    .ff-badge button, .ff-badge a { font: inherit; white-space: nowrap; }
    .ff-badge .ff-x { border: 0; background: none; cursor: pointer; color: var(--muted, #667); font-size: 16px; line-height: 1; padding: 2px 4px; }
    `;
    let styled = false;
    function style(win) {
      if (styled) return;
      styled = true;
      const st = win.document.createElement("style");
      st.textContent = CSS;
      win.document.head.appendChild(st);
    }
    const done = (win, r) => { try { win.dispatchEvent(new win.CustomEvent("fitfolder:done", { detail: r })); } catch (_) {} };

    /** The settings block in `el` (hidden where the browser has no API). opt: {win, store, onDone}. */
    function mount(el, opt) {
      const o = opt || {};
      const win = o.win || root;
      if (!supported(win) || !canUpload(win)) { el.hidden = true; return null; }
      el.hidden = false;
      el.classList.add("ff", "fi");
      style(win);
      const t = o.t || tOf(win);
      const store = o.store || idbStore(win.indexedDB);
      const acct = o.acct || acctOf(win);
      let extra = {};
      async function draw() {
        let st;
        try { st = await status(store, acct); } catch (e) { st = { state: "none" }; extra.msg = String((e && e.message) || e); }
        el.innerHTML = html(st, t, extra);
        el.querySelectorAll("[data-ff]").forEach((b) => {
          const k = b.dataset.ff;
          if (k === "pick" || k === "change") b.addEventListener("click", choose);
          else if (k === "reauth") b.addEventListener("click", () => go(true));
          else if (k === "check") b.addEventListener("click", () => go(false));
          else if (k === "forget") b.addEventListener("click", drop);
        });
      }
      async function go(ask) {
        if (extra.busy) return;
        // the browser asks only inside the click's short activation window: ask before anything else
        if (ask) { try { const rec = await store.get("dir:" + acct); if (rec && rec.handle) await permission(rec.handle, true); } catch (_) {} }
        extra = { busy: true };
        await draw();
        const statusEl = () => el.querySelector('[data-ff="status"]');
        let r;
        try {
          r = await locked(win, acct, () => run({ store, acct, ask, upload: (f, u) => upload(win, f, u),
            onProgress: (d, n) => { const s = statusEl(); if (s) s.textContent = T0(t, "running", { done: d, total: n }); } }));
        } catch (e) { r = { state: "error", error: String((e && e.message) || e) }; }
        extra = { msg: r.state === "busy" ? T0(t, "busy") : r.state === "permission" && ask ? T0(t, "still_permission") : "" };
        await draw();
        done(win, r);
        if (o.onDone && r.state === "ok") o.onDone(r);
      }
      async function choose() {
        if (extra.busy) return;
        const p = await pick({ win, store, acct });
        if (p.state === "picked") return go(false);
        extra = { msg: p.state === "error" ? T0(t, "pick_error", { error: p.error }) : "" };
        return draw();
      }
      async function drop() {
        if (extra.busy) return;
        if (typeof win.confirm === "function" && !win.confirm(T0(t, "forget_confirm"))) return;
        await forget(store, acct);
        extra = { msg: T0(t, "forgot") };
        await draw();
      }
      win.addEventListener("fitfolder:done", (e) => { if (!extra.busy && e.detail && e.detail.state !== "recent") draw(); });
      draw();
      return { refresh: draw };
    }

    // ---- page open (shell.js) --------------------------------------------------
    const DISMISS = "fitfolder.dismissed";
    function showBadge(win, b, onAct) {
      const d = win.document;
      let el = d.getElementById("ff-badge");
      if (!b) { if (el) el.remove(); return; }
      style(win);
      if (!el) { el = d.createElement("div"); el.id = "ff-badge"; d.body.appendChild(el); }
      el.className = "ff-badge " + b.kind;
      el.setAttribute("role", b.kind === "warn" ? "alert" : "status");
      const t = tOf(win);
      const act = b.act === "reauth" ? `<button type="button" data-ffb="reauth">${esc(T0(t, "reauth"))}</button>`
        : b.act === "settings" ? `<a href="/api/v1/app/settings#sync">${esc(T0(t, "badge.settings"))}</a>` : "";
      el.innerHTML = `<span>${esc(b.text)}</span>${act}<button type="button" class="ff-x" data-ffb="x" aria-label="${esc(T0(t, "badge.close"))}">×</button>`;
      el.querySelector('[data-ffb="x"]').addEventListener("click", () => {
        el.remove();
        if (b.kind === "warn") { try { win.sessionStorage.setItem(DISMISS, "1"); } catch (_) {} }
      });
      const ra = el.querySelector('[data-ffb="reauth"]');
      if (ra) ra.addEventListener("click", () => onAct("reauth"));
      if (b.kind === "ok") setTimeout(() => { if (el.isConnected) el.remove(); }, 10000);
    }

    async function autoStart(win) {
      if (!supported(win) || !canUpload(win) || !win.indexedDB) return;
      const store = idbStore(win.indexedDB), acct = acctOf(win), t = tOf(win);
      const up = (f, u) => upload(win, f, u);
      let dismissed = false;
      try { dismissed = win.sessionStorage.getItem(DISMISS) === "1"; } catch (_) {}
      const show = (r) => {
        const b = badge(r, t);
        if (b && b.kind === "warn" && dismissed && r.state !== "ok") return;
        showBadge(win, b, async () => {
          showBadge(win, { kind: "ok", text: T0(t, "scanning"), act: null }, () => {});
          let r2;
          try { r2 = await locked(win, acct, () => run({ store, acct, ask: true, upload: up, onProgress: progress })); } catch (e) { r2 = { state: "error", name: r.name }; }
          showBadge(win, badge(r2, t) || (r2.state === "ok" ? { kind: "ok", text: T0(t, "badge.nothing_new", { name: r2.name }), act: null } : null), () => {});
          done(win, r2);
        });
      };
      const progress = (d, n) => showBadge(win, { kind: "ok", text: T0(t, "running", { done: d, total: n }), act: null }, () => {});
      let r;
      try { r = await locked(win, acct, () => auto({ store, acct, upload: up, onProgress: progress })); } catch (_) { return; }
      show(r);
      done(win, r);
    }

    root.FitFolder = { DEF, GAP, supported, account, fileKey, scan, permission, run, auto, pick, forget, status, html,
      badge, idbStore, mount, autoStart };
  }

  // shell.js adds this script with data-auto="1": check the folder once the page has loaded
  if (cs && cs.dataset && cs.dataset.auto === "1") {
    const go = () => setTimeout(() => root.FitFolder.autoStart(root), 1500);   // the page's own requests first
    if (root.document.readyState === "complete") go(); else root.addEventListener("load", go, { once: true });
  }
})(typeof window !== "undefined" ? window : this);
