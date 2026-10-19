/* 訓練負荷圖 (Training load chart): the chart of the 總覽 page (overview.html, SP-122) and of the manual's
 * live example (manual.html, SP-558) — one drawing, so the manual shows what the app shows.
 *
 *   PmcChart.option(rows, ctx)    the ECharts option: Chronic / Acute load lines, Load balance bars
 *                                 coloured by its band (balance ÷ yesterday's chronic load, the same bands
 *                                 as views/training.json's chart). rows: [{date, ctl, atl, tsb, prev}]
 *                                 (prev = yesterday's ctl); ctx: {css(var), esc, label(date) (axis),
 *                                 day(date) (tooltip), names: {ctl, atl, tsb}, sgn(v, f), marks, onChart}
 *   PmcChart.simulate(loads, o)   daily loads → rows, the app's formula (engine/load_guard.recur):
 *                                 v += (load − v) / days, chronic over o.ctlDays (42), acute over
 *                                 o.atlDays (7); balance = yesterday's chronic − yesterday's acute
 *   PmcChart.band(f)              the band of a balance share f (balance ÷ yesterday's chronic load):
 *                                 {key: high_risk | optimal | grey | fresh | transition, color}
 *   PmcChart.pct(tsb, ctl)        that share, of CTL_MIN under it (engine/load_guard.tsb_share, SP-670)
 *   PmcChart.pctRound(f)          that share in %, whole, or one decimal when it rounds onto a line (LINES)
 *   PmcChart.rule(tsb, ctl)      the schedule's guardrail: "recovery" (< −30 %), "hold" (< −20 %), "ok"
 *                                 (load_guard.week_rule)
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PmcChart = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // [upper bound of balance ÷ yesterday's chronic load, colour, name]
  // the same numbers as engine/load_guard.py (TSB_ZONES, TSB_CTL_MIN, TSB_DEEP / TSB_HOLD; SP-670)
  const BANDS = [[-0.3, "#e34948", "high_risk"], [-0.1, "#1baf7a", "optimal"], [0.1, "#a9a1e6", "grey"],
    [0.35, "#6a5cc9", "fresh"], [Infinity, "#eda100", "transition"]];
  const CTL_MIN = 30;
  const band = (f) => { const b = BANDS.find(([hi]) => f < hi); return { key: b[2], color: b[1] }; };
  const pct = (tsb, ctl) => (tsb == null ? 0 : tsb / Math.max(ctl > 0 ? ctl : 0, CTL_MIN));
  const rule = (tsb, ctl) => { const f = pct(tsb, ctl); return f < -0.3 ? "recovery" : f < -0.2 ? "hold" : "ok"; };
  const share = (r) => pct(r.tsb, r.prev);
  // every line in whole % (load_guard.TSB_LINES_PCT): a share that rounds onto one but isn't on it keeps
  // one decimal, so −30.3 % doesn't read 「−30%，低於 −30%」 (load_guard.tsb_pct_number, SP-670 Q1-5)
  const LINES = [-30, -25, -20, -10, -5, 5, 10, 35];
  const pctRound = (f) => { const p = f * 100, r = Math.round(p), d = Math.round(p * 10) / 10;
    return LINES.includes(r) && d !== r ? d : r; };
  const one = (v) => (v == null ? "–" : (Math.round(v * 10) / 10).toFixed(1));

  function option(rows, ctx) {
    const css = ctx.css, esc = ctx.esc, n = ctx.names;
    const ctlC = css("--s1"), atlC = css("--s2");
    // overview.html's axisStyle()
    const axis = { axisLabel: { color: css("--muted"), fontSize: 11 }, axisLine: { lineStyle: { color: css("--line") } },
      splitLine: { lineStyle: { color: css("--line") } }, axisTick: { show: false } };
    const marks = ctx.onChart ? ctx.marks || [] : [];
    const sgn = ctx.sgn || ((v, f) => (v > 0 ? "+" : "") + f(v));
    return { animation: false, textStyle: { fontFamily: "system-ui, sans-serif" },
      grid: { left: 36, right: 10, top: 10, bottom: 24 },
      tooltip: { trigger: "axis", backgroundColor: css("--panel"), borderColor: css("--line"), textStyle: { color: css("--text"), fontSize: 12 },
        formatter: (ps) => { const r = rows[ps[0].dataIndex];
          return `<b>${esc(ctx.day(r.date))}</b><br>${esc(n.ctl)} ${one(r.ctl)}<br>${esc(n.atl)} ${one(r.atl)}<br>${esc(n.tsb)} ${r.tsb == null ? "–" : sgn(r.tsb, one)}`; } },
      xAxis: { type: "category", data: rows.map((r) => ctx.label(r.date)), ...axis, splitLine: { show: false },
        axisLabel: { color: css("--muted"), fontSize: 11, hideOverlap: true } },
      yAxis: { type: "value", ...axis, splitLine: { lineStyle: { color: css("--line"), type: [3, 3] } } },
      series: [
        { id: "tsb", name: n.tsb, type: "bar", barCategoryGap: "20%", data: rows.map((r) => ({ value: r.tsb == null ? null : Math.round(r.tsb * 10) / 10, itemStyle: { color: band(share(r)).color } })) },
        { id: "ctl", name: n.ctl, type: "line", data: rows.map((r) => r.ctl), showSymbol: false, z: 3, lineStyle: { width: 2.5, color: ctlC }, itemStyle: { color: ctlC },
          // SP-289: a dashed line on the day the LTHR prior (0.90 × max HR) was replaced; the text on hover
          markLine: { symbol: "none", silent: false, label: { show: false }, lineStyle: { type: "dashed", color: css("--muted") },
            tooltip: { formatter: (p) => esc(p.data.text) },
            data: marks.map((m) => ({ xAxis: ctx.label(m.date), text: m.text })) } },
        { id: "atl", name: n.atl, type: "line", data: rows.map((r) => r.atl), showSymbol: false, z: 3, lineStyle: { width: 1.6, color: atlC }, itemStyle: { color: atlC } },
      ] };
  }

  /** loads: [{date, load}] one per day; o: {ctl0, atl0 (the values before the first day), ctlDays, atlDays} */
  function simulate(loads, o) {
    o = o || {};
    const cd = o.ctlDays || 42, ad = o.atlDays || 7;
    let ctl = o.ctl0 || 0, atl = o.atl0 || 0;
    const rows = [];
    for (const d of loads) {
      const prevCtl = ctl, prevAtl = atl;
      ctl += (d.load - ctl) / cd;
      atl += (d.load - atl) / ad;
      rows.push({ date: d.date, load: d.load, ctl, atl, tsb: prevCtl - prevAtl, prev: prevCtl });
    }
    return rows;
  }

  return { BANDS, CTL_MIN, LINES, band, pct, pctRound, rule, share, option, simulate, one };
});
