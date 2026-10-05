/* App shell: the one navigation every page shares.
 *
 *   <script src="/api/v1/static/shell.js" data-page="charts"></script>
 *
 * Put it as the first element inside <body>; it inserts the nav synchronously
 * (no flash) and styles it from the page's own tokens (--panel, --text, --muted,
 * --line, --accent), so dark mode follows each page.
 *   >= 1600 px : top bar, icon + name + one-line purpose
 *   700–1600   : top bar, icon + name (purpose in the tooltip)
 *   < 700 px   : bottom tab bar, icon + short name
 */
(() => {
  // text from the shell catalog (backend/static/i18n/<locale>/shell.json, inlined by the server);
  // captured here so a page's own global `t` can't replace it later
  const L = window.I18N || { locale: "zh-TW", t: (k, fb) => fb ?? k, setLocale: () => {} };
  const T = (k, p) => L.t("shell." + k, p);
  const I = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const PAGES = [
    { id: "home", href: "/api/v1/overview/page", icon: I('<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v9.5h13V10"/><path d="M10 19.5v-5h4v5"/>') },
    { id: "schedule", href: "/api/v1/overview/plan/schedule/page", icon: I('<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/><path d="M7.5 13h2M11 13h2M14.5 13h2M7.5 16.5h2M11 16.5h2"/>') },
    { id: "charts", href: "/api/v1/wko5/viewer", icon: I('<path d="M4 4v16h16"/><path d="m7.5 14.5 3.5-4 3 2.5 5-6"/>') },
    { id: "plan", href: "/api/v1/plan/page", icon: I('<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/><path d="M8 14h3"/>') },
    { id: "activity", href: "/api/v1/wko5/activities/page", icon: I('<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>') },
    { id: "routes", href: "/api/v1/routes/page", icon: I('<circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/><path d="M8 18h6.5a3.5 3.5 0 0 0 0-7h-5a3.5 3.5 0 0 1 0-7H16"/>') },
    // 賽事計算機 (the page id stays "racepower": its URL and storage keys)
    { id: "racepower", href: "/api/v1/racepower/page", icon: I('<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8.5 7h7"/><path d="M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01M8.5 15h.01M12 15h.01M15.5 15h.01M8.5 18h.01M12 18h3.5"/>') },
    // 傷病紀錄 (engine/injuries.py): hidden when its API answers 404 (the demo mode)
    { id: "injuries", href: "/api/v1/wko5/injuries/page", feature: "injuries", icon: I('<rect x="2.8" y="8.2" width="18.4" height="7.6" rx="3.8" transform="rotate(-45 12 12)"/><path d="M10.6 10.6h.01M13.4 13.4h.01M10.6 13.4h.01M13.4 10.6h.01"/>') },
    { id: "settings", href: "/api/v1/wko5/settings", icon: I('<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M4.2 7.5l2 1.2M17.8 15.3l2 1.2M4.2 16.5l2-1.2M17.8 8.7l2-1.2"/><circle cx="12" cy="12" r="6.6"/>') },
  ];
  // who this page is for (GET /api/v1/session, inlined by the server as TRC_SESSION):
  // the demo instance hides the owner-only pages and locks what it can't do
  const SESSION = window.TRC_SESSION || { mode: "owner", caps: null, user: null };
  const DEMO = SESSION.mode === "demo";
  // the static read-only demo (backend/demo/export_static.py): no server; static_shim.js answers
  // the requests, keeps the schedule's edits in this browser and refuses the other writes
  const STATIC = DEMO && !!window.TRC_STATIC;
  const CAPS = new Set(SESSION.caps || []);
  const can = (cap) => !SESSION.caps || CAPS.has(cap);
  if (DEMO) {
    for (const id of ["settings", "injuries"]) {
      const i = PAGES.findIndex((p) => p.id === id);
      if (i >= 0) PAGES.splice(i, 1);
    }
  }
  // less-used pages sit under 「更多」 (owner, 2026-10-02: the bar was too crowded)
  // 成就 is the 成就 tab of 活動列表 (2026-10-02); /api/v1/achievements/page redirects there
  const MORE = new Set(["activity", "routes", "injuries", "settings"]);
  for (const p of PAGES) p.more = MORE.has(p.id);
  for (const p of PAGES) {      // name / short / purpose: shell.json "page.<id>.*"
    p.name = T(`page.${p.id}.name`); p.short = T(`page.${p.id}.short`); p.purpose = T(`page.${p.id}.purpose`);
  }

  const me = document.currentScript;
  const path = location.pathname.replace(/\/+$/, "");
  const cur = PAGES.find((p) => p.id === me?.dataset.page) || PAGES.find((p) => path === p.href) || null;

  const CSS = `
  :root { --shell-top: 53px; }
  @media (max-width: 699px) { :root { --shell-top: 0px; } }
  .appnav { --an-acc: var(--accent, #2563eb); --an-h: 52px;
    position: sticky; top: 0; z-index: 40; display: flex; align-items: stretch; gap: 2px;
    background: var(--panel, #fff); border-bottom: 1px solid var(--line, #e1e5ea); padding: 0 12px;
    font: 13px/1.3 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif; flex: none; }
  .appnav .an-brand { display: flex; align-items: center; gap: 7px; padding: 0 14px 0 4px; margin-right: 6px;
    border-right: 1px solid var(--line, #e1e5ea); color: var(--text, #111); text-decoration: none; font-weight: 700;
    letter-spacing: .02em; white-space: nowrap; }
  .appnav .an-brand i { width: 22px; height: 22px; border-radius: 6px; background: var(--an-acc); display: grid; place-items: center; }
  .appnav .an-brand i svg { width: 15px; height: 15px; color: #fff; }
  .appnav .an-item { position: relative; display: grid; grid-template-columns: 20px auto; grid-template-rows: auto auto;
    column-gap: 8px; align-content: center; padding: 7px 12px; min-height: var(--an-h); text-decoration: none;
    color: var(--muted, #667); border-radius: 8px 8px 0 0; }
  .appnav .an-item svg { grid-row: 1 / span 2; width: 20px; height: 20px; align-self: center; }
  .appnav .an-name { font-weight: 600; color: var(--text, #111); white-space: nowrap; }
  .appnav .an-purpose { font-size: 11px; color: var(--muted, #667); white-space: nowrap; }
  .appnav .an-item:hover { background: color-mix(in srgb, var(--an-acc) 8%, transparent); }
  .appnav .an-item[aria-current="page"] { color: var(--an-acc); background: color-mix(in srgb, var(--an-acc) 10%, transparent); }
  .appnav .an-item[aria-current="page"] .an-name { color: var(--an-acc); }
  .appnav .an-item[aria-current="page"]::after { content: ""; position: absolute; left: 10px; right: 10px; bottom: 0;
    height: 3px; border-radius: 3px 3px 0 0; background: var(--an-acc); }
  .appnav .an-item:focus-visible, .appnav .an-brand:focus-visible { outline: 2px solid var(--an-acc); outline-offset: -2px; }
  .appnav .an-sep { flex: 1; }
  /* nine pages: the one-line purposes fit only on a wide screen (else the bar overflowed at 1360 px) */
  @media (max-width: 1599px) { .appnav .an-purpose { display: none; } }
  @media (max-width: 1100px) {
    .appnav .an-item { grid-template-columns: 20px auto; grid-template-rows: auto; padding: 0 11px; }
    .appnav .an-item svg { grid-row: 1; }
    .appnav .an-purpose { display: none; }
    .appnav .an-brand span { display: none; }
  }
  @media (max-width: 699px) {
    .appnav { position: fixed; top: auto; bottom: 0; left: 0; right: 0; padding: 0 2px env(safe-area-inset-bottom);
      border-bottom: 0; border-top: 1px solid var(--line, #e1e5ea); gap: 0; box-shadow: 0 -4px 16px rgba(0,0,0,.08); }
    .appnav .an-brand, .appnav .an-sep { display: none; }
    .appnav .an-item { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; align-items: center; justify-content: center;
      gap: 2px; padding: 6px 0 5px; min-height: 56px; border-radius: 0; }
    .appnav .an-item svg { width: 22px; height: 22px; }
    .appnav .an-name { font-size: 11px; font-weight: 500; color: inherit; }
    .appnav .an-name .an-long { display: none; }
    .appnav .an-item[aria-current="page"] { background: none; }
    .appnav .an-item[aria-current="page"] .an-name { font-weight: 700; }
    .appnav .an-item[aria-current="page"]::after { top: 0; bottom: auto; left: 28%; right: 28%; border-radius: 0 0 3px 3px; }
    body { padding-bottom: calc(57px + env(safe-area-inset-bottom)) !important; }
  }
  @media (min-width: 700px) { .appnav .an-name .an-short { display: none; } }
  .appnav .an-lang { margin-left: auto; align-self: center; flex: none; padding: 3px 9px; border-radius: 6px; cursor: pointer;
    border: 1px solid var(--line, #e1e5ea); background: none; color: var(--muted, #667); font: inherit; font-size: 12px; font-weight: 600; }
  .appnav .an-lang:hover { color: var(--an-acc); border-color: var(--an-acc); }
  .appnav .an-lang:focus-visible { outline: 2px solid var(--an-acc); outline-offset: 1px; }
  @media (max-width: 699px) { .appnav .an-lang { margin: 0 4px; padding: 3px 6px; font-size: 11px; } }
  /* More (nav.more): a dropdown for the less-used pages */
  .appnav .an-more { position: relative; display: flex; }
  .appnav .an-more > summary { list-style: none; cursor: pointer; }
  .appnav .an-more > summary::-webkit-details-marker { display: none; }
  .appnav .an-more .an-menu { position: absolute; top: calc(100% + 4px); left: 0; z-index: 60; min-width: 210px; padding: 6px;
    background: var(--panel, #fff); border: 1px solid var(--line, #e1e5ea); border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.14);
    display: grid; gap: 2px; }
  .appnav .an-menu a.an-item { border-radius: 7px; min-height: 40px; }
  .appnav .an-menu a.an-item[aria-current="page"]::after { display: none; }
  .appnav .an-menu .an-purpose { display: block !important; }
  @media (max-width: 699px) {
    .appnav .an-more { flex: 1 1 0; min-width: 0; }
    .appnav .an-more > summary { width: 100%; }
    .appnav .an-more .an-menu { top: auto; bottom: calc(100% + 6px); left: auto; right: 4px; }
    .appnav .an-menu a.an-item { flex-direction: row; justify-content: flex-start; padding: 6px 10px; min-height: 44px; }
  }
  @media print { .appnav { display: none; } }
  `;
  const st = document.createElement("style");
  st.id = "appshell-css";
  st.textContent = CSS;
  document.head.appendChild(st);

  const item = (p) => `<a class="an-item" href="${p.href}" title="${T("nav.item_title", { name: p.name, purpose: p.purpose })}"${p.feature ? ` data-feature="${p.feature}"` : ""}${p === cur ? ' aria-current="page"' : ""}>
      ${p.icon}<span class="an-name"><span class="an-long">${p.name}</span><span class="an-short">${p.short}</span></span><span class="an-purpose">${p.purpose}</span></a>`;
  const MORE_ICON = I('<circle cx="5.5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="18.5" cy="12" r="1.6"/>');
  const nav = document.createElement("nav");
  nav.className = "appnav";
  nav.setAttribute("aria-label", T("nav.label"));
  // the language switch shows the other language; remembered in the `lang` cookie (i18n.js setLocale)
  const other = L.locale === "en" ? "zh-TW" : "en";
  nav.innerHTML = `<a class="an-brand" href="${PAGES[0].href}" title="${T("brand.title")}"><i>${PAGES[1].icon}</i><span>${T("brand")}</span></a>` +
    PAGES.filter((p) => !p.more).map(item).join("") +
    `<details class="an-more"><summary class="an-item"${cur && cur.more ? ' aria-current="page"' : ""} title="${T("nav.more_title")}">
      ${MORE_ICON}<span class="an-name"><span class="an-long">${cur && cur.more ? `${T("nav.more")} · ${cur.name}` : T("nav.more")}</span><span class="an-short">${T("nav.more")}</span></span></summary>
      <div class="an-menu" role="menu">${PAGES.filter((p) => p.more).map(item).join("")}</div></details>` +
    `<button type="button" class="an-lang" lang="${other}" data-lang="${other}" title="${T("lang.title")}">${T("lang." + other)}</button>`;
  nav.querySelector(".an-lang").addEventListener("click", (e) => L.setLocale(e.currentTarget.dataset.lang));
  if (STATIC) nav.querySelector(".an-lang").remove();      // the static demo is exported in one language
  // close 更多 on an outside click / Esc
  const more = nav.querySelector(".an-more");
  document.addEventListener("click", (e) => { if (more.open && !more.contains(e.target)) more.open = false; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") more.open = false; });

  // optional pages: drop the link when the feature is off (傷病紀錄 in the demo mode); remembered per tab
  (async () => {
    const FK = "appshell.feature.injuries";
    let on = null;
    try { on = sessionStorage.getItem(FK); } catch (_) {}
    if (on == null) {
      try { on = (await fetch("/api/v1/wko5/injuries/meta", { cache: "no-store" })).status === 404 ? "0" : "1"; } catch (_) { on = "1"; }
      try { sessionStorage.setItem(FK, on); } catch (_) {}
    }
    if (on === "0") nav.querySelectorAll('[data-feature="injuries"]').forEach((a) => a.remove());
  })();
  const mount = () => document.body.prepend(nav);
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);
  if (cur && !document.title.includes("·")) document.title = `${cur.name.split("／")[0]} · ${T("brand")}`;
  window.AppShell = { pages: PAGES, current: cur, session: SESSION, can };

  // ---- writes: CSRF header + the demo's refusals ------------------------
  // every same-origin write carries X-TRC-CSRF = the trc_csrf cookie (double submit,
  // backend/tenancy_mw.py); the token comes from GET /api/v1/session
  const nativeFetch = window.fetch.bind(window);
  let csrfP = null;
  const csrf = () => csrfP || (csrfP = nativeFetch("/api/v1/session", { cache: "no-store", credentials: "same-origin" })
    .then((r) => r.json()).then((j) => { if (j && j.demo) Object.assign(SESSION, { demo: j.demo, caps: j.caps }); return j.csrf || ""; })
    .catch(() => ""));
  const toast = (msg) => {
    let el = document.getElementById("an-toast");
    if (!el) {
      el = document.createElement("div"); el.id = "an-toast"; el.className = "an-toast"; el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = msg; el.hidden = false;
    clearTimeout(el._t); el._t = setTimeout(() => { el.hidden = true; }, 3500);
  };
  window.fetch = async (input, init) => {
    const method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
    let url;
    try { url = new URL(typeof input === "string" ? input : input.url, location.href); } catch (_) { url = null; }
    if (method === "GET" || method === "HEAD" || !url || url.origin !== location.origin) return nativeFetch(input, init);
    const tok = await csrf();
    const headers = new Headers((init && init.headers) || (input instanceof Request ? input.headers : undefined));
    if (tok) headers.set("X-TRC-CSRF", tok);
    const r = await nativeFetch(input, { ...(init || {}), headers, credentials: "same-origin" });
    if (DEMO && (r.status === 403 || r.status === 429 || r.status === 413)) {
      r.clone().json().then((j) => {
        if (j && j.code === "STATIC_SILENT") return;     // the static demo: a background save, refused quietly
        const d = j && (j.detail || j);
        toast((d && d.message) || T("demo.disabled"));
        if (j && j.code === "CSRF") csrfP = null;
      }).catch(() => {});
    }
    if (DEMO && r.ok && !SESSION.demo?.sandbox) {      // the first write made the visitor's sandbox
      csrfP = null; csrf().then(() => renderBanner());
    }
    return r;
  };
  if (!window.TRC_SESSION || DEMO) csrf();     // the cookie (and, without the inline copy, the session)

  // ---- the demo banner + locked buttons ----------------------------------
  let renderBanner = () => {};
  if (DEMO) {
    st.textContent += `
    .an-demo { position: sticky; top: var(--shell-top, 0px); z-index: 38; display: flex; flex-wrap: wrap; gap: 6px 12px;
      align-items: center; padding: 7px 14px; font: 13px/1.45 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
      background: color-mix(in srgb, #f59e0b 16%, var(--panel, #fff)); color: var(--text, #111);
      border-bottom: 1px solid color-mix(in srgb, #f59e0b 45%, var(--line, #e1e5ea)); }
    .an-demo b.an-d-tag { padding: 1px 8px; border-radius: 999px; background: #b45309; color: #fff; font-size: 12px; letter-spacing: .04em; }
    .an-demo .an-d-try a { color: var(--accent, #2563eb); font-weight: 600; text-decoration: none; margin: 0 2px; }
    .an-demo .an-d-try a:hover { text-decoration: underline; }
    .an-demo .an-d-sp { flex: 1; }
    .an-demo button, .an-demo .an-d-cta { font: inherit; font-size: 12.5px; padding: 3px 10px; border-radius: 6px; cursor: pointer;
      border: 1px solid var(--line, #d4d8de); background: var(--panel, #fff); color: var(--text, #111); text-decoration: none; }
    .an-demo button:hover, .an-demo .an-d-cta:hover { border-color: var(--accent, #2563eb); color: var(--accent, #2563eb); }
    .an-demo .an-d-left { color: var(--muted, #667); font-size: 12px; }
    @media (max-width: 699px) { .an-demo { top: 0; font-size: 12.5px; padding: 6px 10px; } .an-demo .an-d-sp { display: none; } }
    [data-cap][data-cap-locked] { opacity: .5; cursor: not-allowed !important; }
    .an-toast { position: fixed; left: 50%; bottom: 84px; transform: translateX(-50%); z-index: 80; max-width: min(92vw, 460px);
      padding: 9px 14px; border-radius: 9px; background: #1f2937; color: #fff; font: 13px/1.4 system-ui, sans-serif;
      box-shadow: 0 8px 24px rgba(0,0,0,.25); }
    .an-toast[hidden] { display: none; }
    `;
    const banner = document.createElement("div");
    banner.className = "an-demo"; banner.setAttribute("role", "note");
    const left = () => {
      const exp = SESSION.demo && SESSION.demo.expires_at ? Date.parse(SESSION.demo.expires_at) : null;
      if (!exp) return T("demo.left_none");
      const h = Math.max(0, (exp - Date.now()) / 3600e3);
      return h >= 1 ? T("demo.left_h", { h: Math.floor(h) }) : T("demo.left_m", { m: Math.max(1, Math.round(h * 60)) });
    };
    renderBanner = () => {
      const href = (id) => (PAGES.find((p) => p.id === id) || {}).href || "#";
      if (STATIC) {
        const edited = window.TRC_STATIC.hasEdits();
        banner.innerHTML = `<b class="an-d-tag">${T("demo.tag")}</b>
          <span>${T("demo.static_text")}</span>
          <span class="an-d-try">${T("demo.try")}<a href="${href("schedule")}">${T("demo.try_schedule")}</a>·<a href="${href("racepower")}">${T("demo.try_race")}</a>·<a href="${href("plan")}">${T("demo.try_plan")}</a></span>
          <span class="an-d-sp"></span>
          <span class="an-d-left">${edited ? T("demo.static_edits") : T("demo.static_none")}</span>
          <button type="button" class="an-d-reset" title="${T("demo.static_reset_title")}"${edited ? "" : " disabled"}>${T("demo.reset")}</button>`;
        banner.querySelector(".an-d-reset").addEventListener("click", () => {
          if (!confirm(T("demo.reset_confirm"))) return;
          window.TRC_STATIC.reset();
          location.reload();
        });
        return;
      }
      banner.innerHTML = `<b class="an-d-tag">${T("demo.tag")}</b>
        <span>${T("demo.text")}</span>
        <span class="an-d-try">${T("demo.try")}<a href="${href("schedule")}">${T("demo.try_schedule")}</a>·<a href="${href("racepower")}">${T("demo.try_race")}</a>·<a href="${href("plan")}">${T("demo.try_plan")}</a></span>
        <span class="an-d-sp"></span>
        <span class="an-d-left" title="${T("demo.left_title")}">${left()}</span>
        <button type="button" class="an-d-reset" title="${T("demo.reset_title")}">${T("demo.reset")}</button>
        ${SESSION.demo && SESSION.demo.cta_url ? `<a class="an-d-cta" href="${SESSION.demo.cta_url}" target="_blank" rel="noopener">${T("demo.cta")}</a>` : ""}`;
      banner.querySelector(".an-d-reset").addEventListener("click", async () => {
        if (!confirm(T("demo.reset_confirm"))) return;
        await window.fetch("/api/v1/demo/reset", { method: "POST" });
        try { sessionStorage.clear(); } catch (_) {}
        location.reload();
      });
    };
    renderBanner();
    const mountBanner = () => nav.after(banner);
    if (document.body) mountBanner(); else document.addEventListener("DOMContentLoaded", mountBanner);
    if (STATIC) window.addEventListener("trc-static-overlay", () => renderBanner());
    else setInterval(() => { const el = banner.querySelector(".an-d-left"); if (el) el.textContent = left(); }, 60e3);

    // buttons / inputs marked data-cap="sync" (… push, share, thresholds.write …) are
    // locked when the session lacks the cap: disabled, with the reason on hover
    // the pages' owner-only controls, by selector (so each page needn't know about the demo)
    const CAP_SELECTORS = [
      ["push", "#push-main, #push-caret, [data-unpush], #sd-push, #coros, #cx-go"],
      ["share", "#share, #share-go"],
      ["backtest", "#bt-run"],
      ["weather.key", "#cwasave, #cwakey"],
      ["thresholds.write", "#addthr, #savethr, [data-apply-cp], [data-apply-at], [data-apply-thr]"],
      ["dataset.write", "#rebuild, [data-rename-route], #ex-save, #act-save, [data-exclude]"],
      ["upload.fit", "input[type=file][accept*='.fit' i]:not([accept*='.gpx' i])"],
      ["ai", "[data-ai]"],
    ];
    // the static demo: writes the live demo allows but a page without a server can't do
    // (static_shim.js keeps only the schedule's session edits / rest days in the browser)
    if (STATIC) CAP_SELECTORS.push(["static.write", window.TRC_STATIC.cfg.locks || ""]);
    const LOCKED = STATIC ? T("demo.static_locked") : T("demo.locked");
    const lock = (root) => {
      if (!root.querySelectorAll) return;
      for (const [cap, sel] of CAP_SELECTORS) {
        if (can(cap) || !sel) continue;
        for (const el of root.querySelectorAll(sel)) if (!el.dataset.cap) el.dataset.cap = cap;
        if (root.matches && root.matches(sel) && !root.dataset.cap) root.dataset.cap = cap;
      }
      for (const el of (root.querySelectorAll ? root.querySelectorAll("[data-cap]") : [])) {
        const need = el.dataset.cap.split(/\s+/).filter(Boolean);
        if (need.every(can) || el.dataset.capLocked) continue;
        el.dataset.capLocked = "1";
        el.title = LOCKED;
        if ("disabled" in el) el.disabled = true;
        el.setAttribute("aria-disabled", "true");
        el.addEventListener("click", (e) => { e.preventDefault(); e.stopImmediatePropagation(); toast(LOCKED); }, true);
      }
    };
    // no WKO5 comparison content in the demo (owner): the help texts' WKO5 references go
    const scrub = (s) => s.replace(/（\s*WKO5\s*）/g, "").replace(/WKO5\s*(的定義|定義)?\s*[：:]\s*/g, "")
      .replace(/(比照|對照|從)\s*WKO5\s*的?/g, "").replace(/WKO5\s*的?\s*/g, "");
    const clean = (root) => {
      if (!root) return;
      if (root.nodeType === 3) { if (root.nodeValue.includes("WKO5")) root.nodeValue = scrub(root.nodeValue); return; }
      if (root.nodeType !== 1) return;
      const tt = root.getAttribute && root.getAttribute("title");
      if (tt && tt.includes("WKO5")) root.setAttribute("title", scrub(tt));
      if (!root.textContent.includes("WKO5") && !(root.innerHTML || "").includes("WKO5")) return;
      const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
      for (let n = w.currentNode; n; n = w.nextNode()) {
        if (n.nodeType === 3) { if (n.nodeValue.includes("WKO5")) n.nodeValue = scrub(n.nodeValue); }
        else for (const a of ["title", "aria-label", "placeholder", "data-tip"]) {
          const v = n.getAttribute(a);
          if (v && v.includes("WKO5")) n.setAttribute(a, scrub(v));
        }
      }
    };
    const startLock = () => {
      lock(document); clean(document.body);
      if (document.title.includes("WKO5")) document.title = scrub(document.title);
      new MutationObserver((ms) => {
        for (const m of ms) {
          if (m.type === "attributes") { clean(m.target); continue; }
          if (m.type === "characterData") { clean(m.target); continue; }
          for (const n of m.addedNodes) { if (n.nodeType === 1) lock(n.parentNode || n); clean(n); }
        }
      }).observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["title"] });
    };
    if (document.body) startLock(); else document.addEventListener("DOMContentLoaded", startLock);
  }

  // ---- dataset build progress -------------------------------------------
  // While the chart Dataset of the data source builds (first start, new FIT
  // files, a changed setting), every page that needs it waits. Instead of a
  // frozen 「載入中…」 they show the build's progress
  // (GET /api/v1/wko5/dataset/status: 「正在處理第 350／808 筆（解析 FIT）…」):
  // a bar under the nav, and the text of the page's own loading placeholders.
  const PCSS = `
  .an-build { position: sticky; top: var(--shell-top, 0px); z-index: 39; display: flex; gap: 10px; align-items: center;
    padding: 6px 14px; font: 12.5px/1.4 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
    background: color-mix(in srgb, var(--accent, #2563eb) 10%, var(--panel, #fff)); color: var(--text, #111);
    border-bottom: 1px solid var(--line, #e1e5ea); }
  .an-build[hidden] { display: none; }
  .an-build progress { width: 160px; height: 8px; flex: none; }
  .an-build .an-b-sub { color: var(--muted, #667); }
  @media (max-width: 699px) { .an-build { top: 0; } .an-build progress { width: 90px; } }
  `;
  st.textContent += PCSS;
  const bar = document.createElement("div");
  bar.className = "an-build"; bar.hidden = true;
  bar.setAttribute("role", "status"); bar.setAttribute("aria-live", "polite");
  bar.innerHTML = `<progress></progress><span class="an-b-msg"></span><span class="an-b-sub"></span>`;
  const mountBar = () => nav.after(bar);
  if (document.body) mountBar(); else document.addEventListener("DOMContentLoaded", mountBar);
  const WAITING = /^(載入中|計算中|讀取中|讀取你的活動資料中|計算訓練狀況中|排本週課表中|載入課表中|…$)/;
  const placeholders = () => [...document.querySelectorAll(".loading, .empty, .meta, td.empty")]
    .filter((el) => el.dataset.buildWait || (!el.children.length && WAITING.test(el.textContent.trim())));
  let tries = 0, wasBuilding = false;
  async function poll() {
    let s = null;
    try { s = await (await fetch("/api/v1/wko5/dataset/status", { cache: "no-store" })).json(); } catch (_) {}
    const building = s && s.state === "building";
    if (building) {
      wasBuilding = true;
      const p = bar.querySelector("progress");
      p.hidden = false;                       // hidden by an earlier error message
      if (s.n_total) { p.max = s.n_total; p.value = Math.min(s.n_done, s.n_total); } else p.removeAttribute("value");
      bar.querySelector(".an-b-msg").textContent = s.message || T("build.preparing");
      bar.querySelector(".an-b-sub").textContent = s.elapsed_s != null ? T("build.elapsed", { s: Math.round(s.elapsed_s) }) : "";
      bar.hidden = false;
      for (const el of placeholders()) {
        // the page has written its own text meanwhile: leave it alone
        if (el.dataset.buildWait && el.textContent !== el.dataset.buildMsg) { delete el.dataset.buildWait; continue; }
        if (!el.dataset.buildWait) el.dataset.buildWait = el.textContent;
        el.textContent = el.dataset.buildMsg = s.message || T("build.preparing");
      }
      setTimeout(poll, 1000);
      return;
    }
    bar.hidden = true;
    if (wasBuilding) {
      wasBuilding = false;
      for (const el of document.querySelectorAll("[data-build-wait]")) {   // still waiting for its own reply
        if (el.textContent === el.dataset.buildMsg) el.textContent = el.dataset.buildWait;
        delete el.dataset.buildWait; delete el.dataset.buildMsg;
      }
    }
    // a failed build: say so, also when it failed before this page opened
    // (the pages behind it would otherwise wait with 「載入中」 and no reason)
    if (s && s.state === "error") {
      bar.hidden = false; bar.querySelector("progress").hidden = true;
      bar.querySelector(".an-b-msg").textContent = T("build.failed", { error: s.error || "" });
      bar.querySelector(".an-b-sub").textContent = "";
    }
    // a page's first requests may start a build a moment after load
    if (++tries < 4) setTimeout(poll, [600, 1500, 4000][tries - 1] || 4000);
  }
  setTimeout(poll, 250);
  window.AppShell.datasetStatus = poll;

  if (DEMO) return;          // the demo has no sync, no settings, no first-run wizard

  // auto-sync on site open (COROS / TP, per the settings page; throttled inside autosync.js)
  const as = document.createElement("script");
  as.src = "/api/v1/static/autosync.js";
  as.defer = true;
  document.head.appendChild(as);

  // the floating suggestion box (B2B weekends, due tests, zone retests): every page shows it
  const sg = document.createElement("script");
  sg.src = "/api/v1/static/suggestions.js";
  sg.defer = true;
  document.head.appendChild(sg);

  // 地區 tw | intl (engine/region.py): hides the Taiwan-only parts outside Taiwan
  const rg = document.createElement("script");
  rg.src = "/api/v1/static/region.js";
  document.head.appendChild(rg);

  // the browser's time zone, once per browser: one input of the automatic zone (engine/localtime.py)
  try {
    const z = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (z && localStorage.getItem("tz.sent") !== z) {
      fetch("/api/v1/sync/timezone/browser", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zone: z }) }).then((r) => { if (r.ok) localStorage.setItem("tz.sent", z); }).catch(() => {});
    }
  } catch (e) { /* storage blocked: skip */ }

  // first run: the 一般設定 精靈 (weight / sex / height / birth year / power source), once
  const sw = document.createElement("script");
  sw.src = "/api/v1/static/setup_wizard.js";
  sw.defer = true;
  document.head.appendChild(sw);
})();
