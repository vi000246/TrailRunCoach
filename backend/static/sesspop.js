// 課表卡片的 hover 說明 (SP-470): a laid-out card instead of the browser's plain title text — the
// session's name and type, its numbers, then one section per question:
//   完成情況  (done / skipped) the verdict, the numbers behind it, what was actually run
//   目標      the target split into its parts (心率 / 功率 / 配速 …), the basis as a footnote
//   怎麼練    the detail, one bullet per clause (「；」 / 「。」, never inside brackets)
//   調整      the automatic adjustment / your edit (plan_mark.js lines)
//   手錶      only when the watch needs attention (需更新 / 失敗)
// The page builds the model (schedule.html popModel); this file only lays it out and shows it next
// to the card on a mouse hover or a keyboard focus (touch: a tap opens the session dialog).
// Words: common.json sesspop.* (zh-TW / en).
//   <script src="/api/v1/static/sesspop.js"></script>     (after sesschip.js)
(function () {
  const W = typeof window !== "undefined" ? window : globalThis;
  const T = (k, p) => (W.I18N ? W.I18N.t("common.sesspop." + k, p) : k);
  const esc = (x) => String(x == null ? "" : x).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---- the text → parts ------------------------------------------------------------------------
  // split on the separators outside brackets / quotes (「（同心率跑更快就自動跟著調；推估）」 stays one)
  const OPEN = "（(「『[【", CLOSE = "）)」』]】";
  function splitOutside(text, isSep) {
    const out = [];
    let depth = 0, cur = "";
    const s = String(text || "");
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (OPEN.includes(c)) depth++;
      else if (CLOSE.includes(c)) depth = Math.max(0, depth - 1);
      const n = depth === 0 ? isSep(s, i) : 0;
      if (n) { out.push(cur); cur = ""; i += n - 1; } else cur += c;
    }
    out.push(cur);
    return out.map((x) => x.trim()).filter(Boolean);
  }
  // 「；」「。」 and an English 「; 」 / 「. 」 (not 8.0 km)
  const clauseSep = (s, i) => ("；。".includes(s[i]) ? 1 : (s[i] === ";" || s[i] === ".") && (i === s.length - 1 || s[i + 1] === " ") ? 1 : 0);
  function clauses(text) {
    return splitOutside(text, clauseSep);
  }
  // the target's parts: 「心率 < 154 bpm · 功率 124–153 W · 最近 6 次輕鬆跑：AeT …（…）」 →
  // items [{label: 心率, value: < 154 bpm}, …] and the basis (a part that explains, 「…：…」) apart
  const LABEL = /^(心率|功率|配速|坡度|速度|爬升|RPE|HR|Heart rate|Power|Pace|Grade|Speed)\s*/i;
  function target(text) {
    const parts = splitOutside(text, (s, i) => (s[i] === "·" ? 1 : 0));
    const items = [], basis = [];
    for (const p of parts) {
      const m = p.match(LABEL);
      if (m && p.length > m[0].length) items.push({ label: m[1], value: p.slice(m[0].length) });
      else if (/：|: /.test(p)) basis.push(p);
      else items.push({ label: "", value: p });
    }
    return { items, basis };
  }

  // ---- the card ---------------------------------------------------------------------------------
  const sec = (key, body, cls) => body ? `<section class="sp-sec${cls ? " " + cls : ""}"><h4>${esc(T(key))}</h4>${body}</section>` : "";
  const list = (xs) => xs.length > 1 ? `<ul>${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : xs.length ? `<p>${esc(xs[0])}</p>` : "";
  function html(m) {
    if (!m) return "";
    const st = m.status || "todo";
    const stats = (m.stats || []).filter((x) => x && x.v).map((x) =>
      `<span class="sp-st"><small>${esc(T(x.k))}</small><b>${esc(x.v)}</b></span>`).join("");
    const head = `<div class="sp-h"><span class="sp-ic">${m.icon || ""}</span><div class="sp-hd"><b class="sp-t">${esc(m.title)}</b>` +
      `<span class="sp-sub">${esc([m.kind, m.day].filter(Boolean).join(" · "))}</span></div>` +
      (m.label ? `<span class="sp-pill sp-${esc(st)}">${m.ring || ""}${esc(m.label)}</span>` : "") + `</div>`;
    // 完成情況: the verdict and its numbers, then what was actually run
    const done = [m.verdict || m.nums ? `<p class="sp-vd">${esc(m.verdict)}${m.nums ? `<span class="sp-nums">${esc(m.nums)}</span>` : ""}</p>` : "",
      m.actual ? `<p><span class="sp-k">${esc(T("actual"))}</span>${esc(m.actual)}</p>` : "",
      ...[m.hr, m.short].filter(Boolean).map((x) => `<p class="sp-dim">${esc(x)}</p>`),
      m.note ? `<p class="sp-warn">⚠ ${esc(m.note)}</p>` : ""].join("");
    const tg = target(m.target);
    const tgt = (tg.items.length ? `<dl class="sp-tg">${tg.items.map((x) =>
      `<dt>${esc(x.label)}</dt><dd${x.label ? "" : ' class="sp-wide"'}>${esc(x.value)}</dd>`).join("")}</dl>` : "") +
      tg.basis.map((b) => `<p class="sp-basis"><span class="sp-k">${esc(T("basis"))}</span>${esc(b)}</p>`).join("");
    const how = list([...clauses(m.detail), ...clauses(m.walk)]);
    const marks = (m.marks || []).length ? `<p class="sp-mk">${esc(m.marks[0])}</p>${list(m.marks.slice(1))}` : "";
    const watch = m.watch ? `<p class="sp-${m.watch.bad ? "bad" : "warn"}">${esc(m.watch.label)}</p>${m.watch.why ? `<p class="sp-dim">${esc(m.watch.why)}</p>` : ""}` : "";
    return head + (stats ? `<div class="sp-stats">${stats}</div>` : "") + sec("done", done, "sp-done") + sec("target", tgt) +
      sec("how", how) + sec("changed", marks, "sp-chg") + sec("watch", watch);
  }

  const CSS = `.sp-pop { position: fixed; z-index: 1000; width: max-content; max-width: min(360px, calc(100vw - 16px)); pointer-events: none;
    background: var(--panel); color: var(--text); border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
    box-shadow: 0 10px 28px rgba(0,0,0,.18), 0 1px 3px rgba(0,0,0,.08); font: 400 12.5px/1.5 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
    opacity: 0; transform: translateY(2px); transition: opacity .12s, transform .12s; }
  .sp-pop.on { opacity: 1; transform: none; }
  .sp-pop p { margin: 0; }
  .sp-h { display: flex; align-items: flex-start; gap: 8px; padding: 10px 12px 8px; min-width: 240px;
    border-top: 3px solid var(--sp-c, var(--accent)); }
  .sp-ic { flex: none; padding-top: 1px; color: var(--muted); }
  .sp-hd { flex: 1; min-width: 0; display: grid; }
  .sp-t { font-size: 14px; font-weight: 700; line-height: 1.35; }
  .sp-sub { color: var(--muted); font-size: 11.5px; }
  .sp-pill { flex: none; display: inline-flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 600; white-space: nowrap;
    padding: 2px 8px 2px 4px; border-radius: 999px; background: color-mix(in srgb, var(--sp-c, var(--faint)) 14%, transparent); color: var(--text); }
  .sp-pill .sc-ring { width: 13px; height: 13px; }
  .sp-pop.st-done { --sp-c: var(--good); } .sp-pop.st-partial, .sp-pop.st-off_plan { --sp-c: var(--watch); }
  .sp-pop.st-missed { --sp-c: var(--faint); } .sp-pop.st-todo { --sp-c: var(--accent); }
  .sp-stats { display: flex; flex-wrap: wrap; gap: 4px 14px; padding: 6px 12px 8px; background: var(--soft); }
  .sp-st { display: grid; line-height: 1.25; }
  .sp-st small { font-size: 10.5px; color: var(--muted); }
  .sp-st b { font-size: 13px; font-weight: 650; font-variant-numeric: tabular-nums; }
  .sp-sec { padding: 7px 12px 8px; border-top: 1px solid var(--line); }
  .sp-sec h4 { margin: 0 0 3px; font-size: 10.5px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--accent); }
  .sp-sec ul { margin: 0; padding-left: 15px; }
  .sp-sec li { margin: 1px 0; }
  .sp-sec li::marker { color: var(--faint); }
  .sp-tg { display: grid; grid-template-columns: auto 1fr; gap: 1px 10px; margin: 0; }
  .sp-tg dt { color: var(--muted); font-size: 11.5px; padding-top: 1px; }
  .sp-tg dd { margin: 0; font-weight: 650; font-variant-numeric: tabular-nums; }
  .sp-tg dd.sp-wide { grid-column: 1 / -1; font-weight: 500; }
  .sp-tg dt:empty { display: none; }
  .sp-basis { margin-top: 4px !important; color: var(--muted); font-size: 11.5px; }
  .sp-k { color: var(--muted); font-size: 11px; font-weight: 600; margin-right: 6px; }
  .sp-vd { font-weight: 650; }
  .sp-nums { display: block; font-weight: 400; color: var(--muted); font-size: 11.5px; font-variant-numeric: tabular-nums; }
  .sp-dim { color: var(--muted); font-size: 11.5px; }
  .sp-warn { color: var(--watch); font-weight: 600; }
  .sp-bad { color: var(--bad); font-weight: 600; }
  .sp-chg h4 { color: var(--muted); }
  .sp-mk { font-weight: 600; }
  .sp-mk + ul, .sp-mk + p { color: var(--muted); font-size: 11.5px; }`;

  // ---- showing it -------------------------------------------------------------------------------
  // one popup for the page; `modelOf(card)` gives the model (null: none). A mouse hover (after a short
  // pause, so passing over the grid does not flash cards) or a keyboard focus shows it; leaving, a
  // click / tap, a drag, a scroll or Escape hides it. pointer-events: none — it never takes a click.
  function bind(root, modelOf, sel) {
    sel = sel || ".chip.sc[data-uid]";
    const doc = root.ownerDocument || root;
    let pop = null, cur = null, timer = null;
    const ensure = () => {
      if (pop) return pop;
      const st = doc.createElement("style");
      st.textContent = CSS;
      doc.head.appendChild(st);
      pop = doc.createElement("div");
      pop.className = "sp-pop";
      pop.id = "sp-pop";
      pop.setAttribute("role", "tooltip");
      pop.hidden = true;
      doc.body.appendChild(pop);
      return pop;
    };
    function place(card) {
      const r = card.getBoundingClientRect(), p = pop.getBoundingClientRect();
      const vw = doc.documentElement.clientWidth, vh = doc.documentElement.clientHeight, g = 8;
      // beside the card when there is room (the week / month grid), else under / over it
      let x = r.right + g, y = r.top;
      if (x + p.width > vw - g) x = r.left - g - p.width;
      if (x < g) { x = Math.min(Math.max(g, r.left), vw - g - p.width); y = r.bottom + 6 + p.height <= vh - g ? r.bottom + 6 : r.top - 6 - p.height; }
      pop.style.left = Math.max(g, x) + "px";
      pop.style.top = Math.max(g, Math.min(y, vh - g - p.height)) + "px";
    }
    function show(card) {
      clearTimeout(timer);
      if (!card.isConnected) return hide();
      const m = modelOf(card);
      if (!m) return hide();
      ensure();
      if (cur && cur !== card) cur.removeAttribute("aria-describedby");
      cur = card;
      pop.className = "sp-pop st-" + (m.status || "todo");
      pop.innerHTML = html(m);
      pop.hidden = false;
      pop.style.left = "-9999px"; pop.style.top = "0px";
      place(card);
      requestAnimationFrame(() => pop && pop.classList.add("on"));
      card.setAttribute("aria-describedby", "sp-pop");
    }
    function hide() {
      clearTimeout(timer);
      if (cur) cur.removeAttribute("aria-describedby");
      cur = null;
      if (pop) { pop.hidden = true; pop.classList.remove("on"); }
    }
    root.addEventListener("pointerover", (e) => {
      if (e.pointerType && e.pointerType !== "mouse") return;
      const card = e.target.closest && e.target.closest(sel);
      if (!card || card === cur) return;
      clearTimeout(timer);
      timer = setTimeout(() => show(card), cur ? 60 : 250);
    });
    root.addEventListener("pointerout", (e) => {
      const card = e.target.closest && e.target.closest(sel);
      if (!card || (e.relatedTarget && card.contains(e.relatedTarget))) return;
      clearTimeout(timer);
      timer = setTimeout(hide, 80);
    });
    root.addEventListener("focusin", (e) => {
      const card = e.target.closest && e.target.closest(sel);
      if (card && card.matches(":focus-visible")) show(card);
    });
    root.addEventListener("focusout", () => hide());
    for (const ev of ["pointerdown", "dragstart"]) root.addEventListener(ev, hide, true);
    W.addEventListener("scroll", hide, true);
    W.addEventListener("resize", hide);
    doc.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
    return { show, hide };
  }

  W.SessPop = { html, target, clauses, bind, CSS };
})();
