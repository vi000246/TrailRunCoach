/* 問 AI (SP-786; the backend: backend/ai, api/ai.py, SP-785; decided in SP-306 Q2 方案 X).
 *
 * One button on every page (desktop: bottom right; phone: right, just above the tab bar — both stacked
 * above the floating suggestion box when it is there; both above a page's own bottom bar that they would cover,
 * shell.js data-float-clear, SP-913). Opening it takes the part of the page you are
 * looking at:
 *   1. the card / chart / session / result you last clicked or opened (a click is the clear signal),
 *   2. else the one in the middle of the screen,
 * shows it on top (「正在問：心率與功率圖（課表）」) — ✕ asks about the whole page instead — and lists 3–5
 * questions for that kind of thing, one click each; typing your own works too. The answer's parts are
 * marked 「App 規則」 (with the rule's source) or 「AI 推論」; a medical question gets a see-a-doctor line.
 *
 * Every ? popup (ManualLink.decorate in shell.js) and every hint's 「詳細」 list (ul.hint-detail,
 * static/hints.js) gets a last row 「還不懂？問 AI」 that opens it on that card, with that text.
 *
 * The page's "AI summary" of a card (docs/spec/ai-qa.spec.md; the server cleans it again,
 * backend/ai/context.clean_focus): {kind, page, title, summary, facts, rules, sources}. By default it is
 * read from the DOM — the card's heading, its visible text, its ? links to the manual; a card may say
 * more with data-ai-card / data-ai-kind / data-ai-title, or with data-ai-facts / data-ai-rules (JSON) —
 * set in the markup, or by the page on the `askai:describe` event the card gets just before it is read.
 *
 * Loaded by shell.js only when the session says so (TRC_SESSION.ai.on: the owner, switched on in 設定 ›
 * 進階 › AI 問答). Text: the shell catalog, shell.askai.* (static/i18n/<locale>/shell.json).
 */
(() => {
  if (window.AskAI) return;
  const L = window.I18N || { t: (k) => k, locale: "zh-TW" };
  const T = (k, p) => L.t("shell.askai." + k, p);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SESSION = window.TRC_SESSION || {};
  const AI = SESSION.ai || {};
  const API = "/api/v1/ai/ask";
  const SETTINGS = "/api/v1/app/settings#askai";
  const PAGE = (document.querySelector("script[data-page]") || { dataset: {} }).dataset.page || "";
  const KINDS = ["page", "card", "chart", "session", "calc", "hint"];
  const PRESETS = { page: 4, card: 3, chart: 3, session: 3, calc: 3, hint: 3 };
  // what counts as one "card" (the nearest one around a click)
  const CARD = "[data-ai-card], .sx, button.sc, .card, .mcard, .tcard, .pl-card, .xp-card, .panel, .chart-card, figure, main > section, section";
  const SKIP = ".askai, .appnav, .sugbox, .an-qpop, script, style";
  const MAX_SUMMARY = 2500;

  const pageName = () => {
    const k = `shell.page.${PAGE === "viewer" ? "charts" : PAGE}.name`;
    const n = L.t(k);
    return n && n !== k ? n : (document.title || "").split(/[·|–-]/)[0].trim();
  };
  const clip = (s, n) => { s = String(s || "").replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim(); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
  const textOf = (el) => clip(el ? (el.innerText ?? el.textContent ?? "") : "", MAX_SUMMARY);

  /** The kind of thing a card is (its data-ai-kind, else guessed from what it holds and the page). */
  function kindOf(el, page) {
    const k = el && el.dataset && el.dataset.aiKind;
    if (KINDS.includes(k)) return k;
    if (page === "racepower") return "calc";
    if (el && el.matches && el.matches(".sx, button.sc")) return "session";        // a session chip (static/sesschip.js), any page
    if (el && el.querySelector && el.querySelector("canvas, svg.chart, .chart, [data-chart], .uplot, .plot")) return "chart";
    if ((page === "schedule" || page === "plan" || page === "templates") && el && el.matches
        && el.matches(".sx, [data-uid], [data-sid], [data-day], .sess, .session, .day, .wk-day, .card")) return "session";
    return "card";
  }
  function titleOf(el) {
    if (!el) return "";
    if (el.dataset && el.dataset.aiTitle) return clip(el.dataset.aiTitle, 120);
    const h = el.querySelector && el.querySelector("h1, h2, h3, h4, legend, summary, .title, .ttl, .card-title, .hd");
    const s = clip(h ? (h.innerText ?? h.textContent) : "", 120).split("\n")[0].replace(/\s*\?$/, "").trim();
    return s || clip((el.getAttribute && el.getAttribute("aria-label")) || textOf(el).split("\n")[0], 60);
  }
  const json = (s) => { try { const v = JSON.parse(s); return Array.isArray(v) ? v : null; } catch (_) { return null; } };
  function sourcesOf(el) {
    const out = [], seen = new Set();
    const href = window.ManualLink && window.ManualLink.href;
    for (const b of (el && el.querySelectorAll ? el.querySelectorAll("[data-manual]") : [])) {
      const url = href ? href(b.dataset.manual) : null;
      if (!url || seen.has(url) || out.length >= 8) continue;
      seen.add(url);
      out.push({ title: clip(b.getAttribute("aria-label") && b.getAttribute("aria-label") !== "?" ? b.getAttribute("aria-label") : titleOf(b.closest(CARD) || el), 120) || url, url });
    }
    return out;
  }
  /** The AI summary of one card (the format above). Just before it is read the card gets an
   *  `askai:describe` event (bubbling): a page that knows the card's numbers and rules better than its
   *  text fills data-ai-* then, from its current data (SP-884: the calculator's result and segments,
   *  racepower.html; a 課表 session card, schedule.html). */
  function focusFrom(el, kind, extra) {
    if (el && el.dispatchEvent) {
      try { el.dispatchEvent(new CustomEvent("askai:describe", { bubbles: true })); } catch (_) { /* the text is still there */ }
    }
    const k = kind || kindOf(el, PAGE);
    const f = { kind: k, page: PAGE, title: titleOf(el) || pageName(), summary: clip(textOf(el) + (el && el.dataset && el.dataset.tip ? "\n" + el.dataset.tip : ""), MAX_SUMMARY) };
    const facts = el && el.dataset && json(el.dataset.aiFacts);
    const rules = el && el.dataset && json(el.dataset.aiRules);
    if (facts) f.facts = facts;
    if (rules) f.rules = rules;
    const src = sourcesOf(el);
    if (src.length) f.sources = src;
    return Object.assign(f, extra || {});
  }
  /** The whole page: its name and its headings (no card's text). */
  function pageFocus() {
    const hs = [...document.querySelectorAll("main h1, main h2, main h3, h1, h2")].filter((h) => !h.closest(SKIP))
      .map((h) => clip(h.innerText ?? h.textContent, 80).split("\n")[0]).filter(Boolean);
    return { kind: "page", page: PAGE, title: pageName(), summary: clip([...new Set(hs)].slice(0, 30).join("\n"), MAX_SUMMARY) };
  }

  // ---- which card: the last one clicked / opened, else the middle of the screen ----------------------
  let last = null;
  const cardOf = (node) => {
    const el = node && node.closest ? node.closest(CARD) : null;
    return el && !el.closest(SKIP) ? el : null;
  };
  document.addEventListener("click", (e) => {
    if (!e.target || !e.target.closest || e.target.closest(SKIP)) return;
    const c = cardOf(e.target);
    if (c) last = c;
  }, true);
  document.addEventListener("toggle", (e) => { const c = cardOf(e.target); if (c && e.target.open) last = c; }, true);
  function inView() {
    if (last && last.isConnected) return last;
    // SP-884: the page drew that card again (the 課表 after its session dialog closes): the same one by its id
    const id = last && last.dataset ? (last.dataset.uid ? `[data-uid="${CSS.escape(last.dataset.uid)}"]` : last.id ? `#${CSS.escape(last.id)}` : "") : "";
    const again = id ? cardOf(document.querySelector(id)) : null;
    if (again) return (last = again);
    const x = innerWidth / 2, y = innerHeight / 2;
    const at = document.elementFromPoint ? document.elementFromPoint(x, y) : null;
    return cardOf(at);
  }

  // ---- the look -------------------------------------------------------------------------------------
  const css = document.createElement("style");
  css.textContent = `
  .askai { font: 13.5px/1.5 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif; color: var(--text, #111); }
  .askai-fab { position: fixed; right: 16px; bottom: max(16px, var(--float-clear, 0px)); z-index: 61; display: flex; align-items: center; gap: 6px;
    padding: 9px 14px; border-radius: 999px; border: 0; background: var(--accent, #2563eb); color: #fff; font: 600 13.5px/1 system-ui, sans-serif;
    box-shadow: 0 8px 22px rgba(0,0,0,.22); cursor: pointer; }
  .askai-fab svg { width: 17px; height: 17px; }
  .askai-fab:focus-visible { outline: 3px solid color-mix(in srgb, var(--accent, #2563eb) 45%, transparent); outline-offset: 2px; }
  .askai-fab[hidden], .askai-dlg[hidden] { display: none; }
  .askai-dlg { position: fixed; right: 16px; bottom: 16px; z-index: 62; width: 400px; max-width: calc(100vw - 32px); height: min(620px, calc(100vh - 90px));
    display: flex; flex-direction: column; background: var(--panel, #fff); border: 1px solid var(--line, #e1e5ea); border-radius: 14px;
    box-shadow: 0 16px 40px rgba(0,0,0,.24); overflow: hidden; }
  .askai-hd { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--line, #e1e5ea); font-weight: 650; }
  .askai-hd svg { width: 18px; height: 18px; color: var(--accent, #2563eb); }
  .askai-x { margin-left: auto; border: 0; background: none; color: var(--muted, #667); font-size: 18px; line-height: 1; cursor: pointer; padding: 2px 6px; border-radius: 6px; }
  .askai-x:hover { background: color-mix(in srgb, var(--muted, #667) 12%, transparent); }
  .askai-ctx { display: flex; align-items: center; gap: 6px; margin: 8px 12px 0; padding: 6px 8px; border-radius: 8px;
    background: color-mix(in srgb, var(--accent, #2563eb) 9%, transparent); font-size: 12.5px; }
  .askai-ctx b { font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .askai-ctx .askai-lab { color: var(--muted, #667); flex: none; }
  .askai-ctx button { flex: none; border: 0; background: none; color: var(--muted, #667); cursor: pointer; font-size: 14px; padding: 0 4px; }
  .askai-presets { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 12px 4px; }
  .askai-presets button { border: 1px solid var(--line, #d5dbe2); background: var(--panel, #fff); color: var(--text, #111); border-radius: 999px;
    padding: 4px 10px; font: inherit; font-size: 12.5px; cursor: pointer; text-align: left; }
  .askai-presets button:hover { border-color: var(--accent, #2563eb); color: var(--accent, #2563eb); }
  .askai-log { flex: 1; overflow: auto; padding: 6px 12px 10px; display: flex; flex-direction: column; gap: 10px; }
  .askai-q { align-self: flex-end; max-width: 85%; background: var(--accent, #2563eb); color: #fff; border-radius: 12px 12px 2px 12px; padding: 6px 10px; white-space: pre-wrap; }
  .askai-a { align-self: stretch; display: grid; gap: 6px; }
  .askai-part { border-left: 3px solid var(--line, #d5dbe2); padding: 2px 0 2px 8px; white-space: pre-wrap; }
  .askai-part.rule { border-left-color: var(--accent, #2563eb); }
  .askai-tag { display: inline-block; margin-right: 6px; padding: 0 6px; border-radius: 5px; font-size: 11px; font-weight: 650; vertical-align: 1px; white-space: nowrap; }
  .askai-part.rule .askai-tag { background: color-mix(in srgb, var(--accent, #2563eb) 14%, transparent); color: var(--accent, #2563eb); }
  .askai-part.inference .askai-tag { background: color-mix(in srgb, var(--muted, #667) 15%, transparent); color: var(--muted, #556); }
  .askai-cite { color: var(--muted, #667); font-size: 11.5px; }
  .askai-src { font-size: 12px; color: var(--muted, #667); border-top: 1px dashed var(--line, #e1e5ea); padding-top: 4px; }
  .askai-src a { color: var(--accent, #2563eb); }
  .askai-med { border: 1px solid var(--bad, #c0392b); color: var(--bad, #c0392b); border-radius: 8px; padding: 5px 8px; font-size: 12.5px; }
  .askai-meta { font-size: 11.5px; color: var(--muted, #667); }
  .askai-err { color: var(--bad, #c0392b); font-size: 12.5px; }
  .askai-err a, .askai-nokey a { color: var(--accent, #2563eb); }
  .askai-nokey { margin: 8px 12px 0; padding: 7px 9px; border-radius: 8px; border: 1px solid var(--line, #e1e5ea); font-size: 12.5px; }
  .askai-ft { display: flex; gap: 6px; padding: 8px 12px 10px; border-top: 1px solid var(--line, #e1e5ea); }
  .askai-ft textarea { flex: 1; resize: none; height: 38px; max-height: 120px; border: 1px solid var(--line, #d5dbe2); border-radius: 8px;
    padding: 8px 9px; font: inherit; background: var(--bg, var(--panel, #fff)); color: var(--text, #111); }
  .askai-ft button { border: 0; border-radius: 8px; padding: 0 14px; background: var(--accent, #2563eb); color: #fff; font: 600 13px system-ui, sans-serif; cursor: pointer; }
  .askai-ft button:disabled { opacity: .5; cursor: default; }
  .askai-row { margin-top: 6px; padding-top: 5px; border-top: 1px solid var(--line, #e1e5ea); white-space: normal; }
  .askai-link { border: 0; background: none; padding: 0; color: var(--accent, #2563eb); font: inherit; font-weight: 600; cursor: pointer; }
  .askai-link:hover { text-decoration: underline; }
  .askai-busy { color: var(--muted, #667); font-size: 12.5px; }
  @media (max-width: 699px) {
    .askai-fab { right: 10px; bottom: max(calc(66px + env(safe-area-inset-bottom)), var(--float-clear, 0px)); padding: 9px 12px; }
    .askai-dlg { left: 0; right: 0; width: auto; max-width: none; bottom: calc(57px + env(safe-area-inset-bottom)); height: min(78vh, calc(100vh - 120px));
      border-radius: 14px 14px 0 0; border-bottom: 0; }
  }
  @media print { .askai-fab, .askai-dlg { display: none; } }`;
  document.head.appendChild(css);

  const ICON = `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.8 4.6L18.5 9.4 13.8 11.2 12 16l-1.8-4.8L5.5 9.4l4.7-1.8z"/><path d="M18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/></svg>`;
  const fab = document.createElement("button");
  fab.type = "button";
  fab.className = "askai askai-fab";
  fab.innerHTML = `${ICON}<span>${esc(T("button"))}</span>`;
  fab.title = T("button_title");
  fab.setAttribute("aria-haspopup", "dialog");
  const dlg = document.createElement("div");
  dlg.className = "askai askai-dlg";
  dlg.hidden = true;
  dlg.setAttribute("role", "dialog");
  dlg.setAttribute("aria-label", T("title"));
  dlg.innerHTML = `<div class="askai-hd">${ICON}<span>${esc(T("title"))}</span><button type="button" class="askai-x" data-askai-close aria-label="${esc(T("close"))}">✕</button></div>
    <div class="askai-ctx" data-askai-ctx><span class="askai-lab">${esc(T("asking"))}</span><b data-askai-ctx-t></b><button type="button" data-askai-page title="${esc(T("whole_page_title"))}" aria-label="${esc(T("whole_page_title"))}">✕</button></div>
    <div class="askai-nokey" data-askai-nokey hidden>${esc(T("no_key"))} <a href="${SETTINGS}">${esc(T("no_key_link"))}</a></div>
    <div class="askai-presets" data-askai-presets></div>
    <div class="askai-log" data-askai-log aria-live="polite"></div>
    <div class="askai-ft"><textarea data-askai-in rows="1" maxlength="500" placeholder="${esc(T("placeholder"))}" aria-label="${esc(T("placeholder"))}"></textarea>
      <button type="button" data-askai-send>${esc(T("send"))}</button></div>`;
  const $ = (sel) => dlg.querySelector(sel);

  // ---- state ----------------------------------------------------------------------------------------
  let focus = null;       // the card's summary (null: the whole page)
  let history = [];       // [{q, a}] of this dialog
  let busy = false;

  function presets(kind) {
    const n = PRESETS[kind] || 3;
    return Array.from({ length: n }, (_, i) => T(`preset.${kind}.${i + 1}`));
  }
  function showCtx() {
    const f = focus || pageFocus();
    $("[data-askai-ctx-t]").textContent = focus ? `${f.title}${f.title === pageName() ? "" : T("in_page", { page: pageName() })}` : T("whole_page", { page: pageName() });
    $("[data-askai-page]").hidden = !focus;
    $("[data-askai-presets]").innerHTML = presets(f.kind).map((q) => `<button type="button" data-askai-q="${esc(q)}">${esc(q)}</button>`).join("");
  }

  /** The answer's HTML: each part tagged app rule / AI inference, the cited ids, the sources, the medical line. */
  function answerHtml(a) {
    const parts = (a.parts || []).map((p) => {
      const cite = p.refs && p.refs.length ? ` <span class="askai-cite">[${p.refs.map(esc).join(", ")}]</span>` : "";
      return `<div class="askai-part ${p.type === "rule" ? "rule" : "inference"}"><span class="askai-tag">${esc(T(p.type === "rule" ? "tag_rule" : "tag_inference"))}</span>${esc(p.text)}${cite}</div>`;
    }).join("");
    const src = (a.sources || []).length ? `<div class="askai-src">${esc(T("sources"))} ${(a.sources || []).map((s) => {
      const name = esc(s.title || s.id) + (s.source ? ` — ${esc(s.source)}` : "");
      return `${esc(s.id)} ${s.url ? `<a href="${esc(s.url)}">${name}</a>` : name}`;
    }).join(" · ")}</div>` : "";
    const med = a.medical ? `<div class="askai-med" role="note">${esc(T("medical"))}</div>` : "";
    const sent = a.sent || {};
    const meta = `<div class="askai-meta">${esc(T("sent", { rules: sent.rules || 0, weeks: sent.weeks || 0 }))}${sent.in_view ? esc(T("sent_card", { title: sent.in_view })) : ""}`
      + ` · ${esc(T("used", { n: a.used_today ?? 0, limit: a.daily_limit ?? 0 }))}${a.cached ? " · " + esc(T("cached")) : ""}</div>`;
    return `<div class="askai-a">${med}${parts}${src}${meta}</div>`;
  }
  function errText(code) {
    const k = `err.${String(code || "AI_UPSTREAM").replace(/^AI_/, "").toLowerCase()}`;
    const v = T(k);
    return v && v !== "shell.askai." + k ? v : T("err.upstream");
  }

  async function ask(q) {
    q = String(q || "").trim();
    if (!q || busy) return;
    busy = true;
    $("[data-askai-send]").disabled = true;
    const log = $("[data-askai-log]");
    log.insertAdjacentHTML("beforeend", `<div class="askai-q">${esc(q)}</div><div class="askai-busy" data-askai-wait>${esc(T("thinking"))}</div>`);
    log.scrollTop = log.scrollHeight;
    let html;
    try {
      const r = await fetch(API, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, focus: focus || pageFocus(), history }) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        const code = body && body.detail && body.detail.code;
        html = `<div class="askai-err">${esc(errText(code))}${code === "AI_NO_KEY" || code === "AI_KEY_INVALID" || code === "AI_OFF" ? ` <a href="${SETTINGS}">${esc(T("no_key_link"))}</a>` : ""}</div>`;
      } else {
        html = answerHtml(body);
        history = history.concat([{ q, a: (body.parts || []).map((p) => p.text).join("\n") }]).slice(-6);
      }
    } catch (e) {
      html = `<div class="askai-err">${esc(T("err.network"))}</div>`;
    }
    const w = log.querySelector("[data-askai-wait]");
    if (w) w.remove();
    log.insertAdjacentHTML("beforeend", html);
    log.scrollTop = log.scrollHeight;
    busy = false;
    $("[data-askai-send]").disabled = false;
  }

  /** Open the dialog on `el` (a card; null: the card in view, false: the whole page); `extra` overrides the summary. */
  function open(el, kind, extra) {
    const c = el === false ? null : (el || inView());
    const next = c ? focusFrom(c, kind, extra) : (extra ? Object.assign(pageFocus(), extra) : null);
    if (JSON.stringify(next) !== JSON.stringify(focus)) { history = []; $("[data-askai-log]").innerHTML = ""; }
    focus = next;
    showCtx();
    $("[data-askai-nokey]").hidden = AI.key !== false;
    dlg.hidden = false;
    fab.hidden = true;
    setTimeout(() => { const i = $("[data-askai-in]"); if (i && !matchMedia("(max-width: 699px)").matches) i.focus(); }, 0);
  }
  function close() { dlg.hidden = true; fab.hidden = false; place(); fab.focus(); }

  fab.addEventListener("click", () => open(null));
  dlg.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.hasAttribute("data-askai-close")) close();
    else if (b.hasAttribute("data-askai-page")) { focus = null; history = []; $("[data-askai-log]").innerHTML = ""; showCtx(); }
    else if (b.dataset.askaiQ) ask(b.dataset.askaiQ);
    else if (b.hasAttribute("data-askai-send")) { const i = $("[data-askai-in]"); const q = i.value; i.value = ""; ask(q); }
  });
  $("[data-askai-in]").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); const q = e.target.value; e.target.value = ""; ask(q); }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !dlg.hidden) close(); });

  // ---- beside the suggestion box when it is there: left of it on a desktop (it may be tall), above it on a
  // phone (it spans the width there) ------------------------------------------------------------------------
  function place() {
    const sg = document.querySelector(".sugbox:not([hidden])");
    const narrow = matchMedia("(max-width: 699px)").matches;
    fab.style.bottom = ""; fab.style.right = "";
    if (!sg) return;
    const r = sg.getBoundingClientRect();
    if (!r.height || getComputedStyle(sg).visibility === "hidden") return;
    // SP-851: a page that leaves the box its room at the right (suggestions.js sugbox-docked, the 課表) — above
    // it, in that room, not left of it over the page
    if (narrow || document.documentElement.classList.contains("sugbox-docked")) {
      const base = parseFloat(getComputedStyle(fab).bottom) || 16;
      const need = innerHeight - r.top + 8;
      if (need > base) fab.style.bottom = `${Math.round(need)}px`;
    } else if (r.left < innerWidth - 40) {
      fab.style.right = `${Math.round(innerWidth - r.left + 12)}px`;
    }
  }
  let ro = null;
  const watch = () => {
    const sg = document.querySelector(".sugbox");
    if (sg && window.ResizeObserver && !sg._askai) { sg._askai = true; ro = ro || new ResizeObserver(place); ro.observe(sg); }
    place();
  };
  addEventListener("resize", place, { passive: true });
  // SP-913: a page's bottom bar (shell.js data-float-clear) lifted the box and this button: stack again
  addEventListener("floatclear", place);

  // ---- 「還不懂？問 AI」 under a ? popup and a hint's 「詳細」 ------------------------------------------
  function row(onClick) {
    const d = document.createElement("div");
    d.className = "askai-row";
    const b = document.createElement("button");
    b.type = "button"; b.className = "askai-link"; b.textContent = T("still_unclear");
    b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); onClick(); });
    d.appendChild(b);
    return d;
  }
  /** A ? popup (shell.js manDecorate): the row last; it opens the dialog on the ?'s card with the popup's text. */
  function decorate(pop, btn) {
    if (!pop || pop.querySelector(".askai-row")) return pop;
    const text = clip(pop.innerText ?? pop.textContent, 1200);
    pop.appendChild(row(() => {
      const card = cardOf(btn) || null;
      const t = titleOf(card) || pageName();
      const sum = clip(text + (card ? "\n—\n" + textOf(card) : ""), MAX_SUMMARY);
      open(card || false, "hint", { kind: "hint", title: T("hint_title", { title: t }), summary: sum });
      if (pop.isConnected && !pop.dataset.pinned) pop.remove();
    }));
    return pop;
  }
  function decorateHints(root) {
    for (const ul of (root && root.querySelectorAll ? root.querySelectorAll("ul.hint-detail:not([data-askai])") : [])) {
      ul.dataset.askai = "1";
      ul.after(row(() => {
        const card = cardOf(ul);
        const t = titleOf(card) || pageName();
        open(card || false, "hint", { kind: "hint", title: T("hint_title", { title: t }), summary: clip(textOf(ul) + (card ? "\n—\n" + textOf(card) : ""), MAX_SUMMARY) });
      }));
    }
  }

  const mount = () => {
    document.body.appendChild(fab);
    document.body.appendChild(dlg);
    decorateHints(document);
    new MutationObserver((ms) => {
      for (const m of ms) for (const n of m.addedNodes) if (n.nodeType === 1) { decorateHints(n); if (n.classList && n.classList.contains("sugbox")) watch(); }
    }).observe(document.body, { childList: true, subtree: true });
    watch();
  };
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);

  window.AskAI = { open, ask, decorate, focusFrom, pageFocus, kindOf, presets, answerHtml, errText, PRESETS };
})();
