// 越野穩定爬坡 Pw:HR (views kind "climbpwhr" → backend/engine/panels/climb_pwhr.py). Self-contained:
// loaded by wko5_viewer.html, which calls cpwQuery(c) when it fetches the card and drawClimbPwhr(box, res)
// to draw it; uses the viewer's globals (esc, css, fmtDur, echarts, charts, renderCard, loadToken, ZOOM,
// fillZoom, pzDark). One route at a time (like for like): each steady-climb segment is a dot (categorical
// slot 1, blue), the 8-week rolling median a line (slot 2, orange) — two identities, a legend, one y-axis.
// 原始／熱調整 switches the y value client-side (both come in the JSON). Hover = per-dot tooltip; a table view
// sits under the chart. With no comparable segment, the card shows how close each trail run came instead.
(function () {
  const KEY = "wko5viewer.climbpwhr";
  const prefs = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } };
  const save = (patch) => { try { localStorage.setItem(KEY, JSON.stringify({ ...prefs(), ...patch })); } catch {} };
  const colors = () => (window.pzDark && pzDark())
    ? { dot: "#3987e5", med: "#d95926" } : { dot: "#2a78d6", med: "#eb6834" };   // palette slots 1, 2 (validated)

  window.cpwQuery = function () {
    const r = prefs().route;
    return r ? `&route=${encodeURIComponent(r)}` : "";
  };

  function style() {
    if (document.getElementById("cpw-style")) return;
    const st = document.createElement("style");
    st.id = "cpw-style";
    st.textContent = `.cpwbar { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; margin: 2px 0 6px; font-size: 12px; }
.cpwbar select { font-size: 12px; max-width: 260px; }
.cpwbar .seg { font-size: 11.5px; padding: 1px; }
.cpwbar .seg button { padding: 1px 9px; font-weight: 500; }
.cpwsum { font-size: 11.5px; color: var(--muted); margin: 0 0 6px; }
.cpwsum b { color: var(--text); font-weight: 600; }
.plot.cpw { height: 300px; }
.cpwleg { display: flex; flex-wrap: wrap; gap: 3px 14px; font-size: 11px; color: var(--muted); margin: 0 0 2px; }
.cpwleg span { white-space: nowrap; }
.cpwleg i { display: inline-block; margin-right: 5px; vertical-align: middle; }
.cpwleg i.dot { width: 9px; height: 9px; border-radius: 50%; }
.cpwleg i.ln { width: 16px; height: 0; border-top: 2px solid; }
.cpwleg i.dash { width: 16px; height: 0; border-top: 1px dashed var(--muted); background: none !important; }
details.cpwdet { margin-top: 6px; } details.cpwdet > summary { font-size: 12px; cursor: pointer; }
details.cpwdet table { margin-top: 4px; font-size: 11.5px; }`;
    document.head.appendChild(st);
  }

  function rerender(card) {
    if (!card._ctx) return;
    const { v, di, c, q } = card._ctx;
    card.insertAdjacentHTML("beforeend", `<div class="loading">計算中…</div>`);
    renderCard(card, v, di, c, q, loadToken).then(() => { if (ZOOM.card === card) fillZoom(); });
  }

  function help(card, res) {
    if (!res.description) return;
    if (res._zoom) { card.insertAdjacentHTML("beforeend", `<p class="desc">${esc(res.description)}</p>`); return; }
    const h2 = card.querySelector("h2");
    const q = `<button type="button" class="qtip" aria-label="說明" data-tip="${esc(res.description)}">?</button>`;
    const zb = h2?.querySelector(".zbtn");
    if (zb) zb.insertAdjacentHTML("beforebegin", q); else h2?.insertAdjacentHTML("beforeend", q);
  }

  const f2 = (x) => x == null ? "—" : x.toFixed(2);
  const mmss = (s) => s == null ? "—" : `${(s / 60).toFixed(1)} 分`;

  function plot(card, option) {
    const el = document.createElement("div");
    el.className = "plot cpw";
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
    card.insertAdjacentHTML("beforeend", `<p class="cpwleg">${items.map(([kind, color, text]) =>
      `<span><i class="${kind}" style="${kind === "ln" ? `border-top-color:${color}` : `background:${color}`}"></i>${esc(text)}</span>`).join("")}</p>`);
  }

  // nothing comparable: each trail run's longest +3…+8 % running climb against the 10-min bar
  function drawNearMiss(card, res) {
    const rows = (res.runs_longest || []).filter((r) => r.longest_s != null);
    if (!rows.length) return;
    const c = colors(), b = axisBase(), bar = (res.min_seg_s || 600) / 60;
    legend(card, [["dot", c.dot, "每次越野跑最長的一段"], ["dash", "", `${bar} 分鐘門檻`]]);
    plot(card, {
      grid: b.grid, xAxis: b.xAxis, tooltip: { ...b.tooltip,
        formatter: (p) => `${esc(p.data.date)}<br>最長一段：<b>${(p.value[1]).toFixed(1)} 分鐘</b>` },
      legend: b.legend,
      yAxis: { type: "value", min: 0, max: Math.max(bar * 1.2, ...rows.map((r) => r.longest_s / 60)) ,
        name: "分鐘", nameTextStyle: { color: b.muted, fontSize: 10 },
        axisLabel: { color: b.muted, fontSize: 10 }, splitLine: { lineStyle: { color: b.line } } },
      series: [{ name: "每次越野跑最長的一段", type: "scatter", symbolSize: 9, color: c.dot,
        itemStyle: { borderColor: css("--panel"), borderWidth: 2 },
        data: rows.map((r) => ({ value: [r.date, r.longest_s / 60], date: r.date })),
        markLine: { silent: true, symbol: "none", lineStyle: { color: b.muted, type: "dashed", width: 1 },
          label: { show: false },
          data: [{ yAxis: bar }] } }],
    });
  }

  window.drawClimbPwhr = function (card, res) {
    style();
    card.querySelector(".loading")?.remove();
    help(card, res);
    const p = prefs(), heat = p.basis === "heat";
    const routes = res.routes || [];
    if (routes.length) {
      const bar = document.createElement("div");
      bar.className = "cpwbar";
      bar.innerHTML = `<label>路線 <select aria-label="路線">${routes.map((r) =>
        `<option value="${esc(r.id)}"${r.id === res.route?.id ? " selected" : ""}>${esc(r.name)}（${r.runs} 次・${r.segments} 段）</option>`).join("")}</select></label>`
        + `<div class="seg" role="group" aria-label="心率用原始或熱調整"><button type="button" data-v="raw" class="${heat ? "" : "on"}" aria-pressed="${!heat}">原始</button><button type="button" data-v="heat" class="${heat ? "on" : ""}" aria-pressed="${heat}">熱調整（推估）</button></div>`;
      card.appendChild(bar);
      bar.querySelector("select").onchange = (e) => { save({ route: e.target.value }); rerender(card); };
      bar.querySelectorAll(".seg button").forEach((btn) => btn.onclick = () => {
        if (btn.classList.contains("on")) return;
        save({ basis: btn.dataset.v });
        card.querySelectorAll(".cpwbar, .cpwsum, .cpwleg, .plot.cpw, details.cpwdet, .nodata").forEach((el) => {
          if (el.classList.contains("plot")) echarts.getInstanceByDom(el)?.dispose();
          el.remove();
        });
        card.querySelector(".qtip")?.remove();
        window.drawClimbPwhr(card, res);
      });
    }
    if (res.empty || !res.points?.length) {
      card.insertAdjacentHTML("beforeend", `<p class="nodata">${esc(res.empty || "沒有資料")}</p>`);
      drawNearMiss(card, res);
      return;
    }
    const pts = res.points, med = heat ? res.median_adj : res.median;
    const y = (q) => heat ? q.pwhr_adj : q.pwhr;
    const shown = pts.filter((q) => y(q) != null);
    const last = [...med].reverse().find((m) => m != null);
    card.insertAdjacentHTML("beforeend", `<p class="cpwsum"><b>${shown.length}</b> 段・<b>${new Set(shown.map((q) => q.file)).size}</b> 次`
      + `；最近 8 週中位數 <b>${f2(last)} W/bpm</b>${heat ? `（熱調整推估；${pts.length - shown.length} 段沒有天氣資料不畫）` : ""}</p>`);
    const c = colors(), b = axisBase();
    const tip = (q) => `${esc(q.start.replace("T", " "))}（第 ${q.at_min} 分起）<br>`
      + `Pw:HR <b>${f2(y(q))} W/bpm</b>${heat ? "（熱調整）" : ""}<br>`
      + `${q.power} W ÷ ${heat ? q.hr_adj : q.hr} bpm${heat ? `（實測 ${q.hr}）` : ""}<br>`
      + `坡度 ${(q.grade * 100).toFixed(1)}%・${mmss(q.duration_s)}（算 ${mmss(q.measured_s)}）・VI ${q.vi}<br>`
      + `心率區：${esc(q.zone || "—")}・${q.temp_c != null ? `${q.temp_c} °C・Hadley ${q.hadley}` : "沒有天氣資料"}`;
    const all = shown.map(y).concat(med.filter((m) => m != null));
    const lo = Math.min(...all), hi = Math.max(...all), pad = Math.max(0.02, (hi - lo) * 0.15);
    legend(card, [["dot", c.dot, "每段爬坡"], ["ln", c.med, "近 8 週中位數"]]);
    plot(card, {
      grid: b.grid, xAxis: b.xAxis,
      tooltip: { ...b.tooltip, formatter: (p) => p.seriesIndex === 0 ? tip(p.data.q)
        : `${esc(p.data.q.date)}<br>近 8 週中位數 <b>${f2(p.value[1])} W/bpm</b>` },
      legend: b.legend,
      yAxis: { type: "value", name: "W/bpm", min: Math.max(0, Math.floor((lo - pad) * 20) / 20), max: Math.ceil((hi + pad) * 20) / 20,
        nameTextStyle: { color: b.muted, fontSize: 10 }, axisLabel: { color: b.muted, fontSize: 10, formatter: (v) => v.toFixed(2) },
        splitLine: { lineStyle: { color: b.line } } },
      series: [
        { name: "每段爬坡", type: "scatter", symbolSize: 9, color: c.dot, z: 3,
          itemStyle: { borderColor: css("--panel"), borderWidth: 2 },
          data: shown.map((q) => ({ value: [q.start, y(q)], q })) },
        { name: "近 8 週中位數", type: "line", color: c.med, lineStyle: { width: 2 }, symbol: "circle", symbolSize: 4,
          showSymbol: pts.length <= 40, connectNulls: true, z: 2,
          data: pts.map((q, i) => med[i] == null ? null : { value: [q.start, med[i]], q }).filter(Boolean) },
      ],
    });
    card.insertAdjacentHTML("beforeend", `<details class="cpwdet"><summary>表格（${pts.length} 段）</summary><table><thead><tr>
      <th>日期</th><th class="num">Pw:HR</th><th class="num">熱調整</th><th class="num">W</th><th class="num">bpm</th><th class="num">坡度</th><th class="num">長度</th><th>心率區</th><th class="num">°C</th></tr></thead><tbody>
      ${pts.map((q) => `<tr><td>${esc(q.date)}</td><td class="num">${f2(q.pwhr)}</td><td class="num">${f2(q.pwhr_adj)}</td><td class="num">${q.power}</td><td class="num">${q.hr}</td>
        <td class="num">${(q.grade * 100).toFixed(1)}%</td><td class="num">${mmss(q.duration_s)}</td><td>${esc(q.zone || "—")}</td><td class="num">${q.temp_c ?? "—"}</td></tr>`).join("")}
      </tbody></table></details>`);
  };
})();
