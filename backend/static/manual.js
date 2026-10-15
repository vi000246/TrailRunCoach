/* 操作手冊 page (SP-558; manual.html, api/manual.py): the search box, the jump to a section (and its
 * 「深入」 part: #<section>--deep), the contents following the reading, the chapter icons, and the
 * live parts drawn by the app's own code — :::chart pmc (static/pmc_chart.js, the 總覽 page's chart),
 * the glossary's other charts (static/manual_charts.js, SP-559) and :::legend (static/sesschip.js, the
 * 課表 cards). Asks the service worker to keep the whole
 * manual for offline (offline_core.js warmManual). */
(() => {
  const L = window.I18N || { locale: "zh-TW", t: (k) => k };
  const t = (k, p) => L.t("manual." + k, p);
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const I = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const ICONS = {
    start: I('<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/>'),
    schedule: I('<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/><path d="M7.5 13h2M11 13h2M14.5 13h2M7.5 16.5h2M11 16.5h2"/>'),
    faq: I('<circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.6"/><path d="M12 16.8v.2"/>'),
    book: I('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M8.5 7.5h7M8.5 11h5"/>'),
  };
  const wrap = document.querySelector(".m-wrap");
  const CHAPTER = wrap ? wrap.dataset.chapter : "";

  // ---- icons: the contents' chapter links and the cards -------------------------------------------
  for (const a of document.querySelectorAll("[data-icon]")) {
    const svg = ICONS[a.dataset.icon] || ICONS.book;
    if (a.classList.contains("m-card-h")) a.insertAdjacentHTML("afterbegin", `<span class="m-ic">${svg}</span>`);
    else a.insertAdjacentHTML("afterbegin", svg);
  }
  // the contents page: the search goes into the big header
  const hero = document.querySelector(".m-hero");
  if (hero) hero.appendChild($("m-search-side"));
  // narrow screens: the contents start folded
  if (matchMedia("(max-width: 899px)").matches && CHAPTER) $("m-toc").open = false;

  // ---- jump to a section; #x--deep opens its 深入 part ------------------------------------------------
  function arrive() {
    const h = decodeURIComponent(location.hash.slice(1));
    if (!h) return;
    const el = document.getElementById(h);
    if (!el) return;
    if (el.tagName === "DETAILS") el.open = true;
    const sec = el.closest(".m-sec");
    if (sec) { sec.classList.remove("m-flash"); void sec.offsetWidth; sec.classList.add("m-flash"); }
    requestAnimationFrame(() => el.scrollIntoView({ block: "start" }));
  }
  addEventListener("hashchange", arrive);
  arrive();

  // ---- the contents follow the reading ----------------------------------------------------------------
  const tocLinks = [...document.querySelectorAll(".m-toc a[data-anchor]")];
  if (tocLinks.length) {
    const secs = tocLinks.map((a) => document.getElementById(a.dataset.anchor));
    // the section being read: the last one whose top has passed a third of the screen
    const mark = () => {
      let k = 0;
      secs.forEach((el, i) => { if (el && el.getBoundingClientRect().top <= innerHeight * 0.3) k = i; });
      if (innerHeight + scrollY >= document.documentElement.scrollHeight - 4) k = secs.length - 1;
      tocLinks.forEach((a, i) => a.classList.toggle("on", i === k));
    };
    addEventListener("scroll", mark, { passive: true });
    addEventListener("resize", mark);
    addEventListener("hashchange", () => setTimeout(mark, 50));
    mark();
  }

  // ---- search ------------------------------------------------------------------------------------------
  const q = $("m-q"), res = $("m-res");
  let index = null, indexP = null, hits = [], sel = -1;
  const loadIndex = () => indexP || (indexP = fetch("/manual/index.json", { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : null)).then((j) => (index = j)).catch(() => null));
  const partTag = (p) => `<span class="m-tag m-tag-${p === "deep" ? "deep" : "simple"}">${esc(t(p === "deep" ? "deep" : "simple"))}</span>`;
  function draw() {
    const text = q.value.trim();
    if (!text) { res.hidden = true; q.setAttribute("aria-expanded", "false"); return; }
    if (!index) { res.innerHTML = `<div class="m-none">${esc(t("search_loading"))}</div>`; res.hidden = false; return; }
    hits = window.ManualSearch.search(index, text, { limit: 8 });
    sel = hits.length ? 0 : -1;
    res.innerHTML = hits.length ? hits.map((h, i) => `<a class="m-hit" role="option" id="m-hit-${i}" href="${esc(h.url)}" aria-selected="${i === sel}">
        <b>${esc(h.title)}</b><span class="m-where">${esc(h.chapter_title)} ${partTag(h.part)}</span><span class="m-snip">${h.snippet}</span></a>`).join("")
      : `<div class="m-none">${esc(t("search_none", { q: text }))}</div>`;
    res.hidden = false;
    q.setAttribute("aria-expanded", "true");
    q.setAttribute("aria-activedescendant", sel >= 0 ? `m-hit-${sel}` : "");
  }
  function pick(i) {
    sel = i;
    res.querySelectorAll(".m-hit").forEach((a, k) => a.setAttribute("aria-selected", String(k === i)));
    const a = res.querySelector(`#m-hit-${i}`);
    if (a) { a.scrollIntoView({ block: "nearest" }); q.setAttribute("aria-activedescendant", a.id); }
  }
  // a hit on this very page: no reload, just the jump (and the 深入 part opened)
  function go(url) {
    res.hidden = true;
    const u = new URL(url, location.href);
    if (u.pathname === location.pathname) {
      if (location.hash === u.hash) arrive(); else location.hash = u.hash;
    } else location.href = u.pathname + u.hash;
  }
  let timer = 0;
  q.addEventListener("focus", () => { loadIndex().then(() => { if (q.value.trim()) draw(); }); });
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => loadIndex().then(draw), 60); });
  q.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && hits.length) { e.preventDefault(); pick(Math.min(hits.length - 1, sel + 1)); }
    else if (e.key === "ArrowUp" && hits.length) { e.preventDefault(); pick(Math.max(0, sel - 1)); }
    else if (e.key === "Enter" && sel >= 0 && hits[sel]) { e.preventDefault(); go(hits[sel].url); }
    else if (e.key === "Escape") { res.hidden = true; q.blur(); }
  });
  res.addEventListener("click", (e) => {
    const a = e.target.closest(".m-hit");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    go(a.getAttribute("href"));
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".m-search")) res.hidden = true; });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== q && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "")) {
      e.preventDefault(); q.focus();
    }
  });
  // ?q=… (a link to a search): fill the box and show the hits
  const pre = new URLSearchParams(location.search).get("q");
  if (pre) { q.value = pre; loadIndex().then(draw); }

  // ---- :::legend — the 課表 cards' legend, drawn by sesschip.js ---------------------------------------
  for (const fig of document.querySelectorAll(".m-legend")) {
    const ui = fig.querySelector(".m-legend-ui");
    if (!window.SessChip) continue;
    ui.innerHTML = SessChip.legend({ levels: { green: 0.2, yellow: 0.5 }, manual: false });
    const d = ui.querySelector("details");
    if (d) d.open = true;
  }

  // ---- :::chart pmc — 訓練負荷圖 with a made-up season, the coming two weeks yours to set -------------
  // The daily load of a typical week (Mon … Sun), as shares of the week; a 3 + 1 rhythm before today
  const WEEK = [0, 0.15, 0.2, 0.12, 0, 0.33, 0.2];
  const PAST = [300, 330, 360, 250, 340, 370, 400, 280, 380, 410, 440, 300];   // 12 weeks of 訓練負荷
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const md = (s) => { const d = new Date(s + "T00:00:00"); return `${d.getMonth() + 1}/${d.getDate()}`; };
  function season(next1, next2) {
    const now = new Date(); now.setHours(0, 0, 0, 0);
    const mon = new Date(now); mon.setDate(now.getDate() - ((now.getDay() + 6) % 7));       // this week's Monday
    const start = new Date(mon); start.setDate(mon.getDate() - 7 * PAST.length);
    const weeks = [...PAST, next1, next2];
    const days = [];
    weeks.forEach((w, k) => WEEK.forEach((f, i) => {
      const d = new Date(start); d.setDate(start.getDate() + 7 * k + i);
      days.push({ date: iso(d), load: Math.round(w * f) });
    }));
    return { days, today: iso(mon) };
  }
  function drawPmc(fig) {
    const ui = fig.querySelector(".m-chart-ui");
    const ratio = /(^|\s)ratio=1(\s|$)/.test(fig.dataset.args || "");
    if (!window.echarts || !window.PmcChart) { ui.innerHTML = `<p class="mc-off">${esc(t("chart.offline"))}</p>`; return; }
    ui.innerHTML = `<div class="mc-ctl">
        <label><span>${esc(t("chart.w1"))} <b data-o="w1"></b></span><input type="range" min="0" max="900" step="10" value="440" data-w="1" aria-label="${esc(t("chart.w1"))}"></label>
        <label><span>${esc(t("chart.w2"))} <b data-o="w2"></b></span><input type="range" min="0" max="900" step="10" value="470" data-w="2" aria-label="${esc(t("chart.w2"))}"></label>
      </div>
      <div class="mc-legend"><span><i style="background:${css("--s1")};height:3px"></i>${esc(t("chart.ctl"))}</span><span><i style="background:${css("--s2")};height:3px"></i>${esc(t("chart.atl"))}</span>${
        PmcChart.BANDS.map(([, c, k]) => `<span><i style="background:${c}"></i>${esc(t("band." + k))}</span>`).join("")}</div>
      <div class="mc-plot" role="img" aria-label="${esc(t("chart.aria"))}"></div>
      <div class="mc-out"></div><div class="mc-rule" role="status" aria-live="polite"></div>`;
    const plot = ui.querySelector(".mc-plot");
    const chart = echarts.init(plot);
    new ResizeObserver(() => chart.resize()).observe(plot);
    const [r1, r2] = ui.querySelectorAll("input[type=range]");
    const one = (v) => (Math.round(v * 10) / 10).toFixed(1);
    const sgn = (v, f) => (v > 0 ? "+" : v < 0 ? "−" : "±") + f(Math.abs(v));
    function render() {
      const w1 = Number(r1.value), w2 = Number(r2.value);
      ui.querySelector('[data-o="w1"]').textContent = w1;
      ui.querySelector('[data-o="w2"]').textContent = w2;
      const s = season(w1, w2);
      // 6 weeks before today and the coming 2; the 6 weeks before them only start the values
      const all = PmcChart.simulate(s.days, { ctl0: 38, atl0: 38 });
      const rows = all.slice(all.length - 8 * 7);
      const atToday = all.find((r) => r.date === s.today), end = all[all.length - 1];
      chart.setOption(PmcChart.option(rows, { css, esc, sgn, label: md, day: md, onChart: true,
        marks: [{ date: s.today, text: t("chart.today") }],
        names: { ctl: t("chart.ctl"), atl: t("chart.atl"), tsb: t("chart.tsb") } }), true);
      const bal = end.ctl - end.atl;                          // the balance the day after the two weeks
      const b = PmcChart.band(PmcChart.pct(bal, end.ctl));
      ui.querySelector(".mc-out").innerHTML =
        `<div><small>${esc(t("chart.ctl"))}</small><b>${one(end.ctl)}</b> <small>${esc(t("chart.vs_today", { d: sgn(end.ctl - atToday.prev, one) }))}</small></div>` +
        `<div><small>${esc(t("chart.atl"))}</small><b>${one(end.atl)}</b></div>` +
        `<div><small>${esc(t("chart.tsb"))}</small><b>${sgn(bal, (v) => v.toFixed(0))}</b><span class="mc-band" style="background:${b.color}">${esc(t("band." + b.key))}</span></div>` +
        // SP-559: the glossary's 負荷比 (Load ratio) = acute ÷ chronic, with the chart's bands
        (ratio ? `<div><small>${esc(t("chart.ratio"))}</small><b>${end.ctl > 0 ? (end.atl / end.ctl).toFixed(2) : "–"}</b></div>` : "");
      const rule = ui.querySelector(".mc-rule");
      const k = PmcChart.rule(bal, end.ctl);               // SP-670: a share of chronic load, as the schedule
      rule.className = "mc-rule " + k;
      rule.textContent = t("chart.rule_" + k, { tsb: sgn(bal, (v) => v.toFixed(0)),
        pct: sgn(Math.round(PmcChart.pct(bal, end.ctl) * 100), (v) => v.toFixed(0)) });
    }
    r1.addEventListener("input", render);
    r2.addEventListener("input", render);
    render();
  }
  const charts = [...document.querySelectorAll('.m-chart[data-chart="pmc"]')];
  // the glossary's other live charts (SP-559): static/manual_charts.js
  const others = [...document.querySelectorAll(".m-chart[data-chart]")].filter((f) => f.dataset.chart !== "pmc");
  if (charts.length || others.length) {
    // echarts.min.js is deferred: draw once it is there (offline without it: say so)
    const start = () => {
      charts.forEach(drawPmc);
      for (const fig of others) {
        if (window.ManualCharts) ManualCharts.draw(fig, fig.dataset.chart, fig.dataset.args || "", { t, css, esc });
        else fig.querySelector(".m-chart-ui").innerHTML = `<p class="mc-off">${esc(t("chart.offline"))}</p>`;
      }
    };
    if (document.readyState === "complete") start(); else addEventListener("load", start);
  }

  // ---- offline: the whole manual into this browser's copy ---------------------------------------------
  if ("serviceWorker" in navigator && window.isSecureContext) {
    addEventListener("load", () => {
      navigator.serviceWorker.ready.then((reg) => reg.active && reg.active.postMessage({ type: "manual" })).catch(() => {});
    });
  }
  window.ManualPage = { arrive, loadIndex, search: (s) => { q.value = s; return loadIndex().then(draw).then(() => hits); } };
})();
