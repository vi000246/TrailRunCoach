/* Dashboard icons shared by 總覽 (overview.html) and 圖表分析 本次重點 (wko5_viewer.html).
 *
 *   <script src="/api/v1/static/dashicons.js"></script>
 *   DI("zap")                 -> an inline <svg> string (1em, currentColor, aria-hidden)
 *   DI("zap", "big")          -> with an extra class
 *
 * Stroke icons on a 24 × 24 grid, drawn in currentColor so the caller's text
 * token (or a series colour on the chip around it) decides the colour. An icon
 * never carries meaning alone: every use sits next to a text label.
 */
(() => {
  const P = {
    // session types (總覽 本週)
    easy: '<path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7Z"/>',
    z3: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    z5: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    long: '<path d="m8 4 4 7 3-3 7 12H2L8 4z"/>',
    hike: '<path d="m8 4 4 7 3-3 7 12H2L8 4z"/><path d="m6.5 13 2 1.5L11 12"/>',
    b2b: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2"/>',
    loaded: '<path d="M5 10a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v10a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2Z"/><path d="M9 6V4.5A2.5 2.5 0 0 1 11.5 2h1A2.5 2.5 0 0 1 15 4.5V6"/><path d="M8.5 14h7v4h-7z"/>',
    test: '<path d="M9 2v6.5L4.2 18.6A2 2 0 0 0 6 21.5h12a2 2 0 0 0 1.8-2.9L15 8.5V2"/><path d="M7.5 2h9M6.5 15h11"/>',
    strength: '<path d="M6.5 6v12M17.5 6v12M3 9.5v5M21 9.5v5M6.5 12h11"/>',
    race: '<path d="M4 21V4"/><path d="M4 4.5c3-2 6 0 8 1s5 1.5 8-.5v9c-3 2-6 1.5-8 .5s-5-3-8-1"/>',
    heat: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    rest: '<path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a7 7 0 0 0 11 11Z"/>',
    notice: '<circle cx="12" cy="12" r="9.5"/><path d="M12 16v-5M12 8h.01"/>',
    // status (always next to a word)
    good: '<circle cx="12" cy="12" r="9.5"/><path d="m8 12.5 2.8 2.8L16.5 9"/>',
    watch: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4.5M12 17h.01"/>',
    bad: '<circle cx="12" cy="12" r="9.5"/><path d="m15 9-6 6M9 9l6 6"/>',
    info: '<circle cx="12" cy="12" r="9.5"/><path d="M12 16v-5M12 8h.01"/>',
    na: '<circle cx="12" cy="12" r="9.5"/><path d="M8 12h8"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    // KPI and stat tiles
    fitness: '<path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    form: '<rect x="2.5" y="7" width="16" height="10" rx="2"/><path d="M21.5 10.5v3"/><path d="M6 10.5v3M9.5 10.5v3"/>',
    volume: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9.5 2.5h5"/>',
    flag: '<path d="M4 21V4"/><path d="M4 4.5c3-2 6 0 8 1s5 1.5 8-.5v9c-3 2-6 1.5-8 .5s-5-3-8-1"/>',
    time: '<circle cx="12" cy="12" r="9.5"/><path d="M12 7v5l3.5 2"/>',
    distance: '<circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><path d="M8.5 19H16a3.5 3.5 0 0 0 0-7H8a3.5 3.5 0 0 1 0-7h7.5"/>',
    gain: '<path d="m2.5 19 6-8 4 4.5L21.5 5"/><path d="M15.5 5h6v6"/>',
    tss: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-.5-2-1-3-1.1-2.1-.2-4 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.2.4-2.3 1-3.2.4 1.4 1.4 2.7 2.5 3.2z"/>',
    hr: '<path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7Z"/><path d="M3.5 12h4l1.5-3 3 6 1.5-3h3"/>',
    power: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    gauge: '<path d="m12 14 4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0"/>',
    drift: '<path d="M3 17c3 0 4-8 9-8s6 4 9 4"/><path d="M3 21h18"/>',
    durability: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    intensity: '<path d="M12 3v3M5.6 6.6l2 2M3 13h3M18 13h3M16.4 8.6l2-2"/><path d="m12 13 3.5-3.5"/><circle cx="12" cy="13" r="1.5"/><path d="M5 20h14"/>',
    zones: '<rect x="2.5" y="9" width="19" height="6" rx="1.5"/><path d="M9 9v6M15 9v6"/>',
    climb: '<path d="m2 20 7-11 4 6 3-4 6 9z"/>',
    tag: '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/><circle cx="7.5" cy="7.5" r="1.3"/>',
    filter: '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>',
    temp: '<path d="M14 14.8V4a2 2 0 1 0-4 0v10.8a4 4 0 1 0 4 0Z"/>',
    ramp: '<path d="M2 19h20L2 9z"/>',
    scissors: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12"/>',
    baseline: '<path d="M3 12h18"/><path d="M6 8v8M18 8v8"/>',
    noise: '<path d="M2 12h2l2-5 3 10 3-12 3 14 3-9 2 2h2"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    chev: '<path d="m9 6 6 6-6 6"/>',
  };
  window.DI = (name, cls = "") => `<svg class="di ${cls}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
    stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${P[name] || P.info}</svg>`;
})();
