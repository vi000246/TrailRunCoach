// 穩定爬坡 VAM:HR (views kind "climbvam" → backend/engine/panels/climb_vam.py). Self-contained:
// loaded by charts.html, which calls cvQuery(c) when it fetches the card and drawClimbVam(box, res)
// to draw it; uses the viewer's globals (esc, css, echarts, charts, renderCard, loadToken, ZOOM, fillZoom,
// pzDark). One route at a time (like for like): each sustained climb is a dot (categorical slot 1, blue) —
// filled = run, hollow = walked (shape, never colour alone) — and the 8-week rolling median a line (slot 2,
// orange); an HTML legend, one y-axis. 原始／熱調整 switches the y value client-side (both are in the JSON).
// Hover = per-dot tooltip; a table view sits under the chart. With no comparable climb, the card shows how
// long each activity's longest climb was instead.
(function () {
  const KEY = "charts.climbvam";
  const prefs = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } };
  const save = (patch) => { try { localStorage.setItem(KEY, JSON.stringify({ ...prefs(), ...patch })); } catch {} };
  const tc = (h) => (window.TrcTheme ? TrcTheme.color(h) : h);
  const colors = () => (window.pzDark && pzDark())
    ? { dot: "#3987e5", med: "#d95926" } : { dot: tc("#2a78d6"), med: tc("#eb6834") };   // palette slots 1, 2 (validated); tc: Trig's same hue (SP-974)
  // i18n: common.climb.* (static/i18n/<locale>/common.json)
  const T = (k, p) => (window.I18N ? window.I18N.t("common.climb." + k, p) : k);
  const KIND = { trail: T("kind_trail"), hike: T("kind_hike") };
  // a segment's run / walk: mode_key from the server, else the rule `mode` was written by (run_share ≥ 0.5)
  const modeKey = (q) => q.mode_key || (q.run_share == null ? null : q.run_share >= 0.5 ? "run" : "walk");

  window.cvQuery = function () {
    const r = prefs().route;
    return r ? `&route=${encodeURIComponent(r)}` : "";
  };

  function style() {
    if (document.getElementById("cv-style")) return;
    const st = document.createElement("style");
    st.id = "cv-style";
    st.textContent = `.cvbar { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; margin: 2px 0 6px; font-size: 12px; }
.cvbar select { font-size: 12px; max-width: 260px; }
.cvbar .seg { font-size: 11.5px; padding: 1px; }
.cvbar .seg button { padding: 1px 9px; font-weight: 500; }
.cvsum { font-size: 11.5px; color: var(--muted); margin: 0 0 6px; }
.cvsum b { color: var(--text); font-weight: 600; }
.plot.cv { height: 300px; }
.cvleg { display: flex; flex-wrap: wrap; gap: 3px 14px; font-size: 11px; color: var(--muted); margin: 0 0 2px; }
.cvleg span { white-space: nowrap; }
.cvleg i { display: inline-block; margin-right: 5px; vertical-align: middle; box-sizing: border-box; }
.cvleg i.dot { width: 9px; height: 9px; border-radius: 50%; }
.cvleg i.ring { width: 9px; height: 9px; border-radius: 50%; border: 2px solid; background: none !important; }
.cvleg i.ln { width: 16px; height: 0; border-top: 2px solid; }
.cvleg i.dash { width: 16px; height: 0; border-top: 1px dashed var(--muted); background: none !important; }
details.cvdet { margin-top: 6px; } details.cvdet > summary { font-size: 12px; cursor: pointer; }
details.cvdet table { margin-top: 4px; font-size: 11.5px; }`;
    document.head.appendChild(st);
  }

  function rerender(card) {
    if (!card._ctx) return;
    const { v, di, c, q } = card._ctx;
    // SP-689: the charts page's one floating 「計算中」 box (charts.html cardBusy), and an older answer never redraws
    if (window.cardBusy) window.cardBusy(card); else card.insertAdjacentHTML("beforeend", `<div class="loading">${esc(T("computing"))}</div>`);
    renderCard(card, v, di, c, q, loadToken).then((drew) => { if (drew !== false && ZOOM.card === card) fillZoom(); });
  }

  function help(card, res) {
    if (!res.description) return;
    if (res._zoom) { card.insertAdjacentHTML("beforeend", `<p class="desc">${esc(res.description)}</p>`); return; }
    const h2 = card.querySelector("h2");
    const q = `<button type="button" class="qtip" aria-label="${esc(T("help"))}" data-tip="${esc(res.description)}">?</button>`;
    const zb = h2?.querySelector(".zbtn");
    if (zb) zb.insertAdjacentHTML("beforebegin", q); else h2?.insertAdjacentHTML("beforeend", q);
  }

  const f2 = (x) => x == null ? "—" : x.toFixed(2);
  const mins = (s) => s == null ? "—" : T("min", { n: (s / 60).toFixed(1) });
  const modeText = (q) => modeKey(q) === "run" ? T("mode_run_pct", { p: Math.round(q.run_share * 100) })
    : modeKey(q) === "walk" ? T("mode_walk_pct", { p: Math.round((q.run_share || 0) * 100) }) : T("no_cadence");
  const modeWord = (q) => modeKey(q) === "run" ? T("run") : modeKey(q) === "walk" ? T("walk") : "—";

  function plot(card, option) {
    const el = document.createElement("div");
    el.className = "plot cv";
    card.appendChild(el);
    const chart = echarts.init(el);
    charts.push(chart);
    chart.setOption(option);
    // the card changes width with the grid / the zoom dialog / a phone rotation
    if (window.ResizeObserver) new ResizeObserver(() => { if (!chart.isDisposed()) chart.resize(); }).observe(el);
    return chart;
  }

  function axisBase() {
    const muted = css("--muted"), line = css("--line");
    return {
      grid: { left: 48, right: 16, top: 32, bottom: 34 },
      xAxis: { type: "time", axisLabel: { color: muted, fontSize: 10, hideOverlap: true },
        axisLine: { lineStyle: { color: line } }, splitLine: { show: false } },
      tooltip: { trigger: "item", confine: true, backgroundColor: css("--panel"), borderColor: line,
        textStyle: { color: css("--text"), fontSize: 12 } },
      legend: { show: false },          // an HTML legend above the plot (legend())
      muted, line,
    };
  }

  function legend(card, items) {
    card.insertAdjacentHTML("beforeend", `<p class="cvleg">${items.map(([kind, color, text]) =>
      `<span><i class="${kind}" style="${kind === "ln" || kind === "ring" ? `border-color:${color}` : `background:${color}`}"></i>${esc(text)}</span>`).join("")}</p>`);
  }

  // nothing comparable: each activity's longest sustained climb against the duration bar
  function drawNearMiss(card, res) {
    const rows = (res.runs_longest || []).filter((r) => r.longest_s != null);
    if (!rows.length) return;
    const c = colors(), b = axisBase(), bar = (res.min_seg_s || 480) / 60;
    legend(card, [["dot", c.dot, T("longest")], ["dash", "", T("bar", { n: bar })]]);
    plot(card, {
      grid: b.grid, xAxis: b.xAxis, legend: b.legend,
      tooltip: { ...b.tooltip, formatter: (p) => `${esc(p.data.date)}${esc(T("dot"))}${esc(KIND[p.data.kind] || "")}<br>${T("longest_is", { n: p.value[1].toFixed(1) })}`
        + (p.data.n ? esc(T("n_ok", { n: p.data.n })) : "") },
      yAxis: { type: "value", min: 0, max: Math.ceil(Math.max(bar * 1.2, ...rows.map((r) => r.longest_s / 60))),
        name: T("minutes"), nameTextStyle: { color: b.muted, fontSize: 10 },
        axisLabel: { color: b.muted, fontSize: 10 }, splitLine: { lineStyle: { color: b.line } } },
      series: [{ name: T("longest"), type: "scatter", symbolSize: 9, color: c.dot,
        itemStyle: { borderColor: css("--panel"), borderWidth: 2 },
        data: rows.map((r) => ({ value: [r.date, r.longest_s / 60], date: r.date, kind: r.kind, n: r.segments })),
        markLine: { silent: true, symbol: "none", lineStyle: { color: b.muted, type: "dashed", width: 1 },
          label: { show: false }, data: [{ yAxis: bar }] } }],
    });
  }

  window.drawClimbVam = function (card, res) {
    style();
    card.querySelector(".loading")?.remove();
    help(card, res);
    const p = prefs(), heat = p.basis === "heat";
    const routes = res.routes || [];
    if (routes.length) {
      const bar = document.createElement("div");
      bar.className = "cvbar";
      bar.innerHTML = `<label>${esc(T("route"))} <select aria-label="${esc(T("route"))}">${routes.map((r) =>
        `<option value="${esc(r.id)}"${r.id === res.route?.id ? " selected" : ""}>${esc(T("route_opt", { name: r.name, n: r.runs, s: r.segments }))}</option>`).join("")}</select></label>`
        + `<div class="seg" role="group" aria-label="${esc(T("basis_aria"))}"><button type="button" data-v="raw" class="${heat ? "" : "on"}" aria-pressed="${!heat}">${esc(T("raw"))}</button><button type="button" data-v="heat" class="${heat ? "on" : ""}" aria-pressed="${heat}">${esc(T("heat_est"))}</button></div>`;
      card.appendChild(bar);
      bar.querySelector("select").onchange = (e) => { save({ route: e.target.value }); rerender(card); };
      bar.querySelectorAll(".seg button").forEach((btn) => btn.onclick = () => {
        if (btn.classList.contains("on")) return;
        save({ basis: btn.dataset.v });
        card.querySelectorAll(".cvbar, .cvsum, .cvleg, .plot.cv, details.cvdet, .nodata").forEach((el) => {
          if (el.classList.contains("plot")) echarts.getInstanceByDom(el)?.dispose();
          el.remove();
        });
        card.querySelector(".qtip")?.remove();
        window.drawClimbVam(card, res);
      });
    }
    if (res.empty || !res.points?.length) {
      card.insertAdjacentHTML("beforeend", `<p class="nodata">${esc(res.empty || T("no_data"))}</p>`);
      drawNearMiss(card, res);
      return;
    }
    const pts = res.points, med = heat ? res.median_adj : res.median;
    const y = (q) => heat ? q.vam_hr_adj : q.vam_hr;
    const shown = pts.filter((q) => y(q) != null);
    const last = [...med].reverse().find((m) => m != null);
    const walked = shown.filter((q) => modeKey(q) === "walk").length, ran = shown.filter((q) => modeKey(q) === "run").length;
    card.insertAdjacentHTML("beforeend", `<p class="cvsum">${T("sum", { n: shown.length, w: walked, r: ran, a: new Set(shown.map((q) => q.file)).size })}`
      + `${T("sum_med", { m: f2(last) })}${heat ? esc(T("sum_heat", { n: pts.length - shown.length })) : ""}</p>`);
    const c = colors(), b = axisBase();
    const dot = esc(T("dot"));
    const tip = (q) => `${esc(q.start.replace("T", " "))}${dot}${esc(KIND[q.kind] || "")}${esc(T("from_min", { n: q.at_min }))}<br>`
      + `VAM:HR <b>${f2(y(q))} m/h/bpm</b>${heat ? esc(T("heat_adj")) : ""}<br>`
      + `VAM ${q.vam} m/h ÷ ${heat ? q.hr_adj : q.hr} bpm${heat ? esc(T("measured", { hr: q.hr })) : ""}<br>`
      + `${esc(modeText(q))}<br>`
      + `${esc(T("grade_line", { g: (q.grade * 100).toFixed(1), up: Math.round(q.gain_m), d: mins(q.duration_s), m: mins(q.measured_s) }))}<br>`
      + `${esc(T("zone_is", { z: q.zone || "—" }))}${dot}${q.temp_c != null ? `${q.temp_c} °C${dot}Hadley ${q.hadley}` : esc(T("no_weather"))}`;
    const all = shown.map(y).concat(med.filter((m) => m != null));
    const lo = Math.min(...all), hi = Math.max(...all), pad = Math.max(0.1, (hi - lo) * 0.15);
    const items = [["dot", c.dot, T("leg_run")], ["ring", c.dot, T("leg_walk")], ["ln", c.med, T("med8")]];
    legend(card, ran ? (walked ? items : [items[0], items[2]]) : [items[1], items[2]]);
    const panel = css("--panel");
    plot(card, {
      grid: b.grid, xAxis: b.xAxis, legend: b.legend,
      tooltip: { ...b.tooltip, formatter: (p) => p.seriesIndex === 0 ? tip(p.data.q)
        : `${esc(p.data.q.date)}<br>${esc(T("med8"))} <b>${f2(p.value[1])} m/h/bpm</b>` },
      yAxis: { type: "value", name: "m/h/bpm", min: Math.max(0, Math.floor((lo - pad) * 2) / 2), max: Math.ceil((hi + pad) * 2) / 2,
        nameTextStyle: { color: b.muted, fontSize: 10 }, axisLabel: { color: b.muted, fontSize: 10, formatter: (v) => v.toFixed(1) },
        splitLine: { lineStyle: { color: b.line } } },
      series: [
        { name: T("each"), type: "scatter", symbolSize: 9, z: 3,
          data: shown.map((q) => ({ value: [q.start, y(q)], q,
            // run = filled; walked / no cadence = a 2px ring (shape carries the mode, not colour)
            itemStyle: modeKey(q) === "run" ? { color: c.dot, borderColor: panel, borderWidth: 2 }
              : { color: panel, borderColor: c.dot, borderWidth: 2 } })) },
        { name: T("med8"), type: "line", color: c.med, lineStyle: { width: 2 }, symbol: "circle", symbolSize: 4,
          showSymbol: pts.length <= 40, connectNulls: true, z: 2,
          data: pts.map((q, i) => med[i] == null ? null : { value: [q.start, med[i]], q }).filter(Boolean) },
      ],
    });
    card.insertAdjacentHTML("beforeend", `<details class="cvdet"><summary>${esc(T("table", { n: pts.length }))}</summary><table><thead><tr>
      <th>${esc(T("th_date"))}</th><th>${esc(T("th_kind"))}</th><th>${esc(T("th_mode"))}</th><th class="num">VAM:HR</th><th class="num">${esc(T("th_heat"))}</th><th class="num">VAM</th><th class="num">bpm</th><th class="num">${esc(T("th_grade"))}</th><th class="num">${esc(T("th_len"))}</th><th>${esc(T("th_zone"))}</th><th class="num">°C</th></tr></thead><tbody>
      ${pts.map((q) => `<tr><td>${esc(q.date)}</td><td>${esc(KIND[q.kind] || "")}</td><td>${esc(modeWord(q))}</td><td class="num">${f2(q.vam_hr)}</td><td class="num">${f2(q.vam_hr_adj)}</td>
        <td class="num">${q.vam}</td><td class="num">${q.hr}</td><td class="num">${(q.grade * 100).toFixed(1)}%</td><td class="num">${mins(q.duration_s)}</td>
        <td>${esc(q.zone || "—")}</td><td class="num">${q.temp_c ?? "—"}</td></tr>`).join("")}
      </tbody></table></details>`);
  };
})();
