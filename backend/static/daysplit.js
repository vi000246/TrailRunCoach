/* 分日預覽 (SP-535; research SP-522): one dialog for the race calculator and the season plan to set the
 * day ends of a multi-day trip on its GPX — candidate buttons (SP-533 camp / hut waypoints, every
 * pass; SP-534 the nights of a recorded track; SP-536 huts and SP-537 Cairn later), a tap on the
 * profile adds a day end, a drag moves one, a tap on one takes it away. Each day's km, climb, descent
 * and estimated moving time update as it changes; nothing is stored until 確定 (取消 changes nothing).
 * The host page stores the answer (its own endpoint) — this file only previews.
 *
 *   <script src="/api/v1/static/daysplit.js"></script>
 *   const r = await DaySplit.open({
 *     name, days, splits,            // the trip's days now and its day ends (km) now
 *     total,                         // km of the course
 *     profile: {km: [], z: []},      // drawn
 *     pieces: [[a_km, b_km, gain_m, loss_m, t_s?], …],   // the course's pieces: the day sums
 *     hours,                         // the whole trip's moving h when pieces have no t (spread by EP)
 *     candidates: [{km, kind: "camp" | "night" | "hut" | "cairn", name?, pass?, passes?, hours?,
 *                   src?: "osm", osm_kind?, name_en?}],   // hut: SP-536's OpenStreetMap list (credited)
 *     osmAttr: {text, url},          // the OSM credit (course.build_course "osm")
 *     proposal: {kind, splits, days},  // e.g. the nights found: preselected, still to be confirmed
 *     applied: "cairn",              // the current day ends came from a Cairn import (SP-537: applied as is)
 *   });                              // → {splits, days} on 確定, null on 取消
 *
 * The day sums are the server's own rule (course.day_cuts + course.cut_at): the day ends are used
 * when they make exactly n days, else every day gets equal km (推估, SP-522 Q1 (a)); a day end
 * inside a piece shares its climb by distance. Moving time: the pieces' own time (the calculator's
 * segments) or the trip's predicted hours shared by EP = km + climb / 100 (推估).
 * i18n: common.daysplit.* (common is inlined on every page).
 */
(() => {
  const T = (k, p) => (window.I18N ? window.I18N.t("common.daysplit." + k, p) : k);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const f1 = (x) => (Number.isFinite(x) ? x.toFixed(1) : "–");
  const n0 = (x) => (Number.isFinite(x) ? Math.round(x).toLocaleString() : "–");
  const hm = (h) => {
    if (!Number.isFinite(h)) return "–";
    const m = Math.round(h * 60);
    return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
  };
  const ICON = { camp: "⛺", night: "🌙", hut: "🛖", cairn: "🪨" };

  // ---- the numbers (pure; tested under node) -------------------------------------------------
  // course.day_cuts: the given day ends when they make exactly n days, else equal km (推估)
  function cuts(splits, total, days) {
    const n = Math.max(1, Math.round(days || 1));
    if (n === 1) return { cuts: [], source: "single" };
    const s = [...new Set((splits || []).map((x) => Math.round(+x * 1000) / 1000))]
      .filter((x) => x > 0 && x < total).sort((a, b) => a - b);
    if (s.length === n - 1) return { cuts: s, source: "stored" };
    return { cuts: Array.from({ length: n - 1 }, (_, i) => total * (i + 1) / n), source: "equal" };
  }
  // course.cut_at summed per day: [{day, km, gain_m, loss_m, h}]
  function dayStats(pieces, cutsKm, days, hours) {
    const c = [...cutsKm].sort((a, b) => a - b).map((x) => x * 1000);
    const n = Math.max(1, days || 1), out = Array.from({ length: n }, (_, i) => ({ day: i + 1, km: 0, gain_m: 0, loss_m: 0, h: 0, ep: 0 }));
    const ep = pieces.reduce((s, p) => s + (p[1] - p[0]) + (p[2] || 0) / 100, 0);
    const timed = pieces.length && pieces.every((p) => Number.isFinite(p[4]));
    const perEp = !timed && Number.isFinite(hours) && hours > 0 && ep > 0 ? hours / ep : null;
    for (const p of pieces) {
      const s0 = p[0] * 1000, s1 = p[1] * 1000, dist = s1 - s0;
      const edges = [s0, ...c.filter((x) => s0 + 1 < x && x < s1 - 1), s1];
      for (let i = 0; i + 1 < edges.length; i++) {
        const a = edges[i], b = edges[i + 1], f = dist ? (b - a) / dist : 1;
        const day = Math.min(n, 1 + c.filter((x) => x <= a + 1).length), d = out[day - 1];
        const g = (p[2] || 0) * f;
        d.km += (b - a) / 1000; d.gain_m += g; d.loss_m += (p[3] || 0) * f;
        d.ep += (b - a) / 1000 + g / 100;
        if (timed) d.h += p[4] * f / 3600;
      }
    }
    for (const d of out) {
      if (perEp != null) d.h = d.ep * perEp;
      else if (!timed) d.h = null;
    }
    return out;
  }
  function stats(o, splits, days) {
    const c = cuts(splits, o.total, days);
    return { ...c, days: dayStats(o.pieces || [], c.cuts, days, o.hours) };
  }

  // ---- the dialog -------------------------------------------------------------------------------
  const CSS = `
dialog.dsplit { width: min(700px, calc(100vw - 16px)); max-height: 92vh; padding: 12px 14px; border: 1px solid var(--line, #ccc);
  border-radius: 10px; background: var(--panel, #fff); color: var(--text, #222); box-sizing: border-box; }
dialog.dsplit:focus { outline: none; }
dialog.dsplit::backdrop { background: rgba(0,0,0,.35); }
.dsplit h2 { font-size: 16px; margin: 0 0 2px; }
.dsplit .ds-meta { color: var(--muted, #777); font-size: 12px; }
.dsplit .ds-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; margin: 8px 0 4px; }
.dsplit .ds-days button { width: 30px; height: 30px; font-size: 16px; }
.dsplit .ds-days b { display: inline-block; min-width: 2.2em; text-align: center; font-variant-numeric: tabular-nums; }
.dsplit .ds-src { font-size: 13px; line-height: 1.5; }
.dsplit .ds-src.warn { color: var(--warn, #b26b00); }
.dsplit svg { display: block; width: 100%; height: auto; touch-action: none; user-select: none; cursor: crosshair; margin: 4px 0 2px; }
.dsplit .ds-cands { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; align-items: center; }
.dsplit .ds-chip { font: inherit; font-size: 12px; border: 1px solid var(--line, #ccc); border-radius: 12px; padding: 3px 10px;
  background: var(--panel, #fff); color: var(--muted, #666); cursor: pointer; font-variant-numeric: tabular-nums; min-height: 28px; }
.dsplit .ds-osm { flex-basis: 100%; }
.dsplit .ds-chip.on { border-color: var(--accent, #2563eb); color: var(--accent, #2563eb); font-weight: 600; }
.dsplit table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 6px; }
.dsplit th, .dsplit td { padding: 3px 6px; border-bottom: 1px solid var(--line, #e5e5e5); text-align: right; font-variant-numeric: tabular-nums; }
.dsplit th:first-child, .dsplit td:first-child { text-align: left; }
.dsplit tfoot td { font-weight: 600; border-bottom: 0; }
.dsplit .ds-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 10px; }
.dsplit .ds-actions .ds-left { margin-right: auto; }
.dsplit .ds-actions button { min-height: 34px; padding: 0 14px; }
`;
  function ensureCss() {
    if (document.getElementById("dsplit-css")) return;
    const st = document.createElement("style");
    st.id = "dsplit-css"; st.textContent = CSS;
    document.head.appendChild(st);
  }
  const css = (v, d) => (getComputedStyle(document.documentElement).getPropertyValue(v) || "").trim() || d;
  const near = (a, b, tol) => Math.abs(a - b) < tol;

  function candLabel(c) {
    if (c.kind === "night") return T("cand.night", { n: c.night ?? "", h: f1(c.hours) });
    if (c.kind === "cairn") return T("cand.cairn_night", { n: c.night ?? "", name: c.name || "" }).trim();
    const pass = c.passes > 1 ? T("cand.pass", { n: c.pass }) : "";
    if (c.kind === "hut") {               // SP-536: the built-in OpenStreetMap list, told apart from the GPX's own
      const en = window.I18N && window.I18N.locale && window.I18N.locale !== "zh-TW";
      const name = (en && c.name_en) || c.name || T("osm_noname");
      return `${name} · ${T(c.osm_kind === "camp_site" ? "osm_camp" : "osm_hut")}${pass}`;
    }
    return `${c.name || T("cand.camp")}${pass}`;
  }

  function open(o) {
    ensureCss();
    const total = +o.total || (o.profile?.km?.length ? o.profile.km[o.profile.km.length - 1] : 0);
    const opt = { ...o, total };
    const cands = (o.candidates || []).filter((c) => c.km > 0.05 && c.km < total - 0.05).sort((a, b) => a.km - b.km);
    let splits = [...(o.proposal ? o.proposal.splits : o.splits || [])].sort((a, b) => a - b);
    let days = Math.max(1, Math.round((o.proposal && o.proposal.days) || o.days || 1));
    const dlg = document.createElement("dialog");
    dlg.className = "dsplit";
    dlg.setAttribute("aria-labelledby", "dsplit-h");
    document.body.appendChild(dlg);
    const W = 640, H = 170, PADL = 4, PADR = 4, TOP = 24, BOT = 19;
    const X = (k) => PADL + (W - PADL - PADR) * (total ? k / total : 0);
    const K = (x) => Math.max(0, Math.min(total, (x - PADL) / (W - PADL - PADR) * total));
    const pr = o.profile || { km: [], z: [] };
    const zs = pr.z.filter(Number.isFinite), zmin = zs.length ? Math.min(...zs) : 0, zmax = zs.length ? Math.max(...zs) : 1;
    const Y = (z) => TOP + (H - TOP - BOT) * (1 - (z - zmin) / ((zmax - zmin) || 1));
    const SNAP_PX = 14;

    dlg.innerHTML = `<h2 id="dsplit-h">${esc(T("title"))}</h2><div class="ds-meta">${esc(o.name || "")}</div>
      <div class="ds-row"><span class="ds-days" role="group" aria-label="${esc(T("days"))}">${esc(T("days"))}
        <button type="button" class="btn" data-dd="-1" aria-label="${esc(T("days_less"))}">−</button><b></b>
        <button type="button" class="btn" data-dd="1" aria-label="${esc(T("days_more"))}">+</button></span></div>
      <div class="ds-src" aria-live="polite"></div>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(T("profile_aria"))}"></svg>
      <div class="ds-meta">${esc(T("tap_hint"))}</div>
      <div class="ds-cands"></div>
      <div style="overflow-x:auto"><table><thead><tr><th>${esc(T("th.day"))}</th><th>${esc(T("th.km"))}</th><th>${esc(T("th.gain"))}</th>
        <th>${esc(T("th.loss"))}</th><th>${esc(T("th.time"))}</th></tr></thead><tbody></tbody><tfoot></tfoot></table></div>
      <div class="ds-meta ds-tnote"></div>
      <div class="ds-actions"><button type="button" class="btn ds-left" data-act="even">${esc(T("even"))}</button>
        <button type="button" class="btn" data-act="cancel">${esc(T("cancel"))}</button>
        <button type="button" class="btn primary" data-act="ok">${esc(T("ok"))}</button></div>`;
    const svg = dlg.querySelector("svg");

    function render() {
      const st = stats(opt, splits, days);
      dlg.querySelector(".ds-days b").textContent = String(days);
      dlg.querySelector('[data-dd="-1"]').disabled = days <= 1;
      dlg.querySelector('[data-dd="1"]').disabled = days >= 10;
      // where the days come from
      const src = dlg.querySelector(".ds-src");
      let txt, warn = false, fix = "";
      if (days === 1) txt = T("src.single");
      else if (st.source === "stored") {
        const same = (ref) => ref && splits.length === ref.length && splits.every((x, i) => near(x, ref[i], 0.05));
        // SP-534: the recorded nights wait for 確定; SP-537: a Cairn import's nights are already the day ends
        if (o.proposal && same(o.proposal.splits)) txt = T("src.proposal_" + (o.proposal.kind || "night"), { n: splits.length });
        else if (o.applied && same(o.splits)) txt = T("src.applied_" + o.applied, { n: splits.length });
        else txt = T("src.stored");
      } else if (!splits.length) { txt = T("src.equal", { n: days }); warn = true; }
      else {
        txt = T("src.mismatch", { k: splits.length, n: days, m: days - 1 }); warn = true;
        if (splits.length + 1 <= 10) fix = ` <button type="button" class="btn" data-act="fit">${esc(T("set_days", { n: splits.length + 1 }))}</button>`;
      }
      src.className = "ds-src" + (warn ? " warn" : "");
      src.innerHTML = esc(txt) + fix;
      // the profile: day bands, the climb, candidates (dotted), day ends (solid, a knob on top)
      let g = "";
      const used = [0, ...st.cuts, total];
      for (let i = 0; i + 1 < used.length; i++) {
        const a = X(used[i]), b = X(used[i + 1]);
        g += `<rect x="${a.toFixed(1)}" y="${TOP}" width="${Math.max(0, b - a).toFixed(1)}" height="${H - TOP - BOT}" fill="${i % 2 ? css("--line", "#e5e7eb") : "transparent"}" opacity=".45"/>`;
        g += `<text x="${((a + b) / 2).toFixed(1)}" y="${H - 3}" font-size="13" text-anchor="middle" fill="${css("--muted", "#777")}">${esc(T("day_short", { n: i + 1 }))}</text>`;
      }
      if (pr.km.length > 1) {
        const pts = pr.km.map((k, i) => `${X(k).toFixed(1)},${Y(pr.z[i]).toFixed(1)}`).join(" ");
        g += `<polygon points="${X(pr.km[0]).toFixed(1)},${H - BOT} ${pts} ${X(pr.km[pr.km.length - 1]).toFixed(1)},${H - BOT}" fill="${css("--bar", "#9ca3af")}" opacity=".35"/>`;
        g += `<polyline points="${pts}" fill="none" stroke="${css("--bar", "#6b7280")}" stroke-width="1.2"/>`;
      }
      for (const c of cands) {
        const x = X(c.km).toFixed(1);
        g += `<line x1="${x}" x2="${x}" y1="${TOP - 4}" y2="${H - BOT}" stroke="${css("--muted", "#888")}" stroke-dasharray="2 3"/>`
          + `<text x="${x}" y="${TOP - 7}" font-size="14" text-anchor="middle">${ICON[c.kind] || "•"}</text>`;
      }
      const grey = st.source !== "stored";
      for (const k of splits) {
        const x = X(k).toFixed(1), col = grey ? css("--muted", "#888") : css("--accent", "#2563eb");
        g += `<line x1="${x}" x2="${x}" y1="${TOP}" y2="${H - BOT}" stroke="${col}" stroke-width="2"${grey ? ' stroke-dasharray="5 3"' : ""}/>`
          + `<circle cx="${x}" cy="${TOP + 6}" r="6" fill="${col}"/>`;
      }
      svg.innerHTML = g;
      // the candidates as buttons
      const box = dlg.querySelector(".ds-cands");
      box.innerHTML = cands.length
        ? `<span class="ds-meta">${esc(T("cands"))}</span>` + cands.map((c, i) => {
          const on = splits.some((x) => near(x, c.km, 0.05));
          return `<button type="button" class="ds-chip${on ? " on" : ""}" aria-pressed="${on}" data-ci="${i}">${ICON[c.kind] || ""} ${esc(candLabel(c))} · ${f1(c.km)} km</button>`;
        }).join("")
        : `<span class="ds-meta">${esc(T("no_cands"))}</span>`;
      // SP-536 (ODbL): the OpenStreetMap credit wherever its huts are shown
      if (cands.some((c) => c.src === "osm")) {
        const a = o.osmAttr || {};
        box.innerHTML += `<div class="ds-meta ds-osm">${esc(T("osm_note"))} <a href="${esc(a.url || "https://www.openstreetmap.org/copyright")}"`
          + ` target="_blank" rel="noopener noreferrer">${esc(a.text || "© OpenStreetMap contributors")}</a></div>`;
      }
      // each day
      const D = st.days, tot = D.reduce((s, d) => ({ km: s.km + d.km, gain_m: s.gain_m + d.gain_m, loss_m: s.loss_m + d.loss_m, h: d.h == null || s.h == null ? null : s.h + d.h }), { km: 0, gain_m: 0, loss_m: 0, h: 0 });
      dlg.querySelector("tbody").innerHTML = D.map((d) => `<tr><td>${esc(T("day_n", { n: d.day }))}</td><td>${f1(d.km)}</td><td>${n0(d.gain_m)}</td>
        <td>${n0(d.loss_m)}</td><td>${d.h == null ? "–" : hm(d.h)}</td></tr>`).join("");
      dlg.querySelector("tfoot").innerHTML = `<tr><td>${esc(T("total"))}</td><td>${f1(tot.km)}</td><td>${n0(tot.gain_m)}</td><td>${n0(tot.loss_m)}</td><td>${tot.h == null ? "–" : hm(tot.h)}</td></tr>`;
      const timed = (o.pieces || []).length && (o.pieces || []).every((p) => Number.isFinite(p[4]));
      dlg.querySelector(".ds-tnote").textContent = timed ? T("time_segments") : Number.isFinite(o.hours) ? T("time_ep") : T("time_none");
    }

    const setSplits = (s) => { splits = [...s].map((x) => Math.round(x * 100) / 100).sort((a, b) => a - b); render(); };
    // SNAP_PX screen pixels in the drawing's units (the svg is scaled to the dialog's width)
    const tol = () => SNAP_PX * W / (svg.getBoundingClientRect().width || W) / ((W - PADL - PADR) / (total || 1));
    const snap = (k) => {
      let best = null;
      for (const c of cands) if (Math.abs(c.km - k) < tol() && (best == null || Math.abs(c.km - k) < Math.abs(best - k))) best = c.km;
      return best ?? k;
    };
    const kmOf = (ev) => {
      const r = svg.getBoundingClientRect();
      return K((ev.clientX - r.left) / (r.width || 1) * W);
    };
    let drag = null;
    svg.addEventListener("pointerdown", (ev) => {
      const k = kmOf(ev), i = splits.findIndex((x) => Math.abs(x - k) < tol());
      drag = { i, x0: ev.clientX, moved: false };
      svg.setPointerCapture?.(ev.pointerId);
      ev.preventDefault();
    });
    svg.addEventListener("pointermove", (ev) => {
      if (!drag || drag.i < 0) return;
      if (Math.abs(ev.clientX - drag.x0) > 4) drag.moved = true;
      if (!drag.moved) return;
      const k = snap(kmOf(ev));
      if (k <= 0.05 || k >= total - 0.05) return;
      splits[drag.i] = Math.round(k * 100) / 100;
      render();
    });
    const up = (ev) => {
      if (!drag) return;
      const d = drag; drag = null;
      if (d.i >= 0 && d.moved) { setSplits(splits); return; }                 // a drag: keep where it ended
      if (d.i >= 0) { setSplits(splits.filter((_, j) => j !== d.i)); return; } // a tap on a day end: take it away
      const k = snap(kmOf(ev));
      if (k > 0.05 && k < total - 0.05) setSplits([...splits, k]);             // a tap elsewhere: a new day end
    };
    svg.addEventListener("pointerup", up);
    svg.addEventListener("pointercancel", () => { drag = null; render(); });

    return new Promise((resolve) => {
      let answer = null;
      dlg.addEventListener("click", (ev) => {
        const b = ev.target.closest("button");
        if (!b) return;
        if (b.dataset.dd) { days = Math.max(1, Math.min(10, days + +b.dataset.dd)); render(); return; }
        if (b.dataset.ci != null) {
          const c = cands[+b.dataset.ci], i = splits.findIndex((x) => near(x, c.km, 0.05));
          setSplits(i >= 0 ? splits.filter((_, j) => j !== i) : [...splits, c.km]);
          return;
        }
        const act = b.dataset.act;
        if (act === "fit") { days = splits.length + 1; render(); }
        else if (act === "even") setSplits([]);
        else if (act === "cancel") dlg.close();
        else if (act === "ok") { answer = { splits: [...splits], days }; dlg.close(); }
      });
      dlg.addEventListener("close", () => { dlg.remove(); resolve(answer); });
      render();
      dlg.tabIndex = -1;
      dlg.showModal();
      dlg.focus();                          // not the first button (−): Enter / a stray tap changes nothing
    });
  }

  window.DaySplit = { cuts, dayStats, stats, open };
})();
