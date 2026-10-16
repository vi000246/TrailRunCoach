// 賽前準備 / 減量檢查 (kind "readiness", SP-651, split by SP-677): the wide card on top of the 專項期 and 減量期
// pages ({readiness, schedule, phase, other} from the chart API; `other` = the other card's link).
// phase "build" (race_readiness.board): 本週要做 (+ 安排課表) → the two gates (專項量, 表現驗證: counts only,
// a 2/3 tick; a click shows only that stage in the calendar) → the week calendar (10 → 3 weeks to race, one
// row per item grouped by stage, the criterion next to its name; a click on a cell says that week's target
// and actual) → what is looked at every week, and a line to the 減量期 card.
// Narrow (a phone, container < 640 px): the gates stack and only this week's items are listed (with 看圖);
// the whole calendar is on a wide screen.
// phase "taper" (race_readiness.taper, drawTaper): 「賽前 N 天」 and the taper's day, a countdown strip,
// 這幾天要做, four check cards (one number, a target band bar, the state, what to change when it is off,
// 看圖), and one line of the 專項期 result with its card's link. No calendar.
// SP-672: a cell shows the board's `show` as is (a later / taper cell: its target, set by race_readiness).
// Every non-count item has 「看圖」: an existing chart (it.chart.href: that page, card and weeks) or the
// card's own small chart under the calendar (it.chart.own: "repeat" = each session's best of the last 6 weeks
// with the best and the pass line, the top 5 marked; "long_climb" = each week's longest continuous climb).
//   Readiness.draw(box, res)
(function () {
  const L = window.I18N || { t: (k) => k };
  const T = (k, p) => L.t("viewer.readiness." + k, p);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const md = (iso) => { const [, m, d] = String(iso || "").split("-").map(Number); return m ? `${m}/${d}` : ""; };
  const addDays = (iso, n) => { const d = new Date(iso + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  const NUM = ["①", "②", "③"];

  function style() {
    if (document.getElementById("rd-style")) return;
    const st = document.createElement("style");
    st.id = "rd-style";
    st.textContent = `
.rd { --rd-good: var(--ok, #15803d); --rd-bad: var(--err, #b91c1c); --rd-warn: var(--warn, #b45309); --rd-tap: #7c3aed;
  container-type: inline-size; display: grid; gap: 10px; min-width: 0; font-size: 13px; }
.rd-head { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; }
.rd-head b { font-size: 14px; }
.rd-lv { font-size: 11px; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; }
.rd-lv.behind { color: var(--rd-bad); } .rd-lv.on_track { color: var(--rd-good); } .rd-lv.unknown { color: var(--muted); }
.rd-here { background: var(--accent-soft); border-left: 4px solid var(--accent); border-radius: 8px; padding: 8px 12px;
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.rd-here .k { color: var(--accent); font-size: 12px; font-weight: 650; width: 100%; }
.rd-chip { border: 1.5px solid var(--accent); color: var(--accent); background: var(--panel); border-radius: 999px;
  padding: 1px 10px; font-size: 12.5px; }
.rd-sched { margin-left: auto; color: #fff; background: var(--accent); border-radius: 6px; padding: 3px 10px;
  text-decoration: none; font-weight: 650; font-size: 12.5px; white-space: nowrap; }
.rd-rail { display: flex; align-items: stretch; gap: 8px; }
.rd-gate { flex: 1; min-width: 0; text-align: left; font: inherit; color: inherit; border: 1px solid var(--line);
  border-radius: 10px; padding: 8px 12px; background: var(--panel); cursor: pointer; }
.rd-gate.open { border: 2px solid var(--accent); }
.rd-gate.lock { background: color-mix(in srgb, var(--line) 35%, var(--panel)); color: var(--muted); }
.rd-gate.sel { box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 30%, transparent); }
.rd-gate .g1 { display: flex; align-items: center; gap: 8px; }
.rd-gate .num { width: 22px; height: 22px; flex: none; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; font-weight: 700; font-size: 12px; border: 1.5px solid var(--muted); color: var(--muted); }
.rd-gate.open .num { background: var(--accent); border-color: var(--accent); color: #fff; }
.rd-gate.passed .num { background: var(--rd-good); border-color: var(--rd-good); color: #fff; }
.rd-gate .nm { font-weight: 700; white-space: nowrap; }
.rd-gate .st { margin-left: auto; font-size: 11px; padding: 0 8px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
.rd-gate.open .st { color: var(--accent); } .rd-gate.passed .st { color: var(--rd-good); }
.rd-gate .g2 { font-size: 12px; color: var(--muted); margin-top: 2px; }
.rd-pbar { height: 5px; background: var(--line); border-radius: 3px; margin-top: 6px; position: relative; }
.rd-pbar i { display: block; height: 100%; border-radius: 3px; background: var(--accent); }
.rd-gate.passed .rd-pbar i { background: var(--rd-good); }
.rd-pbar b { position: absolute; top: -3px; left: 66.6%; width: 2px; height: 11px; background: var(--text); }
.rd-desk { overflow-x: auto; }
.rd-cal { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12.5px; }
.rd-cal th, .rd-cal td { padding: 4px 3px; text-align: center; border-bottom: 1px solid color-mix(in srgb, var(--line) 60%, transparent); }
.rd-cal thead th { color: var(--muted); font-weight: 500; font-size: 11.5px; line-height: 1.3; }
.rd-cal th.lab, .rd-cal td.lab { text-align: left; padding-left: 6px; }
.rd-cal td.lab { min-width: 150px; max-width: 240px; }
.rd-cal td.lab small { display: block; color: var(--muted); font-size: 11px; }
.rd-cal .cw { background: color-mix(in srgb, var(--accent) 9%, transparent); }
.rd-cal thead th.cw { color: var(--accent); font-weight: 700; }
.rd-cal tr.grp td { background: color-mix(in srgb, var(--line) 25%, transparent); text-align: left; font-weight: 700;
  padding: 6px; border-top: 1px solid var(--line); }
.rd-cal tr.grp td span { font-weight: 400; color: var(--muted); margin-left: 6px; }
.rd-cal tr.locked td:not(.lab), .rd-cal tr.locked td.lab { opacity: .5; }
.rd-cal tr.dim { opacity: .3; }
.rd-ok { color: var(--rd-good); }
.rd-c { display: inline-flex; min-width: 26px; height: 22px; padding: 0 4px; border-radius: 11px; align-items: center;
  justify-content: center; font-size: 11px; font-weight: 700; cursor: pointer; border: 0; font-family: inherit; }
.rd-c.s-done { background: var(--rd-good); color: #fff; }
.rd-c.s-due { border: 2px solid var(--accent); color: var(--accent); background: var(--panel); }
.rd-c.s-miss { background: color-mix(in srgb, var(--rd-bad) 16%, transparent); color: var(--rd-bad); }
.rd-c.s-fut { border: 1.5px dashed var(--line); color: var(--muted); font-weight: 500; background: transparent; }
.rd-c.s-tap { background: color-mix(in srgb, var(--rd-tap) 14%, transparent); color: var(--rd-tap); }
.rd-c.s-unk { background: color-mix(in srgb, var(--line) 45%, transparent); color: var(--muted); }
.rd-legend { display: flex; flex-wrap: wrap; gap: 6px 12px; font-size: 11.5px; color: var(--muted); margin-top: 8px; align-items: center; }
.rd-legend .rd-c { min-width: 20px; height: 16px; font-size: 9px; cursor: default; }
.rd-detail { margin-top: 8px; background: color-mix(in srgb, var(--line) 18%, var(--panel)); border: 1px solid var(--line);
  border-radius: 8px; padding: 6px 12px; min-height: 34px; }
.rd-detail .dk { color: var(--muted); font-size: 12px; }
.rd-mob { display: none; }
.rd-mlist .mg { font-weight: 700; font-size: 12.5px; margin: 8px 0 2px; }
.rd-mlist .mr { display: flex; gap: 8px; align-items: center; padding: 3px 0; }
.rd-mlist .mr small { color: var(--muted); }
.rd-mlist .empty { color: var(--muted); padding: 4px 0; }
.rd-gr { font: inherit; font-size: 11px; color: var(--accent); background: none; border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
  border-radius: 999px; padding: 0 7px; margin-left: 6px; cursor: pointer; text-decoration: none; white-space: nowrap; }
.rd-gr.on { background: var(--accent); color: #fff; }
.rd-fig { margin-top: 8px; border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; background: var(--panel); }
.rd-fig .fh { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; margin-bottom: 4px; }
.rd-fig .fh b { font-size: 13px; } .rd-fig .note { color: var(--muted); font-size: 12px; }
.rd-fig .keys { display: inline-flex; gap: 4px; margin-left: auto; }
.rd-fig .keys button { font: inherit; font-size: 11.5px; border: 1px solid var(--line); background: var(--panel); color: var(--text);
  border-radius: 6px; padding: 0 8px; cursor: pointer; }
.rd-fig .keys button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.rd-fig svg { width: 100%; height: auto; display: block; }
.rd-fig svg text { font-size: 10px; fill: var(--muted); }
.rd-foot { padding-top: 8px; border-top: 1px dashed var(--line); display: flex; gap: 6px 16px; flex-wrap: wrap; }
.rd-foot .k { color: var(--muted); }
.rd-foot .watch { color: var(--rd-warn); } .rd-foot .bad { color: var(--rd-bad); }
@container (max-width: 640px) {
  .rd-desk { display: none; } .rd-mob { display: block; }
  .rd-rail { flex-direction: column; gap: 6px; }
  .rd-sched { margin-left: 0; }
  .rd .rd-checks { grid-template-columns: 1fr; }
}
.rd-mhint { color: var(--muted); font-size: 12px; margin: 6px 0 0; }
.rd-go { color: var(--accent); text-decoration: none; margin-left: auto; }
.rd-count { display: flex; align-items: baseline; gap: 4px 10px; flex-wrap: wrap; }
.rd-count .big { font-size: 30px; font-weight: 800; color: var(--rd-tap); line-height: 1.2; }
.rd-count .meta { color: var(--muted); }
.rd-days { display: flex; gap: 3px; min-width: 0; }
.rd-days span { flex: 1; min-width: 0; height: 26px; border-radius: 5px; background: color-mix(in srgb, var(--rd-tap) 13%, var(--panel));
  color: var(--rd-tap); font-size: 11px; display: flex; align-items: center; justify-content: center; overflow: hidden; white-space: nowrap; }
.rd-days span.past { background: var(--rd-tap); color: #fff; }
.rd-days span.today { outline: 2px solid var(--rd-tap); outline-offset: -2px; background: var(--panel); font-weight: 700; }
.rd-days span.race { background: var(--warn, #b45309); color: #fff; font-weight: 700; }
.rd-checks { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.rd-chk { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: var(--panel); min-width: 0; }
.rd-chk .h { display: flex; align-items: center; gap: 6px; font-weight: 700; flex-wrap: wrap; }
.rd-chk .tag { margin-left: auto; font-size: 11px; padding: 0 8px; border-radius: 999px; font-weight: 600; white-space: nowrap; }
.rd-chk .tag.ok { background: color-mix(in srgb, var(--rd-good) 15%, transparent); color: var(--rd-good); }
.rd-chk .tag.warn { background: color-mix(in srgb, var(--rd-warn) 16%, transparent); color: var(--rd-warn); }
.rd-chk .tag.wait, .rd-chk .tag.lock { background: color-mix(in srgb, var(--line) 50%, transparent); color: var(--muted); }
.rd-chk .now { font-size: 22px; font-weight: 800; margin-top: 4px; }
.rd-chk .tg { color: var(--muted); font-size: 12px; }
.rd-chk .todo { font-size: 13px; margin-top: 4px; color: var(--rd-warn); }
.rd-bar { height: 8px; border-radius: 4px; background: color-mix(in srgb, var(--line) 45%, transparent); position: relative; margin: 8px 0 4px; }
.rd-bar .band { position: absolute; top: 0; bottom: 0; background: color-mix(in srgb, var(--rd-tap) 30%, transparent); border-radius: 4px; }
.rd-bar .pt { position: absolute; top: -4px; width: 4px; height: 16px; margin-left: -2px; border-radius: 2px; background: var(--rd-tap); }
.rd-bar .pt.warn { background: var(--rd-warn); }`;
    document.head.appendChild(st);
  }

  const pct = (x) => `${Math.round(x * 100)}%`;
  // the board sets what a cell shows (a later / taper cell: its target)
  function shown(c) {
    if (c.state === "done" && !c.show) return "✓";
    return c.show || "·";
  }

  // ---- the card's own small charts (SP-672) ----
  // the width follows the card (a phone: ~340) so the 10-px labels stay readable
  let W = 640;
  const H = 210, PL = 44, PR = 12, PT = 12, PB = 26;
  function axisY(lo, hi, y) {
    const st = [lo, (lo + hi) / 2, hi];
    return st.map((v) => `<line x1="${PL}" x2="${W - PR}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" stroke-width="0.6"/><text x="${PL - 4}" y="${y(v) + 3}" text-anchor="end">${Math.round(v)}</text>`).join("");
  }
  function repeatFig(it, key) {
    const pts = (it.points || {})[key] || [];
    if (pts.length < 1) return `<p class="note">${esc(T("fig_none"))}</p>`;
    const best = Math.max(...pts.map((p) => p.v)), ok = it.ok || 0.95, n = it.top_n || 5;
    const top = [...pts].sort((a, b) => b.v - a.v).slice(0, n), topSet = new Set(top);
    const mean = top.length >= n ? top.reduce((a, p) => a + p.v, 0) / n / best : null;
    const t0 = new Date(pts[0].date).getTime(), t1 = Math.max(new Date(pts[pts.length - 1].date).getTime(), t0 + 864e5);
    const vs = pts.map((p) => p.v), lo = Math.min(...vs, best * ok) * 0.97, hi = best * 1.02;
    const x = (d) => PL + ((new Date(d).getTime() - t0) / (t1 - t0)) * (W - PL - PR), y = (v) => PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB);
    let g = axisY(lo, hi, y);
    g += `<line x1="${PL}" x2="${W - PR}" y1="${y(best)}" y2="${y(best)}" stroke="var(--text)" stroke-width="1.2"/><text x="${W - PR}" y="${y(best) - 3}" text-anchor="end">${esc(T("fig_best"))}</text>`;
    g += `<line x1="${PL}" x2="${W - PR}" y1="${y(best * ok)}" y2="${y(best * ok)}" stroke="var(--rd-good)" stroke-width="1.2" stroke-dasharray="5 4"/><text x="${W - PR}" y="${y(best * ok) + 11}" text-anchor="end">${esc(T("fig_ok", { p: pct(ok) }))}</text>`;
    g += pts.map((p) => `<circle cx="${x(p.date)}" cy="${y(p.v)}" r="${topSet.has(p) ? 5 : 3.5}" fill="${topSet.has(p) ? "var(--accent)" : "var(--muted)"}" opacity="${topSet.has(p) ? 1 : 0.55}"><title>${esc(md(p.date))} · ${Math.round(p.v)}</title></circle>`).join("");
    g += `<text x="${PL}" y="${H - 8}">${esc(md(pts[0].date))}</text><text x="${W - PR}" y="${H - 8}" text-anchor="end">${esc(md(pts[pts.length - 1].date))}</text>`;
    const note = mean != null ? T("fig_mean", { n, p: pct(mean) }) : T("fig_few", { n });
    return `<p class="note">${esc(note)}</p><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(it.name)}">${g}</svg>`;
  }
  function climbFig(it) {
    const se = (it.series || []).slice().sort((a, b) => b.week - a.week), race = it.race_climb_m || 0, ms = it.milestones || {};
    if (!se.length) return `<p class="note">${esc(T("fig_none"))}</p>`;
    const hi = Math.max(race, ...se.map((s) => s.m || 0)) * 1.08 || 100;
    const bw = (W - PL - PR) / se.length, y = (v) => PT + (1 - v / hi) * (H - PT - PB);
    let g = axisY(0, hi, y);
    se.forEach((s, i) => {
      const x0 = PL + i * bw + bw * 0.2, bwi = bw * 0.6, m = s.m || 0;
      g += `<rect x="${x0}" y="${y(m)}" width="${bwi}" height="${Math.max(0, y(0) - y(m))}" rx="2" fill="var(--accent)"><title>${esc(T("week_n", { n: s.week, from: md(s.monday) }))} · ${Math.round(m)} m</title></rect>`;
      if (ms[String(s.week)] != null) g += `<line x1="${x0 - 3}" x2="${x0 + bwi + 3}" y1="${y(ms[String(s.week)])}" y2="${y(ms[String(s.week)])}" stroke="var(--rd-warn)" stroke-width="2.5"><title>${esc(T("fig_target"))} ${ms[String(s.week)]} m</title></line>`;
      g += `<text x="${x0 + bwi / 2}" y="${H - 8}" text-anchor="middle">${s.week}</text>`;
    });
    if (race) g += `<line x1="${PL}" x2="${W - PR}" y1="${y(race)}" y2="${y(race)}" stroke="var(--text)" stroke-width="1.2" stroke-dasharray="5 4"/><text x="${W - PR}" y="${y(race) - 3}" text-anchor="end">${esc(T("fig_race_climb", { m: Math.round(race) }))}</text>`;
    return `<p class="note">${esc(T("fig_climb_note"))}</p><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(it.name)}">${g}</svg>`;
  }

  function drawTaper(box, res) {
    const R = res.readiness || {};
    const root = document.createElement("div");
    root.className = "rd";
    box.appendChild(root);
    if (!R.race) { root.innerHTML = `<p class="meta">${esc(R.text || "")}</p>`; return; }
    const head = `<div class="rd-head"><b>${esc(R.race.name)}</b><span class="meta">${esc(md(R.race.start))} · ${esc(T("taper_len", { n: R.taper_days, d: md(R.taper_from) }))}</span></div>`;
    const count = `<div class="rd-count"><span class="big">${esc(R.text)}</span>${R.taper_day ? `<span class="meta">${esc(T("taper_day", { d: R.taper_day, n: R.taper_days }))}</span>` : ""}</div>`;
    const strip = `<div class="rd-days" aria-hidden="true">${(R.countdown || []).map((d) => `<span class="${esc(d.state)}">${esc(d.label)}</span>`).join("")}</div>`;
    const here = (R.this_week || []).length ? `<div class="rd-here"><div class="k">${esc(T("these_days"))}</div>${R.this_week.map((x) => `<span class="rd-chip">${esc(x)}</span>`).join("")}
      <a class="rd-sched" href="${esc(res.schedule || "")}">${esc(T("schedule"))} ›</a></div>` : "";
    const pos = (x, sc) => Math.max(0, Math.min(100, ((x - sc[0]) / (sc[1] - sc[0])) * 100));
    const card = (k) => {
      const sc = k.scale || [0, 1];
      const bar = k.band ? `<div class="rd-bar"><div class="band" style="left:${pos(k.band[0], sc)}%;width:${pos(k.band[1], sc) - pos(k.band[0], sc)}%"></div>${k.value != null ? `<div class="pt ${k.state === "warn" ? "warn" : ""}" style="left:${pos(k.value, sc)}%"></div>` : ""}</div>` : "";
      const chart = k.chart && k.chart.href ? `<a class="rd-gr" href="${esc(k.chart.href)}">${esc(T("chart"))} ›</a>` : "";
      return `<div class="rd-chk" data-id="${esc(k.id)}"><div class="h">${esc(k.name)}${chart}<span class="tag ${esc(k.state)}">${esc(k.label)}</span></div>
        <div class="now">${esc(k.show)}</div><div class="tg">${esc(k.target)}</div>${bar}${k.todo ? `<div class="todo">${esc(k.todo)}</div>` : ""}</div>`;
    };
    const go = res.other ? ` <a class="rd-go" href="${esc(res.other)}">${esc(T("go_build"))}</a>` : "";
    const bl = R.build_line ? `<span>${esc(T("build_line", { x: R.build_line }))}</span>` : "";
    const inj = R.injury ? `<span class="${R.injury_on ? "watch" : ""}">${R.injury_on ? "◐" : '<span class="rd-ok">✓</span>'} ${esc(R.injury)}</span>` : "";
    root.innerHTML = head + count + strip + here + `<div class="rd-checks">${(R.checks || []).map(card).join("")}</div>` +
      `<div class="rd-foot">${bl}${inj}${go}</div>`;
  }

  function draw(box, res) {
    style();
    if (res.phase === "taper") { drawTaper(box, res); return; }
    const R = res.readiness || {};
    const root = document.createElement("div");
    root.className = "rd";
    box.appendChild(root);
    if (!R.race) { root.innerHTML = `<p class="meta">${esc(R.text || "")}</p>`; return; }
    const weeks = R.weeks || [], cur = R.weeks_out;
    const mon = Object.fromEntries(weeks.map((w) => [w.week, w.monday]));
    const S = { sel: null, cell: null, fig: null, rkey: null };
    const cellOf = (it, w) => (it.cells || {})[String(w)];
    const stages = R.stages || [];

    function head() {
      const lv = R.preview ? "" : `<span class="rd-lv ${esc(R.level)}">${esc(R.label)}</span>`;
      const wo = cur > 0 ? ` · ${esc(T("weeks_out", { n: cur }))}` : "";
      return `<div class="rd-head"><b>${esc(R.race.name)}</b><span class="meta">${esc(md(R.race.start))}${wo} · ${esc(R.text || "")}</span>${lv}</div>`;
    }
    function here() {
      if (R.preview || !(R.this_week || []).length) return "";
      const m = mon[cur];
      const chips = R.this_week.map((x) => `<span class="rd-chip">${esc(x.name)}${x.detail ? esc(T("sep")) + esc(x.detail) : ""}</span>`).join("");
      return `<div class="rd-here"><div class="k">${esc(T("week_of", { n: cur, from: md(m), to: md(addDays(m, 6)) }))}</div>${chips}
        <a class="rd-sched" href="${esc(res.schedule || "")}">${esc(T("schedule"))} ›</a></div>`;
    }
    function rail() {
      return `<div class="rd-rail">${stages.map((s, i) => {
        const lock = s.state === "lock";
        const g2 = lock ? esc(s.span) : `${esc(s.span)} · ${esc(T("done_of", { d: s.done, t: s.total }))}`;
        const w = s.total ? Math.round((s.done / s.total) * 100) : 0;
        return `<button type="button" class="rd-gate ${esc(s.state)}${S.sel === s.id ? " sel" : ""}" data-stage="${esc(s.id)}" aria-pressed="${S.sel === s.id}">
          <div class="g1"><span class="num">${lock ? "🔒" : s.passed ? "✓" : i + 1}</span><span class="nm">${esc(s.name)}</span><span class="st">${esc(s.label)}</span></div>
          <div class="g2">${g2}</div><div class="rd-pbar"><i style="width:${w}%"></i><b></b></div></button>`;
      }).join("")}</div>`;
    }
    function cal() {
      let h = `<table class="rd-cal"><thead><tr class="rd-wk"><th class="lab">${esc(T("weeks_to_race"))}</th>${weeks.map((w) => `<th class="w${w.current ? " cw" : ""}">${w.current ? esc(T("this_week_short")) + "<br>" : ""}${w.week}<br><span style="font-weight:400">${esc(md(w.monday))}</span></th>`).join("")}</tr></thead><tbody>`;
      stages.forEach((s, si) => {
        const dim = S.sel && S.sel !== s.id ? " dim" : "";
        const lock = s.state === "lock";
        h += `<tr class="grp${dim}" data-stage="${esc(s.id)}"><td colspan="${weeks.length + 1}">${lock ? "🔒" : NUM[si] || ""} ${esc(s.name)}<span>${esc(s.span)}</span></td></tr>`;
        s.items.forEach((it, ii) => {
          const sub = [it.crit, it.met == null && it.reason ? it.reason : ""].filter(Boolean).map(esc).join(" · ");
          h += `<tr class="${lock ? "locked" : ""}${dim}" data-stage="${esc(s.id)}"><td class="lab">${it.met ? '<span class="rd-ok">✓</span> ' : ""}${esc(it.name)}${gr(it, si, ii)}${sub ? `<small>${sub}</small>` : ""}</td>`;
          weeks.forEach((w) => {
            const c = cellOf(it, w.week);
            h += `<td class="${w.current ? "cw" : ""}">${c ? `<button type="button" class="rd-c s-${esc(c.state)}" data-s="${si}" data-i="${ii}" data-w="${w.week}">${esc(shown(c))}</button>` : ""}</td>`;
          });
          h += "</tr>";
        });
      });
      h += "</tbody></table>";
      const lg = [["done", "✓", "lg_done"], ["due", "1", "lg_due"], ["miss", "1", "lg_miss"], ["fut", "1", "lg_fut"], ["unk", "·", "lg_unk"]]
        .map(([s, x, k]) => `<span><span class="rd-c s-${s}">${x}</span> ${esc(T(k))}</span>`).join("");
      return `${h}<div class="rd-legend">${lg}<span>· ${esc(T("lg_hint"))}</span></div><div class="rd-detail">${detail()}</div>`;
    }
    // 「看圖」: a link to the item's chart, or a toggle of the card's own small chart
    function gr(it, si, ii) {
      const ch = it.chart;
      if (!ch) return "";
      if (ch.href) return `<a class="rd-gr" href="${esc(ch.href)}">${esc(T("chart"))} ›</a>`;
      if (!ch.own) return "";
      const on = S.fig && S.fig.si === si && S.fig.ii === ii;
      return `<button type="button" class="rd-gr${on ? " on" : ""}" data-fs="${si}" data-fi="${ii}" aria-expanded="${on}">${esc(T("chart"))}</button>`;
    }
    function fig() {
      if (!S.fig) return "";
      const it = ((stages[S.fig.si] || {}).items || [])[S.fig.ii];
      if (!it || !it.chart) return "";
      let head = `<b>${esc(it.name)}</b>`, body;
      if (it.chart.own === "repeat") {
        const power = it.mode !== "climb" && !(it.points || {}).vam;
        const key = power ? (S.rkey || it.key || "1200") : "vam";
        if (power) head += `<span class="note">${esc(T("fig_power", { n: Math.round(+key / 60) }))}</span><span class="keys">${["300", "1200", "3600"].map((k) => `<button type="button" data-rk="${k}" class="${k === key ? "on" : ""}">${esc(T("fig_min", { n: +k / 60 }))}</button>`).join("")}</span>`;
        else head += `<span class="note">${esc(T("fig_vam"))}</span>`;
        body = repeatFig(it, key);
      } else body = climbFig(it);
      return `<div class="rd-fig"><div class="fh">${head}</div>${body}</div>`;
    }
    function detail() {
      if (!S.cell) return `<span class="dk">${esc(T("detail_hint"))}</span>`;
      const { si, ii, w } = S.cell, it = stages[si].items[ii], c = cellOf(it, w);
      const txt = c.detail || T("st_" + c.state);
      return `<b>${esc(it.name)}</b> · ${esc(T("week_n", { n: w, from: md(mon[w]) }))}<br>${esc(txt)}`;
    }
    function mob() {
      // SP-677: a phone lists only this week's items (the last build week once the taper started)
      const ws = weeks.map((w) => w.week), wk = ws.includes(cur) ? cur : cur < Math.min(...ws) ? Math.min(...ws) : null;
      if (wk == null) return `<div class="empty">${esc(T("week_empty"))}</div>`;
      let h = "";
      stages.forEach((s, si) => {
        if (S.sel && S.sel !== s.id) return;
        const rs = s.items.filter((it) => cellOf(it, wk));
        if (!rs.length) return;
        h += `<div class="mg">${NUM[si] || ""} ${esc(s.name)} · ${esc(wk === cur ? T("this_week_short") : T("week_n", { n: wk, from: md(mon[wk]) }))}</div>`;
        rs.forEach((it) => {
          const c = cellOf(it, wk);
          h += `<div class="mr"><span class="rd-c s-${esc(c.state)}">${esc(shown(c))}</span><span>${esc(it.name)}${gr(it, si, s.items.indexOf(it))}<br><small>${esc(c.detail || it.crit || T("st_" + c.state))}</small></span></div>`;
        });
      });
      return `<div class="rd-mlist">${h || `<div class="empty">${esc(T("week_empty"))}</div>`}</div><p class="rd-mhint">${esc(T("mob_hint"))}</p>`;
    }
    function foot() {
      const rows = (R.weekly || []).map((x) => {
        const ks = Object.keys(x.cells || {}).map(Number);
        if (!ks.length) return "";
        const w = ks.includes(cur) ? cur : Math.min(...ks);
        const c = x.cells[String(w)];
        const ic = c.state === "ok" ? '<span class="rd-ok">✓</span>' : c.state === "bad" ? '<span class="bad">✕</span>' : '<span class="watch">◐</span>';
        return `<span>${ic} ${esc(c.detail || x.name)}</span>`;
      }).filter(Boolean);
      const go = res.other ? `<a class="rd-go" href="${esc(res.other)}">${esc(T("go_taper"))}</a>` : "";
      return rows.length || go ? `<div class="rd-foot">${rows.length ? `<span class="k">${esc(T("every_week"))}</span>${rows.join("")}` : ""}${go}</div>` : "";
    }
    function render() {
      W = Math.max(320, Math.min(900, Math.round(root.clientWidth || 640) - 24));
      root.innerHTML = head() + here() + rail() + `<div class="rd-desk">${cal()}</div><div class="rd-mob">${mob()}</div>` + fig() + foot();
    }
    root.addEventListener("click", (ev) => {
      const g = ev.target.closest(".rd-gate"), c = ev.target.closest(".rd-cal .rd-c");
      const f = ev.target.closest("button.rd-gr"), rk = ev.target.closest("[data-rk]");
      if (f) {
        const si = +f.dataset.fs, ii = +f.dataset.fi;
        S.fig = S.fig && S.fig.si === si && S.fig.ii === ii ? null : { si, ii };
      } else if (rk) S.rkey = rk.dataset.rk;
      else if (g) S.sel = S.sel === g.dataset.stage ? null : g.dataset.stage;
      else if (c) S.cell = { si: +c.dataset.s, ii: +c.dataset.i, w: +c.dataset.w };
      else return;
      render();
    });
    render();
  }

  window.Readiness = { draw };
})();
