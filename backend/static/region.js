/* 地區 (engine/region.py): tw | intl. Loaded by shell.js on every page.
 *
 * Outside Taiwan (intl) the Taiwan-only parts go away, declaratively:
 *   data-tw-only                 hidden
 *   data-intl-text="多日登山"      text replaced
 *   data-intl-placeholder="…"     placeholder replaced
 * Pages that need it in code: `window.AppRegion.then((r) => r.region === "tw")`.
 */
(() => {
  const p = fetch("/api/v1/region").then((r) => (r.ok ? r.json() : { region: "tw" })).catch(() => ({ region: "tw" }));
  window.AppRegion = p;
  const apply = (r) => {
    if (r.region === "tw") return;
    document.querySelectorAll("[data-tw-only]").forEach((el) => { el.hidden = true; });
    document.querySelectorAll("[data-intl-text]").forEach((el) => { el.textContent = el.dataset.intlText; });
    document.querySelectorAll("[data-intl-placeholder]").forEach((el) => { el.placeholder = el.dataset.intlPlaceholder; });
  };
  p.then((r) => {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => apply(r));
    else apply(r);
  });
})();
