/* i18n: t(), fmt.* and the language switch, for every page.
 *
 * The server (backend/i18n/pages.py, render_page) puts
 *   <script>window.__I18N__ = {locale, debug, catalog}</script>
 *   <script src="/api/v1/static/i18n/i18n.js"></script>
 * first in <head>, so this runs before shell.js and the page's own scripts.
 * The catalog is this page's namespaces (+ common, shell), already merged
 * with the zh-TW fallback: t() never needs a request.
 *
 *   t("overview.ramp.good", { ramp: 2.4 })        // "{ramp}" placeholders
 *   t("schedule.n_sessions", { n: 3 })             // {"one": "…", "other": "…"} picks by n
 *   t("shell.brand", "訓練教練")                    // fallback when the key isn't in the catalog
 *   I18N.fmt.date("2026-10-02", "mdw")             // 10/2（週四） · en: Thu 10/2
 *   I18N.setLocale("en")                           // cookie `lang` (1 year), then reload
 *
 * Same function names as backend/i18n/fmt.py. Units stay metric; dist() /
 * elev() are the one place a km/mi preference will change.
 */
(() => {
  const boot = window.__I18N__ || {};
  const SUPPORTED = ["zh-TW", "en"];
  const htmlLang = (document.documentElement.getAttribute("lang") || "").toLowerCase();
  const locale = SUPPORTED.includes(boot.locale) ? boot.locale : (htmlLang.startsWith("en") ? "en" : "zh-TW");
  const catalog = boot.catalog || {};

  const fill = (s, p) => (p ? String(s).replace(/\{(\w+)\}/g, (m, k) => (p[k] != null ? p[k] : m)) : String(s));

  function t(key, a, b) {
    let params = a, fallback;
    if (typeof a === "string") { fallback = a; params = b; }
    let v = catalog[key];
    if (v && typeof v === "object") {
      const n = params && (params.n != null ? params.n : params.count);
      v = (n === 1 ? v.one : v.other) || v.other || v.one;
    }
    if (v == null) v = fallback != null ? fallback : key;
    return fill(v, params);
  }

  // ---- fmt ---------------------------------------------------------------
  const WD = {
    "zh-TW": { narrow: ["一", "二", "三", "四", "五", "六", "日"], short: ["週一", "週二", "週三", "週四", "週五", "週六", "週日"],
               long: ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"] },
    en: { narrow: ["M", "T", "W", "T", "F", "S", "S"], short: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
          long: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] },
  };
  const toDate = (d) => (d instanceof Date ? d : new Date(String(d).slice(0, 10) + "T00:00:00"));
  const nf = {};
  const numFmt = (d) => (nf[d] ||= new Intl.NumberFormat(locale, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const bad = (x) => x == null || Number.isNaN(+x);

  const fmt = {
    /** the seven names, Monday first ({sundayFirst: true}: ECharts calendar order) */
    weekdays(style = "short", opt = {}) {
      const a = [...(WD[locale] || WD["zh-TW"])[style]];
      return opt.sundayFirst ? [a[6], ...a.slice(0, 6)] : a;
    },
    /** a date's weekday, or an index (Monday = 0): 週四 / Thu; narrow 四 / T */
    weekday(d, style = "short") {
      const i = typeof d === "number" ? d : (toDate(d).getDay() + 6) % 7;
      return fmt.weekdays(style)[((i % 7) + 7) % 7];
    },
    /** md 10/2 · mdw 10/2（週四） / Thu 10/2 · ymd 2026-10-02 */
    date(d, style = "md") {
      const x = toDate(d);
      if (style === "ymd") return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
      const md = `${x.getMonth() + 1}/${x.getDate()}`;
      if (style !== "mdw") return md;
      const w = fmt.weekday(x, "short");
      return locale === "zh-TW" ? `${md}（${w}）` : `${w} ${md}`;
    },
    num(x, d = 0) { return bad(x) ? "" : numFmt(d).format(+x); },
    pct(x, d = 0) { return bad(x) ? "" : `${(+x * 100).toFixed(d)}%`; },
    /** hm: 3 小時 20 分 / 3h 20m · hms: 3:20:05 */
    dur(sec, style = "hm") {
      if (bad(sec)) return "";
      const s = Math.round(+sec), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
      if (style === "hms") return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
      if (locale === "zh-TW") return h ? `${h} 小時 ${m} 分` : `${m} 分`;
      return h ? `${h}h ${m}m` : `${m}m`;
    },
    pace(sPerKm) {
      if (bad(sPerKm) || +sPerKm <= 0) return "";
      const s = Math.round(+sPerKm);
      return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} /km`;
    },
    dist(km, d = 1) { return bad(km) ? "" : `${(+km).toFixed(d)} km`; },
    elev(m) { return bad(m) ? "" : `${numFmt(0).format(Math.round(+m))} m`; },
  };

  function setLocale(loc) {
    if (!SUPPORTED.includes(loc)) return;
    document.cookie = `lang=${loc}; Max-Age=${365 * 24 * 3600}; Path=/; SameSite=Lax`;
    const u = new URL(location.href);
    u.searchParams.delete("lang");          // ?lang= beats the cookie
    location.replace(u.toString());
  }

  window.I18N = { locale, supported: SUPPORTED, debug: !!boot.debug, catalog, t, fmt, setLocale };
  if (!("t" in window)) window.t = t;
  if (!("fmt" in window)) window.fmt = fmt;
})();
