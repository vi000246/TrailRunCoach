/* App shell: the one navigation every page shares.
 *
 *   <script src="/api/v1/static/shell.js" data-page="charts"></script>
 *
 * Put it as the first element inside <body>; it inserts the nav synchronously
 * (no flash) and styles it from the page's own tokens (--panel, --text, --muted,
 * --line, --accent), so dark mode follows each page.
 *   >= 1100 px : top bar, icon + name + one-line purpose
 *   700–1100   : top bar, icon + name (purpose in the tooltip)
 *   < 700 px   : bottom tab bar, icon + short name
 */
(() => {
  const I = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const PAGES = [
    { id: "home", href: "/api/v1/overview/page", name: "首頁／本週", short: "首頁", purpose: "狀況・待辦・本週課表",
      icon: I('<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v9.5h13V10"/><path d="M10 19.5v-5h4v5"/>') },
    { id: "charts", href: "/api/v1/wko5/viewer", name: "圖表分析", short: "圖表", purpose: "趨勢與單次活動圖表",
      icon: I('<path d="M4 4v16h16"/><path d="m7.5 14.5 3.5-4 3 2.5 5-6"/>') },
    { id: "plan", href: "/api/v1/plan/page", name: "賽事周期", short: "周期", purpose: "目標賽事・周期・門檻",
      icon: I('<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/><path d="M8 14h3"/>') },
    { id: "racepower", href: "/api/v1/racepower/page", name: "賽事功率", short: "功率", purpose: "比賽功率與完賽時間預估",
      icon: I('<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>') },
    { id: "achievements", href: "/api/v1/achievements/page", name: "成就", short: "成就", purpose: "百岳與越野紀錄",
      icon: I('<path d="M2.5 19.5 9 8l4 6.5 2.5-3.5 6 8.5z"/><path d="M9 8V3.5l3 1.2-3 1.2"/>') },
    { id: "settings", href: "/api/v1/wko5/settings", name: "設定", short: "設定", purpose: "計算模式・體重・資料校正",
      icon: I('<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M4.2 7.5l2 1.2M17.8 15.3l2 1.2M4.2 16.5l2-1.2M17.8 8.7l2-1.2"/><circle cx="12" cy="12" r="6.6"/>') },
  ];

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
  .appnav a.an-item { position: relative; display: grid; grid-template-columns: 20px auto; grid-template-rows: auto auto;
    column-gap: 8px; align-content: center; padding: 7px 12px; min-height: var(--an-h); text-decoration: none;
    color: var(--muted, #667); border-radius: 8px 8px 0 0; }
  .appnav a.an-item svg { grid-row: 1 / span 2; width: 20px; height: 20px; align-self: center; }
  .appnav .an-name { font-weight: 600; color: var(--text, #111); white-space: nowrap; }
  .appnav .an-purpose { font-size: 11px; color: var(--muted, #667); white-space: nowrap; }
  .appnav a.an-item:hover { background: color-mix(in srgb, var(--an-acc) 8%, transparent); }
  .appnav a.an-item[aria-current="page"] { color: var(--an-acc); background: color-mix(in srgb, var(--an-acc) 10%, transparent); }
  .appnav a.an-item[aria-current="page"] .an-name { color: var(--an-acc); }
  .appnav a.an-item[aria-current="page"]::after { content: ""; position: absolute; left: 10px; right: 10px; bottom: 0;
    height: 3px; border-radius: 3px 3px 0 0; background: var(--an-acc); }
  .appnav a.an-item:focus-visible, .appnav .an-brand:focus-visible { outline: 2px solid var(--an-acc); outline-offset: -2px; }
  .appnav .an-sep { flex: 1; }
  @media (max-width: 1100px) {
    .appnav a.an-item { grid-template-columns: 20px auto; grid-template-rows: auto; padding: 0 11px; }
    .appnav a.an-item svg { grid-row: 1; }
    .appnav .an-purpose { display: none; }
    .appnav .an-brand span { display: none; }
  }
  @media (max-width: 699px) {
    .appnav { position: fixed; top: auto; bottom: 0; left: 0; right: 0; padding: 0 2px env(safe-area-inset-bottom);
      border-bottom: 0; border-top: 1px solid var(--line, #e1e5ea); gap: 0; box-shadow: 0 -4px 16px rgba(0,0,0,.08); }
    .appnav .an-brand, .appnav .an-sep { display: none; }
    .appnav a.an-item { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; align-items: center; justify-content: center;
      gap: 2px; padding: 6px 0 5px; min-height: 56px; border-radius: 0; }
    .appnav a.an-item svg { width: 22px; height: 22px; }
    .appnav .an-name { font-size: 11px; font-weight: 500; color: inherit; }
    .appnav .an-name .an-long { display: none; }
    .appnav a.an-item[aria-current="page"] { background: none; }
    .appnav a.an-item[aria-current="page"] .an-name { font-weight: 700; }
    .appnav a.an-item[aria-current="page"]::after { top: 0; bottom: auto; left: 28%; right: 28%; border-radius: 0 0 3px 3px; }
    body { padding-bottom: calc(57px + env(safe-area-inset-bottom)) !important; }
  }
  @media (min-width: 700px) { .appnav .an-name .an-short { display: none; } }
  @media print { .appnav { display: none; } }
  `;
  const st = document.createElement("style");
  st.id = "appshell-css";
  st.textContent = CSS;
  document.head.appendChild(st);

  const nav = document.createElement("nav");
  nav.className = "appnav";
  nav.setAttribute("aria-label", "主選單");
  nav.innerHTML = `<a class="an-brand" href="${PAGES[0].href}" title="回首頁"><i>${PAGES[1].icon}</i><span>訓練教練</span></a>` +
    PAGES.map((p) => `<a class="an-item" href="${p.href}" title="${p.name}：${p.purpose}"${p === cur ? ' aria-current="page"' : ""}>
      ${p.icon}<span class="an-name"><span class="an-long">${p.name}</span><span class="an-short">${p.short}</span></span><span class="an-purpose">${p.purpose}</span></a>`).join("");

  const mount = () => document.body.prepend(nav);
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);
  if (cur && !document.title.includes("·")) document.title = `${cur.name.split("／")[0]} · 訓練教練`;
  window.AppShell = { pages: PAGES, current: cur };
})();
