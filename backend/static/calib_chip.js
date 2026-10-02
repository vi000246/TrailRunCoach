/* Calibration chip (engine/calibrate.py): the small tag next to a number that
 * was estimated per athlete — 「本人 n=24」, 「手動」 or 「預設（文獻／推估）」 —
 * with the explanation on hover only (UI 要簡潔).
 *
 *   <script src="/api/v1/static/calib_chip.js"></script>
 *   el.innerHTML = `${value} ${calibChip(item.chip, item.source)}`;
 *
 * `chip` is the API's {text, tip}; `source` is default | fitted | user.
 * Colours come from the page's own tokens (--muted, --line, --accent, --warn).
 */
(() => {
  const CSS = `
  .cchip { display: inline-block; font-size: 11px; line-height: 16px; padding: 0 6px; margin-left: 4px;
    border-radius: 8px; border: 1px solid var(--line, #e1e5ea); color: var(--muted, #6a7482);
    white-space: nowrap; cursor: help; vertical-align: 1px; }
  .cchip.fitted { color: var(--accent, #2563eb); border-color: var(--accent, #2563eb); }
  .cchip.user { color: var(--warn, #b45309); border-color: var(--warn, #b45309); }`;
  const st = document.createElement("style");
  st.textContent = CSS;
  document.head.appendChild(st);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  window.calibChip = (chip, source) => chip
    ? `<span class="cchip ${esc(source || "default")}" title="${esc(chip.tip)}">${esc(chip.text)}</span>`
    : "";
})();
