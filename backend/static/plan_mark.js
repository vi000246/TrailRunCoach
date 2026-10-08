// 課表標記 (SP-318): a session's `mark` from GET /overview/plan/calendar (engine/plan_marks.py),
// drawn the same way on the 課表 page and the 總覽:
//   auto  ↻  changed by the automatic adjustment (rule, reason, before → after in the details)
//   user  ✎  the user's own (edited, added, or restored by 復原)
//   none      nothing
// On the session only the small glyph; the details go in the tooltip / the session dialog.
//   <script src="/api/v1/static/plan_mark.js"></script>   (not deferred: the pages render with it)
(function () {
  const T = (k, p) => (window.I18N ? window.I18N.t("common.planmark." + k, p) : k);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const RULES = ["missed_easy", "missed_quality", "missed_long", "overhard", "rpe_hard", "fatigue", "cp", "plan"];
  const GLYPH = { auto: "↻", user: "✎" };
  const st = document.createElement("style");
  st.textContent = `.pmk { font-weight: 700; font-size: 11px; line-height: 1; }
  .pmk.pmk-auto { color: var(--accent, #2f6fde); }
  .pmk.pmk-user { color: var(--muted, #888); }
  .pmk-box { margin: 4px 0 0; font-size: 12.5px; }
  .pmk-box ul { margin: 2px 0 0; padding-left: 18px; }`;
  (document.head || document.documentElement).appendChild(st);

  const state = (s) => (s && s.mark && s.mark.state) || "none";
  const rule = (m) => T("rule." + (RULES.includes(m.rule) ? m.rule : "plan"));
  const day = (v) => (v && window.I18N ? I18N.fmt.date(v, "mdw") : v || "—");
  // one before → after line per changed field; the long text fields only say they changed
  function diffLines(m, kinds) {
    return (m.diff || []).map((d) => {
      const k = d.key;
      if (k === "detail" || k === "terrain" || k === "protocol") return T("field." + k + "_changed");
      const f = k === "day" ? day : k === "minutes" ? (v) => (v == null ? "—" : T("minutes", { n: v }))
        : k === "kind" ? (v) => (kinds && kinds[v]) || v || "—" : (v) => (v == null || v === "" ? "—" : v);
      return `${T("field." + k)} ${f(d.before)} → ${f(d.after)}`;
    });
  }
  // the label the session's tooltip / aria-label starts with
  function label(s) {
    const x = state(s);
    if (x === "auto") return T("auto_label", { rule: rule(s.mark) });
    if (x === "user") return s.mark.restored ? T("restored_label") : T("user_label");
    return "";
  }

  window.PlanMark = {
    state,
    // the small glyph on the session ("" when untouched)
    icon(s) {
      const x = state(s);
      return GLYPH[x] ? `<span class="pmk pmk-${x}" aria-hidden="true">${GLYPH[x]}</span>` : "";
    },
    // the tooltip lines: label, reason, before → after (none when untouched)
    lines(s, kinds) {
      const x = state(s);
      if (x === "none") return [];
      const m = s.mark;
      const out = [`${GLYPH[x]} ${label(s)}`];
      if (x === "auto") {
        if (m.reason) out.push(m.reason);
        out.push(...diffLines(m, kinds));
      } else if (m.restored) out.push(T("restored_tip"));
      return out;
    },
    label,
    // the session dialog: the same, as HTML
    detail(s, kinds) {
      const x = state(s);
      // the dialog's subtitle already says 已修改 / 自訂: a plain 「yours」 adds nothing there
      if (x === "none" || (x === "user" && !s.mark.restored)) return "";
      const rest = this.lines(s, kinds).slice(1);
      return `<div class="pmk-box"><span class="pmk pmk-${x}" aria-hidden="true">${GLYPH[x]}</span> <b>${esc(label(s))}</b>` +
        (rest.length ? `<ul>${rest.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : "") + `</div>`;
    },
  };
})();
