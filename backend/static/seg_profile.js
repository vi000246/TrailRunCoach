/* SP-784: the race calculator's 分段目標 — the target line (pace / power / heart rate, one at a time) over the
 * elevation profile, and the compact segment table under it — as one component for every page that shows a
 * plan's segment targets: the calculator (racepower.html, loaded by its address) and the read-only share
 * page (share.html, its built-in examples included: the server writes this file into the page, since a
 * signed-out visitor gets only /share/* through Cloudflare Access — backend/i18n/pages.py INLINE_SCRIPTS).
 *
 * The page keeps its own state (the selected / hovered segments, the day ends, the group's legs, the map)
 * and passes what the chart should show; this file holds the drawing, the table, the hover text, the
 * legend and their styles, so a change of the calculator's look reaches the share page too.
 *
 * Reads the plan's `chart_rows` (engine/racepower/seg_targets.chart_rows; a share: its snapshot's, or the
 * rows the server derives for a share made before SP-784). Text: the racepower.* catalog (both pages load it).
 */
(() => {
  const T = (k, p) => (window.I18N ? window.I18N.t("racepower." + k, p) : k);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const ok = (v) => v != null && isFinite(v);
  const f1 = (v) => ok(v) ? v.toFixed(1) : "–";
  const n0 = (v) => ok(v) ? Math.round(v).toLocaleString() : "–";
  const W = (v) => ok(v) ? Math.round(v) + " W" : "–";
  function hms(s) { if (!ok(s)) return "–"; s = Math.round(s); return `${Math.floor(s / 3600)}:${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; }
  function dur(s) { if (!ok(s)) return "–"; s = Math.round(s); return s < 3600 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : hms(s); }
  function pace(s) { if (!ok(s)) return "–"; s = Math.round(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} /km`; }
  function mmss(s) { s = Math.round(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
  const help = (t) => ` <span class="help" tabindex="0">?<span class="tip">${t}</span></span>`;
  const axisStyle = () => ({ axisLabel: { color: css("--muted"), fontSize: 11 }, axisLine: { lineStyle: { color: css("--line") } },
    splitLine: { lineStyle: { color: css("--line") } }, axisTick: { show: false }, nameTextStyle: { color: css("--muted") } });

  // The main chart: x = km 0…N, y = one measure (pace / power / heart rate, toggle), the elevation
  // profile as a light background area. Each segment's target comes from the plan (chart_rows =
  // seg_targets.chart_rows): grade-adjusted pace everywhere; power only where Stryd is a valid target
  // (road; trail flat / runnable 3–8 %); the HR cap (AeT on long races) except on trail descents.
  const MEASURE = {
    pace: { val: (r) => ok(r.pace_s_per_km) ? r.pace_s_per_km : null, axis: (v) => mmss(v), unit: "/km", inverse: true },
    power: { val: (r) => ok(r.power) ? r.power : null, axis: (v) => Math.round(v), unit: "W", inverse: false },
    hr: { val: (r) => ok(r.hr_cap) ? r.hr_cap : null, axis: (v) => Math.round(v), unit: "bpm", inverse: false },
  };
  const rowsOf = (P) => (P && P.chart_rows) || [];
  const rowAt = (P, km) => rowsOf(P).find((r) => km >= r.start_km && km <= r.end_km);
  // the measures a plan can show (百岳: no power; a share made before SP-784 without a heart-rate cap: no HR)
  const HAS = { pace: () => true, power: (r) => r.power || r.power_ref, hr: (r) => r.hr_cap || r.hr_ref };
  const measures = (P) => ["pace", "power", "hr"].filter((k) => !(k === "power" && P.type === "baiyue") && rowsOf(P).some(HAS[k]));

  // a measure that is not a valid target on a segment (trail: power on steep climbs / descents, HR on
  // descents) is written as a grey 「參考」 (seg_targets.chart_rows power_ref / hr_ref), never left blank
  function refCell(m, txt) { return `<span class="ref" title="${esc(T("ref.tip_" + m))}">${esc(T("ref.cell", { v: txt }))}</span>`; }
  function fmtRow(r, m, plain = false) {
    if (m === "pace") return ok(r.pace_s_per_km) ? pace(r.pace_s_per_km) : "–";
    if (m === "power") {
      if (r.power) return `${Math.round(r.power_band[0])}–${Math.round(r.power_band[1])} W`;
      if (r.power_ref) return plain ? T("ref.power_plain", { w: Math.round(r.power_ref) }) : refCell("power", T("ref.power_about", { w: Math.round(r.power_ref) }));
      return "–";
    }
    if (r.hr_cap) return `≤ ${Math.round(r.hr_cap)}`;
    if (r.hr_ref) return plain ? T("ref.hr_plain", { hr: Math.round(r.hr_ref) }) : refCell("hr", `≤ ${Math.round(r.hr_ref)}`);
    return "–";
  }
  // SP-728: the 攀爬段 (engine/racepower/climb.py) — stretches where you may need hands too
  const climbs = (P) => ((P && P.climb && P.climb.marks) || []);
  const climbIn = (P, r) => climbs(P).filter((c) => c.end_km > r.start_km && c.start_km < r.end_km);
  const pct0 = (v) => (ok(v) ? Math.round(v * 100) : "–");
  function climbTip(P) {
    const cv = (P.climb && P.climb.cv) || {};
    return T("climb.tip", { top: cv.top_pct != null ? cv.top_pct : 5, hit: pct0(cv.top_share), base: pct0(cv.base_share) });
  }

  // SP-872: no marks because your climbing records can't say yet (climb.py: `few` — not enough runs or
  // scrambles; `weak` — the cross-validation does not separate them): one faint legend item that says so,
  // instead of nothing (no 攀爬段 and too little data looked the same). A share keeps no such plan's climb.
  function climbNone(P) {
    const c = P && P.climb;
    if (!c || c.usable || climbs(P).length || !["few", "weak"].includes(c.reason)) return "";
    const cv = c.cv || {};
    return `<span class="climb none">🧗 ${esc(T("climb." + c.reason))}${help(esc(T("climb." + c.reason + "_tip",
      { acts: ok(cv.n_acts) ? cv.n_acts : 0, pos: ok(cv.n_pos) ? cv.n_pos : 0 })))}</span>`;
  }

  // SP-870: the stretches you ran before (位置記憶, SP-726: chart_rows' `memory`, its passes' km0–km1) —
  // per segment the passes' stretches merged, clipped to the segment ({a, b, i, applied}); the whole
  // segment when its passes carry no km. Drawn only when the page asks (o.memory: the calculator — the
  // share page shows no one's history)
  function memSpans(P) {
    const out = [];
    for (const r of rowsOf(P)) {
      const m = r.memory;
      if (!m) continue;
      const iv = (m.passes || []).filter((p) => ok(p.km0) && ok(p.km1))
        .map((p) => [Math.max(r.start_km, p.km0), Math.min(r.end_km, p.km1)]).filter(([a, b]) => b > a)
        .sort((x, y) => x[0] - y[0]);
      const merged = [];
      for (const [a, b] of iv.length ? iv : [[r.start_km, r.end_km]]) {
        const last = merged[merged.length - 1];
        if (last && a <= last[1] + 1e-6) last[1] = Math.max(last[1], b); else merged.push([a, b]);
      }
      for (const [a, b] of merged) out.push({ a, b, i: r.i, applied: !!m.applied });
    }
    return out;
  }
  const memLegend = (P) => {
    const sp = memSpans(P);
    if (!sp.length) return "";
    return `<span class="memk"><i style="background:${css("--s7")};opacity:.4"></i>📍 ${esc(T("mem.legend"))}${help(esc(T(sp.some((s) => !s.applied) ? "mem.legend_tip_off" : "mem.legend_tip")))}</span>`;
  };

  // the hover text of one segment; `more(r)`: the page's own lines under it (the calculator: the group's leg)
  function rowTip(P, r, more) {
    const hike = P.type === "baiyue";
    const lines = [`<b>${r.mark} ${esc(r.label)}</b>${r.walk ? ` <span class="badge watch">${esc(T("walk"))}</span>` : r.gait === "either" ? ` <span class="badge">${esc(T("gait.either"))}</span>` : ""}`,
      `${f1(r.start_km)}–${f1(r.end_km)} km · ${f1((r.grade || 0) * 100)}% · ↑${n0(r.gain_m)} ↓${n0(r.loss_m)} m`,
      `${T("m.pace")} ${fmtRow(r, "pace")}${hike ? "" : ` · ${T("m.power")} ${fmtRow(r, "power", true)}`} · ${T("m.hr")} ${fmtRow(r, "hr", true)}`,
      `${T("split")} ${dur(r.t)} · ${T("cum")} ${dur(r.cum_s)}${r.eta ? " · ETA " + esc(r.eta) : ""}${ok(r.temp_c) ? ` · ${f1(r.temp_c)} °C` : ""}`];
    if (r.basis === "safe") lines.push(esc(T("safe")));
    if (r.fuel) lines.push("🍬 " + esc(r.fuel));
    if (r.night) lines.push("🌙 " + esc(T("night.col")));
    for (const c of climbIn(P, r)) lines.push(esc(T("climb.row", { a: f1(c.start_km), b: f1(c.end_km) })));
    if (more) lines.push(...more(r));
    return lines.join("<br>");
  }

  // The chart's echarts option.
  //   measure       pace | power | hr
  //   sel, hover    the selected segments (a Set of chart_rows' i) and the hovered one (shaded)
  //   touch, narrow a touch screen (one finger scrolls the page) / a phone-wide window
  //   before        series drawn under everything (the calculator's group legs)
  //   elevMarkLine  the elevation series' markLine (the calculator's day ends)
  //   stops         [{km, icon, name}]: aid stations pinned on the profile
  //   more          extra hover lines per row (rowTip)
  function option(P, o = {}) {
    const pr = P.profile, R = rowsOf(P), m = o.measure || "pace", M = MEASURE[m];
    const sel = o.sel || new Set(), TOUCH = !!o.touch;
    // the target as steps; a measure that is not a target on a segment leaves a gap there
    const step = [];
    for (const r of R) {
      const v = M.val(r);
      if (v == null) { step.push([r.start_km, null]); continue; }
      step.push([r.start_km, v], [r.end_km, v]);
    }
    const vals = R.map(M.val).filter(ok);
    const km = P.summary.km || R[R.length - 1].end_km;
    // SP-254: segments run in the dark on a dark background (behind the walk / selection shading)
    const walkAreas = R.filter((r) => r.night).map((r) => [{ xAxis: r.start_km, itemStyle: { color: css("--muted"), opacity: 0.16 } }, { xAxis: r.end_km }]);
    walkAreas.push(...R.filter((r) => r.walk).map((r) => [{ xAxis: r.start_km, itemStyle: { color: css("--watch"), opacity: 0.10 } }, { xAxis: r.end_km }]));
    for (const r of R.filter((x) => sel.has(x.i)))
      walkAreas.push([{ xAxis: r.start_km, itemStyle: { color: css("--accent"), opacity: 0.16 } }, { xAxis: r.end_km }]);
    const hv = R.find((r) => r.i === o.hover && !sel.has(r.i));
    if (hv) walkAreas.push([{ xAxis: hv.start_km, itemStyle: { color: css("--accent"), opacity: 0.28 } }, { xAxis: hv.end_km }]);
    const series = [...(o.before || [])];
    // SP-728: the 攀爬段, red under the elevation, a 🧗 on top of each
    const CL = climbs(P);
    if (CL.length) series.push({ name: T("climb.legend"), type: "line", yAxisIndex: 1, data: [], silent: true, z: 0,
      markArea: { silent: true, data: CL.map((c) => [{ xAxis: c.start_km, itemStyle: { color: css("--bad"), opacity: 0.22 },
        label: { show: true, position: "insideTop", formatter: "🧗", fontSize: 12 } }, { xAxis: c.end_km }]) } });
    // SP-870: the stretches you ran before, purple under the elevation with a 📍 (dashed: only marked —
    // the back-test has not switched place memory on, the time is the model's)
    const MS = o.memory ? memSpans(P) : [];
    if (MS.length) series.push({ name: T("mem.legend"), type: "line", yAxisIndex: 1, data: [], silent: true, z: 0,
      markArea: { silent: true, data: MS.map((s) => [{ xAxis: s.a, itemStyle: { color: css("--s7"), opacity: s.applied ? 0.2 : 0.1,
        borderColor: css("--s7"), borderWidth: s.applied ? 0 : 1, borderType: "dashed" },
        label: { show: true, position: "insideBottom", formatter: "📍", fontSize: 11 } }, { xAxis: s.b }]) } });
    if (pr) {
      series.push({ name: T("elev"), type: "line", yAxisIndex: 1, data: pr.km.map((x, i) => [x, pr.z[i]]), symbol: "none", silent: true, z: 1,
        lineStyle: { color: css("--bar"), width: 1 }, areaStyle: { color: css("--bar"), opacity: 0.28 }, markLine: o.elevMarkLine });
      const st = o.stops || [];
      if (st.length) {
        const zAt = (k) => { const i = pr.km.findIndex((x) => x >= k); return i <= 0 ? pr.z[Math.max(0, i)] : pr.z[i - 1] + (pr.z[i] - pr.z[i - 1]) * (k - pr.km[i - 1]) / ((pr.km[i] - pr.km[i - 1]) || 1); };
        series.push({ name: T("stops"), type: "scatter", yAxisIndex: 1, data: st.map((s) => ({ value: [s.km, zAt(s.km)], name: s.name })),
          symbol: "pin", symbolSize: 24, itemStyle: { color: css("--s3") }, z: 5,
          label: { show: true, formatter: (p) => st[p.dataIndex].icon, fontSize: 10, offset: [0, -3] },
          tooltip: { trigger: "item", formatter: (p) => esc(p.name) } });
      }
    }
    series.push({ name: T("m." + m), type: "line", yAxisIndex: 0, data: step, symbol: "none", connectNulls: false, z: 3,
      lineStyle: { color: css("--accent"), width: 2.4 }, markArea: { silent: true, data: walkAreas } });
    // at least ± 30 s/km, ± 10 % W, ± 15 bpm around the targets: a flat cap is not drawn as a big swing
    const span = vals.length ? Math.max(...vals) - Math.min(...vals) : 0, mid = vals.length ? Math.max(...vals) : 0;
    const pad = Math.max(span * 0.15, m === "pace" ? 30 : m === "power" ? mid * 0.10 : 15);
    // SP-458: an inverted axis (pace) ends at the bottom — its name 「/km」 sat on the km axis's 「0」; the name at the top
    const yMain = { type: "value", name: M.unit, nameLocation: M.inverse ? "start" : "end", scale: true, inverse: M.inverse, ...axisStyle(),
      min: vals.length ? Math.floor(Math.min(...vals) - pad) : null, max: vals.length ? Math.ceil(Math.max(...vals) + pad) : null,
      axisLabel: { color: css("--muted"), fontSize: 11, formatter: M.axis } };
    const yElev = { type: "value", show: false, scale: true, min: pr ? Math.min(...pr.z) - (Math.max(...pr.z) - Math.min(...pr.z) || 50) * 0.1 : null };
    const tick = km <= 12 ? 1 : km <= 30 ? 2 : km <= 60 ? 5 : 10;
    return {
      animation: false, grid: { left: 52, right: 14, top: 32, bottom: o.narrow && !TOUCH ? 30 : 52 },
      tooltip: { trigger: "axis", confine: true, axisPointer: { type: "line" },
        formatter: (ps) => { const k = ps[0]?.axisValue ?? ps[0]?.value?.[0]; const r = rowAt(P, +k); return r ? rowTip(P, r, o.more) : `${f1(+k)} km`; } },
      xAxis: { type: "value", name: "km", min: 0, max: Math.ceil(km * 10) / 10, interval: tick, ...axisStyle(),
        axisLabel: { color: css("--muted"), fontSize: 11, formatter: (v) => Math.round(v), showMaxLabel: false } },
      yAxis: [yMain, yElev],
      // SP-380: on a touch screen a one-finger drag over the chart panned it and the page didn't
      // scroll (inside dataZoom cancels the touch) — there the finger scrolls the page, two fingers
      // zoom, and the slider underneath pans
      dataZoom: TOUCH ? [{ type: "inside", moveOnMouseMove: false, preventDefaultMouseMove: false },
                           { type: "slider", height: 14, bottom: 4 }]
        : [{ type: "inside" }, { type: "slider", height: 14, bottom: 6 }],
      series,
    };
  }

  // the chart's legend; `more`: the page's own items after it (the calculator: the group's tiers)
  function legend(P, o = {}) {
    const pr = P.profile, R = rowsOf(P), m = o.measure || "pace", M = MEASURE[m], hike = P.type === "baiyue";
    return `<span><i style="background:${css("--accent")}"></i>${esc(T("legend.target", { m: T("m." + m) }))}</span>` +
      (pr ? `<span><i style="background:${css("--bar")};opacity:.6"></i>${esc(T("elev"))}</span>` : "") +
      (R.some((r) => r.walk) ? `<span><i style="background:${css("--watch")};opacity:.35"></i>${esc(T("legend.walk"))}</span>` : "") +
      (R.some((r) => r.night) ? `<span><i style="background:${css("--muted")};opacity:.35"></i>${esc(T("legend.night"))}</span>` : "") +
      (m !== "pace" && R.some((r) => M.val(r) == null) ? `<span>${esc(T(m === "power" ? "legend.gap_power" : "legend.gap_hr"))}</span>` : "") +
      // SP-224: one HR cap for the whole race; late in a trail race HR drops by itself (Kerhervé 2015)
      (m === "hr" && P.type === "trail" ? `<span>${esc(T("legend.hr_fade"))}</span>` : "") +
      (P.summary.runwalk && !hike ? `<span class="faint">${esc(T(P.summary.runwalk.personal ? "rw.legend_you" : "rw.legend_default"))}</span>` : "") +
      // SP-728: how often the marked stretches really were scrambles (your own runs, grouped cross-validation)
      (climbs(P).length ? `<span class="climb"><i style="background:${css("--bad")};opacity:.35"></i>🧗 ${esc(T("climb.legend"))}${help(esc(climbTip(P)))}</span>` : "") +
      climbNone(P) +                                                    // SP-872
      (o.memory ? memLegend(P) : "") +                                  // SP-870
      (o.more || "");
  }

  // The compact table under the chart: each segment's target pace / power / HR, split and fuelling.
  //   measure, sel   as option()
  //   cell(r)        the page's own marks after the fuelling (the calculator: 會用登山杖, 位置記憶)
  function table(P, o = {}) {
    const R = rowsOf(P);
    if (!R.length) return "";
    const hike = P.type === "baiyue", m = o.measure || "pace", sel = o.sel || new Set();
    const nightCol = !!P.night;           // SP-254: only with a start time and sunrise / sunset
    const th = (k, lab) => `<th class="n opt ${m === k ? "cur" : ""}">${esc(lab)}</th>`;
    const td = (k, r) => `<td class="n opt ${m === k ? "cur" : ""}">${fmtRow(r, k)}</td>`;
    // SP-485: 「看 30 秒平均功率」 (RC26) is the power column's ? instead of a line under the table
    const thp = hike ? "" : th("power", T("m.power")).replace("</th>", `${help(esc(T("p30.tip")))}</th>`);
    let h = `<table><thead><tr><th>#</th><th class="n">km</th><th class="n">${esc(T("grade"))}</th>${th("pace", T("m.pace"))}${thp}${th("hr", T("m.hr"))}` +
      `<th class="n">${esc(T("split"))}</th><th class="n">${esc(T("cum"))}</th>${nightCol ? `<th>${esc(T("night.col"))}</th>` : ""}<th>${esc(T("fuel"))}</th></tr></thead><tbody>`;
    for (const r of R) {
      h += `<tr data-row="${r.i}" class="${sel.has(r.i) ? "hl" : ""}" title="${esc(r.label)}${r.hr_cap_src ? " · " + esc(T("hrcap_src", { src: r.hr_cap_src })) : ""}">
        <td>${r.mark}${hike && r.day ? ` <span class="faint">D${r.day}</span>` : ""}</td><td class="n">${f1(r.start_km)}–${f1(r.end_km)}</td>
        <td class="n ${r.walk ? "walk" : ""}">${f1((r.grade || 0) * 100)}%${r.walk ? " " + esc(T("walk")) : r.gait === "either" ? " " + esc(T("gait.either")) : ""}${climbIn(P, r).length ? ` <span class="climb" title="${esc(climbIn(P, r).map((c) => T("climb.row", { a: f1(c.start_km), b: f1(c.end_km) })).join("\n"))}">🧗</span>` : ""}</td>${td("pace", r)}${hike ? "" : td("power", r)}${td("hr", r)}
        <td class="n">${dur(r.t)}</td><td class="n">${dur(r.cum_s)}${r.eta ? ` <span class="faint">${esc(r.eta)}</span>` : ""}</td>
        ${nightCol ? `<td>${r.night ? "🌙" : ""}</td>` : ""}<td class="fuel">${esc(r.fuel || "")}${o.cell ? o.cell(r) : ""}</td></tr>`;
    }
    const s = P.summary;
    h += `<tr class="center"><td colspan="2">${esc(T("total"))}</td><td></td><td class="n opt ${m === "pace" ? "cur" : ""}">${pace(s.pace_s_per_km || (s.time_s / s.km))}</td>` +
      `${hike ? "" : `<td class="n opt ${m === "power" ? "cur" : ""}">${W(s.power)}</td>`}<td class="opt ${m === "hr" ? "cur" : ""}"></td><td class="n">${hms(s.time_s)}</td><td></td>${nightCol ? "<td></td>" : ""}<td></td></tr></tbody></table>`;
    return h;
  }

  // the measure buttons (the calculator writes them in its markup; the share page asks for them)
  const buttons = (P, m) => measures(P).map((k) => `<button data-m="${k}" class="${k === m ? "on" : ""}">${esc(T("m." + k))}</button>`).join("");

  // The look of the chart head, the legend, the table and the 「參考」 cells — the one place for both pages.
  // Scoped to .segp (the section holding the component) so it never restyles the rest of a page.
  const STYLE = `
.segp .chead { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; margin: 0 0 4px; }
.segp .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11.5px; color: var(--muted); }
.segp .legend i { display: inline-block; width: 12px; height: 8px; border-radius: 2px; margin-right: 4px; vertical-align: 0; }
.segp .chead .seg { display: inline-flex; flex-wrap: wrap; }
.segp .chead .seg button { font: inherit; font-size: 13px; border: 1px solid var(--line); background: var(--panel); color: var(--text);
  padding: 3px 10px; cursor: pointer; min-height: 30px; margin-left: -1px; }
.segp .chead .seg button:first-child { border-radius: 6px 0 0 6px; margin-left: 0; }
.segp .chead .seg button:last-child { border-radius: 0 6px 6px 0; }
.segp .chead .seg button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.segp .chead .seg button.hidden { display: none; }
.segp .profile { width: 100%; height: 320px; }
.segp .faint { color: var(--faint); }
.segp .ref { color: var(--faint); cursor: help; }
.segp .badge.watch { color: var(--watch); border-color: var(--watch); }
.segp .cmp { max-height: 440px; overflow: auto; }
.segp .cmp table th { position: sticky; top: 0; background: var(--panel); z-index: 1; }
.segp .cmp td.cur, .segp .cmp th.cur { font-weight: 650; color: var(--accent); }
.segp .cmp td.walk { color: var(--watch); }
.segp .cmp tr { cursor: pointer; }
.segp tr.hl td { background: var(--soft); }
.segp tr.center td { background: var(--soft); font-weight: 600; }
.segp .cmp td.fuel { white-space: normal; min-width: 160px; font-size: 12px; color: var(--muted); }
.segp .pole { display: block; font-size: 12px; color: var(--muted); }      /* SP-244 the trekking-pole hint */
.segp .mem { white-space: nowrap; font-size: 12px; color: var(--accent); cursor: help; }   /* SP-726 the place memory */
.segp .mem.off { color: var(--faint); }
.segp .climb { cursor: help; }                                       /* SP-728 the scramble stretches */
.segp .legend .climb { color: var(--bad); }
.segp .legend .climb.none { color: var(--muted); }                      /* SP-872 not enough records yet */
.segp .help { display: inline-grid; place-items: center; width: 17px; height: 17px; border-radius: 50%; border: 1px solid var(--line);
  font-size: 11px; color: var(--muted); cursor: help; position: relative; }
.segp .help:hover .tip, .segp .help:focus .tip { display: block; }
.segp .tip { display: none; position: absolute; top: 20px; left: -8px; z-index: 5; width: min(320px, 80vw); background: var(--panel);
  border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; font-size: 12px; color: var(--text); line-height: 1.5;
  box-shadow: 0 4px 16px rgba(0,0,0,.15); white-space: normal; font-weight: 400; }
@media (max-width: 700px) {
  .segp .cmp .opt:not(.cur) { display: none; }
  .segp .profile { height: 220px; }
  .segp .chead .seg button { min-height: 40px; }
}`;
  function style() {
    if (document.getElementById("segp-style")) return;
    const el = document.createElement("style");
    el.id = "segp-style";
    el.textContent = STYLE;
    document.head.appendChild(el);
  }
  style();

  window.SegProfile = { MEASURE, measures, rowsOf, rowAt, fmtRow, rowTip, option, legend, table, buttons, climbs, memSpans, STYLE };
})();
