// 課表卡片 (SP-395; docs/research/schedule-chip-ui.md §6–7, §10 ①): the parts a session card is
// drawn from, shared by the 課表 page (schedule.html chipHtml) and the 總覽 本週 list (overview.html
// sessHtml), so one session looks the same on both. One channel per question:
//   是什麼課  the type icon in a 22px tile, never coloured        SessChip.kind(s) / actKind(a), icon(key)
//   偏離多少  the colour of the card's left bar and of the ring   SessChip.cls(s): done cp-ok|cp-part|cp-off
//   發生了什麼 the ring's shape (Linear style)                   SessChip.ring(status, pct, {prov})
//   做了沒    outline (fut) vs left bar (done); 跳過 = grey (miss)
// Completion is green / yellow / grey with a shape each (WCAG 2.2 SC 1.4.1) — never red (SP-393:
// red is only a failed push to the watch). status() mirrors engine/compliance.status_of; the backend's
// green / yellow / red `level` stays for the coach's views, a card reads only the status.
//   <script src="/api/v1/static/dashicons.js"></script>
//   <script src="/api/v1/static/sesschip.js"></script>      (not deferred: the pages render with it)
(function () {
  const W = typeof window !== "undefined" ? window : globalThis;
  // words from common.json (common.legend.*), shared by every page that draws a card
  const T = (k, p) => (W.I18N ? W.I18N.t("common.legend." + k, p) : k);
  const DI = (k) => (W.DI ? W.DI(k) : "");

  // ---- which icon -------------------------------------------------------------------------------
  // 熱適應課 (engine/heat_plan.py) keep kind easy / long, tagged by the title — the sun replaces 「熱」
  const HEAT = /熱適應|熱水浴|桑拿/;
  // a 強度課 by its family (SP-79): 有氧間歇 → z3, VO2max 間歇 / 速度 → z5; one with no family by its
  // ladder rung or title (the 總覽's old rule)
  const FAM = { aerobic: "z3", vo2max: "z5", speed: "z5" };
  function kind(s) {
    const k = s.kind, t = s.title || "";
    if (k === "test" || k === "race" || k === "notice" || k === "rest") return k;
    if (k === "quality") {
      const f = s.quality_family && FAM[s.quality_family.id];
      return f || (/^z5/i.test(s.rung_key || "") || /VO2max|5 區|Z5/i.test(t) ? "z5" : "z3");
    }
    if (k === "strength") return /ME|肌耐力/.test(t) ? "loaded" : "strength";
    if (k === "heat_passive" || HEAT.test(t)) return "heat";
    if (/^B2B/.test(t) || /^long[23]$/.test(s.gen_key || "")) return "b2b";
    if (/背\s*[\d.]+\s*kg|背包/.test(t) || /^(carry|me)$/.test(s.gen_key || "")) return "loaded";
    if (k === "long" || k === "mountain") return "long";
    if (k === "hike") return "hike";
    return "easy";
  }
  // an activity no session holds: its category (engine/overview.CATEGORIES); a run by its session
  // class (workout_review.type_icon) when that is one of the card icons
  const CAT = { bike: "bike", hike: "hike", walk: "hike", strength: "strength", other: "other" };
  const RUN_ICONS = ["easy", "z3", "z5", "long", "test", "strength"];
  function actKind(a) {
    if (CAT[a.category]) return CAT[a.category];
    const ic = a.session && a.session.icon;
    return RUN_ICONS.includes(ic) ? ic : "easy";
  }

  // ---- status (engine/compliance.status_of; no "open" here: a past session not done is 還沒做) -----
  function status(s) {
    if (s.state === "missed") return "missed";
    if (s.state !== "done") return "todo";
    const c = s.compliance;
    if (!c) return "done";
    if (c.off_plan || (c.wrong_type && !c.soft)) return "off_plan";
    return c.level === "green" ? "done" : "partial";
  }
  const prov = (s) => !!(s.provisional && s.state === "active");
  const CP = { done: "cp-ok", partial: "cp-part", off_plan: "cp-off" };
  function cls(s) {
    const st = status(s);
    if (CP[st]) return "done " + CP[st];
    if (st === "missed") return "miss";
    return prov(s) ? "fut prov" : "fut";
  }
  function label(st, pct, o) {
    if (st === "done") return T("status.done");
    if (st === "partial") return T("status.partial") + (pct != null ? ` ${pct}%` : "");
    if (st === "off_plan") return T("status.off_plan");
    if (st === "missed") return T("status.missed");
    return o && o.prov ? T("status.prov") : T("status.todo");
  }

  // ---- the ring (16 × 16; shapes from docs/research/schedule-chip-ui-mockup.html) ----------------
  const R = 6, HALF = Math.PI * R;          // the pie: a circle of r = R/2 stroked R wide, dashed to the %
  const n2 = (v) => +v.toFixed(2);
  const circ = (st, inner, dash) => `<svg class="sc-ring sc-r-${st}" viewBox="0 0 16 16" aria-hidden="true">` +
    `<circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.5"${dash ? ` stroke-dasharray="${dash}"` : ""}/>${inner}</svg>`;
  const G = {
    check: () => '<path d="m5 8.3 2 2L11 6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    slash: () => '<path d="M11 5 5 11" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>',
    pie: (pct) => {
      const f = pct == null ? 1 : Math.max(0, Math.min(1, pct / 100));
      return `<circle cx="8" cy="8" r="${R / 2}" fill="none" stroke="currentColor" stroke-width="${R}" ` +
        `stroke-dasharray="${n2(f * HALF)} ${n2(HALF)}" transform="rotate(-90 8 8)"/>`;
    },
  };
  function ring(st, pct, o) {
    if (st === "done") return circ(st, G.check());
    if (st === "partial") return circ(st, G.pie(pct));
    if (st === "off_plan") return circ(st, G.slash());
    if (st === "missed") return circ(st, "");
    return circ("todo", "", o && o.prov ? "2 1.6" : "");
  }

  // ---- the type tile: the icon only, ↻ / ✎ (plan_mark.js PlanMark.icon(s, true)) in its corner ----
  function icon(k, o) {
    o = o || {};
    if (k === "quality") k = FAM[o.fam] || "z3";
    return `<span class="sc-ic" aria-hidden="true">${DI(k)}${o.badge || ""}</span>`;
  }

  // the look both pages share; a page lays the card out (grid, sizes) around these
  const CSS = `.sc-ic { position: relative; flex: none; width: 22px; height: 22px; border-radius: 6px; display: grid; place-items: center;
    color: var(--type, var(--muted)); }
  .sc-ic svg { width: 15px; height: 15px; }
  .sc-ic .pmk-badge { top: -5px; right: -6px; }
  .sc-ring { width: 16px; height: 16px; flex: none; display: block; }
  .sc-r-done { color: var(--good); }
  .sc-r-partial, .sc-r-off_plan { color: var(--watch); }
  .sc-r-missed, .sc-r-todo { color: var(--faint); }
  .sc { border: 1px solid var(--line); background: var(--cell, var(--panel)); color: var(--text); }
  .sc.cp-ok { --cp: var(--good); }
  .sc.cp-part, .sc.cp-off { --cp: var(--watch); }
  .sc.done { border-left: 4px solid var(--cp); }
  .sc.done .sc-ic { color: var(--text); }
  .sc.fut { color: var(--muted); }
  .sc.fut .sc-t { color: var(--text); font-weight: 500; }
  .sc.prov { border-style: dashed; }
  .sc.miss, .sc.miss .sc-t, .sc.miss .sc-ic { color: var(--faint); }
  .sc.miss .sc-t { font-weight: 500; }
  .sc.act { border-style: dotted; }
  .sc.act .sc-t { font-weight: 500; }
  .sc-leg { font-size: 12px; color: var(--muted); }
  .sc-leg > summary { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; list-style: none; color: var(--text); }
  .sc-leg > summary::-webkit-details-marker { display: none; }
  .sc-leg > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
  .sc-q { display: inline-grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; border: 1px solid var(--line);
    font: 600 11px/1 system-ui, sans-serif; color: var(--muted); background: var(--panel); }
  .sc-leg-pop { margin-top: 6px; max-width: 640px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 6px 10px 8px; }
  .sc-leg table { border-collapse: collapse; width: 100%; }
  .sc-leg th { text-align: left; color: var(--text); font-weight: 600; padding: 8px 0 3px; }
  .sc-leg td { padding: 2px 8px 2px 0; vertical-align: top; line-height: 1.45; }
  .sc-leg td:first-child { width: 24px; }
  .sc-leg td b { color: var(--text); font-weight: 600; }
  .sc-leg-types { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 2px 12px; }
  .sc-leg-types span { display: inline-flex; align-items: center; gap: 4px; }
  .sc-leg-act { display: inline-block; width: 16px; height: 12px; border: 1px dotted var(--muted); border-radius: 3px; }
  .sc-leg .pmk { font-weight: 700; } .sc-leg .pmk-auto { color: var(--accent); } .sc-leg .pmk-user { color: var(--muted); }`;
  if (typeof document !== "undefined" && document.createElement) {
    const st = document.createElement("style");
    st.textContent = CSS;
    (document.head || document.documentElement).appendChild(st);
  }

  // the type's name (common.legend.type.*): the icon is never alone — hover, aria-label, legend
  const typeName = (k) => T("type." + k);

  // ---- the legend (design §7): one 「? 圖例」 on the 課表 page and the 總覽 本週 list, three sections —
  // 類型 / 完成度 / 其他 (+ the page's own rows: the watch, the day grid …). Thresholds are written only
  // here, from the payload's `compliance_levels` (engine/compliance.COMPLIANCE); the intensity's 80 % /
  // 50 % (plan_match.graded) are in the words.
  const TYPES = ["easy", "z3", "z5", "long", "b2b", "loaded", "hike", "test", "strength", "race", "heat", "rest", "notice", "bike", "other"];
  const STATES = [["done"], ["partial"], ["off_plan"], ["missed"], ["todo"], ["todo", true]];
  const pctOf = (v, d) => Math.round((v != null ? v : d) * 100);
  const esc = (x) => String(x == null ? "" : x).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tipOf = (st, prov, lv) => T((prov ? "prov" : st) + "_tip", { green: pctOf(lv.green, 0.2), yellow: pctOf(lv.yellow, 0.5) });
  // the 完成度 section as plain text (the 課表 dialog's ?, 課表統計's 照計畫完成 ?): 「完成：時間和 TSS …」
  // one per line, then how intensity and a walking session are judged (levels_note, SP-398)
  function levelsText(lv) {
    lv = lv || {};
    return STATES.slice(0, 4).map(([st]) => T("line", { label: label(st), tip: tipOf(st, false, lv) })).join("\n") +
      "\n" + T("levels_note");
  }
  function legend(o) {
    o = o || {};
    const lv = o.levels || {};
    const head = (k) => `<tr><th colspan="2" scope="colgroup">${esc(T(k))}</th></tr>`;
    const row = (ic, txt) => `<tr><td>${ic}</td><td>${txt}</td></tr>`;
    const other = [[`<span class="pmk pmk-auto" aria-hidden="true">↻</span>`, esc(T("auto"))], [`<span class="pmk pmk-user" aria-hidden="true">✎</span>`, esc(T("user"))],
      [`<span class="sc-leg-act" aria-hidden="true"></span>`, esc(T("act"))], ...(o.other || [])];
    return `<details class="sc-leg"><summary aria-label="${esc(T("aria"))}"><span class="sc-q" aria-hidden="true">?</span>${esc(T("title"))}</summary>` +
      `<div class="sc-leg-pop"><table>` + head("h_type") +
      `<tr><td colspan="2"><div class="sc-leg-types">${TYPES.map((k) => `<span>${icon(k)}${esc(typeName(k))}</span>`).join("")}</div></td></tr>` +
      head("h_status") + STATES.map(([st, pv]) => row(ring(st, 65, { prov: pv }), `<b>${esc(label(st, null, { prov: pv }))}</b> ${esc(tipOf(st, pv, lv))}`)).join("") +
      head("h_other") + other.map(([ic, txt]) => row(ic, txt)).join("") + `</table></div></details>`;
  }

  // SP-402: graded on time only because the run's HR is unreliable (compliance `uncertain`) — a hover /
  // dialog line, the ring stays what the time says
  const hrNote = (cp) => (cp && cp.uncertain ? T("hr_uncertain") : "");

  W.SessChip = { kind, actKind, typeName, status, cls, label, ring, icon, legend, levelsText, hrNote, CSS };
})();
