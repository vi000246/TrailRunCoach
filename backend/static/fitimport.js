/* 手動匯入 FIT (SP-323): pick FIT files or a whole folder and upload them — the widget of
 * 設定 › 資料同步 and of the 活動列表.
 *
 * Every browser picks files (<input type=file multiple>); a whole folder needs `webkitdirectory`
 * (desktop browsers, iPhone from iOS 18.4, Android Chrome from 132: docs/research/fit-on-device.md
 * §4.1) and its button is hidden where the browser has none. Names that are not FIT (.fit /
 * .fit.gz, or TCX / GPX: SP-349) are skipped and listed, never sent; a file over the size cap fails here. The rest go to
 * POST /api/v1/sync/upload/fit (backend/sync/upload.py) in batches below the server's caps (read
 * from GET of the same URL), one after the other: a failed batch fails its own files only. The
 * last batch carries last=1 and what the earlier ones added, so the server rebuilds the charts,
 * the overview and the plan inputs once, as after a sync.
 *
 *   FitImport.mount(el, { onDone(result), listLink })   the widget in `el`
 *   FitImport.plan(files, limits)                       pure: { send: [[file…]…], skipped, tooBig, total, overSelect }
 *   FitImport.upload(files, { limits, fetch, onProgress })   the batches -> { added, duplicate, failed, skipped, items }
 *
 * i18n: common.fitimport.* (the common catalog is inlined on every page).
 */
(() => {
  const URL = "/api/v1/sync/upload/fit";
  const T = (k, p) => (window.I18N ? window.I18N.t("common.fitimport." + k, p) : k);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // the server's caps (backend/sync/upload.py limits()); GET answers the real ones. batch_bytes:
  // what one request carries (its parsing must end well inside a proxy's response time limit)
  const DEF = { max_file_bytes: 20 * 1048576, max_files: 20, max_request_bytes: 100 * 1048576, batch_bytes: 2 * 1048576,
    max_select: 3000, exts: [".fit", ".fit.gz", ".tcx", ".tcx.gz", ".gpx", ".gpx.gz", ".pwx", ".pwx.gz"], max_track_bytes: 64 * 1048576 };
  // TCX / GPX too (SP-349: what Garmin's and TrainingPeaks' exports hold besides FIT; .pwx SP-833; the server
  // turns them into FIT). The inputs carry data-cap themselves: shell.js's demo lock finds FIT
  // inputs by an accept without .gpx (the course GPX inputs stay open there)
  const ACCEPT = ".fit,.fit.gz,.tcx,.tcx.gz,.gpx,.gpx.gz,.pwx,.pwx.gz";
  const TRACK = [".tcx", ".tcx.gz", ".gpx", ".gpx.gz", ".pwx", ".pwx.gz"];
  const LIST_MAX = 200;                 // names shown per list (the rest as 「還有 N 個」)
  const SRC = { coros: "COROS", garmin: "Garmin", trainingpeaks: "TrainingPeaks", tp: "TrainingPeaks" };

  // a folder pick names a file by its path inside the folder
  const nameOf = (f) => (f && (f.webkitRelativePath || f.name)) || "";
  const isFit = (name, exts) => { const n = name.toLowerCase(); return exts.some((e) => n.endsWith(e)); };
  // a TCX / GPX holds several times the bytes of the same FIT: its own cap
  const capOf = (name, lim) => (isFit(name, TRACK) ? (lim.max_track_bytes || lim.max_file_bytes) : lim.max_file_bytes);

  function plan(files, limits) {
    const lim = { ...DEF, ...(limits || {}) };
    const skipped = [], tooBig = [], ok = [];
    for (const f of files) {
      const name = nameOf(f);
      if (!isFit(name, lim.exts)) skipped.push(name);
      else if (f.size > capOf(name, lim)) tooBig.push(name);
      else ok.push(f);
    }
    // ≤ max_files per request and ≤ batch_bytes (≤ 90 % of the body cap: room for the multipart
    // headers); a file bigger than that goes alone
    const cap = Math.min(lim.batch_bytes || Infinity, lim.max_request_bytes * 0.9);
    const send = [];
    let cur = [], bytes = 0;
    for (const f of ok) {
      if (cur.length && (cur.length >= lim.max_files || bytes + f.size > cap)) { send.push(cur); cur = []; bytes = 0; }
      cur.push(f);
      bytes += f.size;
    }
    if (cur.length) send.push(cur);
    return { send, skipped, tooBig, total: ok.length, overSelect: ok.length > lim.max_select };
  }

  // a refused request -> one reason for each of its files
  async function refusal(r) {
    let code = null;
    try { const j = await r.json(); code = j && j.detail && j.detail.code; } catch (_) {}
    if (code === "UPLOAD_BUSY") return "busy";
    if (r.status === 403) return "forbidden";
    if (r.status === 413) return "too_large_batch";
    // a proxy gave up waiting (Cloudflare 524, a gateway 502 / 504): the server may still finish
    if (r.status === 502 || r.status === 504 || r.status === 524) return "timeout";
    return "http_" + r.status;
  }

  async function upload(files, opt = {}) {
    const lim = { ...DEF, ...(opt.limits || {}) };
    const send = opt.fetch || ((u, o) => window.fetch(u, o));
    const p = plan(files, lim);
    const res = { added: 0, duplicate: 0, failed: 0, skipped: 0, items: [], stopped: null };
    const add = (it) => { res.items.push(it); if (it.status in res) res[it.status] += 1; };
    for (const name of p.skipped) add({ name, status: "skipped", reason: "not_fit" });
    for (const name of p.tooBig) add({ name, status: "failed", reason: "too_large" });
    if (p.overSelect) { res.stopped = "too_many"; return res; }
    let done = 0;
    for (let i = 0; i < p.send.length; i++) {
      const batch = p.send[i];
      const fd = new FormData();
      for (const f of batch) fd.append("files", f, nameOf(f));
      fd.append("last", i === p.send.length - 1 ? "1" : "0");
      fd.append("earlier_added", String(res.added));
      let j = null, why = null;
      try {
        const r = await send(URL, { method: "POST", body: fd });
        if (r.ok) j = await r.json(); else why = await refusal(r);
      } catch (_) { why = "network"; }
      if (j) for (const it of j.items || []) add(it);
      else for (const f of batch) add({ name: nameOf(f), status: "failed", reason: why });
      done += batch.length;
      if (opt.onProgress) opt.onProgress(done, p.total);
      if (why === "busy" || why === "forbidden") {          // the rest would be refused too
        for (const b of p.send.slice(i + 1)) for (const f of b) add({ name: nameOf(f), status: "failed", reason: why });
        res.stopped = why;
        break;
      }
    }
    return res;
  }

  // ---- the widget ------------------------------------------------------------
  const CSS = `
  .fi-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 8px; }
  .fi-meta { color: var(--muted, #667); font-size: 12.5px; margin: 6px 0 0; }
  .fi-sum { margin: 8px 0 0; font-weight: 600; }
  .fi-err { color: var(--err, #b91c1c); margin: 6px 0 0; font-size: 13px; }
  .fi-list { margin-top: 6px; font-size: 13px; }
  .fi-list ul { margin: 4px 0 0; padding-left: 18px; max-height: 220px; overflow: auto; }
  .fi-name { word-break: break-all; }
  .fi-why { color: var(--muted, #667); }
  .fi progress { width: 160px; height: 8px; }
  @media (max-width: 480px) { .fi progress { width: 100px; } }
  `;
  let styled = false;
  const style = () => {
    if (styled) return;
    styled = true;
    const st = document.createElement("style");
    st.textContent = CSS;
    document.head.appendChild(st);
  };

  const why = (it) => {
    const r = it.reason;
    if (!r) return "";
    if (r.startsWith("http_")) return T("reason.http", { status: r.slice(5) });
    const src = it.of === "upload" ? T("src_upload") : (SRC[it.of] || it.of || "");
    return T((r === "same_activity" && !src) ? "reason.same_activity_any" : "reason." + r, { src, date: it.date || "" });
  };

  function render(box, res, opt) {
    const parts = [T("sum.added", { n: res.added }), T("sum.duplicate", { n: res.duplicate }), T("sum.failed", { n: res.failed })];
    if (res.skipped) parts.push(T("sum.skipped", { n: res.skipped }));
    let html = `<p class="fi-sum">${esc(parts.join(T("sep")))}</p>`;
    if (opt.noneFit) html += `<p class="fi-err">${esc(T("none_fit"))}</p>`;
    if (res.stopped) html += `<p class="fi-err">${esc(T("stopped." + res.stopped, { max: opt.limits.max_select }))}</p>`;
    if (res.added) {
      html += `<p class="fi-meta">${esc(T("updating"))}` +
        (opt.listLink ? ` <a href="/api/v1/app/activities/page">${esc(T("to_list"))}</a>` : "") + "</p>";
    }
    for (const st of ["failed", "duplicate", "skipped"]) {
      const rows = res.items.filter((i) => i.status === st);
      if (!rows.length) continue;
      const shown = rows.slice(0, LIST_MAX);
      html += `<details class="fi-list"${st === "failed" ? " open" : ""}><summary>${esc(T("list." + st, { n: rows.length }))}</summary><ul>` +
        shown.map((i) => `<li><span class="fi-name">${esc(i.name)}</span> <span class="fi-why">${esc(why(i))}</span></li>`).join("") +
        (rows.length > shown.length ? `<li class="fi-why">${esc(T("more", { n: rows.length - shown.length }))}</li>` : "") +
        "</ul></details>";
    }
    box.innerHTML = html;
  }

  function mount(el, opt = {}) {
    style();
    const canUpload = !(window.AppShell && window.AppShell.can) || window.AppShell.can("upload.fit");
    const folderOk = "webkitdirectory" in document.createElement("input");
    el.classList.add("fi");
    // data-cap: the demo's lock (shell.js) disables the buttons with its reason
    el.innerHTML = `<p class="fi-meta">${esc(T("intro"))}</p>
      <div class="fi-row">
        <button type="button" data-fi="pick" data-cap="upload.fit">${esc(T("pick_files"))}</button>
        <button type="button" data-fi="pickdir" data-cap="upload.fit"${folderOk ? "" : " hidden"}>${esc(T("pick_folder"))}</button>
        <input type="file" accept="${ACCEPT}" multiple hidden data-fi="files" data-cap="upload.fit">
        <input type="file" accept="${ACCEPT}" multiple webkitdirectory hidden data-fi="dir" data-cap="upload.fit">
        <progress hidden data-fi="prog"></progress>
        <span class="fi-meta" role="status" aria-live="polite" data-fi="status"></span>
      </div>
      <p class="fi-meta" data-fi="hint"></p>
      <div data-fi="result"></div>`;
    const q = (k) => el.querySelector(`[data-fi="${k}"]`);
    const status = q("status"), prog = q("prog"), result = q("result"), hint = q("hint");
    const btns = [q("pick"), q("pickdir")];
    let lim = { ...DEF }, busy = false;
    const showHint = () => {
      hint.textContent = T("limits", { mb: Math.round(lim.max_file_bytes / 1048576), n: lim.max_select }) +
        (folderOk ? "" : " " + T("no_folder"));
    };
    const refresh = async () => {
      if (!canUpload) return;
      try {
        const r = await fetch(URL, { cache: "no-store" });
        if (!r.ok) return;
        const j = await r.json();
        lim = { ...DEF, ...(j.limits || {}) };
        showHint();
        if (!busy) status.textContent = j.rows ? T("count", { n: j.rows }) : "";
      } catch (_) { /* the defaults stay */ }
    };
    showHint();
    refresh();
    q("pick").addEventListener("click", () => q("files").click());
    q("pickdir").addEventListener("click", () => q("dir").click());
    const go = async (input) => {
      const files = Array.from(input.files || []);
      input.value = "";                      // the same files can be picked again
      if (!files.length || busy) return;
      const p = plan(files, lim);
      if (p.overSelect) {
        status.textContent = "";
        result.innerHTML = `<p class="fi-err">${esc(T("stopped.too_many", { max: lim.max_select }))}</p>`;
        return;
      }
      busy = true;
      btns.forEach((b) => { b.disabled = true; });
      result.innerHTML = "";
      prog.hidden = !p.total;
      prog.max = Math.max(1, p.total);
      prog.value = 0;
      status.textContent = p.total ? T("progress", { done: 0, total: p.total }) : "";
      let res;
      try {
        res = await upload(files, { limits: lim, onProgress: (done, total) => {
          prog.value = done;
          status.textContent = T("progress", { done, total });
        } });
      } finally {
        busy = false;
        btns.forEach((b) => { b.disabled = false; });
        prog.hidden = true;
        status.textContent = "";
      }
      render(result, res, { ...opt, limits: lim, noneFit: !p.total && !p.tooBig.length });
      refresh();
      if (opt.onDone) opt.onDone(res);
      window.dispatchEvent(new CustomEvent("fitimport:done", { detail: res }));
    };
    q("files").addEventListener("change", (e) => go(e.target));
    q("dir").addEventListener("change", (e) => go(e.target));
    return { refresh };
  }

  window.FitImport = { mount, plan, upload, URL };
})();
