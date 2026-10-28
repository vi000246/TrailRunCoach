/* 外觀 (SP-974; docs/spec/ui-theme.spec.md). Written inline at the top of every page's <head>
   (backend/i18n/pages.py, with theme.css), so the colours are right before the first paint.

   The choice (設定 › 偏好設定 › 外觀; 個人資料 until SP-1011) is this browser's, in the `theme` cookie (1 year), like the
   language (`lang`):
     trig-cream    Trig 米白         <html data-palette="trig-cream" data-theme="light">
     trig-badge    Trig 深綠         <html data-palette="trig-badge" data-theme="light">
     classic       經典（跟隨系統）   no attribute: each page's own light / dark rules
     classic-dark  經典深色         <html data-theme="dark">
   Not chosen: Trig 米白 on a light system, 原本 (dark) on a dark one — and it follows the system when
   that changes.

   TrcTheme.saved()        the cookie's choice, "" when none
   TrcTheme.current()      the choice in effect (the default resolved)
   TrcTheme.set(name)      remember and apply ("" forgets: the default again); fires "trc-theme"
   TrcTheme.isDark()       the page is dark (charts pick their dark colours)
   TrcTheme.color(hex)     a chart colour of the original palette → the Trig palette's same hue
   TrcTheme.recolor(obj)   the same over every "color" key of a chart definition (mutates, returns it)
   TrcTheme.themeColor()   the browser bar's colour (<meta name="theme-color">) */
(function () {
  "use strict";
  var NAMES = ["trig-cream", "trig-badge", "classic", "classic-dark"];
  var DEFAULT_LIGHT = "trig-cream", DEFAULT_DARK = "classic";
  var COOKIE = "theme", ORIGINAL_BAR = "#2563eb";
  // the original series colours (views/*.json, overview's --s1…) → the Trig slot of the same hue,
  // so a chart's description (「藍線」「綠色」) still names what is drawn
  var HUES = {
    "#2a78d6": "--s3", "#2563eb": "--s3",      // blue
    "#eb6834": "--s2",                          // orange
    "#1baf7a": "--s6",                          // green
    "#eda100": "--s4",                          // gold
    "#e87ba4": "--s5",                          // pink
    "#8a8984": "--faint", "#52514e": "--muted"  // the greys
  };
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function saved() {
    var m = /(?:^|;\s*)theme=([^;]*)/.exec(document.cookie || "");
    var v = m ? decodeURIComponent(m[1]) : "";
    return NAMES.indexOf(v) >= 0 ? v : "";
  }
  function resolve(choice, systemDark) {
    return NAMES.indexOf(choice) >= 0 ? choice : (systemDark ? DEFAULT_DARK : DEFAULT_LIGHT);
  }
  function current() { return resolve(saved(), !!(mq && mq.matches)); }
  function attrs(name) {
    if (name === "trig-cream" || name === "trig-badge") return { palette: name, theme: "light" };
    if (name === "classic-dark") return { palette: null, theme: "dark" };
    return { palette: null, theme: null };
  }
  function themeColor() {
    if (!root.getAttribute("data-palette")) return ORIGINAL_BAR;
    var v = getComputedStyle(root).getPropertyValue("--theme-color").trim();
    return v || ORIGINAL_BAR;
  }
  function apply() {
    var a = attrs(current());
    if (a.palette) root.setAttribute("data-palette", a.palette); else root.removeAttribute("data-palette");
    if (a.theme) root.setAttribute("data-theme", a.theme); else root.removeAttribute("data-theme");
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", themeColor());
  }
  function changed() {
    var ev;
    try { ev = new CustomEvent("trc-theme", { detail: { theme: current() } }); }
    catch (_) { ev = document.createEvent("CustomEvent"); ev.initCustomEvent("trc-theme", false, false, { theme: current() }); }
    window.dispatchEvent(ev);
  }
  function set(name) {
    var v = NAMES.indexOf(name) >= 0 ? name : "";
    document.cookie = v ? COOKIE + "=" + v + "; Max-Age=" + 365 * 24 * 3600 + "; Path=/; SameSite=Lax"
      : COOKIE + "=; Max-Age=0; Path=/; SameSite=Lax";
    apply();
    changed();
    return current();
  }
  function isDark() {
    var t = root.getAttribute("data-theme");
    return t === "dark" || (t !== "light" && !!(mq && mq.matches));
  }
  function color(hex) {
    if (typeof hex !== "string" || !root.getAttribute("data-palette")) return hex;
    var v = HUES[hex.toLowerCase()];
    if (!v) return hex;
    return getComputedStyle(root).getPropertyValue(v).trim() || hex;
  }
  function recolor(o) {
    if (!root.getAttribute("data-palette") || !o || typeof o !== "object") return o;
    if (Array.isArray(o)) { for (var i = 0; i < o.length; i++) recolor(o[i]); return o; }
    for (var k in o) {
      if (!Object.prototype.hasOwnProperty.call(o, k)) continue;
      if (k === "color" && typeof o[k] === "string") o[k] = color(o[k]);
      else if (o[k] && typeof o[k] === "object") recolor(o[k]);
    }
    return o;
  }

  apply();
  if (mq) {
    var onSystem = function () { if (!saved()) { apply(); changed(); } };
    if (mq.addEventListener) mq.addEventListener("change", onSystem);
    else if (mq.addListener) mq.addListener(onSystem);
  }
  window.TrcTheme = { NAMES: NAMES.slice(), saved: saved, current: current, resolve: resolve, set: set,
    apply: apply, isDark: isDark, color: color, recolor: recolor, themeColor: themeColor };
})();
