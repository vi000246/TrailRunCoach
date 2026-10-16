// 賽前完備程度 (kind "readiness", SP-651): the wide card on top of the 專項期 and 減量期 pages. The data is
// race_readiness.board (SP-650) as the chart API sends it ({readiness, schedule}). Top to bottom, each
// thing once: 本週要做 (+ 安排課表) → the three gates (counts only, a 2/3 tick, a locked one says when it
// unlocks; a click shows only that stage in the calendar) → the week calendar (10 → 1 weeks to race, one row
// per item grouped by stage, the criterion next to its name; a click on a cell says that week's target and
// actual; a locked stage is faded) → one line of what is looked at every week.
// Narrow (a phone, container < 640 px): the gates stack and the calendar becomes a row of week buttons
// (a colour bar: red = something missed, blue = something due, green = all done); a button lists that week.
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
.rd-rail { display: flex; align-items: stretch; }
.rd-arrow { display: flex; align-items: center; color: var(--muted); padding: 0 6px; font-size: 18px; }
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
.rd-cal thead tr.ph th { font-size: 11px; border-bottom: 0; padding-bottom: 0; }
.rd-cal .phs { color: var(--accent); border-top: 3px solid color-mix(in srgb, var(--accent) 35%, transparent); }
.rd-cal .pht { color: var(--rd-tap); border-top: 3px solid color-mix(in srgb, var(--rd-tap) 35%, transparent); }
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
.rd-weeks { display: flex; flex-wrap: wrap; gap: 4px; }
.rd-wkb { flex: 1 0 calc(20% - 4px); min-width: 0; font: inherit; border: 1px solid var(--line); border-radius: 8px; background: var(--panel);
  text-align: center; padding: 3px 0; font-size: 11px; color: var(--muted); cursor: pointer; }
.rd-wkb b { display: block; font-size: 14px; color: var(--text); }
.rd-wkb.t { border-color: color-mix(in srgb, var(--rd-tap) 40%, var(--line)); }
.rd-wkb.cur { border: 2px solid var(--accent); }
.rd-wkb.sel { background: var(--accent); color: #fff; } .rd-wkb.sel b { color: #fff; }
.rd-wkb i { display: block; height: 4px; margin: 2px 8px 0; border-radius: 2px; background: var(--line); }
.rd-wkb i.r { background: var(--rd-bad); } .rd-wkb i.b { background: var(--accent); } .rd-wkb i.g { background: var(--rd-good); }
.rd-mlist .mg { font-weight: 700; font-size: 12.5px; margin: 8px 0 2px; }
.rd-mlist .mr { display: flex; gap: 8px; align-items: center; padding: 3px 0; }
.rd-mlist .mr small { color: var(--muted); }
.rd-mlist .empty { color: var(--muted); padding: 4px 0; }
.rd-foot { padding-top: 8px; border-top: 1px dashed var(--line); display: flex; gap: 6px 16px; flex-wrap: wrap; }
.rd-foot .k { color: var(--muted); }
.rd-foot .watch { color: var(--rd-warn); } .rd-foot .bad { color: var(--rd-bad); }
@container (max-width: 640px) {
  .rd-desk { display: none; } .rd-mob { display: block; }
  .rd-rail { flex-direction: column; gap: 6px; } .rd-arrow { display: none; }
  .rd-sched { margin-left: 0; }
}`;
    document.head.appendChild(st);
  }

  const pct = (x) => `${Math.round(x * 100)}%`;
  // a later week's cell says its target (the board's `show` of a taper check is the share so far)
  function shown(c, w, cur) {
    if (c.state === "tap" && w < cur) {
      const t = c.target;
      if (Array.isArray(t)) return t[0] > 0 && t[0] < 1 && t[1] <= 1 ? `${Math.round(t[0] * 100)}–${pct(t[1])}` : `${t[0] >= 0 ? "+" : ""}${Math.round(t[0] * 100)}–${Math.round(t[1] * 100)}%`;
      if (typeof t === "number" && t > 0 && t < 1) return `≤${pct(t)}`;
      return "·";
    }
    if (c.state === "done" && !c.show) return "✓";
    return c.show || "·";
  }

  function draw(box, res) {
    style();
    const R = res.readiness || {};
    const root = document.createElement("div");
    root.className = "rd";
    box.appendChild(root);
    if (!R.race) { root.innerHTML = `<p class="meta">${esc(R.text || "")}</p>`; return; }
    const weeks = R.weeks || [], cur = R.weeks_out;
    const mon = Object.fromEntries(weeks.map((w) => [w.week, w.monday]));
    const S = { sel: null, wsel: weeks.some((w) => w.week === cur) ? cur : (weeks[0] || {}).week, cell: null };
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
        return `${i ? '<div class="rd-arrow" aria-hidden="true">›</div>' : ""}<button type="button" class="rd-gate ${esc(s.state)}${S.sel === s.id ? " sel" : ""}" data-stage="${esc(s.id)}" aria-pressed="${S.sel === s.id}">
          <div class="g1"><span class="num">${lock ? "🔒" : s.passed ? "✓" : i + 1}</span><span class="nm">${esc(s.name)}</span><span class="st">${esc(s.label)}</span></div>
          <div class="g2">${g2}</div><div class="rd-pbar"><i style="width:${w}%"></i><b></b></div></button>`;
      }).join("")}</div>`;
    }
    function cal() {
      const sp = weeks.filter((w) => w.phase !== "taper").length, tp = weeks.length - sp;
      let h = `<table class="rd-cal"><thead><tr class="ph"><th></th>${sp ? `<th colspan="${sp}" class="phs">${esc(T("phase_specific"))}</th>` : ""}${tp ? `<th colspan="${tp}" class="pht">${esc(T("phase_taper"))}</th>` : ""}</tr>
        <tr class="rd-wk"><th class="lab">${esc(T("weeks_to_race"))}</th>${weeks.map((w) => `<th class="w${w.current ? " cw" : ""}">${w.current ? esc(T("this_week_short")) + "<br>" : ""}${w.week}<br><span style="font-weight:400">${esc(md(w.monday))}</span></th>`).join("")}</tr></thead><tbody>`;
      stages.forEach((s, si) => {
        const dim = S.sel && S.sel !== s.id ? " dim" : "";
        const lock = s.state === "lock";
        h += `<tr class="grp${dim}" data-stage="${esc(s.id)}"><td colspan="${weeks.length + 1}">${lock ? "🔒" : NUM[si] || ""} ${esc(s.name)}<span>${esc(s.span)}</span></td></tr>`;
        s.items.forEach((it, ii) => {
          const sub = [it.crit, it.met == null && it.reason ? it.reason : ""].filter(Boolean).map(esc).join(" · ");
          h += `<tr class="${lock ? "locked" : ""}${dim}" data-stage="${esc(s.id)}"><td class="lab">${it.met ? '<span class="rd-ok">✓</span> ' : ""}${esc(it.name)}${sub ? `<small>${sub}</small>` : ""}</td>`;
          weeks.forEach((w) => {
            const c = cellOf(it, w.week);
            h += `<td class="${w.current ? "cw" : ""}">${c ? `<button type="button" class="rd-c s-${esc(c.state)}" data-s="${si}" data-i="${ii}" data-w="${w.week}">${esc(shown(c, w.week, cur))}</button>` : ""}</td>`;
          });
          h += "</tr>";
        });
      });
      h += "</tbody></table>";
      const lg = [["done", "✓", "lg_done"], ["due", "1", "lg_due"], ["miss", "1", "lg_miss"], ["fut", "1", "lg_fut"], ["tap", "✓", "lg_tap"], ["unk", "·", "lg_unk"]]
        .map(([s, x, k]) => `<span><span class="rd-c s-${s}">${x}</span> ${esc(T(k))}</span>`).join("");
      return `${h}<div class="rd-legend">${lg}<span>· ${esc(T("lg_hint"))}</span></div><div class="rd-detail">${detail()}</div>`;
    }
    function detail() {
      if (!S.cell) return `<span class="dk">${esc(T("detail_hint"))}</span>`;
      const { si, ii, w } = S.cell, it = stages[si].items[ii], c = cellOf(it, w);
      const txt = c.detail || T("st_" + c.state);
      return `<b>${esc(it.name)}</b> · ${esc(T("week_n", { n: w, from: md(mon[w]) }))}<br>${esc(txt)}`;
    }
    function mob() {
      const btns = weeks.map((w) => {
        const st = stages.flatMap((s) => s.items.map((it) => cellOf(it, w.week)).filter(Boolean).map((c) => c.state));
        const bar = st.includes("miss") ? "r" : st.includes("due") ? "b" : st.length && st.every((x) => x === "done" || x === "tap") && st.includes("done") ? "g" : "";
        return `<button type="button" class="rd-wkb${w.phase === "taper" ? " t" : ""}${w.current ? " cur" : ""}${S.wsel === w.week ? " sel" : ""}" data-wk="${w.week}">${esc(w.current ? T("this_week_short") : T("wk"))}<b>${w.week}</b>${esc(md(w.monday))}<i class="${bar}"></i></button>`;
      }).join("");
      let h = "";
      stages.forEach((s, si) => {
        if (S.sel && S.sel !== s.id) return;
        const rs = s.items.filter((it) => cellOf(it, S.wsel));
        if (!rs.length) return;
        h += `<div class="mg">${s.state === "lock" ? "🔒" : NUM[si] || ""} ${esc(s.name)}</div>`;
        rs.forEach((it) => {
          const c = cellOf(it, S.wsel);
          h += `<div class="mr"><span class="rd-c s-${esc(c.state)}">${esc(shown(c, S.wsel, cur))}</span><span>${esc(it.name)}<br><small>${esc(c.detail || it.crit || T("st_" + c.state))}</small></span></div>`;
        });
      });
      return `<div class="rd-weeks">${btns}</div><div class="rd-mlist">${h || `<div class="empty">${esc(T("week_empty"))}</div>`}</div>`;
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
      return rows.length ? `<div class="rd-foot"><span class="k">${esc(T("every_week"))}</span>${rows.join("")}</div>` : "";
    }
    function render() {
      root.innerHTML = head() + here() + rail() + `<div class="rd-desk">${cal()}</div><div class="rd-mob">${mob()}</div>` + foot();
    }
    root.addEventListener("click", (ev) => {
      const g = ev.target.closest(".rd-gate"), c = ev.target.closest(".rd-cal .rd-c"), b = ev.target.closest(".rd-wkb");
      if (g) S.sel = S.sel === g.dataset.stage ? null : g.dataset.stage;
      else if (c) S.cell = { si: +c.dataset.s, ii: +c.dataset.i, w: +c.dataset.w };
      else if (b) S.wsel = +b.dataset.wk;
      else return;
      render();
    });
    render();
  }

  window.Readiness = { draw };
})();
