// mFTP 與 VO2max 趨勢 (views kind "pdtrend" → backend/engine/panels/pd_trend.py, SP-466). Self-contained:
// loaded by wko5_viewer.html, which calls drawPdTrend(box, res); uses the viewer's globals (esc, css,
// echarts, charts, t, pzDark). Two lines on a time axis, two y axes: mFTP (W, left, blue circles) and
// WKO5's VO2max (ml/kg/min, right, orange diamonds) — palette slots 1 / 2 like the pole chart, shape too,
// never colour alone. Each point is one PD fit of the 課表 (never refitted here); no body weight →
// the VO2max line is left out and the card says why, with a link to the personal settings.
(function () {
  const colors = () => (window.pzDark && pzDark())
    ? { mftp: "#3987e5", vo2: "#d95926" } : { mftp: "#2a78d6", vo2: "#eb6834" };

  function style() {
    if (document.getElementById("pdt-style")) return;
    const st = document.createElement("style");
    st.id = "pdt-style";
    st.textContent = `.pdtsum { font-size: 12px; color: var(--muted); margin: 0 0 6px; }
.pdtsum b { color: var(--text); font-weight: 600; }
.pdtnote { font-size: 11.5px; color: var(--text); margin: 0 0 6px; padding: 4px 8px; border-left: 3px solid var(--line); }
.plot.pdt { height: 260px; }
.pdtleg { display: flex; flex-wrap: wrap; gap: 3px 14px; font-size: 11px; color: var(--muted); margin: 0 0 2px; }
.pdtleg span { white-space: nowrap; display: inline-flex; align-items: center; gap: 5px; }
.pdtleg svg { width: 10px; height: 10px; }
details.pdtdet { margin-top: 6px; } details.pdtdet > summary { font-size: 12px; cursor: pointer; }
details.pdtdet table { margin-top: 4px; font-size: 11.5px; }`;
    document.head.appendChild(st);
  }

  function help(card, res) {
    if (!res.description) return;
    if (res._zoom) { card.insertAdjacentHTML("beforeend", `<p class="desc">${esc(res.description)}</p>`); return; }
    const h2 = card.querySelector("h2");
    const q = `<button type="button" class="qtip" aria-label="${esc(t("viewer.pdtrend.help"))}" data-tip="${esc(res.description)}">?</button>`;
    const zb = h2?.querySelector(".zbtn");
    if (zb) zb.insertAdjacentHTML("beforebegin", q); else h2?.insertAdjacentHTML("beforeend", q);
  }

  const num = (x, d) => x == null ? "—" : Number(x).toFixed(d);

  function legendHtml(c, vo2) {
    const dot = `<svg viewBox="0 0 10 10" aria-hidden="true"><circle cx="5" cy="5" r="4.5" fill="${c.mftp}"/></svg>`;
    const dia = `<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M5 0 L10 5 L5 10 L0 5 Z" fill="${c.vo2}"/></svg>`;
    return `<p class="pdtleg"><span>${dot}${esc(t("viewer.pdtrend.mftp_axis"))}</span>`
      + (vo2 ? `<span>${dia}${esc(t("viewer.pdtrend.vo2_axis"))}</span>` : "") + `</p>`;
  }

  function tip(p) {
    const lines = [`<b>${esc(p.date)}</b>`,
      `${esc(t("viewer.pdtrend.mftp"))} <b>${num(p.mftp, 0)} W</b>`,
      p.vo2max_kg == null ? esc(t("viewer.pdtrend.tip_no_vo2"))
        : `VO2max <b>${num(p.vo2max_kg, 1)}</b> ml/kg/min (${num(p.vo2max_l, 2)} L/min)`,
      `W′ ${num(p.frc_kj, 1)} kJ`,
      p.weight == null ? esc(t("viewer.pdtrend.tip_no_weight")) : esc(t("viewer.pdtrend.tip_weight", { kg: num(p.weight, 1) })),
      p.runs == null ? "" : esc(t("viewer.pdtrend.tip_runs", { n: p.runs }))];
    return lines.filter(Boolean).join("<br>");
  }

  function plot(card, res) {
    const c = colors(), muted = css("--muted"), line = css("--line"), panel = css("--panel");
    const pts = res.points, vo2 = pts.some((p) => p.vo2max_kg != null);
    card.insertAdjacentHTML("beforeend", legendHtml(c, vo2));
    const el = document.createElement("div");
    el.className = "plot pdt";
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", t("viewer.pdtrend.aria"));
    card.appendChild(el);
    const chart = echarts.init(el);
    charts.push(chart);
    // whole-number bounds a little outside the data: two fits 0.3 W apart must not print 202 / 202 / 202
    const bounds = (vals, pad) => {
      const v = vals.filter((x) => x != null);
      return v.length ? { min: Math.floor(Math.min(...v)) - pad, max: Math.ceil(Math.max(...v)) + pad } : {};
    };
    const yAxis = (name, pos, d) => ({ type: "value", name, position: pos,
      ...(d ? bounds(pts.map((p) => p.vo2max_kg), 1) : bounds(pts.map((p) => p.mftp), 3)),
      nameTextStyle: { color: muted, fontSize: 10 }, axisLabel: { color: muted, fontSize: 10, formatter: (v) => num(v, d) },
      splitLine: { show: pos === "left", lineStyle: { color: line } } });
    const day = (s) => new Date(s + "T00:00:00").getTime();
    chart.setOption({
      grid: { left: 46, right: vo2 ? 46 : 16, top: 26, bottom: 28 },
      tooltip: { trigger: "axis", confine: true, backgroundColor: panel, borderColor: line,
        textStyle: { color: css("--text"), fontSize: 12 }, formatter: (ps) => tip(pts[ps[0].dataIndex]) },
      legend: { show: false },
      xAxis: { type: "time", min: day(res.x_min), max: day(res.x_max),
        axisLabel: { color: muted, fontSize: 10, hideOverlap: true }, axisLine: { lineStyle: { color: line } } },
      yAxis: vo2 ? [yAxis("W", "left", 0), yAxis("ml/kg/min", "right", 1)] : [yAxis("W", "left", 0)],
      series: [
        { name: t("viewer.pdtrend.mftp"), type: "line", yAxisIndex: 0, color: c.mftp, symbol: "circle", symbolSize: 8,
          lineStyle: { width: 2 }, itemStyle: { color: c.mftp, borderColor: panel, borderWidth: 1.5 },
          data: pts.map((p) => [day(p.date), p.mftp]) },
        ...(vo2 ? [{ name: "VO2max", type: "line", yAxisIndex: 1, color: c.vo2, symbol: "diamond", symbolSize: 10,
          lineStyle: { width: 2 }, itemStyle: { color: c.vo2, borderColor: panel, borderWidth: 1.5 }, connectNulls: false,
          data: pts.map((p) => [day(p.date), p.vo2max_kg]) }] : []),
      ],
    });
    if (window.ResizeObserver) new ResizeObserver(() => { if (!chart.isDisposed()) chart.resize(); }).observe(el);
  }

  function table(card, res) {
    const rows = [...res.points].reverse().map((p) => `<tr><td>${esc(p.date)}</td><td class="num">${num(p.mftp, 1)}</td>`
      + `<td class="num">${num(p.vo2max_kg, 1)}</td><td class="num">${num(p.frc_kj, 1)}</td>`
      + `<td class="num">${num(p.weight, 1)}</td><td class="num">${p.runs ?? "—"}</td></tr>`);
    card.insertAdjacentHTML("beforeend", `<details class="pdtdet"><summary>${esc(t("viewer.pdtrend.table"))}</summary><table><thead><tr>
      <th>${esc(t("viewer.pdtrend.col_date"))}</th><th class="num">${esc(t("viewer.pdtrend.col_mftp"))}</th><th class="num">VO2max (ml/kg/min)</th>
      <th class="num">W′ (kJ)</th><th class="num">${esc(t("viewer.pdtrend.col_weight"))}</th><th class="num">${esc(t("viewer.pdtrend.col_runs"))}</th></tr></thead>
      <tbody>${rows.join("")}</tbody></table></details>`);
  }

  window.drawPdTrend = function (card, res) {
    style();
    card.querySelector(".loading")?.remove();
    help(card, res);
    if (res.empty || !(res.points || []).length) {
      card.insertAdjacentHTML("beforeend", `<p class="nodata">${esc(res.empty || t("viewer.pdtrend.none"))}</p>`);
      return;
    }
    const L = res.latest;
    const vo2 = L.vo2max_kg == null ? esc(t("viewer.pdtrend.no_vo2"))
      : t("viewer.pdtrend.latest_vo2", { v: `<b>${num(L.vo2max_kg, 1)}</b>`, kg: num(L.weight, 1) });
    card.insertAdjacentHTML("beforeend", `<p class="pdtsum">${t("viewer.pdtrend.latest", {
      date: esc(L.date), w: `<b>${num(L.mftp, 0)} W</b>` })} · ${vo2}</p>`);
    if (res.note) {
      const link = res.no_weight ? ` <a href="/api/v1/wko5/settings#personal">${esc(t("viewer.pdtrend.weight_link"))}</a>` : "";
      card.insertAdjacentHTML("beforeend", `<p class="pdtnote">${esc(res.note)}${link}</p>`);
    }
    plot(card, res);
    table(card, res);
    if (res.method) card.insertAdjacentHTML("beforeend", `<p class="meta">${esc(res.method)}</p>`);
  };
})();
