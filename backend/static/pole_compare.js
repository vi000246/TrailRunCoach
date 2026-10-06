// 有杖 vs 沒杖 (views kind "polecompare" → backend/engine/panels/pole_compare.py, SP-243). Self-contained:
// loaded by wko5_viewer.html, which calls drawPoleCompare(box, res); uses the viewer's globals (esc, css,
// echarts, charts, t, pzDark). One measure at a time (a segmented switch, remembered per browser): grade bins
// on a category axis, the two groups as two marks per bin — 有杖 = blue circle, 沒杖 = orange diamond
// (palette slots 1, 2, validated light + dark; shape too, never colour alone), each labelled with its n.
// A group with n < min_n gets no mark (its n stays under the axis label). No verdict line: the caveat
// (heavier pack, steeper, longer on the pole days) is shown on the card. A table view sits under the chart.
(function () {
  const KEY = "wko5viewer.polecompare";
  const prefs = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } };
  const save = (patch) => { try { localStorage.setItem(KEY, JSON.stringify({ ...prefs(), ...patch })); } catch {} };
  const colors = () => (window.pzDark && pzDark())
    ? { with: "#3987e5", without: "#d95926" } : { with: "#2a78d6", without: "#eb6834" };
  const SYM = { with: "circle", without: "diamond" };
  const GROUPS = ["with", "without"];

  function style() {
    if (document.getElementById("pc-style")) return;
    const st = document.createElement("style");
    st.id = "pc-style";
    st.textContent = `.pcbar { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; margin: 2px 0 6px; font-size: 12px; }
.pcbar .seg { font-size: 11.5px; padding: 1px; flex-wrap: wrap; }
.pcbar .seg button { padding: 1px 9px; font-weight: 500; }
.pcsum { font-size: 11.5px; color: var(--muted); margin: 0 0 6px; }
.pcsum b { color: var(--text); font-weight: 600; }
.pcnote { font-size: 11.5px; color: var(--text); margin: 0 0 6px; padding: 4px 8px; border-left: 3px solid var(--line); background: var(--bg, transparent); }
.plot.pc { height: 280px; }
.pcleg { display: flex; flex-wrap: wrap; gap: 3px 14px; font-size: 11px; color: var(--muted); margin: 0 0 2px; }
.pcleg span { white-space: nowrap; display: inline-flex; align-items: center; gap: 5px; }
.pcleg svg { width: 10px; height: 10px; }
details.pcdet { margin-top: 6px; } details.pcdet > summary { font-size: 12px; cursor: pointer; }
details.pcdet table { margin-top: 4px; font-size: 11.5px; }`;
    document.head.appendChild(st);
  }

  function help(card, res) {
    if (!res.description) return;
    if (res._zoom) { card.insertAdjacentHTML("beforeend", `<p class="desc">${esc(res.description)}</p>`); return; }
    const h2 = card.querySelector("h2");
    const q = `<button type="button" class="qtip" aria-label="${esc(t("viewer.pole.help"))}" data-tip="${esc(res.description)}">?</button>`;
    const zb = h2?.querySelector(".zbtn");
    if (zb) zb.insertAdjacentHTML("beforebegin", q); else h2?.insertAdjacentHTML("beforeend", q);
  }

  const fmt = (x, d) => x == null ? "—" : Number(x).toFixed(d);
  const gname = (g) => t(`viewer.pole.${g}`);

  function legendHtml(c) {
    const shape = (g) => g === "with"
      ? `<svg viewBox="0 0 10 10" aria-hidden="true"><circle cx="5" cy="5" r="4.5" fill="${c[g]}"/></svg>`
      : `<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M5 0 L10 5 L5 10 L0 5 Z" fill="${c[g]}"/></svg>`;
    return `<p class="pcleg">${GROUPS.map((g) => `<span>${shape(g)}${esc(gname(g))}</span>`).join("")}</p>`;
  }

  function drawMetric(card, res, m) {
    const c = colors(), muted = css("--muted"), line = css("--line"), panel = css("--panel");
    const minN = res.min_n || 3;
    const cats = m.rows.map((r) => `${r.label}\n${t("viewer.pole.axis_n", { w: r.with.n, wo: r.without.n })}`);
    const vals = m.rows.flatMap((r) => GROUPS.map((g) => r[g].value)).filter((v) => v != null);
    const el = document.createElement("div");
    el.className = "plot pc";
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", t("viewer.pole.aria", { metric: m.label }));
    card.appendChild(el);
    const chart = echarts.init(el);
    charts.push(chart);
    const tip = (p) => {
      const r = m.rows[p.dataIndex], s = r[p.data.g];
      return `${esc(r.label)}・${esc(gname(p.data.g))}<br>${esc(t("viewer.pole.median"))} <b>${fmt(s.value, m.decimals)} ${esc(m.unit)}</b><br>`
        + `n = ${s.n}${s.q1 != null ? `・${esc(t("viewer.pole.iqr"))} ${fmt(s.q1, m.decimals)}–${fmt(s.q3, m.decimals)}` : ""}`;
    };
    chart.setOption({
      grid: { left: 52, right: 16, top: 28, bottom: 46 },
      tooltip: { trigger: "item", confine: true, backgroundColor: panel, borderColor: line,
        textStyle: { color: css("--text"), fontSize: 12 }, formatter: tip },
      legend: { show: false },
      xAxis: { type: "category", data: cats, axisLabel: { color: muted, fontSize: 10, interval: 0, lineHeight: 14 },
        axisLine: { lineStyle: { color: line } }, axisTick: { show: false } },
      // scale: not from 0 — the gap between the two groups is what is read (the n labels say how sure)
      yAxis: { type: "value", name: m.unit, scale: true, boundaryGap: ["25%", "25%"], ...(vals.length ? {} : { min: 0, max: 1 }),
        nameTextStyle: { color: muted, fontSize: 10 },
        axisLabel: { color: muted, fontSize: 10, formatter: (v) => fmt(v, Math.min(m.decimals, 2)) },
        splitLine: { lineStyle: { color: line } } },
      series: GROUPS.map((g, gi) => ({
        name: gname(g), type: "scatter", symbol: SYM[g], symbolSize: g === "with" ? 12 : 14,
        symbolOffset: [gi ? 9 : -9, 0], color: c[g],
        itemStyle: { color: c[g], borderColor: panel, borderWidth: 2 },
        label: { show: true, position: "top", color: muted, fontSize: 10, formatter: (p) => `n=${m.rows[p.dataIndex][g].n}` },
        data: m.rows.map((r) => r[g].value == null || r[g].n < minN ? { value: null, g } : { value: r[g].value, g }),
      })),
    });
    if (window.ResizeObserver) new ResizeObserver(() => { if (!chart.isDisposed()) chart.resize(); }).observe(el);
  }

  function table(card, res) {
    const minN = res.min_n || 3;
    const rows = res.metrics.flatMap((m) => m.rows.flatMap((r) => GROUPS.map((g) => {
      const s = r[g], shown = s.n >= minN;
      return `<tr><td>${esc(m.label)}</td><td>${esc(r.label)}</td><td>${esc(gname(g))}</td><td class="num">${s.n}</td>`
        + `<td class="num">${shown ? `${fmt(s.value, m.decimals)} ${esc(m.unit)}` : s.n ? esc(t("viewer.pole.too_few")) : "—"}</td>`
        + `<td class="num">${shown && s.q1 != null ? `${fmt(s.q1, m.decimals)}–${fmt(s.q3, m.decimals)}` : "—"}</td></tr>`;
    })));
    card.insertAdjacentHTML("beforeend", `<details class="pcdet"><summary>${esc(t("viewer.pole.table"))}</summary><table><thead><tr>
      <th>${esc(t("viewer.pole.col_metric"))}</th><th>${esc(t("viewer.pole.col_bin"))}</th><th>${esc(t("viewer.pole.col_group"))}</th>
      <th class="num">n</th><th class="num">${esc(t("viewer.pole.median"))}</th><th class="num">${esc(t("viewer.pole.iqr"))}</th></tr></thead>
      <tbody>${rows.join("")}</tbody></table></details>`);
  }

  window.drawPoleCompare = function (card, res) {
    style();
    card.querySelector(".loading")?.remove();
    help(card, res);
    const cnt = res.counts || {};
    if (res.empty) {
      card.insertAdjacentHTML("beforeend", `<p class="nodata">${esc(res.empty)}</p>`);
      return;
    }
    const a = res.activities || {};
    card.insertAdjacentHTML("beforeend", `<p class="pcsum">${t("viewer.pole.summary", {
      w: `<b>${a.with || 0}</b>`, wo: `<b>${a.without || 0}</b>`, n: res.min_n || 3,
      year_w: cnt.with ?? "—", year_wo: cnt.without ?? "—" })}</p>`);
    card.insertAdjacentHTML("beforeend", `<p class="pcnote">${esc(res.caveat || "")}</p>`);
    if (res.note) card.insertAdjacentHTML("beforeend", `<p class="meta pcmeta">${esc(res.note)}</p>`);
    const ms = res.metrics || [];
    if (!ms.length) return;
    const want = prefs().metric;
    const m = ms.find((x) => x.id === want) || ms[0];
    const bar = document.createElement("div");
    bar.className = "pcbar";
    bar.innerHTML = `<div class="seg" role="group" aria-label="${esc(t("viewer.pole.metric_aria"))}">${ms.map((x) =>
      `<button type="button" data-m="${esc(x.id)}" class="${x.id === m.id ? "on" : ""}" aria-pressed="${x.id === m.id}">${esc(x.label)}</button>`).join("")}</div>`;
    card.appendChild(bar);
    bar.querySelectorAll("button").forEach((btn) => btn.onclick = () => {
      if (btn.classList.contains("on")) return;
      save({ metric: btn.dataset.m });
      card.querySelectorAll(".pcsum, .pcnote, .pcbar, .pcleg, .plot.pc, details.pcdet, .pcmeta, .nodata").forEach((el) => {
        if (el.classList.contains("plot")) echarts.getInstanceByDom(el)?.dispose();
        el.remove();
      });
      card.querySelector(".qtip")?.remove();
      window.drawPoleCompare(card, res);
    });
    if (!ms.some((x) => x.id === "impact_g" || x.id === "ilr"))
      card.insertAdjacentHTML("beforeend", `<p class="meta pcmeta">${esc(t("viewer.pole.no_impact"))}</p>`);
    card.insertAdjacentHTML("beforeend", legendHtml(colors()));
    drawMetric(card, res, m);
    table(card, res);
  };
})();
