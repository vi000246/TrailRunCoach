/* The manual's glossary charts (SP-559; manual.html, api/manual.py :::chart <name>): each term drawn with
 * made-up numbers the reader moves. Where the app has a rule of its own the chart uses that rule, and
 * backend/tests/test_manual_sp559.py checks it against the Python side:
 *
 *   zones   the 課表's heart-rate zones: engine/hr_profile.DEFAULT_RATIOS / zone_rows (whole bpm,
 *           Python's rounding)
 *   rpe     a load entered by feel: engine/rpe_load.DEFAULT_IF, load per hour = intensity² × 100
 *   grade   grade-adjusted pace: engine/algorithms/minetti.grade_factor (Minetti 2002, downhill floor 0.9),
 *           VAM and effort distance (km + climb ÷ 100)
 *   power   weighted power, intensity and training load: Coggan's published definitions
 *   cp      the two-parameter critical power model P = CP + W′ / t (Monod & Scherrer 1965)
 *   wbal    the anaerobic reserve during intervals: Skiba et al. 2015's published differential model
 *           (an illustration; the app's own recharge is described in the text)
 *   drift   heart-rate drift at a steady power: Pw:HR of the first half against the second
 *
 * The pure parts load under node (module.exports); ManualCharts.draw(fig, name, args, ctx) draws one into
 * a manual figure in the browser (ECharts, as every chart of the app); ctx: {t(key, params), css(var), esc}.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ManualCharts = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------------------------------------------------------------------------------------- the rules

  // engine/hr_profile.DEFAULT_RATIOS: the upper edges of Z1 … Z5 (Z6 is open)
  const RATIOS = { lthr: [0.80, 0.90, 0.95, 1.02, 1.06], hrr: [0.59, 0.74, 0.84, 0.88, 0.95], hrmax: [0.50, 0.60, 0.70, 0.80, 0.90] };
  // Python's round(): halves to the even neighbour (zone_rows rounds with it)
  const pyRound = (x) => { const r = Math.round(x); return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r; };
  /** The edges (bpm) between the six zones of a model; null when a number it needs is missing. */
  function zoneEdges(model, o) {
    const rs = RATIOS[model];
    if (model === "lthr") return o.lthr ? rs.map((r) => pyRound(r * o.lthr)) : null;
    if (model === "hrr") return o.mhr && o.rhr && o.mhr - o.rhr >= 40 ? rs.map((r) => pyRound(o.rhr + r * (o.mhr - o.rhr))) : null;
    if (model === "hrmax") return o.mhr ? rs.map((r) => pyRound(r * o.mhr)) : null;
    throw new Error("model " + model);
  }

  // engine/rpe_load: the five levels (Borg CR-10) and their default intensity
  const RPE = [["easy", 2, 0.74], ["moderate", 4, 0.82], ["hard", 5, 0.88], ["very_hard", 7, 0.95], ["max", 10, 1.10]];
  const rpePerHour = (level) => { const r = RPE.find((x) => x[0] === level); return 100 * r[2] * r[2]; };
  const rpeLoad = (level, minutes) => rpePerHour(level) * minutes / 60;

  // engine/algorithms/minetti: the energy cost of running on a grade (J/kg/m), relative to the flat
  const RUN = [155.4, -30.4, -43.3, 46.3, 19.5, 3.6];
  function gradeFactor(g, floor = 0.9) {
    const i = Math.max(-0.45, Math.min(0.45, g));
    const c = RUN;
    const f = (((((c[0] * i + c[1]) * i + c[2]) * i + c[3]) * i + c[4]) * i + c[5]) / c[5];
    return floor != null && g < 0 ? Math.max(f, floor) : f;
  }
  /** pace: s per horizontal km on grade g → {gap (s/km on the flat), vam (m/h), ep (effort km per km)} */
  const trail = (pace, g) => ({ gap: pace / gradeFactor(g), vam: 3600 / pace * 1000 * g, ep: 1 + 10 * Math.max(0, g) });

  // weighted power: the 30-second rolling mean, its 4th powers averaged, the 4th root (Coggan)
  function rolling30(p) {
    const out = [];
    let s = 0;
    for (let i = 0; i < p.length; i++) {
      s += p[i];
      if (i >= 30) s -= p[i - 30];
      if (i >= 29) out.push(s / 30);
    }
    return out;
  }
  function weighted(p) {
    const r = rolling30(p);
    if (!r.length) return null;
    return Math.pow(r.reduce((a, v) => a + v * v * v * v, 0) / r.length, 0.25);
  }
  const mean = (p) => p.reduce((a, v) => a + v, 0) / p.length;
  /** Training load of `sec` seconds at weighted power wp against the threshold: an hour at it = 100 */
  const load = (sec, wp, thr) => { const f = wp / thr; return sec * f * f * 100 / 3600; };
  /** An hour of 2 minutes on, 2 minutes off around avg (± swing, a share of avg): 1 value per second */
  function surges(avg, swing) {
    const p = [];
    for (let s = 0; s < 3600; s++) p.push(Math.floor(s / 120) % 2 === 0 ? avg * (1 + swing) : avg * (1 - swing));
    return p;
  }

  // the two-parameter critical power model
  const cpPower = (t, cp, w) => cp + w / t;
  const tte = (p, cp, w) => (p > cp ? w / (p - cp) : Infinity);

  // the anaerobic reserve (Skiba, Fulford, Clarke & Skiba 2015): used 1 J per J above CP, recharged
  // below CP at (W′0 − W′bal) ÷ W′0 of the gap to CP
  function wbal(p, cp, w0) {
    let w = w0;
    return p.map((x) => {
      if (x > cp) w -= x - cp;
      else w += (cp - x) * (w0 - w) / w0;
      return w;
    });
  }
  /** 10 min easy, 5 × (3 min at work × CP, rest seconds at half CP), 5 min easy: 1 value per second */
  function intervals(cp, work, rest) {
    const p = [];
    const add = (sec, v) => { for (let i = 0; i < sec; i++) p.push(v); };
    add(600, 0.7 * cp);
    for (let k = 0; k < 5; k++) { add(180, work * cp); if (k < 4) add(rest, 0.5 * cp); }
    add(300, 0.6 * cp);
    return p;
  }

  // heart-rate drift at a steady power: (Pw:HR first half − second half) ÷ first half
  const drift = (hr1, hr2) => (1 - hr1 / hr2) * 100;
  // the AeT drift test's reading (the 總覽's AeT help): < 3.5 % room above, 3.5–5 % = AeT, > 5 % too high
  const driftBand = (d) => (d < 3.5 ? "under" : d <= 5 ? "at" : "over");

  // ---------------------------------------------------------------------------------------- drawing

  const ZC = ["#8fb8de", "#4fa36b", "#c6b13b", "#e28a2f", "#d6493f", "#8a4fb0"];
  const fmtPace = (s) => { const m = Math.floor(s / 60), r = Math.round(s - m * 60); return r === 60 ? `${m + 1}:00` : `${m}:${String(r).padStart(2, "0")}`; };
  const mmss = (s) => (s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(Math.round(s % 60)).padStart(2, "0")}` : fmtPace(s));
  const parseArgs = (s) => Object.fromEntries(String(s || "").split(/\s+/).filter(Boolean).map((a) => a.split("=")));

  /** sliders [{k, label, min, max, step, value, show(v)}] + plot + outputs; returns {get, plot, out, rule, on} */
  function frame(ui, ctx, sliders, opts = {}) {
    const esc = ctx.esc;
    ui.innerHTML = `<div class="mc-ctl">${sliders.map((s) => `<label><span>${esc(s.label)} <b data-o="${s.k}"></b></span>` +
      `<input type="range" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.value}" data-k="${s.k}" aria-label="${esc(s.label)}"></label>`).join("")}</div>` +
      (opts.legend ? `<div class="mc-legend">${opts.legend}</div>` : "") +
      `<div class="mc-plot" role="img" aria-label="${esc(opts.aria || "")}"${opts.height ? ` style="height:${opts.height}px"` : ""}></div>` +
      `<div class="mc-out"></div><div class="mc-rule" role="status" aria-live="polite"${opts.rule ? "" : " hidden"}></div>`;
    const inputs = [...ui.querySelectorAll("input[type=range]")];
    const get = () => Object.fromEntries(inputs.map((i) => [i.dataset.k, Number(i.value)]));
    const show = () => { const v = get(); for (const s of sliders) ui.querySelector(`[data-o="${s.k}"]`).textContent = s.show ? s.show(v[s.k]) : v[s.k]; };
    const plot = ui.querySelector(".mc-plot");
    const chart = window.echarts.init(plot);
    new ResizeObserver(() => chart.resize()).observe(plot);
    return {
      get, chart, out: ui.querySelector(".mc-out"), rule: ui.querySelector(".mc-rule"),
      on(render) { const r = () => { show(); render(get()); }; inputs.forEach((i) => i.addEventListener("input", r)); r(); },
    };
  }
  const box = (esc, label, value, extra = "") => `<div><small>${esc(label)}</small><b>${esc(value)}</b>${extra ? ` <small>${esc(extra)}</small>` : ""}</div>`;
  function axes(css) {
    return { axisLabel: { color: css("--muted"), fontSize: 11 }, axisLine: { lineStyle: { color: css("--line") } },
      splitLine: { lineStyle: { color: css("--line"), type: [3, 3] } }, axisTick: { show: false }, nameTextStyle: { color: css("--muted"), fontSize: 11 } };
  }
  const tipStyle = (css) => ({ backgroundColor: css("--panel"), borderColor: css("--line"), textStyle: { color: css("--text"), fontSize: 12 } });

  const DRAW = {};

  DRAW.zones = (ui, ctx) => {
    const { t, css, esc } = ctx;
    const f = frame(ui, ctx, [
      { k: "lthr", label: t("gl.lthr"), min: 140, max: 190, step: 1, value: 168 },
      { k: "mhr", label: t("gl.mhr"), min: 165, max: 210, step: 1, value: 190 },
      { k: "rhr", label: t("gl.rhr"), min: 38, max: 75, step: 1, value: 52 },
    ], { aria: t("gl.zones_aria"), height: 200,
      legend: ZC.map((c, i) => `<span><i style="background:${c}"></i>Z${i + 1} ${esc(t("gl.z" + (i + 1)))}</span>`).join("") });
    const models = ["lthr", "hrr", "hrmax"];
    f.on((v) => {
      const lo = 80, top = Math.max(v.mhr, v.lthr * 1.1) + 4;
      const edges = models.map((m) => zoneEdges(m, v));
      const series = ZC.map((c, z) => ({
        type: "bar", stack: "z", barWidth: 22, itemStyle: { color: c },
        label: { show: z < 5, position: "insideRight", color: "#fff", fontSize: 10.5, fontWeight: 600, formatter: (p) => p.data.edge ?? "" },
        data: edges.map((e) => {
          if (!e) return { value: 0 };
          const a = z === 0 ? 0 : e[z - 1], b = z < 5 ? e[z] : top;
          return { value: Math.max(0, b - a), edge: z < 5 ? e[z] : null };
        }),
      }));
      f.chart.setOption({ animation: false, grid: { left: 92, right: 14, top: 6, bottom: 22 },
        tooltip: { show: false },
        xAxis: { type: "value", min: lo, max: Math.ceil(top), ...axes(css) },
        yAxis: { type: "category", inverse: true, data: models.map((m) => t("gl.model_" + m)), ...axes(css), splitLine: { show: false } },
        series }, true);
      f.out.innerHTML = models.map((m, i) => { const e = edges[i];
        return box(esc, t("gl.model_" + m), e ? `${e[0]}–${e[1]}` : "–", t("gl.z2_is")); }).join("");
    });
  };

  DRAW.rpe = (ui, ctx) => {
    const { t, css, esc } = ctx;
    const f = frame(ui, ctx, [
      { k: "lvl", label: t("gl.rpe_level"), min: 0, max: 4, step: 1, value: 2, show: (i) => t("gl.rpe_show", { name: t("gl.rpe_" + RPE[i][0]), n: RPE[i][1] }) },
      { k: "min", label: t("gl.minutes"), min: 10, max: 240, step: 5, value: 60 },
    ], { aria: t("gl.rpe_aria"), height: 200 });
    f.on((v) => {
      const lv = RPE[v.lvl][0];
      f.chart.setOption({ animation: false, grid: { left: 40, right: 10, top: 16, bottom: 24 },
        tooltip: { show: false },
        xAxis: { type: "category", data: RPE.map((r) => `${t("gl.rpe_" + r[0])} ${r[1]}`), ...axes(css), splitLine: { show: false } },
        yAxis: { type: "value", name: t("gl.per_hour"), ...axes(css) },
        series: [{ type: "bar", barWidth: "55%", data: RPE.map((r, i) => ({ value: Math.round(rpePerHour(r[0])),
          itemStyle: { color: i === v.lvl ? css("--s2") : css("--s1"), opacity: i === v.lvl ? 1 : 0.45 } })),
          label: { show: true, position: "top", color: css("--muted"), fontSize: 11 } }] }, true);
      f.out.innerHTML = box(esc, t("gl.intensity"), RPE[v.lvl][2].toFixed(2)) + box(esc, t("gl.per_hour"), String(Math.round(rpePerHour(lv)))) +
        box(esc, t("gl.load_of", { m: v.min }), String(Math.round(rpeLoad(lv, v.min))));
    });
  };

  DRAW.grade = (ui, ctx) => {
    const { t, css, esc } = ctx;
    const f = frame(ui, ctx, [
      { k: "g", label: t("gl.grade"), min: -30, max: 40, step: 1, value: 12, show: (g) => `${g > 0 ? "+" : ""}${g}%` },
      { k: "pace", label: t("gl.pace"), min: 240, max: 900, step: 15, value: 480, show: (s) => `${fmtPace(s)} /km` },
    ], { aria: t("gl.grade_aria"), height: 210 });
    const xs = []; for (let g = -30; g <= 40; g++) xs.push(g);
    f.on((v) => {
      const g = v.g / 100, r = trail(v.pace, g);
      f.chart.setOption({ animation: false, grid: { left: 40, right: 12, top: 14, bottom: 40 },
        tooltip: { trigger: "axis", ...tipStyle(css), formatter: (ps) => `${ps[0].axisValue}%: × ${ps[0].data[1].toFixed(2)}` },
        xAxis: { type: "value", min: -30, max: 40, name: t("gl.grade_axis"), nameLocation: "middle", nameGap: 26, ...axes(css), splitLine: { show: false } },
        yAxis: { type: "value", min: 0, ...axes(css) },
        series: [{ type: "line", showSymbol: false, data: xs.map((x) => [x, gradeFactor(x / 100)]), lineStyle: { color: css("--s1"), width: 2.5 },
          markPoint: { symbol: "circle", symbolSize: 10, itemStyle: { color: css("--s2") }, label: { show: false },
            data: [{ coord: [v.g, gradeFactor(g)] }] } }] }, true);
      f.out.innerHTML = box(esc, t("gl.factor"), `× ${gradeFactor(g).toFixed(2)}`) +
        box(esc, t("gl.gap"), `${fmtPace(r.gap)} /km`) + box(esc, "VAM", g > 0 ? `${Math.round(r.vam)} m/h` : "–") +
        box(esc, t("gl.ep_km"), r.ep.toFixed(2));
    });
  };

  DRAW.power = (ui, ctx) => {
    const { t, css, esc } = ctx;
    const CP = 250;
    const f = frame(ui, ctx, [
      { k: "avg", label: t("gl.avg_power"), min: 120, max: 260, step: 5, value: 190, show: (w) => `${w} W` },
      { k: "swing", label: t("gl.swing"), min: 0, max: 60, step: 5, value: 30, show: (s) => `± ${s}%` },
    ], { aria: t("gl.power_aria"), height: 220, rule: true,
      legend: `<span><i style="background:${css("--s1")};height:3px"></i>${esc(t("gl.p30"))}</span><span><i style="background:${css("--muted")};height:3px"></i>${esc(t("gl.avg_power"))}</span><span><i style="background:${css("--s2")};height:3px"></i>${esc(t("gl.weighted"))}</span>` });
    f.on((v) => {
      const p = surges(v.avg, v.swing / 100), r = rolling30(p), wp = weighted(p), avg = mean(p);
      const xs = r.map((_, i) => (i + 29) / 60);
      f.chart.setOption({ animation: false, grid: { left: 40, right: 12, top: 12, bottom: 40 },
        tooltip: { show: false },
        xAxis: { type: "value", min: 0, max: 60, name: t("gl.minute_axis"), nameLocation: "middle", nameGap: 26, ...axes(css), splitLine: { show: false } },
        yAxis: { type: "value", min: 0, name: "W", ...axes(css) },
        series: [{ type: "line", showSymbol: false, data: xs.map((x, i) => [x, r[i]]), lineStyle: { color: css("--s1"), width: 1.4 }, areaStyle: { color: css("--s1"), opacity: 0.12 } },
          { type: "line", showSymbol: false, data: [[0, avg], [60, avg]], lineStyle: { color: css("--muted"), type: "dashed", width: 1.6 } },
          { type: "line", showSymbol: false, data: [[0, wp], [60, wp]], lineStyle: { color: css("--s2"), width: 2.2 } }] }, true);
      f.out.innerHTML = box(esc, t("gl.avg_power"), `${Math.round(avg)} W`) + box(esc, t("gl.weighted"), `${Math.round(wp)} W`) +
        box(esc, t("gl.intensity"), (wp / CP).toFixed(2), `CP ${CP} W`) + box(esc, t("gl.load_hour"), String(Math.round(load(3600, wp, CP))));
      f.rule.className = "mc-rule";
      f.rule.textContent = t("gl.power_rule", { d: Math.round(wp - avg) });
    });
  };

  DRAW.cp = (ui, ctx) => {
    const { t, css, esc } = ctx;
    const f = frame(ui, ctx, [
      { k: "cp", label: "CP", min: 150, max: 400, step: 5, value: 260, show: (w) => `${w} W` },
      { k: "w", label: t("gl.ac"), min: 5, max: 30, step: 0.5, value: 13, show: (k) => `${k} kJ` },
    ], { aria: t("gl.cp_aria"), height: 230,
      legend: `<span><i style="background:${css("--s1")};height:3px"></i>${esc(t("gl.cp_curve"))}</span><span><i style="background:${css("--s2")};height:3px"></i>CP</span>` });
    const ts = []; for (let s = 60; s <= 3600; s += 15) ts.push(s);    // seconds; drawn in minutes
    f.on((v) => {
      const w = v.w * 1000;
      f.chart.setOption({ animation: false, grid: { left: 44, right: 14, top: 12, bottom: 40 },
        tooltip: { trigger: "axis", ...tipStyle(css), formatter: (ps) => `${mmss(ps[0].data[0] * 60)}: ${Math.round(ps[0].data[1])} W` },
        xAxis: { type: "value", min: 1, max: 60, name: t("gl.duration_axis"), nameLocation: "middle", nameGap: 26, ...axes(css), splitLine: { show: false },
          axisLabel: { color: css("--muted"), fontSize: 11, formatter: (m) => `${m}′` } },
        yAxis: { type: "value", min: Math.floor(v.cp * 0.8 / 10) * 10, name: "W", ...axes(css) },
        series: [{ type: "line", showSymbol: false, data: ts.map((s) => [s / 60, cpPower(s, v.cp, w)]), lineStyle: { color: css("--s1"), width: 2.5 } },
          { type: "line", showSymbol: false, data: [[1, v.cp], [60, v.cp]], lineStyle: { color: css("--s2"), type: "dashed", width: 1.8 } }] }, true);
      const over = Math.round(v.cp * 1.1);
      f.out.innerHTML = box(esc, t("gl.p_at", { m: 3 }), `${Math.round(cpPower(180, v.cp, w))} W`) +
        box(esc, t("gl.p_at", { m: 20 }), `${Math.round(cpPower(1200, v.cp, w))} W`) +
        box(esc, t("gl.tte", { w: over }), mmss(tte(over, v.cp, w)));
    });
  };

  DRAW.wbal = (ui, ctx) => {
    const { t, css, esc } = ctx;
    const CP = 250, W0 = 15000;
    const f = frame(ui, ctx, [
      { k: "work", label: t("gl.work"), min: 100, max: 130, step: 1, value: 112, show: (x) => `${x}% CP` },
      { k: "rest", label: t("gl.rest"), min: 30, max: 240, step: 15, value: 120, show: (s) => `${s} s` },
    ], { aria: t("gl.wbal_aria"), height: 230, rule: true,
      legend: `<span><i style="background:${css("--s2")};height:8px;opacity:.5"></i>${esc(t("gl.power"))}</span><span><i style="background:${css("--s1")};height:3px"></i>${esc(t("gl.reserve"))}</span>` });
    f.on((v) => {
      const p = intervals(CP, v.work / 100, v.rest), wb = wbal(p, CP, W0);
      const pct = wb.map((x) => Math.max(0, x / W0 * 100));
      const low = Math.min(...wb) / W0 * 100;
      const step = 5, idx = []; for (let i = 0; i < p.length; i += step) idx.push(i);
      f.chart.setOption({ animation: false, grid: { left: 40, right: 40, top: 14, bottom: 40 },
        tooltip: { show: false },
        xAxis: { type: "value", min: 0, max: Math.ceil(p.length / 60), name: t("gl.minute_axis"), nameLocation: "middle", nameGap: 26, ...axes(css), splitLine: { show: false } },
        yAxis: [{ type: "value", min: 0, name: "W", ...axes(css) }, { type: "value", min: 0, max: 100, name: "%", ...axes(css), splitLine: { show: false } }],
        series: [{ type: "line", step: "end", showSymbol: false, data: idx.map((i) => [i / 60, p[i]]), lineStyle: { width: 0 }, areaStyle: { color: css("--s2"), opacity: 0.35 } },
          { type: "line", yAxisIndex: 1, showSymbol: false, data: idx.map((i) => [i / 60, pct[i]]), lineStyle: { color: css("--s1"), width: 2.4 } }] }, true);
      f.out.innerHTML = box(esc, t("gl.lowest"), `${Math.max(0, Math.round(low))}%`) + box(esc, t("gl.ac"), `${W0 / 1000} kJ`, `CP ${CP} W`);
      const k = low <= 0 ? "empty" : low < 25 ? "deep" : "ok";
      f.rule.className = "mc-rule" + (k === "empty" ? " recovery" : k === "deep" ? " hold" : "");
      f.rule.textContent = t("gl.wbal_" + k, { p: Math.max(0, Math.round(low)) });
    });
  };

  DRAW.drift = (ui, ctx) => {
    const { t, css, esc } = ctx;
    const f = frame(ui, ctx, [
      { k: "h1", label: t("gl.hr_start"), min: 120, max: 160, step: 1, value: 140, show: (b) => `${b} bpm` },
      { k: "d", label: t("gl.hr_rise"), min: 0, max: 15, step: 1, value: 6, show: (b) => `+${b} bpm` },
    ], { aria: t("gl.drift_aria"), height: 200, rule: true,
      legend: `<span><i style="background:${css("--s2")};height:3px"></i>${esc(t("gl.hr"))}</span><span><i style="background:${css("--s1")};height:3px"></i>${esc(t("gl.power"))} 200 W</span>` });
    f.on((v) => {
      const hr = []; for (let m = 0; m <= 40; m += 0.5) hr.push([m, v.h1 + v.d * (m - 10) / 20]);
      const d = drift(v.h1, v.h1 + v.d), b = driftBand(d);
      f.chart.setOption({ animation: false, grid: { left: 40, right: 40, top: 14, bottom: 40 },
        tooltip: { show: false },
        xAxis: { type: "value", min: 0, max: 40, name: t("gl.minute_axis"), nameLocation: "middle", nameGap: 26, ...axes(css), splitLine: { show: false } },
        yAxis: [{ type: "value", min: 100, max: 200, interval: 20, name: "bpm", ...axes(css) }, { type: "value", min: 0, max: 300, interval: 60, name: "W", ...axes(css), splitLine: { show: false } }],
        series: [{ type: "line", showSymbol: false, data: hr, lineStyle: { color: css("--s2"), width: 2.4 },
          markLine: { symbol: "none", silent: true, label: { show: false }, lineStyle: { color: css("--muted"), type: "dashed" }, data: [{ xAxis: 20 }] } },
          { type: "line", yAxisIndex: 1, showSymbol: false, data: [[0, 200], [40, 200]], lineStyle: { color: css("--s1"), width: 2 } }] }, true);
      f.out.innerHTML = box(esc, t("gl.half1"), `${v.h1} bpm`) + box(esc, t("gl.half2"), `${v.h1 + v.d} bpm`) + box(esc, t("gl.drift"), `${d.toFixed(1)}%`);
      f.rule.className = "mc-rule" + (b === "over" ? " hold" : "");
      f.rule.textContent = t("gl.drift_" + b, { d: d.toFixed(1) });
    });
  };

  function draw(fig, name, args, ctx) {
    const ui = fig.querySelector(".m-chart-ui");
    if (typeof window === "undefined" || !window.echarts) { ui.innerHTML = `<p class="mc-off">${ctx.esc(ctx.t("chart.offline"))}</p>`; return; }
    if (DRAW[name]) DRAW[name](ui, ctx, parseArgs(args));
  }

  return { RATIOS, pyRound, zoneEdges, RPE, rpePerHour, rpeLoad, gradeFactor, trail, rolling30, weighted, mean, load, surges,
    cpPower, tte, wbal, intervals, drift, driftBand, NAMES: Object.keys(DRAW), draw };
});
