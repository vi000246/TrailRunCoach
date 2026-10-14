/* 提示多寡與每頁上限 (SP-482; docs/research/ui-hint-audit.md §4–§5, docs/spec/ui-hints.spec.md).
 *
 * A hint is the object engine/hints.py makes — {id, cls, prio, smart, text, detail, show, ack, fp} — or an
 * old plain string (shown as before). Hints.filter() is §4.4's five steps:
 *   1. class: cls 1 goes straight to step 5 (every level, no cap; a catalog-marked one — OV8b — can be
 *      put away with 「知道了」); cls 2 / 3 go on
 *   2. level: the row's symbol for 完整／標準／精簡 — ✕ hidden · ▷ into 「詳細」 · ◐ step 3 · ● step 4
 *   3. smart condition (§5): holds → step 4, else 「詳細」
 *   4. 「知道了」 with the same condition fingerprint → 「詳細」
 *   5. cap and order (§5.2): every cls 1 in one box; the reminders by priority (1 安全…6 其他), newer
 *      first, the first CAP shown and the rest folded into 「還有 N 則提醒 ▸」
 *
 *   const st = await Hints.state();                 // GET /api/v1/plan/profile/hint-level (once a page)
 *   const r = Hints.render(list, { ...st, item: (h) => html });
 *   el.innerHTML = r.html;  detailEl.innerHTML = r.detailHtml;
 *   Hints.bind(el, () => draw());                    // 「知道了」 / 「再提醒我」, then redraw
 *
 * Strings: common.hints.* (static/i18n/{zh-TW,en}/common.json).
 */
(() => {
  const LEVELS = ["full", "standard", "lean"];
  const CAP = 3;
  const SETTINGS_HREF = "/api/v1/wko5/settings#hint-level";
  const API = "/api/v1/plan/profile/hint-level";
  const ACK_API = "/api/v1/plan/profile/hint-ack";
  const t = (k, p) => (window.I18N && I18N.t ? I18N.t(k, p) : k);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /** an object hint, or an old string: class 2, shown as before (●●●), no 「知道了」 */
  function norm(h) {
    if (h && typeof h === "object") {
      const cls = [1, 2, 3].includes(h.cls) ? h.cls : 2;
      const show = typeof h.show === "string" && [...h.show].length === 3 ? h.show : "●●●";
      return { ...h, cls, prio: h.prio >= 1 && h.prio <= 6 ? h.prio : 6, smart: h.smart !== false, show,
               text: h.text == null ? "" : String(h.text), ack: cls === 1 ? h.ack === true : h.ack !== false && !!h.fp };
    }
    return { id: null, cls: 2, prio: 6, smart: true, text: String(h ?? ""), detail: null, show: "●●●", ack: false, fp: null };
  }
  const text = (h) => (h && typeof h === "object" ? String(h.text ?? "") : String(h ?? ""));
  const lvl = (level) => (LEVELS.includes(level) ? level : "standard");
  const sym = (h, level) => [...norm(h).show][LEVELS.indexOf(lvl(level))];
  const acked = (h, acks) => !!h.id && !!h.fp && (acks || []).some((a) => a.id === h.id && a.fp === h.fp);

  /** §4.4 → {must, shown, more, detail, hidden}; each item {…hint, i (input order), acked} */
  function filter(list, opts = {}) {
    const level = lvl(opts.level), acks = opts.acks || [], cap = opts.cap ?? CAP;
    const out = { must: [], shown: [], more: [], detail: [], hidden: [] };
    const rem = [];
    (list || []).forEach((raw, i) => {
      const h = { ...norm(raw), i };
      h.acked = acked(h, acks);
      if (h.cls === 1) {                                         // step 1
        (h.ack && h.acked ? out.detail : out.must).push(h);
        return;
      }
      const s = sym(h, level);                                   // step 2
      if (s === "✕") { out.hidden.push(h); return; }
      if (s === "▷") { out.detail.push(h); return; }
      if (s === "◐" && !h.smart) { out.detail.push(h); return; } // step 3
      if (h.acked) { out.detail.push(h); return; }               // step 4
      rem.push(h);
    });
    const when = (h) => String(h.date || h.week_start || "");
    const order = (a, b) => (a.prio - b.prio) || (when(b) > when(a) ? 1 : when(b) < when(a) ? -1 : 0) || (a.i - b.i);
    out.must.sort(order);
    rem.sort(order);                                             // step 5
    out.shown = rem.slice(0, Math.max(0, cap));
    out.more = rem.slice(Math.max(0, cap));
    return out;
  }

  /** one mark that is not a sentence in a list (a chip, a badge, a shaded stretch; engine/hints.py `flag`,
   *  SP-487): §4.4 steps 1–4 without the cap → "show" | "detail" | "hidden". No hint (null) → "show" */
  function where(h, opts = {}) {
    if (!h || typeof h !== "object") return "show";
    const n = norm(h);
    if (n.cls === 1) return "show";
    const s = sym(n, opts.level);
    if (s === "✕") return "hidden";
    if (s === "▷" || (s === "◐" && !n.smart)) return "detail";
    return acked(n, opts.acks) ? "detail" : "show";
  }

  const demo = () => ((window.TRC_SESSION || {}).mode === "demo");
  const ackBtn = (h, undo) => (h.ack && h.id && h.fp && !demo()
    ? ` <button type="button" class="hint-ack" data-hint-ack="${esc(h.id)}" data-hint-fp="${esc(h.fp)}"${undo ? ' data-hint-undo="1"' : ""}>${esc(undo ? t("common.hints.unack") : t("common.hints.ack"))}</button>`
    : "");
  const plain = (h) => `<span class="hint-text">${esc(h.text)}</span>`;

  /** the page's HTML: one box for class 1, ≤ cap reminders, 「還有 N 則提醒 ▸」 + 「提示太多？」;
   *  detailHtml = what goes into the page's 「詳細」 (▷, condition not met, 知道了) */
  function render(list, opts = {}) {
    const f = filter(list, opts);
    const item = opts.item || plain;
    const row = (h) => `<div class="hint-row" data-hint-id="${esc(h.id || "")}">${item(h)}${ackBtn(h, false)}</div>`;
    let html = "";
    if (f.must.length) html += `<div class="hint-must" role="alert">${f.must.map((h) => `<div class="hint-row" data-hint-id="${esc(h.id || "")}">${item(h)}${ackBtn(h, false)}</div>`).join("")}</div>`;
    html += f.shown.map(row).join("");
    if (f.more.length) {
      const link = demo() || opts.settingsLink === false ? ""
        : ` <a class="hint-too-many" href="${esc(opts.settingsHref || SETTINGS_HREF)}">${esc(t("common.hints.too_many"))}</a>`;
      html += `<details class="hint-more"><summary>${esc(t("common.hints.more", { n: f.more.length }))} ▸</summary>${f.more.map(row).join("")}</details>${link ? `<div class="hint-more-link">${link}</div>` : ""}`;
    }
    const detailHtml = f.detail.length ? `<ul class="hint-detail">${f.detail.map((h) => `<li data-hint-id="${esc(h.id || "")}">${esc(h.text)}${h.acked ? ackBtn(h, true) : ""}</li>`).join("")}</ul>` : "";
    return { html, detailHtml, counts: { must: f.must.length, shown: f.shown.length, more: f.more.length, detail: f.detail.length, hidden: f.hidden.length }, f };
  }

  // ---- the setting and 「知道了」 ------------------------------------------------------------------
  let stateP = null;
  const FALLBACK = { level: "standard", auto: true, acks: [], cap: CAP };
  /** {level, auto, acks, cap, …}; one request a page; offline / failed → standard, nothing acknowledged */
  function state(refresh) {
    if (!stateP || refresh) {
      stateP = fetch(API, { credentials: "same-origin" })
        .then((r) => (r.ok ? r.json() : FALLBACK)).catch(() => FALLBACK)
        .then((s) => ({ ...FALLBACK, ...s, acks: Array.isArray(s && s.acks) ? s.acks : [] }));
    }
    return stateP;
  }
  async function ack(id, fp, undo) {
    const r = await fetch(ACK_API, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                                     body: JSON.stringify({ id, fp, undo: !!undo }) });
    if (!r.ok) throw new Error(`${r.status}`);
    const j = await r.json();
    const s = await state();
    stateP = Promise.resolve({ ...s, acks: j.acks || [] });
    return stateP;
  }
  /** 「知道了」 / 「再提醒我」 inside `root`; `redraw(state)` after the answer */
  function bind(root, redraw) {
    if (!root || root.dataset.hintsBound) return;
    root.dataset.hintsBound = "1";
    root.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-hint-ack]");
      if (!b || !root.contains(b)) return;
      e.preventDefault(); e.stopPropagation();
      b.disabled = true;
      try { redraw(await ack(b.dataset.hintAck, b.dataset.hintFp, b.dataset.hintUndo === "1")); }
      catch (err) { b.disabled = false; b.title = t("common.hints.ack_failed"); }
    });
  }

  // the shared look (the page's own colours when it defines them)
  if (typeof document !== "undefined" && document.head && !document.getElementById("hints-css")) {
    const st = document.createElement("style");
    st.id = "hints-css";
    st.textContent = `.hint-must{border:1px solid var(--bad,var(--err,#c0392b));border-radius:8px;padding:4px 8px;margin:0 0 6px;display:grid;gap:4px}
.hint-row{display:flex;align-items:flex-start;gap:6px}.hint-row>:first-child{flex:1;min-width:0}
button.hint-ack{flex:none;font-size:12px;padding:1px 8px;border:1px solid var(--line,#ccc);border-radius:6px;background:var(--panel,transparent);color:var(--muted,#666);cursor:pointer;margin-top:2px}
.hint-more{margin-top:4px}.hint-more>summary{cursor:pointer;color:var(--muted,#666);font-size:13px;list-style:none}
.hint-more>summary::-webkit-details-marker{display:none}.hint-more[open]>summary{margin-bottom:4px}
.hint-more-link{font-size:12.5px;margin-top:2px}.hint-too-many{color:var(--muted,#666)}
ul.hint-detail{margin:4px 0;padding-left:18px;font-size:12.5px;color:var(--muted,#666)}ul.hint-detail li{margin:2px 0}`;
    document.head.appendChild(st);
  }

  window.Hints = { LEVELS, CAP, SETTINGS_HREF, norm, text, sym, filter, where, render, state, ack, bind };
})();
