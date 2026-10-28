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
    { id: "charts", href: "/api/v1/app/viewer", icon: I('<path d="M4 4v16h16"/><path d="m7.5 14.5 3.5-4 3 2.5 5-6"/>') },
    { id: "plan", href: "/api/v1/plan/page", icon: I('<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/><path d="M8 14h3"/>') },
    { id: "activity", href: "/api/v1/app/activities/page", icon: I('<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>') },
    { id: "routes", href: "/api/v1/routes/page", icon: I('<circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/><path d="M8 18h6.5a3.5 3.5 0 0 0 0-7h-5a3.5 3.5 0 0 1 0-7H16"/>') },
    // 賽事計算機 (the page id stays "racepower": its URL and storage keys)
    { id: "racepower", href: "/api/v1/racepower/page", icon: I('<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8.5 7h7"/><path d="M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01M8.5 15h.01M12 15h.01M15.5 15h.01M8.5 18h.01M12 18h3.5"/>') },
    // 賽事管理 (SP-675, api/race_manager.py): every race, one race's day, two compared — under 更多
    { id: "races", href: "/api/v1/app/races/page", icon: I('<path d="M6 21V4"/><path d="M6 4.5h11l-2.2 3.5L17 11.5H6"/>') },
    // 傷病紀錄 (engine/injuries.py): hidden when its API answers 404 (the demo mode)
    { id: "injuries", href: "/api/v1/app/injuries/page", feature: "injuries", icon: I('<rect x="2.8" y="8.2" width="18.4" height="7.6" rx="3.8" transform="rotate(-45 12 12)"/><path d="M10.6 10.6h.01M13.4 13.4h.01M10.6 13.4h.01M13.4 10.6h.01"/>') },
    // 操作手冊 (SP-558, api/manual.py): public, the one page a signed-out visitor's bar has
    { id: "manual", href: "/manual", icon: I('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/><path d="M8.5 7.5h7M8.5 11h5"/>') },
    { id: "settings", href: "/api/v1/app/settings", icon: I('<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M4.2 7.5l2 1.2M17.8 15.3l2 1.2M4.2 16.5l2-1.2M17.8 8.7l2-1.2"/><circle cx="12" cy="12" r="6.6"/>') },
    // 管理 (SP-351, api/admin.py): site settings and users — only an admin with sign-in on sees the link
    { id: "admin", href: "/admin", icon: I('<path d="M12 3 4.5 6v5.5c0 4.4 3.1 8.2 7.5 9.5 4.4-1.3 7.5-5.1 7.5-9.5V6z"/><path d="m9 12 2.2 2.2L15.5 10"/>') },
    // 關於 (SP-495, api/about.py): version, sources, licences, trademarks; public like the manual, last in 更多
    { id: "about", href: "/about", icon: I('<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/><path d="M12 7.5h.01"/>') },
  ];
  // who this page is for (GET /api/v1/session, inlined by the server as TRC_SESSION):
  // the demo instance hides the owner-only pages and locks what it can't do
  const SESSION = window.TRC_SESSION || { mode: "owner", caps: null, user: null };
  const DEMO = SESSION.mode === "demo";
  const CAPS = new Set(SESSION.caps || []);
  const can = (cap) => !SESSION.caps || CAPS.has(cap);
  // signed out (sign-in mode) on a public page — the manual, 關於: their links only, plus 登入; no data is asked for
  const PUBLIC = SESSION.mode === "signed_out";
  const PUBLIC_PAGES = new Set(["manual", "about"]);
  // 管理 (SP-351): in sign-in mode, for whoever holds an admin scope; the owner's single-user app keeps
  // its menu as it was (the page still answers at /admin there), the demo never has it
  const ADMIN = !!SESSION.login && !DEMO && ((SESSION.membership && SESSION.membership.scopes) || [])
    .some((s) => s === "admin:config" || s === "admin:users");
  const adminCur = location.pathname.replace(/\/+$/, "") === "/admin";
  if (!ADMIN && !adminCur) PAGES.splice(PAGES.findIndex((p) => p.id === "admin"), 1);
  if (PUBLIC) PAGES.splice(0, PAGES.length, ...PAGES.filter((p) => PUBLIC_PAGES.has(p.id)));
  if (DEMO) {
    for (const id of ["settings", "injuries"]) {
      const i = PAGES.findIndex((p) => p.id === id);
      if (i >= 0) PAGES.splice(i, 1);
    }
  }
  // 代看 (SP-791, accounts/viewing.py): a coach viewing a student — the injury log and the admin page are
  // closed (the server refuses them too); the banner and 回到我自己 below; the controls locked by caps.
  // SP-822: the injury log stays when the student turned on 「看傷病紀錄」 (read only: its writes locked below)
  const VIEW = (!PUBLIC && !DEMO && SESSION.login && SESSION.viewing) || null;
  if (VIEW) {
    for (const id of (VIEW.perms && VIEW.perms.injury === true) ? ["admin"] : ["injuries", "admin"]) {
      const i = PAGES.findIndex((p) => p.id === id);
      if (i >= 0) PAGES.splice(i, 1);
    }
  }
  // less-used pages sit under 「更多」 (owner, 2026-10-02: the bar was too crowded)
  // 成就 is the 成就 tab of 活動列表 (2026-10-02); /api/v1/achievements/page redirects there
  // 操作手冊 sits right above 設定 (SP-505: 「選單放在設定頁的上面」)
  // 關於 (SP-495) last, under 設定
  const MORE = new Set(["activity", "routes", "races", "injuries", "manual", "settings", "admin", "about"]);
  for (const p of PAGES) p.more = !PUBLIC && MORE.has(p.id);
  // SP-480: 設定's purpose names 計算模式 only for whoever may switch it (SP-464: in sign-in mode an
  // admin; the owner's app and the demo have no `login`)
  const NOMODE = !!SESSION.login && !((SESSION.membership && SESSION.membership.scopes) || []).includes("admin");
  for (const p of PAGES) {      // name / short / purpose: shell.json "page.<id>.*"
    p.name = T(`page.${p.id}.name`); p.short = T(`page.${p.id}.short`);
    p.purpose = T(`page.${p.id}.purpose${p.id === "settings" && NOMODE ? "_nomode" : ""}`);
  }

  const me = document.currentScript;
  const path = location.pathname.replace(/\/+$/, "");
  const cur = PAGES.find((p) => p.id === me?.dataset.page) || PAGES.find((p) => path === p.href)
    || PAGES.find((p) => p.id === "manual" && path.startsWith("/manual/")) || null;

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
  .appnav .an-brand img { width: 24px; height: 24px; flex: none; }
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
  /* sign out (sign-in mode, SP-58): a button styled as the menu links */
  .appnav .an-menu button.an-item { border: 0; border-top: 1px solid var(--line, #e1e5ea); border-radius: 0 0 7px 7px;
    background: none; font: inherit; text-align: left; cursor: pointer; min-height: 40px; width: 100%; }
  .appnav .an-menu button.an-item .an-purpose { overflow: hidden; text-overflow: ellipsis; max-width: 220px; }
  @media (max-width: 699px) {
    .appnav .an-more { flex: 1 1 0; min-width: 0; }
    .appnav .an-more > summary { width: 100%; }
    .appnav .an-more .an-menu { top: auto; bottom: calc(100% + 6px); left: auto; right: 4px; }
    .appnav .an-menu a.an-item, .appnav .an-menu button.an-item { flex-direction: row; justify-content: flex-start;
      padding: 6px 10px; min-height: 44px; }
  }
  @media print { .appnav { display: none; } }
  /* SP-751: a read-only field looks read-only on every page (greyed, dashed border; the page's own --muted /
     --panel, so dark mode too) — a field with its own lock look (.locked, the 課表 dialog's 分鐘) keeps it */
  input[readonly]:not(.locked):not([type=checkbox]):not([type=radio]), textarea[readonly]:not(.locked) {
    background-color: color-mix(in srgb, var(--muted, #6a7482) 16%, var(--panel, #fff)) !important;
    border-style: dashed !important; color: var(--muted, #6a7482); cursor: default; }
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
  // SP-971: the Trig badge, its version for 32 px and below
  const LOGO = '<img src="/api/v1/static/icons/trig-logo-small.svg" alt="" width="24" height="24">';
  const LOGIN_ICON = I('<path d="M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3"/><path d="M14 17l5-5-5-5"/><path d="M19 12H8"/>');
  const login = () => `<a class="an-item an-login" href="/login?${new URLSearchParams({ next: location.pathname + location.hash })}" title="${T("account.login_title")}">
      ${LOGIN_ICON}<span class="an-name"><span class="an-long">${T("account.login")}</span><span class="an-short">${T("account.login")}</span></span></a>`;
  nav.innerHTML = `<a class="an-brand" href="${PUBLIC ? "/" : PAGES[0].href}" title="${T("brand.title")}">${LOGO}<span>${T("brand")}</span></a>` +
    PAGES.filter((p) => !p.more).map(item).join("") +
    (PUBLIC ? login() : `<details class="an-more"><summary class="an-item"${cur && cur.more ? ' aria-current="page"' : ""} title="${T("nav.more_title")}">
      ${MORE_ICON}<span class="an-name"><span class="an-long">${cur && cur.more ? `${T("nav.more")} · ${cur.name}` : T("nav.more")}</span><span class="an-short">${T("nav.more")}</span></span></summary>
      <div class="an-menu" role="menu">${PAGES.filter((p) => p.more).map(item).join("")}</div></details>`) +
    `<button type="button" class="an-lang" lang="${other}" data-lang="${other}" title="${T("lang.title")}">${T("lang." + other)}</button>`;
  nav.querySelector(".an-lang").addEventListener("click", (e) => L.setLocale(e.currentTarget.dataset.lang));
  // close 更多 on an outside click / Esc
  const more = nav.querySelector(".an-more") || { open: false, contains: () => false };
  document.addEventListener("click", (e) => { if (more.open && !more.contains(e.target)) more.open = false; });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") more.open = false; });
  // sign-in mode (SP-328): 方案與訂閱 in 更多, above 登出 (not for the site owner: every feature already)
  const role = SESSION.membership && SESSION.membership.role;
  if (SESSION.login && SESSION.user && (role === "user" || role === "subscriber")) {
    const pl = document.createElement("a");
    pl.className = "an-item an-plans"; pl.href = "/plans"; pl.setAttribute("role", "menuitem");
    if (location.pathname === "/plans") pl.setAttribute("aria-current", "page");
    pl.innerHTML = `${I('<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/><path d="M7 15h4"/>')}<span class="an-name"></span><span class="an-purpose"></span>`;
    pl.querySelector(".an-name").textContent = T("account.plans");
    pl.querySelector(".an-purpose").textContent = T(role === "subscriber" ? "account.plans_sub" : "account.plans_free");
    pl.title = T("account.plans_title");
    nav.querySelector(".an-menu").appendChild(pl);
  }
  // 教練看板 (SP-790, api/coach_board.py): in 更多 for a coach (GET /api/v1/session user.coach)
  if (SESSION.login && SESSION.user && SESSION.user.coach && !DEMO) {
    const cb = document.createElement("a");
    cb.className = "an-item an-coach"; cb.href = "/api/v1/coach/board/page"; cb.setAttribute("role", "menuitem");
    if (location.pathname === "/api/v1/coach/board/page") cb.setAttribute("aria-current", "page");
    cb.innerHTML = `${I('<circle cx="9" cy="8" r="3"/><path d="M3.5 19.5a5.5 5.5 0 0 1 11 0"/><path d="M16 5.5a3 3 0 0 1 0 5.6M17.5 14.2a5.5 5.5 0 0 1 3 5.3"/>')}<span class="an-name"></span><span class="an-purpose"></span>`;
    cb.querySelector(".an-name").textContent = T("account.coach_board");
    cb.querySelector(".an-purpose").textContent = T("account.coach_board_purpose");
    cb.title = T("account.coach_board_purpose");
    nav.querySelector(".an-menu").appendChild(cb);
  }
  // sign-in mode (SP-58): 登出 at the end of 更多, with whose account this is
  if (SESSION.login && SESSION.user) {
    const out = document.createElement("button");
    out.type = "button";
    out.className = "an-item an-logout";
    out.setAttribute("role", "menuitem");
    out.innerHTML = `${I('<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 17l-5-5 5-5"/><path d="M5 12h11"/>')}<span class="an-name"></span><span class="an-purpose"></span>`;
    out.querySelector(".an-name").textContent = T("account.logout");
    out.querySelector(".an-purpose").textContent = SESSION.user.email || SESSION.user.name || "";
    out.title = T("account.logout_title");
    out.addEventListener("click", async () => {
      let to = "/login";
      // SP-314: this person's offline copy goes first (the next one to sign in never sees it)
      try { await Promise.race([window.AppOffline.clear(), new Promise((r) => setTimeout(r, 1500))]); } catch (_) {}
      try {
        const j = await (await window.fetch("/api/v1/account/logout", { method: "POST" })).json();
        if (j && typeof j.next === "string" && j.next.startsWith("/") && !j.next.startsWith("//")) to = j.next;
      } catch (_) {}
      location.href = to;
    });
    nav.querySelector(".an-menu").appendChild(out);
  }

  // optional pages: drop the link when the feature is off (傷病紀錄 in the demo mode); remembered per tab
  if (!PUBLIC) (async () => {
    const FK = "appshell.feature.injuries";
    let on = null;
    try { on = sessionStorage.getItem(FK); } catch (_) {}
    if (on == null) {
      try { on = (await fetch("/api/v1/app/injuries/meta", { cache: "no-store" })).status === 404 ? "0" : "1"; } catch (_) { on = "1"; }
      try { sessionStorage.setItem(FK, on); } catch (_) {}
    }
    if (on === "0") nav.querySelectorAll('[data-feature="injuries"]').forEach((a) => a.remove());
  })();
  const mount = () => document.body.prepend(nav);
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);
  // 站內公告 (SP-351): the admin's notice under the bar, until closed (remembered per notice text)
  if (SESSION.notice && !PUBLIC) {
    const NK = "appshell.notice.closed";
    let closed = null;
    try { closed = localStorage.getItem(NK); } catch (_) {}
    if (closed !== SESSION.notice) {
      const ns = document.createElement("style");
      ns.textContent = `.an-notice { display: flex; gap: 10px; align-items: flex-start; padding: 7px 14px;
        font: 13px/1.5 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif; color: var(--text, #111);
        background: color-mix(in srgb, var(--accent, #2563eb) 10%, var(--panel, #fff));
        border-bottom: 1px solid color-mix(in srgb, var(--accent, #2563eb) 30%, var(--line, #e1e5ea)); }
        .an-notice span { flex: 1; white-space: pre-line; overflow-wrap: anywhere; }
        .an-notice button { font: inherit; border: 0; background: none; color: var(--muted, #667); cursor: pointer; padding: 0 4px; }`;
      document.head.appendChild(ns);
      const bar = document.createElement("div");
      bar.className = "an-notice"; bar.setAttribute("role", "note");
      const txt = document.createElement("span");
      txt.textContent = SESSION.notice;
      const x = document.createElement("button");
      x.type = "button"; x.textContent = "×"; x.title = T("notice.close"); x.setAttribute("aria-label", T("notice.close"));
      x.addEventListener("click", () => { bar.remove(); try { localStorage.setItem(NK, SESSION.notice); } catch (_) {} });
      bar.append(txt, x);
      const put = () => nav.after(bar);
      if (document.body) put(); else document.addEventListener("DOMContentLoaded", put);
    }
  }
  // 測試帳號 (SP-611): a strip on every page, so a test account's browser is never taken for a real one
  if (SESSION.user && SESSION.user.test && !PUBLIC) {
    const ts = document.createElement("style");
    ts.textContent = `.an-test { padding: 4px 14px; font: 600 12.5px/1.5 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
      color: #fff; background: #b45309; text-align: center; overflow-wrap: anywhere; }`;
    document.head.appendChild(ts);
    const tb = document.createElement("div");
    tb.className = "an-test"; tb.setAttribute("role", "note");
    tb.textContent = T("test_account", { name: SESSION.user.name || `#${SESSION.user.id}` });
    const putT = () => nav.after(tb);
    if (document.body) putT(); else document.addEventListener("DOMContentLoaded", putT);
  }
  // 代看 (SP-791): 「正在看 X 的資料」, what the student lets the coach change (SP-792), 回到我自己 — on every page
  const VIEW_CSS = `.an-view { position: sticky; top: var(--shell-top, 0px); z-index: 38; display: flex; flex-wrap: wrap;
      gap: 4px 12px; align-items: center; padding: 7px 14px; font: 13px/1.45 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
      color: #fff; background: #1d4ed8; overflow-wrap: anywhere; }
    .an-view b { font-weight: 700; }
    .an-view .an-v-what { font-size: 12.5px; opacity: .92; }
    .an-view .an-v-sp { flex: 1; }
    .an-view button { font: inherit; font-size: 12.5px; font-weight: 600; padding: 3px 12px; border-radius: 6px; cursor: pointer;
      border: 1px solid #fff; background: #fff; color: #1d4ed8; }
    .an-view button:disabled { opacity: .6; cursor: default; }
    .an-vnote { padding: 6px 14px; font: 13px/1.45 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif; color: var(--text, #111);
      background: color-mix(in srgb, #1d4ed8 10%, var(--panel, #fff)); border-bottom: 1px solid color-mix(in srgb, #1d4ed8 30%, var(--line, #e1e5ea)); }
    .an-vnote a { color: var(--accent, #2563eb); font-weight: 600; margin-left: 8px; white-space: nowrap; }
    @media (max-width: 699px) { .an-view { top: 0; padding: 6px 10px; } .an-view .an-v-sp { display: none; } }`;
  let viewBar = null;               // the banner first, a note under it
  const putUnderNav = (el) => {
    const put = () => (viewBar && viewBar !== el && viewBar.isConnected ? viewBar.after(el) : nav.after(el));
    if (document.body) put(); else document.addEventListener("DOMContentLoaded", put);
  };
  const viewNote = new URLSearchParams(location.search).get("coach_view");
  if (VIEW || viewNote || ((SESSION.coach_edit || SESSION.coach_requests) && !PUBLIC && !DEMO)) {
    const vs = document.createElement("style");
    vs.textContent = VIEW_CSS;
    document.head.appendChild(vs);
  }
  if (VIEW) {
    const vb = document.createElement("div");
    vb.className = "an-view"; vb.setAttribute("role", "status");
    const who = document.createElement("b");
    who.textContent = T("view.banner", { name: VIEW.name || `#${VIEW.student_id}` });
    const perms = VIEW.perms || {};
    const can = [perms.plan && T("view.perm_plan"), perms.training && T("view.perm_training")].filter(Boolean);
    const what = document.createElement("span");
    what.className = "an-v-what";
    what.textContent = can.length ? T("view.can", { what: can.join(T("view.sep")) }) : T("view.readonly");
    const sp = document.createElement("span"); sp.className = "an-v-sp";
    const back = document.createElement("button");
    back.type = "button"; back.textContent = T("view.back"); back.title = T("view.back_title");
    back.addEventListener("click", async () => {
      back.disabled = true;
      let to = VIEW.back || "/api/v1/app/settings#coach";
      try {
        const j = await (await window.fetch("/api/v1/coach/view/stop", { method: "POST" })).json();
        if (j && typeof j.go === "string" && j.go.startsWith("/") && !j.go.startsWith("//")) to = j.go;
      } catch (_) {}
      location.href = to;
    });
    vb.append(who, what, sp, back);
    putUnderNav(vb);
    viewBar = vb;
  }
  // a page 代看 may not open sent the coach to the overview; a withdrawal ended the view (their own 教練 tab)
  if (viewNote === "blocked" || viewNote === "ended") {
    const n = document.createElement("div");
    n.className = "an-vnote"; n.setAttribute("role", "note");
    n.textContent = T(viewNote === "ended" ? "view.ended" : "view.blocked");
    putUnderNav(n);
    try {
      const u = new URL(location.href);
      u.searchParams.delete("coach_view");
      history.replaceState(history.state, "", u.pathname + u.search + u.hash);
    } catch (_) {}
  }
  // SP-792: the student's 首頁 and 課表 say when their coach last changed something (the last 14 days)
  if (!VIEW && !PUBLIC && !DEMO && SESSION.coach_edit && cur && (cur.id === "home" || cur.id === "schedule")) {
    const e = SESSION.coach_edit, CK = "appshell.coach_edit.seen";
    let seen = null;
    try { seen = localStorage.getItem(CK); } catch (_) {}
    if (seen !== e.at) {
      const n = document.createElement("div");
      n.className = "an-vnote"; n.setAttribute("role", "note");
      let at = e.at;
      try { at = new Date(e.at).toLocaleString(L.locale || undefined, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); } catch (_) {}
      const txt = document.createElement("span");
      txt.textContent = T("coach_edit.line", { name: e.coach || T("coach_edit.someone"), at });
      const a = document.createElement("a");
      a.href = "/api/v1/app/settings#coach"; a.textContent = T("coach_edit.log");
      const x = document.createElement("a");
      x.href = "#"; x.textContent = T("notice.close"); x.setAttribute("role", "button");
      x.addEventListener("click", (ev) => { ev.preventDefault(); n.remove(); try { localStorage.setItem(CK, e.at); } catch (_) {} });
      n.append(txt, a, x);
      putUnderNav(n);
    }
  }
  // SP-823: a coach with students waiting for a yes (they typed the code) reads it on every page — not only
  // in 設定 › 教練. Closing it hides this batch (the newest request's time); a new request shows it again
  if (!VIEW && !PUBLIC && !DEMO && SESSION.coach_requests && SESSION.coach_requests.n > 0) {
    const q = SESSION.coach_requests, CK = "appshell.coach_requests.seen";
    let seen = null;
    try { seen = localStorage.getItem(CK); } catch (_) {}
    if (seen !== q.at) {
      const n = document.createElement("div");
      n.className = "an-vnote an-creq"; n.setAttribute("role", "note");
      const txt = document.createElement("span");
      txt.textContent = T("coach_req.line", { n: q.n });
      const a = document.createElement("a");
      a.href = "/api/v1/app/settings#coach"; a.textContent = T("coach_req.go");
      const x = document.createElement("a");
      x.href = "#"; x.textContent = T("coach_req.close"); x.setAttribute("role", "button");
      x.addEventListener("click", (ev) => { ev.preventDefault(); n.remove(); try { localStorage.setItem(CK, q.at); } catch (_) {} });
      n.append(txt, a, x);
      putUnderNav(n);
    }
  }
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
  // link (SP-328): {href, text} after the message — the upgrade hint's 「看方案與訂閱」; stays longer
  const toast = (msg, link) => {
    let el = document.getElementById("an-toast");
    if (!el) {
      el = document.createElement("div"); el.id = "an-toast"; el.className = "an-toast"; el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = msg; el.hidden = false;
    if (link) {
      const a = document.createElement("a");
      a.href = link.href; a.textContent = link.text;
      a.style.cssText = "color:#9cc3ff;margin-left:10px;font-weight:600;white-space:nowrap";
      el.appendChild(a);
    }
    clearTimeout(el._t); el._t = setTimeout(() => { el.hidden = true; }, link ? 8000 : 3500);
  };
  // 代看 (SP-791): the student withdrew — the page reloads (the server sends the coach to their own 教練 tab);
  // a write the student does not allow, or that only the student may make, says why
  const viewCheck = (r, write) => {
    if (r && r.status === 403) {
      r.clone().json().then((j) => {
        const d = j && (j.detail || j), code = d && d.code;
        if (code === "COACH_VIEW_ENDED") location.reload();
        else if (write && typeof code === "string" && code.startsWith("COACH_")) toast((d && d.message) || T("view.locked"));
      }).catch(() => {});
    }
    return r;
  };
  window.fetch = async (input, init) => {
    const method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
    let url;
    try { url = new URL(typeof input === "string" ? input : input.url, location.href); } catch (_) { url = null; }
    if (method === "GET" || method === "HEAD" || !url || url.origin !== location.origin) {
      const p = nativeFetch(input, init);
      return VIEW && url && url.origin === location.origin ? p.then((r) => viewCheck(r, false)) : p;
    }
    const tok = await csrf();
    const headers = new Headers((init && init.headers) || (input instanceof Request ? input.headers : undefined));
    if (tok) headers.set("X-TRC-CSRF", tok);
    const r = await nativeFetch(input, { ...(init || {}), headers, credentials: "same-origin" });
    if (VIEW) viewCheck(r, true);
    if (DEMO && (r.status === 403 || r.status === 429 || r.status === 413)) {
      r.clone().json().then((j) => {
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

  // ---- plans and scopes (SP-330) -------------------------------------------
  // GET /api/v1/session's `membership.scopes` says what this person's plan / role allows. A control
  // marked data-scope="calc:backtest" is disabled with the reason; a 403 SCOPE_REQUIRED / 429
  // QUOTA_EXCEEDED from any request shows the reason and, for the free tier, the upgrade hint.
  // No membership (the owner's single-user app, an old server): everything stays as it was.
  const MS = SESSION.membership || null;
  const SCOPES = MS ? new Set(MS.scopes || []) : null;
  const hasScope = (s) => !SCOPES || SCOPES.has(s);
  window.AppScopes = { has: hasScope, membership: MS };
  const UPGRADE = T("scope.upgrade");
  // SP-328: in sign-in mode the upgrade hint leads to 方案與訂閱 (/plans: the plans and the checkout)
  const PLANS = SESSION.login && MS && MS.role === "user" ? { href: "/plans", text: T("scope.see_plans") } : null;
  // SP-569 ⑤: the subscribe hint (scope.upgrade) only for what a subscription adds (membership.upgrade_scopes);
  // a scope no plan gives (TrainingPeaks' website login …) says it is the site's own, not to subscribe
  const UPGRADABLE = new Set((MS && MS.upgrade_scopes) || []);
  const lockReason = (need) => {
    const missing = need.filter((s) => !hasScope(s));
    if (missing.some((s) => (MS.paused_sources || []).includes(s))) return T("scope.paused");
    if (MS.role === "user" && missing.every((s) => UPGRADABLE.has(s))) return UPGRADE;
    return T(MS.role === "user" ? "scope.site_only" : "scope.none");
  };
  // the pages' controls by selector (so a page needn't know about plans)
  const SCOPE_SELECTORS = [
    ["calc:backtest", "#bt-run"],
    ["sync:tp_web", "#src-tp [data-act=login], #src-tp [data-act=sync]"],
    ["sync:coros_credential", "#src-coros [data-act=login], #src-coros [data-act=sync]"],
  ];
  const lockScopes = (root) => {
    if (!SCOPES || !root || !root.querySelectorAll) return;
    for (const [sc, sel] of SCOPE_SELECTORS) {
      if (hasScope(sc)) continue;
      for (const el of root.querySelectorAll(sel)) if (!el.dataset.scope) el.dataset.scope = sc;
    }
    for (const el of root.querySelectorAll("[data-scope]")) {
      if (el.dataset.scopeLocked) continue;
      const need = el.dataset.scope.split(/\s+/).filter(Boolean);
      if (need.every(hasScope)) continue;
      el.dataset.scopeLocked = "1";
      el.title = lockReason(need);
      if ("disabled" in el) el.disabled = true;
      el.setAttribute("aria-disabled", "true");
      el.addEventListener("click", (e) => { e.preventDefault(); e.stopImmediatePropagation();
        toast(el.title, el.title === UPGRADE ? PLANS : null); }, true);
    }
  };
  if (SCOPES) {
    const run = () => { lockScopes(document); new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach(lockScopes)))
      .observe(document.body, { childList: true, subtree: true }); };
    if (document.body) run(); else document.addEventListener("DOMContentLoaded", run);
    const scopedFetch = window.fetch;
    let lastNote = 0;
    window.fetch = async (input, init) => {
      const r = await scopedFetch(input, init);
      if ((r.status === 403 || r.status === 429) && Date.now() - lastNote > 4000) {
        r.clone().json().then((j) => {
          const d = j && j.detail;
          if (!d || (d.code !== "SCOPE_REQUIRED" && d.code !== "QUOTA_EXCEEDED")) return;
          lastNote = Date.now();
          const what = d.label || d.scope || d.quota || "";
          toast(d.code === "QUOTA_EXCEEDED"
            ? T("scope.quota_used", { what }) + (d.upgrade ? T("scope.sep") + UPGRADE : "")
            : T("scope.what", { what, why: d.paused ? T("scope.paused_short") : d.upgrade ? UPGRADE
              : T(MS.role === "user" ? "scope.site_only" : "scope.none") }),
          d.upgrade && !d.paused ? PLANS : null);
        }).catch(() => {});
      }
      return r;
    };
  }

  // ---- low quota (SP-718): a free user down to 20 % of a quota hears once, on the page that uses it
  // (計算機: calc, 圖表: charts), that a subscription lifts it — never blocking. Once per quota and
  // period (localStorage, by membership.quotas[key].resets); the page's own requests that count a use
  // re-read GET /api/v1/session (debounced), so it can come up while the page is open.
  // AppQuota.left(key) / .low(u) serve 設定's plan summary.
  {
    const QPAGE = { racepower: ["calc"], charts: ["charts"] };
    const leftOf = (u) => (u && u.limit != null ? Math.max(0, u.limit - (u.used || 0)) : null);
    const isLow = (u) => !!u && u.limit != null && u.limit > 0 && leftOf(u) <= u.limit * 0.2;
    window.AppQuota = { left: leftOf, low: isLow, quotas: () => (MS && MS.quotas) || {} };
    const keys = (MS && SESSION.login && MS.role === "user" && QPAGE[me?.dataset.page]) || [];
    if (keys.length) {
      const LS = "trc.quotaLow.";
      const seen = (k, u) => { try { return localStorage.getItem(LS + k) === String(u.resets); } catch (_) { return false; } };
      const mark = (k, u) => { try { localStorage.setItem(LS + k, String(u.resets)); } catch (_) {} };
      let shown = false;
      const show = (k, u) => {
        shown = true; mark(k, u);
        if (!document.getElementById("an-qlow-css")) {
          const st = document.createElement("style"); st.id = "an-qlow-css";
          st.textContent = `.an-qlow { position: fixed; left: 50%; bottom: 84px; transform: translateX(-50%); z-index: 79;
            width: max-content; max-width: min(92vw, 480px); box-sizing: border-box; display: flex; gap: 10px; align-items: center; padding: 9px 10px 9px 14px; border-radius: 9px;
            background: #1f2937; color: #fff; font: 13px/1.4 system-ui, sans-serif; box-shadow: 0 8px 24px rgba(0,0,0,.25); }
            .an-qlow a { color: #9cc3ff; font-weight: 600; white-space: nowrap; }
            .an-qlow button { font: inherit; font-size: 16px; line-height: 1; padding: 2px 6px; border: 0; border-radius: 6px;
              background: transparent; color: #cbd5e1; cursor: pointer; }
            .an-qlow button:hover { background: rgba(255,255,255,.12); color: #fff; }`;
          document.head.appendChild(st);
        }
        const el = document.createElement("div");
        el.className = "an-qlow"; el.setAttribute("role", "status"); el.dataset.quota = k;
        const msg = document.createElement("span");
        msg.textContent = T(`quota.low.${k}`, { n: leftOf(u), limit: u.limit }) + T("scope.sep") + T("quota.low.upgrade");
        const a = document.createElement("a"); a.href = "/plans"; a.textContent = T("scope.see_plans");
        const x = document.createElement("button"); x.type = "button"; x.textContent = "×";
        x.setAttribute("aria-label", T("quota.low.close")); x.title = T("quota.low.close");
        x.addEventListener("click", () => el.remove());
        el.append(msg, a, x);
        document.body.appendChild(el);
        setTimeout(() => el.remove(), 15000);
      };
      const check = (qs) => {
        if (shown) return;
        for (const k of keys) { const u = qs && qs[k]; if (isLow(u) && !seen(k, u)) { show(k, u); return; } }
      };
      const start = () => check(MS.quotas);
      if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
      // the requests that count a use on these pages: the calculator's entries, a chart
      const COUNTS = /^\/api\/v1\/(racepower\/(plan|predict|estimate|share|export\/plan)$|app\/views\/[^/]+\/dashboards\/\d+\/charts\/)/;
      let timer = 0, last = 0;
      const reread = () => {
        last = Date.now();
        nativeFetch("/api/v1/session", { cache: "no-store", credentials: "same-origin" }).then((r) => r.json())
          .then((j) => { const q = j && j.membership && j.membership.quotas; if (q) { MS.quotas = q; check(q); } }).catch(() => {});
      };
      const qFetch = window.fetch;
      window.fetch = async (input, init) => {
        const r = await qFetch(input, init);
        if (!shown && r.ok) {
          let p = "";
          try { p = new URL(typeof input === "string" ? input : input.url, location.href).pathname; } catch (_) {}
          if (COUNTS.test(p)) {
            clearTimeout(timer);
            timer = setTimeout(reread, Math.max(2000, 15000 - (Date.now() - last)));
          }
        }
        return r;
      };
    }
  }

  // ---- cool-downs (SP-714, backend/cooldown.py): after a manual recompute the same person waits
  // before the next. GET /api/v1/session's membership.cooldowns {key: seconds left} and any 429
  // COOLDOWN {key, retry_after} disable the matching button ([data-cooldown="<key>"], or the
  // selectors below) and say 「X 分鐘後可以再按」 next to it until the wait is over.
  {
    const CD = {};                                    // key -> until (ms)
    for (const [k, s] of Object.entries((MS && MS.cooldowns) || {})) CD[k] = Date.now() + s * 1000;
    const CD_SELECTORS = [
      ["calib", "#calib-run"], ["check", "#s-check"], ["routes", "#rebuild"], ["plan_auto", "#ap-run"],
      ["check:fill-coros", '[data-fill="coros"]'], ["check:fill-tp", '[data-fill="tp"]'],
      ...["coros", "tp", "garmin", "intervals"].map((s) => [`sync:${s}`, `#src-${s} [data-act=sync]`]),
    ];
    const cdText = (ms) => {
      const min = Math.max(1, Math.ceil(ms / 60000));
      return min < 60 ? T("cooldown.min", { n: min }) : T("cooldown.hour", { n: Math.ceil(min / 60) });
    };
    let timer = 0;
    const tick = () => {
      const now = Date.now();
      for (const [k, sel] of CD_SELECTORS) for (const el of document.querySelectorAll(sel)) if (!el.dataset.cooldown) el.dataset.cooldown = k;
      let live = Object.values(CD).some((u) => u > now);      // also before the page rendered its button
      for (const el of document.querySelectorAll("[data-cooldown]")) {
        if (el.dataset.scopeLocked) continue;
        const left = (CD[el.dataset.cooldown] || 0) - now;
        let note = el.nextElementSibling && el.nextElementSibling.classList.contains("cd-note") ? el.nextElementSibling : null;
        if (left > 0) {
          el.dataset.cdLocked = "1";
          el.disabled = true;
          const txt = cdText(left);
          el.title = txt;
          if (!note) { note = document.createElement("span"); note.className = "cd-note meta"; note.style.marginLeft = "6px"; el.after(note); }
          note.textContent = txt;
        } else if (el.dataset.cdLocked) {
          el.disabled = false;                       // the page re-renders what it disables for its own reasons
          delete el.dataset.cdLocked; el.removeAttribute("title");
          if (note) note.remove();
        }
      }
      clearTimeout(timer);
      // while one cools down: every second (the count, and the buttons a page renders meanwhile)
      timer = live ? setTimeout(tick, 1000) : 0;
    };
    const start = () => tick();
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
    window.AppCooldown = {
      // a page's catch: any 429 was already explained by the shell (the toast) — COOLDOWN, RATE_LIMITED
      is: (err) => !!err && (err.status === 429 || /^429\b/.test(String(err.message || ""))),
      left: (key) => Math.max(0, (CD[key] || 0) - Date.now()),
      text: cdText,
    };
    const cdFetch = window.fetch;
    let lastCd = 0;
    window.fetch = async (input, init) => {
      const r = await cdFetch(input, init);
      if (r.status === 429) {
        r.clone().json().then((j) => {
          const d = j && j.detail;
          if (d && d.code === "RATE_LIMITED" && !DEMO && Date.now() - lastCd > 4000) {     // SP-714: the per-person limit
            lastCd = Date.now(); toast(d.message || T("cooldown.rate")); return;
          }
          if (!d || d.code !== "COOLDOWN" || !d.key) return;
          CD[d.key] = Date.now() + (Number(d.retry_after) || 60) * 1000;
          setTimeout(tick, 0); setTimeout(tick, 300);        // after the page's own finally (re-enabling the button)
          if (Date.now() - lastCd > 4000) { lastCd = Date.now(); toast(cdText((Number(d.retry_after) || 60) * 1000)); }
        }).catch(() => {});
      }
      return r;
    };
  }

  // ---- the public mirror (SP-500): no calculation core, so a request that needs it answers
  // 503 CORE_NOT_INCLUDED (backend/main.py); say so once per page
  {
    const coreFetch = window.fetch;
    let told = false;
    window.fetch = async (input, init) => {
      const r = await coreFetch(input, init);
      if (r.status === 503 && !told) {
        r.clone().json().then((j) => {
          const d = j && j.detail;
          if (!d || d.code !== "CORE_NOT_INCLUDED" || told) return;
          told = true;
          if (d.message) toast(d.message);
        }).catch(() => {});
      }
      return r;
    };
  }

  // ---- sign-in mode (SP-58): a session that ended meanwhile (signed out elsewhere, expired,
  // disabled) answers 401 LOGIN_REQUIRED: back to the sign-in page, then to this page; new terms
  // answer 403 TERMS_REQUIRED: to the terms page, then back here
  if (SESSION.login && !PUBLIC) {
    const authedFetch = window.fetch;
    let leaving = false;
    window.fetch = async (input, init) => {
      const r = await authedFetch(input, init);
      if ((r.status === 401 || r.status === 403) && !leaving) {
        r.clone().json().then((j) => {
          const to = j && j.code === "LOGIN_REQUIRED" ? "/login" : j && j.code === "TERMS_REQUIRED" ? "/signup" : null;
          if (!to || leaving) return;
          leaving = true;
          location.href = to + "?" + new URLSearchParams({ next: location.pathname + location.search });
        }).catch(() => {});
      }
      return r;
    };
  }

  // ---- offline copy (SP-314; /sw.js, static/offline_core.js) ---------------------------------
  // A service worker keeps a read-only copy of 總覽 / 課表 / 活動列表 for the person signed in
  // here. Offline (or while the server is down) the pages read it: a bar says from when, the
  // writes are refused here with the reason (never queued), the edit buttons are dimmed and
  // explain on a click. Back online the copy refreshes and the bar offers 重新整理. Never in the
  // demo. window.AppOffline: {active, slow, at, clear(), status()} (schedule.html, SP-322).
  const OFF = { on: false, slow: false, at: null, back: false };
  const offT = (k, p) => T("offline." + k, p);
  const SW_OK = !DEMO && "serviceWorker" in navigator && window.isSecureContext;
  const swPost = (msg) => {
    if (!SW_OK) return;
    navigator.serviceWorker.ready.then((reg) => reg.active && reg.active.postMessage(msg)).catch(() => {});
  };
  const swAsk = (msg) => new Promise((resolve) => {
    if (!SW_OK || !navigator.serviceWorker.controller) return resolve(null);
    const ch = new MessageChannel();
    ch.port1.onmessage = (e) => resolve(e.data);
    navigator.serviceWorker.controller.postMessage(msg, [ch.port2]);
    setTimeout(() => resolve(null), 3000);
  });
  // the edit controls of the cached pages (and anything marked data-offline-lock)
  const OFFLINE_LOCK = ["[data-offline-lock]", "button[type=submit]", "input[type=file]", "[data-approve]", "[data-reject]",
    "[data-undo]", "[data-aet-apply]", "[data-apply-at]", "[data-apply-thr]", ".sg-item [data-act=accept]",
    ".sg-item [data-act=dismiss]", ".sg-item [data-act=decline]", "#push-main", "#push-caret", "#pull-btn", "#rec-btn",
    "#sd-del", "#sd-push", "#sd-swap", "#sd-climb", "#ap-run", "#pf-reset", "[data-unpush]", '[data-act="del-expired"]',
    // 課表's 「＋」 on a day and the context menu's 新增 / 刪除 (they open an empty session to save)
    "[data-add]", '[data-ctx="add"]', '[data-ctx="del"]'].join(", ");
  window.AppOffline = {
    get active() { return OFF.on && !OFF.slow; }, get slow() { return OFF.on && OFF.slow; }, get at() { return OFF.at; },
    // forget this browser's copy of the person (sign-out): the worker and the caches themselves
    clear: async () => {
      swPost({ type: "logout" });
      try {
        if (!window.caches) return;
        const ks = await caches.keys();
        await Promise.all(ks.filter((k) => k.startsWith("trc-u-") || k === "trc-meta").map((k) => caches.delete(k)));
      } catch (_) { /* storage blocked: nothing kept either */ }
    },
    status: () => swAsk({ type: "status" }),
  };
  if (!DEMO) {
    st.textContent += `
    .an-offline { position: sticky; top: var(--shell-top, 0px); z-index: 39; display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center;
      padding: 7px 14px; font: 13px/1.45 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif; color: var(--text, #111);
      background: color-mix(in srgb, #64748b 16%, var(--panel, #fff)); border-bottom: 1px solid var(--line, #e1e5ea); }
    .an-offline[hidden] { display: none; }
    .an-offline.an-o-back { background: color-mix(in srgb, #16a34a 14%, var(--panel, #fff)); }
    .an-offline button { font: inherit; font-size: 12.5px; padding: 2px 10px; border-radius: 6px; cursor: pointer;
      border: 1px solid var(--line, #d4d8de); background: var(--panel, #fff); color: var(--text, #111); }
    @media (max-width: 699px) { .an-offline { top: 0; font-size: 12.5px; padding: 6px 10px; } }
    html[data-trc-offline] :is(${OFFLINE_LOCK}) { opacity: .45 !important; cursor: not-allowed !important; }
    .an-toast { position: fixed; left: 50%; bottom: 84px; transform: translateX(-50%); z-index: 80; max-width: min(92vw, 460px);
      padding: 9px 14px; border-radius: 9px; background: #1f2937; color: #fff; font: 13px/1.4 system-ui, sans-serif;
      box-shadow: 0 8px 24px rgba(0,0,0,.25); }
    .an-toast[hidden] { display: none; }
    `;
    const offBar = document.createElement("div");
    offBar.className = "an-offline"; offBar.hidden = true;
    offBar.setAttribute("role", "status"); offBar.setAttribute("aria-live", "polite");
    const mountOff = () => nav.after(offBar);
    if (document.body) mountOff(); else document.addEventListener("DOMContentLoaded", mountOff);
    const two = (n) => String(n).padStart(2, "0");
    const when = (iso) => { const d = iso ? new Date(iso) : null; return d && !isNaN(d) ? `${d.getMonth() + 1}/${d.getDate()} ${two(d.getHours())}:${two(d.getMinutes())}` : ""; };
    const renderOff = () => {
      document.documentElement.toggleAttribute("data-trc-offline", OFF.on && !OFF.slow);
      offBar.classList.toggle("an-o-back", OFF.back);
      if (OFF.back) {
        offBar.innerHTML = `<span></span><button type="button"></button>`;
        offBar.querySelector("span").textContent = offT("back");
        const b = offBar.querySelector("button");
        b.textContent = offT("reload"); b.title = offT("reload_title");
        b.addEventListener("click", () => location.reload());
      } else if (OFF.on) {
        const at = when(OFF.at);
        offBar.textContent = OFF.slow ? offT("slow", { at }) : at ? offT("banner", { at }) : offT("banner_now");
      }
      offBar.hidden = !(OFF.on || OFF.back);
    };
    const goOffline = (at, slow) => {
      const hard = OFF.on && !OFF.slow;
      OFF.on = true; OFF.slow = !!slow && !hard; OFF.back = false;
      if (at && (!OFF.at || at < OFF.at)) OFF.at = at;          // the oldest data on the page
      renderOff();
    };
    const goOnline = () => {
      if (!OFF.on) return;
      const wasCopy = !!OFF.at || !!window.TRC_OFFLINE_PAGE;
      OFF.on = false; OFF.slow = false; OFF.back = wasCopy;
      renderOff();
      swPost({ type: "sync", force: true });                    // refresh the copy now
    };
    // this page itself came from the copy (sw.js put it there before any script)
    const PAGE_COPY = window.TRC_OFFLINE_PAGE;
    if (PAGE_COPY) goOffline(PAGE_COPY.at, PAGE_COPY.reason === "slow");
    else if (navigator.onLine === false) goOffline(null);
    addEventListener("offline", () => goOffline(null));
    addEventListener("online", goOnline);
    // a click on an edit control while offline: nothing happens but the reason
    let gesture = 0;
    for (const ev of ["pointerdown", "keydown"]) addEventListener(ev, () => { gesture = Date.now(); }, true);
    document.addEventListener("click", (e) => {
      if (!(OFF.on && !OFF.slow) || !e.target.closest || !e.target.closest(OFFLINE_LOCK)) return;
      e.preventDefault(); e.stopImmediatePropagation();
      toast(offT("locked"));
    }, true);
    // writes while offline are refused here (never sent, never queued); answers from the copy say so
    const onlineFetch = window.fetch;
    window.fetch = async (input, init) => {
      const method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
      let url;
      try { url = new URL(typeof input === "string" ? input : input.url, location.href); } catch (_) { url = null; }
      const same = !!url && url.origin === location.origin;
      if (same && method !== "GET" && method !== "HEAD" && OFF.on && !OFF.slow) {
        if (Date.now() - gesture < 4000) toast(offT("locked"));
        return new Response(JSON.stringify({ code: "OFFLINE", detail: { code: "OFFLINE", message: offT("locked") } }),
          { status: 503, headers: { "Content-Type": "application/json" } });
      }
      try {
        const r = await onlineFetch(input, init);
        if (same) {
          const c = r.headers.get("x-trc-cache");
          if (c) goOffline(r.headers.get("x-trc-stored-at"), c === "slow");
          else if (r.status < 500) goOnline();
        }
        return r;
      } catch (e) {
        if (same && !(e && e.name === "AbortError")) goOffline(null);
        throw e;
      }
    };

    // the worker: registered once the page has loaded; told who this page is for (another person
    // than the copy's: the copy goes), then the copy is refilled when due
    const STANDALONE = (window.matchMedia && matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true;
    if (SW_OK) {
      addEventListener("load", () => {
        navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => {});
        if (window.TRC_SESSION) {
          const s = SESSION;
          swPost({ type: "session", session: { mode: s.mode, user: s.user ? { id: s.user.id } : null, login: s.login || null,
                                                viewing: !!s.viewing } });     // SP-791: 代看 — the copy stays out of it
        } else swPost({ type: "sync" });
        // an installed app keeps its data (Chrome / Safari decide without asking)
        if (STANDALONE && navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
      });
    }
    // 加到主畫面 (SP-319): on an iPhone / iPad in Safari, once on 總覽, until 知道了
    const IOS = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    let hinted = "1";
    try { hinted = localStorage.getItem("trc.a2hs") || ""; } catch (_) {}
    if (SW_OK && IOS && !STANDALONE && !hinted && cur && cur.id === "home") {
      const hint = document.createElement("div");
      hint.className = "an-offline an-o-hint";
      hint.innerHTML = `<span></span><button type="button"></button>`;
      hint.querySelector("span").textContent = offT("a2hs");
      hint.querySelector("button").textContent = offT("a2hs_ok");
      hint.querySelector("button").addEventListener("click", () => {
        hint.remove();
        try { localStorage.setItem("trc.a2hs", "1"); } catch (_) {}
      });
      const mountHint = () => offBar.after(hint);
      if (document.body) mountHint(); else document.addEventListener("DOMContentLoaded", mountHint);
    }
    // the installable app: manifest, icons (the pages need no change)
    const HEAD = [["link", { rel: "manifest", href: "/manifest.webmanifest" }],
      ["link", { rel: "apple-touch-icon", href: "/api/v1/static/icons/apple-touch-icon.png" }],
      ["link", { rel: "icon", type: "image/svg+xml", href: "/api/v1/static/icons/icon.svg" }],
      // the browser bar follows 外觀 (theme.js, SP-974): the original blue, or the Trig palette's accent
      ["meta", { name: "theme-color", content: window.TrcTheme ? TrcTheme.themeColor() : "#2563eb" }],
      ["meta", { name: "mobile-web-app-capable", content: "yes" }],
      ["meta", { name: "apple-mobile-web-app-capable", content: "yes" }],
      ["meta", { name: "apple-mobile-web-app-title", content: T("brand") }]];
    for (const [tag, attrs] of HEAD) {
      if (tag === "link" && document.head.querySelector(`link[rel="${attrs.rel}"]`)) continue;
      const el = document.createElement(tag);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
      document.head.appendChild(el);
    }
  }

  // ---- the demo banner + locked buttons ----------------------------------
  // buttons / inputs marked data-cap="sync" (… push, share, thresholds.write …) are
  // locked when the session lacks the cap: disabled, with the reason on hover
  // the pages' owner-only controls, by selector (so each page needn't know about the demo)
  const CAP_SELECTORS = [
    ["push", "#push-main, #push-caret, [data-unpush], #sd-push, #coros, #cx-go"],
    ["share", "#share, #share-go"],
    ["backtest", "#bt-run"],
    ["weather.key", "#cwasave, #cwakey"],
    ["thresholds.write", "#addthr, #savethr, [data-apply-at], [data-apply-thr]"],
    ["dataset.write", "#rebuild, [data-rename-route], #ex-save, #act-save, [data-exclude]"],
    ["upload.fit", "input[type=file][accept*='.fit' i]:not([accept*='.gpx' i])"],
    ["ai", "[data-ai]"],
  ];
  function capLock(selectors, LOCKED) {
    const lock = (root) => {
      if (!root.querySelectorAll) return;
      for (const [cap, sel] of selectors) {
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
    const startLock = () => {
      lock(document);
      new MutationObserver((ms) => {
        for (const m of ms) for (const n of m.addedNodes) if (n.nodeType === 1) lock(n.parentNode || n);
      }).observe(document.body, { childList: true, subtree: true });
    };
    if (document.body) startLock(); else document.addEventListener("DOMContentLoaded", startLock);
  }
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
    .an-demo .an-d-cta { background: var(--accent, #2563eb); border-color: var(--accent, #2563eb); color: #fff; font-weight: 600; }
    .an-demo .an-d-cta:hover { color: #fff; filter: brightness(1.08); }
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
    setInterval(() => { const el = banner.querySelector(".an-d-left"); if (el) el.textContent = left(); }, 60e3);

    capLock(CAP_SELECTORS, T("demo.locked"));
  }
  // 代看 (SP-791, SP-792): the same locks from the view's caps — plan.write / push / upload.gpx with the
  // student's 課表 switch, thresholds.write with 門檻與訓練設定; the rest (sync, share, …) never
  if (VIEW) {
    st.textContent += `[data-cap][data-cap-locked] { opacity: .5; cursor: not-allowed !important; }`;
    capLock([...CAP_SELECTORS,
      ["thresholds.write", "#hr-model, #hr-max-save, #hr-rest-save, #hr-max-auto, #hr-rest-auto, #pz-model, #pmc-save, #pmc-clear, "
        + "#race-save, #race-clear, #thr-add, #thr-save, [data-tcapply], [data-apply], [data-trm], [data-race]"],
      ["plan.write", "[data-approve], [data-reject], [data-undo], [data-aet-apply], .sg-item [data-act=accept], .sg-item [data-act=dismiss], "
        + ".sg-item [data-act=decline], #pull-btn, #rec-btn, #sd-del, #sd-swap, #sd-climb, #pf-reset, [data-act=\"del-expired\"], "
        + "[data-add], [data-ctx=\"add\"], [data-ctx=\"del\"]"],
      ["upload.gpx", "input[type=file][accept*='.gpx' i]"],
      // SP-822: the injury log and a run's pain are read only to a coach (no cap ever gives "injury.write");
      // the ids below are the injury page's own (races.html has an #add of its own: the 課表 switch's)
      ["injury.write", (location.pathname.startsWith("/api/v1/app/injuries")
        ? "#add, #f-save, #f-del, #f-today, #f-area-new, [data-a=resolve], [data-a=walkrun], #set-pat, #set-up, #f-area [data-rm], "
        : "") + "#pain-chip"],
    ], T("view.locked"));
  }

  // ---- ? help → the manual (SP-558) -------------------------------------------------------------
  // A ? with data-manual="<chapter>#<section>" gets 「看手冊 →」 after its text. A page's own popup
  // calls ManualLink.decorate(popup, button) when it shows one (and leaves it open while the pointer
  // moves into it: ManualLink.into(mouseout event); a click inside it is the link's own). A ? whose
  // text is only its title (the settings page) gets a small popup of its own here on a click — SP-750: on the
  // settings page every ? (button.q with a title), not only the data-manual ones, so a phone reads it too. Every
  // data-manual must name a section that exists (backend/tests/test_manual_sp558.py).
  // SP-559: a popup whose text names a glossary word (/manual/terms.json: the 名詞表's :::terms) also gets
  // a last row 「名詞表：長期訓練負荷 · 負荷平衡」 linking to those sections (at most GL_MAX, in the text's
  // order; the words load once per page, the popup gets its row when they are there).
  st.textContent += `
  .an-man-row { margin-top: 6px; padding-top: 5px; border-top: 1px solid var(--line, #e1e5ea); white-space: normal; }
  .an-man { color: var(--accent, #2563eb); font-weight: 600; text-decoration: none; }
  .an-man:hover { text-decoration: underline; }
  /* the gap between the ? and its popup: still the popup, so the pointer can reach the link */
  .qtip-pop:has(.an-man)::before, .qpop:has(.an-man)::before, .an-qpop::before { content: ""; position: absolute; left: 0; right: 0; top: -9px; height: 9px; }
  .qtip-pop:has(.an-man)::after, .qpop:has(.an-man)::after, .an-qpop::after { content: ""; position: absolute; left: 0; right: 0; bottom: -9px; height: 9px; }
  .an-gl-lab { color: var(--muted, #6b7280); }
  .an-gl-row .an-man { font-weight: 600; }
  .an-qpop { position: fixed; z-index: 1000; max-width: min(360px, calc(100vw - 24px)); white-space: pre-line; text-align: left;
    background: var(--panel, #fff); color: var(--text, #111); border: 1px solid var(--line, #e1e5ea); border-radius: 8px;
    padding: 8px 10px; font: 400 12px/1.55 system-ui, sans-serif; box-shadow: 0 6px 18px rgba(0,0,0,.18); }
  `;
  const MAN_RE = /^[a-z0-9][a-z0-9-]*$/;
  const manHref = (key) => {
    const [c, a] = String(key || "").split("#");
    if (!MAN_RE.test(c || "") || (a !== undefined && !MAN_RE.test(a))) return null;
    return "/manual/" + c + (a ? "#" + a : "");
  };
  // a popup with a link: the pointer may move into it, and leaving it closes it (unless pinned)
  function manLive(pop, btn) {
    if (pop._manLive) return;
    pop._manLive = true;
    pop.style.pointerEvents = "auto";
    pop.addEventListener("mouseleave", (e) => { if (!(e.relatedTarget && btn.contains(e.relatedTarget)) && !pop.dataset.pinned) pop.remove(); });
  }
  // ---- SP-559: glossary words → their sections
  const GL_MAX = 4;
  const ASCII = /^[\x20-\x7e]+$/;
  let gl = null, glP = null;
  function glCompile(list) {
    const map = new Map();
    for (const x of list || []) if (x && x.term && x.url && manHref(String(x.url).replace(/^\/manual\//, "")) === x.url && !map.has(x.term)) map.set(x.term, x.url);
    const words = [...map.keys()].sort((a, b) => b.length - a.length);
    const esc = (w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    // a Latin word (CP, LTHR, Chronic load) only as a whole word and as written (the glossary lists
    // "Chronic load" and "chronic load"; a plain "intensity" in a sentence is not the metric); Chinese anywhere
    const alt = words.map((w) => (ASCII.test(w) ? `(?<![A-Za-z0-9])${esc(w)}(?![A-Za-z0-9])` : esc(w)));
    return { map, re: alt.length ? new RegExp(alt.join("|"), "g") : null };
  }
  const glLoad = () => glP || (glP = fetch(`/manual/terms.json?lang=${encodeURIComponent(L.locale || "zh-TW")}`, { credentials: "same-origin" })
    .then((r) => (r && r.ok ? r.json() : null)).catch(() => null).then((j) => (gl = glCompile(j && j.terms))));
  /** The glossary sections a text names: [{url, word}], in the text's order, at most GL_MAX, not `skip` */
  function glFind(text, skip) {
    const out = new Map();
    if (!gl || !gl.re || !text) return [];
    for (const m of String(text).matchAll(gl.re)) {
      const url = gl.map.get(m[0]);
      if (url && url !== skip && !out.has(url)) out.set(url, m[0]);
      if (out.size >= GL_MAX) break;
    }
    return [...out].map(([url, word]) => ({ url, word }));
  }
  function glRow(pop, btn, text, skip) {
    if (pop.querySelector(".an-gl-row")) return;
    const hits = glFind(text, skip);
    if (!hits.length) return;
    const row = document.createElement("div");
    row.className = "an-man-row an-gl-row";
    const lab = document.createElement("span");
    lab.className = "an-gl-lab"; lab.textContent = T("manual.glossary") + " ";
    row.appendChild(lab);
    hits.forEach((h, i) => {
      if (i) row.appendChild(document.createTextNode(" · "));
      const a = document.createElement("a");
      a.className = "an-man an-gl"; a.href = h.url; a.textContent = h.word; a.title = T("manual.glossary_title");
      row.appendChild(a);
    });
    const ai = pop.querySelector(".askai-row");                    // 「還不懂？問 AI」 (SP-786) stays last
    if (ai) pop.insertBefore(row, ai); else pop.appendChild(row);
    manLive(pop, btn);
  }
  function manDecorate(pop, btn) {
    if (!pop || !btn) return pop;
    const text = pop.textContent;
    const href = btn.dataset ? manHref(btn.dataset.manual) : null;
    if (href && !pop.querySelector(".an-man")) {
      const row = document.createElement("div");
      row.className = "an-man-row";
      const a = document.createElement("a");
      a.className = "an-man"; a.href = href; a.textContent = T("manual.see"); a.title = T("manual.see_title");
      row.appendChild(a);
      pop.appendChild(row);
      manLive(pop, btn);
    }
    if (gl) glRow(pop, btn, text, href);
    else glLoad().then(() => { if (pop.isConnected) glRow(pop, btn, text, href); });
    // SP-786: 「還不懂？問 AI」 last, when the AI 問答 is on (static/askai.js)
    if (window.AskAI) { window.AskAI.decorate(pop, btn); manLive(pop, btn); }
    return pop;
  }
  // the pointer left the ? for its popup (which has the link): keep it open
  const manInto = (e) => !!(e && e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest(".qtip-pop, .qpop, .an-qpop"));
  let qpop = null;
  // SP-750: the settings page's ? are title-only (no popup of the page's own)
  const QSEL = "[data-manual][title]:not([data-tip])" + (cur && cur.id === "settings" ? ", button.q[title]:not([data-tip])" : "");
  const qpopHide = () => { if (qpop) { qpop.remove(); qpop = null; } };
  document.addEventListener("click", (e) => {
    if (!e.target.closest) return;
    if (e.target.closest(".an-qpop")) return;
    const b = e.target.closest(QSEL);
    if (!b) { qpopHide(); return; }
    const again = qpop && qpop._for === b;
    qpopHide();
    if (again) return;
    e.preventDefault();
    qpop = document.createElement("div");
    qpop.className = "an-qpop"; qpop.setAttribute("role", "note"); qpop._for = b; qpop.dataset.pinned = "1";
    qpop.textContent = b.title;
    manDecorate(qpop, b);
    (b.closest("dialog[open]") || document.body).appendChild(qpop);
    const r = b.getBoundingClientRect(), w = qpop.offsetWidth, h = qpop.offsetHeight;
    qpop.style.left = `${Math.max(12, Math.min(r.left - 8, innerWidth - w - 12))}px`;
    qpop.style.top = `${r.bottom + 6 + h > innerHeight - 8 ? Math.max(8, r.top - h - 6) : r.bottom + 6}px`;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") qpopHide(); });
  addEventListener("scroll", qpopHide, { passive: true });
  window.ManualLink = { href: manHref, decorate: manDecorate, into: manInto, terms: glLoad, find: (t, skip) => glFind(t, skip) };
  // the words load while the page is idle, so the first ? already has its row
  if (!PUBLIC && !(cur && cur.id === "manual") && typeof setTimeout === "function") setTimeout(() => { glLoad(); }, 1500);

  // ---- dataset build progress -------------------------------------------
  // While the chart Dataset of the data source builds (first start, new FIT
  // files, a changed setting), every page that needs it waits. Instead of a
  // frozen 「載入中…」 they show the build's progress
  // (GET /api/v1/app/dataset/status: 「正在處理第 350／808 筆（解析 FIT）…」):
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
  let tries = 0, wasBuilding = false, wasBackfill = false;
  async function poll() {
    let s = null;
    try { s = await (await fetch("/api/v1/app/dataset/status", { cache: "no-store" })).json(); } catch (_) {}
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
    // SP-394: a first import / a re-parse built the last 90 days first; the older activities are
    // filled in in the background (api/backfill.py): their progress, then a note to reload
    const b = s && s.backfill;
    if (b && b.state === "running") {
      wasBackfill = true;
      const p = bar.querySelector("progress");
      p.hidden = false;
      if (b.n_total) { p.max = b.n_total; p.value = Math.min(b.n_done, b.n_total); } else p.removeAttribute("value");
      bar.querySelector(".an-b-msg").textContent = T("build.backfill", { done: b.n_done || 0, total: b.n_total || 0 });
      bar.querySelector(".an-b-sub").textContent = "";
      bar.hidden = false;
      setTimeout(poll, 5000);
      return;
    }
    if (wasBackfill) {
      wasBackfill = false;
      bar.hidden = false; bar.querySelector("progress").hidden = true;
      bar.querySelector(".an-b-msg").textContent = T("build.backfill_done");
      bar.querySelector(".an-b-sub").textContent = "";
      return;
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
  if (!PUBLIC) setTimeout(poll, 250);       // signed out: no one's data to wait for
  window.AppShell.datasetStatus = poll;

  // ---- a page's own bottom bar clears the floating buttons (SP-913) ------------------------------------------
  // An element marked data-float-clear (the 活動列表's sticky 比較 bar) that the floating 建議 box
  // (static/suggestions.js) or 問 AI button (static/askai.js) would cover — measured where they float on their
  // own — lifts them above it: <html> gets --float-clear (its top's distance from the viewport bottom + 8 px),
  // which both use as their least `bottom`, and a `floatclear` event (askai.js re-stacks its button). Pages
  // without such a bar, or while it is hidden or out of their way: 0px, the boxes stay where they were.
  // AppShell.floatClear() measures again (a page calls it after showing / hiding its bar).
  const FLOATS = ".sugbox:not([hidden]), .askai-fab:not([hidden])";
  let fcPx = 0, fcQueued = false, fcRo = null;
  function floatClear() {
    const root = document.documentElement;
    const bars = [...document.querySelectorAll("[data-float-clear]")]
      .map((b) => b.getBoundingClientRect()).filter((r) => r.height > 0 && r.top < innerHeight && r.bottom > 0);
    if (!bars.length && !fcPx) return;
    const set = (px) => { root.style.setProperty("--float-clear", `${px}px`); window.dispatchEvent(new Event("floatclear")); };
    set(0);                                                    // where they float on their own
    const floats = [...document.querySelectorAll(FLOATS)].map((f) => f.getBoundingClientRect()).filter((r) => r.height > 0);
    const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    let need = 0;
    for (const r of bars) if (floats.some((f) => hit(f, r))) need = Math.max(need, Math.ceil(innerHeight - r.top + 8));
    fcPx = need;
    if (need) set(need);
  }
  const fcLater = () => { if (fcQueued) return; fcQueued = true; requestAnimationFrame(() => { fcQueued = false; floatClear(); }); };
  function fcWatch() {
    const bars = document.querySelectorAll("[data-float-clear]");
    if (!bars.length) return;
    if (!fcRo && window.ResizeObserver) fcRo = new ResizeObserver(fcLater);  // shown / hidden / taller
    if (fcRo) for (const b of bars) if (!b._fc) { b._fc = true; fcRo.observe(b); }
    if (!fcWatch.on) {
      fcWatch.on = true;
      addEventListener("scroll", fcLater, { passive: true, capture: true });  // a sticky bar moves while scrolling
      addEventListener("resize", fcLater, { passive: true });
    }
    fcLater();
  }
  window.AppShell.floatClear = () => { fcWatch(); floatClear(); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fcWatch); else fcWatch();

  if (DEMO || PUBLIC) return;  // the demo has no sync, no settings, no first-run wizard; a visitor neither

  // 代看 (SP-791): nothing below writes into the student's data on its own — no auto-sync, no FIT folder,
  // no time zone, no first-run wizard (the suggestion box and the region stay: they read)
  // auto-sync on site open (COROS / TP, per the settings page; throttled inside autosync.js)
  if (!VIEW) {
    const as = document.createElement("script");
    as.src = "/api/v1/static/autosync.js";
    as.defer = true;
    document.head.appendChild(as);
  }

  // SP-324: the remembered FIT folder (desktop Chrome / Edge only) imports its new files on open
  if (!VIEW && typeof window.showDirectoryPicker === "function" && can("upload.fit")) {
    const ff = document.createElement("script");
    ff.src = "/api/v1/static/fitfolder.js";
    ff.dataset.auto = "1";
    ff.defer = true;
    document.head.appendChild(ff);
  }

  // the floating suggestion box (B2B weekends, due tests, zone retests): every page shows it — but the
  // manual (SP-558) and 關於 (SP-495), pages for reading that it would cover; nor the 教練看板 (SP-790):
  // the coach's own suggestions there covered the students' alerts
  if ((!cur || !PUBLIC_PAGES.has(cur.id)) && me?.dataset.page !== "coach_board") {
    const sg = document.createElement("script");
    sg.src = "/api/v1/static/suggestions.js";
    sg.defer = true;
    document.head.appendChild(sg);
  }

  // 問 AI (SP-786, static/askai.js; the backend SP-785): the owner only, while 設定 › 進階 › AI 問答 is on — the
  // session says so (api/session.py `ai`); never while viewing a student (no `ai` then)
  if (SESSION.ai && SESSION.ai.on && !VIEW) {
    const ai = document.createElement("script");
    ai.src = "/api/v1/static/askai.js";
    ai.defer = true;
    document.head.appendChild(ai);
  }

  // 地區 tw | intl (engine/region.py): hides the Taiwan-only parts outside Taiwan
  const rg = document.createElement("script");
  rg.src = "/api/v1/static/region.js";
  document.head.appendChild(rg);

  if (VIEW) return;
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
