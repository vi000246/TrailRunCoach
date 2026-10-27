/* 匯入匯出檔 (SP-349): a platform's whole-account export (a ZIP: Garmin's 「Export Your Data」, a
 * ZIP of FIT / TCX / GPX files the user packed, a TrainingPeaks or COROS export zipped) fills in the
 * history a sync cannot reach — the widget of 設定 › 資料同步.
 *
 * The file goes up in pieces (PUT /api/v1/sync/upload/archive/<id>?offset=…, each ≤ the server's
 * chunk_bytes: far below a proxy's request cap); a piece that fails is sent again a few times, and
 * an upload cut off resumes where the server says it got to (also after a reload: picking the same
 * file again). The server then works through the archive in the background (backend/sync/archive.py:
 * the job queue, in parts); this page polls GET …/<id> for the progress and shows the result:
 * added / duplicate / failed / skipped, the files skipped (not activities) listed by reason.
 * 重試 goes on after an interrupted run (and tries again the files that failed for a passing reason);
 * 清除 forgets the import (the activities stay).
 *
 *   ExportImport.mount(el, { onDone(status) })            the widget in `el`
 *   ExportImport.send(file, { fetch, onProgress, sleep, tries })   the upload -> the status, or { error }
 *   ExportImport.summary(status)                           pure: the lists the page shows
 *   ExportImport.line(status)                              pure: the progress line while it works,
 *                                                          { v, max (null: no bar), text }
 *
 * i18n: common.expimport.* and the per-file reasons of common.fitimport.reason.* (the common catalog
 * is inlined on every page).
 */
(() => {
  const URL = "/api/v1/sync/upload/archive";
  const T = (k, p) => (window.I18N ? window.I18N.t("common.expimport." + k, p) : k);
  const TF = (k, p) => (window.I18N ? window.I18N.t("common.fitimport." + k, p) : k);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // the server's caps (backend/sync/archive.py limits()); GET answers the real ones
  const DEF = { max_archive_bytes: 4 * 1073741824, chunk_bytes: 8 * 1048576, exts: [".zip"] };
  const ACCEPT = ".zip,application/zip";
  const TRIES = 4;                       // one piece: sent at most this often before the upload stops
  const POLL_MS = 2000;
  const LIST_MAX = 200;                  // names shown per list (the rest as 「還有 N 個」)
  const ACTIVE = new Set(["queued", "running"]);
  const SRC = { coros: "COROS", garmin: "Garmin", trainingpeaks: "TrainingPeaks", tp: "TrainingPeaks" };
  // the archive's own reasons (the per-file ones are fitimport's)
  const OWN = new Set(["not_activity_file", "unsupported_format", "polar_json", "apple_health", "apple_health_unreadable", "too_deep",
    "too_many_files", "too_large_inflated", "not_zip", "too_many_entries"]);

  const mb = (n) => { const v = n / 1048576; return v >= 10 ? String(Math.round(v)) : v.toFixed(1); };

  async function jsonOf(r) { try { return await r.json(); } catch (_) { return null; } }

  async function send(file, opt = {}) {
    const go = opt.fetch || ((u, o) => window.fetch(u, o));
    const sleep = opt.sleep || ((ms) => new Promise((res) => setTimeout(res, ms)));
    const tries = opt.tries || TRIES;
    let r;
    try {
      r = await go(URL, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: file.name, size: file.size }) });
    } catch (_) { return { error: "network" }; }
    let st = await jsonOf(r);
    if (!r.ok) return { error: (st && st.detail && st.detail.code) || "http_" + r.status, detail: st && st.detail };
    const chunk = (st.limits && st.limits.chunk_bytes) || DEF.chunk_bytes;
    let off = st.received || 0, fails = 0;
    if (opt.onProgress) opt.onProgress(off, file.size);
    while (off < file.size) {
      const piece = file.slice(off, Math.min(file.size, off + chunk));
      let res = null, j = null;
      try {
        res = await go(`${URL}/${st.id}?offset=${off}`, { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: piece });
        j = await jsonOf(res);
      } catch (_) { res = null; }
      if (res && res.ok && j) {
        st = j;
        off = j.received;
        fails = 0;
        if (opt.onProgress) opt.onProgress(off, file.size);
        continue;
      }
      const code = j && j.detail && j.detail.code;
      if (code === "ARCHIVE_OFFSET") {          // a piece landed twice / a reload: go on where the server is
        off = (j.detail.params && j.detail.params.received) || 0;
        continue;
      }
      if (res && res.status < 500 && res.status !== 408 && res.status !== 429) {
        return { error: code || "http_" + res.status, detail: j && j.detail, id: st.id };
      }
      fails += 1;                               // a network error, a 5xx, a proxy timeout: again
      if (fails >= tries) return { error: "upload_stopped", id: st.id, received: off };
      await sleep(1000 * 2 ** fails);
    }
    return st;
  }

  // the progress line while the server works on it: { v, max (null: no bar), text }. SP-957: while
  // Apple Health's export.xml is read (in parts, no list yet) the server says how far (`reading`:
  // bytes of export.xml) — 「正在讀取 Apple 健康資料 N%」 instead of 「排隊中」; below 100 until the
  // list is there (then 「處理中 done／total」 as before)
  function line(st) {
    const r = st.reading;
    if (st.total == null && r && r.size > 0) {
      const pct = Math.max(0, Math.min(99, Math.floor((100 * r.pos) / r.size)));
      return { v: pct, max: 100, text: T("reading", { pct }) };
    }
    if (st.state === "queued" || st.total == null) {
      return { v: 0, max: null, text: st.position ? T("queued_n", { n: st.position }) : T("queued") };
    }
    return { v: st.done, max: st.total, text: T("working", { done: st.done, total: st.total }) };
  }

  // what the page lists: { failed: [...], duplicate: [...], skipped: [...] } of { name, reason, of, date }
  function summary(st) {
    const out = { failed: [], duplicate: [], skipped: [] };
    for (const it of st.items || []) if (out[it.status]) out[it.status].push(it);
    const more = { skipped: 0 };
    for (const [reason, v] of Object.entries(st.skipped_files || {})) {
      for (const name of v.names || []) out.skipped.push({ name, reason });
      more.skipped += Math.max(0, (v.n || 0) - (v.names || []).length);
    }
    return { lists: out, more };
  }

  const why = (it) => {
    const r = it.reason;
    if (!r) return "";
    if (OWN.has(r)) return T("reason." + r);
    const src = it.of === "upload" ? TF("src_upload") : (SRC[it.of] || it.of || "");
    return TF((r === "same_activity" && !src) ? "reason.same_activity_any" : "reason." + r, { src, date: it.date || "" });
  };

  // ---- the widget ------------------------------------------------------------
  const CSS = `
  .ei-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 8px; }
  .ei-meta { color: var(--muted, #667); font-size: 12.5px; margin: 6px 0 0; }
  .ei-sum { margin: 8px 0 0; font-weight: 600; }
  .ei-err { color: var(--err, #b91c1c); margin: 6px 0 0; font-size: 13px; }
  .ei-note { margin: 6px 0 0; font-size: 13px; padding: 6px 8px; border-radius: 6px; background: var(--chip, rgba(127,127,127,.12)); }
  .ei-list { margin-top: 6px; font-size: 13px; }
  .ei-list ul { margin: 4px 0 0; padding-left: 18px; max-height: 220px; overflow: auto; }
  .ei-name { word-break: break-all; }
  .ei-why { color: var(--muted, #667); }
  .ei-how { margin-top: 6px; font-size: 12.5px; }
  .ei-how ul { margin: 4px 0 0; padding-left: 18px; }
  .ei progress { width: 160px; height: 8px; }
  @media (max-width: 480px) { .ei progress { width: 100px; } }
  `;
  let styled = false;
  const style = () => {
    if (styled) return;
    styled = true;
    const s = document.createElement("style");
    s.textContent = CSS;
    document.head.appendChild(s);
  };

  function mount(el, opt = {}) {
    style();
    const canUpload = !(window.AppShell && window.AppShell.can) || window.AppShell.can("upload.fit");
    el.classList.add("ei");
    el.innerHTML = `<p class="ei-meta">${esc(T("intro"))}</p>
      <div class="ei-row">
        <button type="button" data-ei="pick" data-cap="upload.fit">${esc(T("pick"))}</button>
        <input type="file" accept="${ACCEPT}" hidden data-ei="file" data-cap="upload.fit">
        <progress hidden data-ei="prog"></progress>
        <span class="ei-meta" role="status" aria-live="polite" data-ei="status"></span>
      </div>
      <p class="ei-meta" data-ei="hint"></p>
      <details class="ei-how"><summary>${esc(T("how.title"))}</summary><ul>
        <li>${esc(T("how.garmin"))}</li><li>${esc(T("how.tp"))}</li><li>${esc(T("how.coros"))}</li>
        <li>${esc(T("how.own"))}</li><li>${esc(T("how.polar"))}</li><li>${esc(T("how.apple"))}</li></ul></details>
      <div data-ei="result"></div>`;
    const q = (k) => el.querySelector(`[data-ei="${k}"]`);
    const pick = q("pick"), input = q("file"), prog = q("prog"), status = q("status"), result = q("result"), hint = q("hint");
    let lim = { ...DEF }, busy = false, timer = null, cur = null, notified = null;
    const showHint = () => { hint.textContent = T("limits", { gb: Math.round(lim.max_archive_bytes / 1073741824) }); };
    const setBusy = (b) => { busy = b; pick.disabled = b; };
    const progress = (v, max, text) => {
      prog.hidden = max == null;
      if (max != null) { prog.max = Math.max(1, max); prog.value = v; }
      status.textContent = text || "";
    };

    const api = async (method, path) => {
      try {
        const r = await fetch(URL + path, { method, cache: "no-store" });
        const j = await jsonOf(r);
        return r.ok ? j : { error: (j && j.detail && j.detail.code) || "http_" + r.status };
      } catch (_) { return { error: "network" }; }
    };

    function render(st, extra) {
      cur = st;
      clearTimeout(timer);
      if (!st) { progress(0, null, ""); result.innerHTML = extra || ""; setBusy(false); return; }
      let html = "";
      const kind = st.kind && st.kind !== "generic" ? `<p class="ei-note">${esc(T("kind." + st.kind))}</p>` : "";
      const head = `<p class="ei-meta">${esc(T("file", { name: st.name, mb: mb(st.size) }))}</p>`;
      const clear = `<button type="button" data-ei-act="clear">${esc(T("clear"))}</button>`;
      if (st.state === "uploading") {
        progress(0, null, "");
        html = head + `<p class="ei-err">${esc(T("unfinished", { got: mb(st.received), all: mb(st.size) }))}</p>` +
          `<div class="ei-row">${clear}</div>`;
        setBusy(false);
      } else if (ACTIVE.has(st.state)) {
        setBusy(true);
        const l = line(st);
        progress(l.v, l.max, l.text);
        html = head + kind + `<p class="ei-meta">${esc(T("background"))}</p>`;
        timer = setTimeout(poll, POLL_MS);
      } else {
        progress(0, null, "");
        setBusy(false);
        if (st.state === "failed") {
          html = head + `<p class="ei-err">${esc(T("failed." + (st.error || "other")))}</p><div class="ei-row">${clear}</div>`;
        } else {
          const parts = [TF("sum.added", { n: st.added }), TF("sum.duplicate", { n: st.duplicate }),
            TF("sum.failed", { n: st.failed })];
          if (st.skipped) parts.push(T("sum.skipped", { n: st.skipped }));
          html = head + kind;
          if (st.state === "interrupted") html += `<p class="ei-err">${esc(T("interrupted", { done: st.done, total: st.total ?? "?" }))}</p>`;
          html += `<p class="ei-sum">${esc(parts.join(TF("sep")))}</p>`;
          // 「正在更新」 only right after it ended (the steps after a sync take a minute or so), else the link alone
          const fresh = st.finished && Date.now() / 1000 - st.finished < 300;
          if (st.state === "done" && st.added) {
            html += `<p class="ei-meta">${fresh ? esc(TF("updating")) + " " : ""}<a href="/api/v1/app/activities/page">${esc(TF("to_list"))}</a></p>`;
          }
          const { lists } = summary(st);
          for (const k of ["failed", "duplicate", "skipped"]) {
            const rows = lists[k];
            const n = k === "skipped" ? st.skipped : st[k];
            if (!n) continue;
            const shown = rows.slice(0, LIST_MAX);
            const rest = n - shown.length;
            html += `<details class="ei-list"${k === "failed" ? " open" : ""}><summary>${esc(TF("list." + k, { n }))}</summary><ul>` +
              shown.map((i) => `<li><span class="ei-name">${esc(i.name)}</span> <span class="ei-why">${esc(why(i))}</span></li>`).join("") +
              (rest > 0 ? `<li class="ei-why">${esc(TF("more", { n: rest }))}</li>` : "") + "</ul></details>";
          }
          const retry = st.can_retry ? `<button type="button" data-ei-act="retry">${esc(T(st.state === "interrupted" ? "resume" : "retry", { n: st.retryable }))}</button>` : "";
          html += `<div class="ei-row">${retry}${clear}</div>`;
          if (st.state === "done" && notified !== st.id) {
            notified = st.id;
            if (opt.onDone) opt.onDone(st);
            window.dispatchEvent(new CustomEvent("expimport:done", { detail: st }));
          }
        }
      }
      result.innerHTML = html + (extra || "");
    }

    async function poll() {
      if (!cur) return;
      const st = await api("GET", "/" + cur.id);
      if (st && !st.error) render(st);
      else timer = setTimeout(poll, POLL_MS * 2);
    }

    result.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-ei-act]");
      if (!b || !cur) return;
      b.disabled = true;
      if (b.dataset.eiAct === "clear") {
        await api("DELETE", "/" + cur.id);
        notified = null;
        render(null);
      } else {
        const st = await api("POST", `/${cur.id}/retry`);
        if (st && !st.error) render(st);
        else render(cur, `<p class="ei-err">${esc(T("error.ARCHIVE_NOTHING_TO_RETRY"))}</p>`);
      }
    });

    const load = async () => {
      if (!canUpload) return;
      const j = await api("GET", "");
      if (!j || j.error) return;
      lim = { ...DEF, ...(j.limits || {}) };
      showHint();
      if (j.import) { notified = j.import.state === "done" ? j.import.id : null; render(j.import); }
    };
    showHint();
    load();

    pick.addEventListener("click", () => input.click());
    input.addEventListener("change", async () => {
      const file = input.files && input.files[0];
      input.value = "";
      if (!file || busy) return;
      if (!/\.zip$/i.test(file.name)) { render(cur, `<p class="ei-err">${esc(T("error.ARCHIVE_NOT_ZIP"))}</p>`); return; }
      if (file.size > lim.max_archive_bytes) {
        render(cur, `<p class="ei-err">${esc(T("error.ARCHIVE_TOO_LARGE", { gb: Math.round(lim.max_archive_bytes / 1073741824) }))}</p>`);
        return;
      }
      setBusy(true);
      clearTimeout(timer);
      result.innerHTML = `<p class="ei-meta">${esc(T("file", { name: file.name, mb: mb(file.size) }))}</p>`;
      progress(0, file.size, T("uploading", { got: "0", all: mb(file.size) }));
      const st = await send(file, { onProgress: (got, all) => progress(got, all, T("uploading", { got: mb(got), all: mb(all) })) });
      if (st.error) {
        setBusy(false);
        progress(0, null, "");
        const known = ["ARCHIVE_BUSY", "ARCHIVE_NOT_ZIP", "ARCHIVE_TOO_LARGE", "ARCHIVE_EMPTY", "UPLOAD_FIT_FORBIDDEN", "upload_stopped", "network"];
        const key = known.includes(st.error) ? st.error : "other";
        const msg = T("error." + key, { gb: Math.round(lim.max_archive_bytes / 1073741824) });
        if (st.id) {
          const now = await api("GET", "/" + st.id);
          render(now && !now.error ? now : null, `<p class="ei-err">${esc(msg)}</p>`);
        } else {
          render(cur, `<p class="ei-err">${esc(msg)}</p>`);
        }
        return;
      }
      notified = null;
      render(st);
    });
    return { refresh: load };
  }

  window.ExportImport = { mount, send, summary, line, URL };
})();
