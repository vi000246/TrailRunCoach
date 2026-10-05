/*
 * The 課表 page's structured workout editor (docs/plans/workout-editor.plan.md §3.3,
 * engine/workout_steps.py, api/plan_sessions.py /steps/*).
 *
 *   const we = WorkoutEditor.mount(el, { api, session: () => ({uid, kind, title, …}), onView });
 *   we.open({ uid, ro, kind })      derive (stored or from the text; nothing stored)
 *   we.refresh()                    the dialog's fields changed (目標用, kind, minutes …)
 *   we.rederive(extra)              a new library template was chosen in the dialog
 *   we.payload()                    {steps} to save, {steps: null} to clear, or null (untouched)
 *   we.load(doc, {ro})              show a given structure (the 範本 page: a template, not a session)
 *
 * 插入範本: category tabs, then 主課強度 (SP-84: each row's `target_types`, workout_steps.target_types;
 * kept per viewer in localStorage under the 範本 page's key), then the 推薦 block and the rows.
 *
 * Options: saveTemplate (default true): the 「儲存成範本」 button (POST /sessions/{uid}/save-as-template,
 * else /steps/templates/user; SP-36). A structure made from a user template keeps its id (`tpl`)
 * and, once saved as a session, its own copy of the route profile (`route`, carried through as is):
 * when there is a route GPX, POST /steps/check returns `elev` (engine/user_templates.route_elevation)
 * and the chart switches to the route's distance axis (km; each step from `elev.x`), the elevation
 * drawn as a light background like the chart viewer's (wko5_viewer.html drawClimbProfile).
 * Without one the chart stays on the time axis.
 *
 * Every number shown (targets, totals, issues, the watch preview) comes from
 * POST /steps/check — the server resolves; this file only edits the structure.
 * Zone colours: the dataviz reference sequential blue (validate_palette.js --ordinal:
 * light #86b6ef→#104281 on #fcfcfb, dark #184f95→#9ec5f4 on #1a1a19, all PASS);
 * height repeats the intensity so colour is never the only cue.
 */
(function () {
  "use strict";
  const STYLE = `
:root { --wz1: #86b6ef; --wz2: #5598e7; --wz3: #2a78d6; --wz4: #1c5cab; --wz5: #104281; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --wz1: #184f95; --wz2: #256abf; --wz3: #3987e5; --wz4: #6da7ec; --wz5: #9ec5f4; } }
:root[data-theme="dark"] { --wz1: #184f95; --wz2: #256abf; --wz3: #3987e5; --wz4: #6da7ec; --wz5: #9ec5f4; }
/* the route's elevation behind the bars: the chart viewer's neutral --cp-elev (light area + thin line) */
:root { --we-elev: #77756f; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --we-elev: #9a9890; } }
:root[data-theme="dark"] { --we-elev: #9a9890; }
.we-save { display: grid; gap: 6px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); font-size: 12.5px; }
.we-save .row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.we-save input.nm { flex: 1 1 200px; min-width: 0; }
.we-save .cats { display: flex; flex-wrap: wrap; gap: 4px; }
.we-save .cats label { display: inline-flex !important; align-items: center; gap: 4px; border: 1px solid var(--line); border-radius: 12px; padding: 1px 9px;
  cursor: pointer; font-size: 12px !important; color: var(--text) !important; }
.we-save .cats label:has(input:checked) { border-color: var(--accent); color: var(--accent) !important; background: color-mix(in srgb, var(--accent) 12%, transparent); }
.we-save .cats input { width: auto !important; margin: 0; }
.we-save .msg:empty { display: none; }
.we-save .msg.err { color: var(--bad); } .we-save .msg.ok { color: var(--good); }
.we-pop button.t .mine { font-size: 11px; color: var(--accent); border: 1px solid var(--accent); border-radius: 8px; padding: 0 5px; white-space: nowrap; }
dialog.sd.we-wide { width: min(880px, 96vw); }
.we-tpb { color: var(--watch); font-size: 11.5px; white-space: nowrap; }
.we-tpl { font-size: 11.5px; margin-left: 4px; }
.we { border: 1px solid var(--line); border-radius: 9px; padding: 0; min-width: 0; }
.we > summary { cursor: pointer; padding: 7px 10px; font-size: 13px; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; list-style: none; }
.we > summary::-webkit-details-marker { display: none; }
.we > summary::before { content: "▸"; color: var(--faint); }
.we[open] > summary::before { content: "▾"; }
.we > summary b { font-weight: 700; }
.we-sumtxt { color: var(--muted); font-size: 12.5px; font-variant-numeric: tabular-nums; min-width: 0; overflow-wrap: anywhere; }
.we-body { padding: 0 10px 10px; display: grid; gap: 8px; min-width: 0; }
.we-chart { position: relative; }
.we-chart svg { display: block; width: 100%; height: 124px; overflow: visible; }
.we-chart .blk { cursor: pointer; }
.we-tip { position: absolute; pointer-events: none; background: var(--panel); color: var(--text); border: 1px solid var(--line); border-radius: 8px;
  padding: 6px 8px; font-size: 12px; line-height: 1.45; box-shadow: 0 4px 14px rgba(0,0,0,.14); white-space: nowrap; transform: translate(-50%, -100%); z-index: 5; }
.we-legend { display: flex; flex-wrap: wrap; gap: 3px 12px; font-size: 11.5px; color: var(--muted); align-items: center; }
.we-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
.we-stats { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 12.5px; color: var(--muted); align-items: baseline; }
.we-stats b { color: var(--text); font-size: 14.5px; font-variant-numeric: tabular-nums; }
.we-issues { list-style: none; margin: 0; padding: 0; display: grid; gap: 3px; font-size: 12.5px; }
.we-issues li { display: flex; gap: 6px; align-items: baseline; cursor: default; }
.we-issues li[data-id] { cursor: pointer; }
.we-issues .ic { width: 1.1em; flex: none; text-align: center; font-weight: 700; }
.we-issues .err { color: var(--bad); } .we-issues .warn { color: var(--watch); } .we-issues .info { color: var(--muted); }
.we-tools { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.we-tools .sp { flex: 1; }
.we-menu { position: relative; display: inline-flex; align-items: center; }
.we .btn.we-tplbtn { font-weight: 700; }
.we-sumtpl { margin-left: auto; font-size: 12px; padding: 2px 9px; border-radius: 12px; border: 1px solid var(--accent); color: var(--accent); background: none; cursor: pointer; }
.we-sumtpl:hover, .we-sumtpl:focus-visible { background: color-mix(in srgb, var(--accent) 12%, transparent); }
.we-pop { position: absolute; z-index: 40; top: calc(100% + 4px); left: 0; width: min(440px, 92vw); max-height: 60vh; overflow: auto; background: var(--panel);
  border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 10px 28px rgba(0,0,0,.18); padding: 6px; }
.we-pop .tabs { display: flex; flex-wrap: wrap; gap: 4px; padding: 2px 4px 6px; }
.we-pop .tabs button { border: 1px solid var(--line); background: none; color: var(--muted); border-radius: 12px; padding: 2px 10px; font-size: 12.5px; cursor: pointer; }
.we-pop .tabs button.on { border-color: var(--accent); color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); font-weight: 600; }
.we-pop .tabs.sub button { font-size: 12px; padding: 1px 9px; }
.we-pop .g { font-size: 11.5px; color: var(--faint); padding: 6px 8px 2px; }
.we-pop button.t { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 2px 10px; align-items: center; width: 100%; text-align: left; border: 0; background: none; color: var(--text);
  font-size: 13px; padding: 6px 8px; border-radius: 6px; cursor: pointer; }
.we-pop button.t svg { grid-row: span 3; width: 96px; height: 26px; display: block; }
.we-pop button.t .pur { font-size: 11.5px; color: var(--text); overflow-wrap: anywhere; }
.we-pop button.t .fam { font-size: 11px; color: var(--muted); border: 1px solid var(--line); border-radius: 8px; padding: 0 5px; white-space: nowrap; }
.we-pop button.t .src { font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }
.we-pop button.t:hover, .we-pop button.t:focus-visible { background: var(--soft); }
.we-pop .mode { display: flex; flex-wrap: wrap; gap: 10px; padding: 4px 8px 6px; font-size: 12.5px; border-top: 1px solid var(--line); margin-top: 4px; }
.we-pop .mode label { display: inline-flex !important; gap: 4px; align-items: center; color: var(--text) !important; font-size: 12.5px !important; }
.we-pop .empty { color: var(--muted); font-size: 12.5px; padding: 8px; }
.we-pop .g.rec { color: var(--accent); font-weight: 700; display: flex; gap: 6px; align-items: center; }
.we-pop button.t .src.why { color: var(--text); }
.we-pop details.more { border-top: 1px solid var(--line); margin-top: 4px; }
.we-pop details.more > summary { cursor: pointer; font-size: 12.5px; color: var(--muted); padding: 6px 8px; }
.we-lap { color: var(--muted); font-size: 12px; white-space: nowrap; }
.we-list { display: grid; gap: 5px; }
.we-row { display: grid; grid-template-columns: 18px 84px 150px minmax(0, 1fr) minmax(0, .8fr) auto; gap: 6px; align-items: center; padding: 5px 6px;
  border: 1px solid var(--line); border-left: 4px solid var(--zc, var(--bar)); border-radius: 8px; background: var(--panel); min-width: 0; }
.we-row.sel { outline: 2px solid color-mix(in srgb, var(--accent) 55%, transparent); }
.we-row.drop, .we-rep.drop { box-shadow: 0 -3px 0 var(--accent); }
.we-row:focus-visible { outline: 2px solid var(--accent); }
.we .grip { cursor: grab; color: var(--faint); user-select: none; text-align: center; font-size: 14px; line-height: 1; }
dialog.sd .we select, dialog.sd .we input { font-size: 13px; padding: 3px 6px; border-radius: 6px; width: auto; min-width: 0; }
dialog.sd .we input.dur { width: 4.6em; font-variant-numeric: tabular-nums; }
dialog.sd .we input.num { width: 4.8em; font-variant-numeric: tabular-nums; }
dialog.sd .we input.note { width: 100%; }
.we-dur { display: flex; gap: 4px; align-items: center; min-width: 0; }
.we-tbtn { display: flex; flex-wrap: wrap; gap: 0 6px; align-items: baseline; text-align: left; border: 1px dashed transparent; background: none; color: var(--text);
  padding: 2px 5px; border-radius: 6px; cursor: pointer; min-width: 0; font-size: 13px; }
.we-tbtn:hover, .we-tbtn:focus-visible, .we-tbtn.on { border-color: var(--line); background: var(--bg); }
.we-tbtn b { font-weight: 600; font-variant-numeric: tabular-nums; }
.we-tbtn .s { color: var(--muted); font-size: 11.5px; }
.we-tbtn .src { font-size: 10.5px; border-radius: 8px; padding: 0 6px; border: 1px solid var(--line); color: var(--muted); }
.we-tbtn .src.ov { border-color: var(--accent); color: var(--accent); }
.we-tbtn .w { color: var(--watch); font-size: 11.5px; } .we-tbtn .e { color: var(--bad); font-size: 11.5px; }
.we-acts { display: flex; gap: 1px; }
.we-acts button { border: 0; background: none; color: var(--faint); cursor: pointer; padding: 3px 5px; border-radius: 5px; font-size: 13px; line-height: 1; }
.we-acts button:hover, .we-acts button:focus-visible { color: var(--text); background: var(--soft); }
.we-tg { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 6px 8px; border-radius: 7px; background: var(--bg); font-size: 12.5px; }
.we-tg .res { color: var(--muted); font-variant-numeric: tabular-nums; }
.we-rep { border: 1px dashed var(--line); border-radius: 10px; padding: 5px; display: grid; gap: 5px; background: color-mix(in srgb, var(--soft) 45%, transparent); min-width: 0; }
.we-rep-h { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 13px; padding: 0 2px; }
.we-rep-h .sp { flex: 1; }
.we-rep-h label { display: inline-flex !important; align-items: center; gap: 4px; color: var(--muted) !important; font-size: 12px !important; }
dialog.sd .we-rep-h input[type=checkbox] { width: auto; }
.we-rep-in { display: grid; gap: 5px; padding-left: 12px; min-height: 26px; }
.we-rep-in:empty::after { content: "把步驟拖進來"; color: var(--faint); font-size: 12px; padding: 4px; }
.we-watch > summary { cursor: pointer; font-size: 13px; font-weight: 600; }
.we-watch ol { margin: 6px 0 0; padding-left: 22px; font-size: 12.5px; font-variant-numeric: tabular-nums; display: grid; gap: 1px; }
.we-watch ol ol { list-style: lower-alpha; margin: 2px 0; }
.we-watch .nm { color: var(--faint); }
.we-lim { list-style: none; margin: 6px 0 0; padding: 0; display: grid; gap: 2px; font-size: 12px; color: var(--muted); }
.we-lim li.hit { color: var(--watch); }
.we-lim li::before { content: "· "; }
.we-lim li.hit::before { content: "! "; font-weight: 700; }
.we-empty { color: var(--muted); font-size: 12.5px; }
.we-ro .we-acts, .we-ro .we-tools, .we-ro .we-sumtpl, .we-ro .we-save { display: none; }
.we-ro .grip { visibility: hidden; }        /* (kept in the row's grid: the columns stay aligned) */
@media (max-width: 699px) {
  .we-chart svg { height: 96px; }
  .we-row { grid-template-columns: 18px minmax(0, 1fr) auto; grid-template-areas: "g k a" "g d d" "g t t" "g n n"; row-gap: 4px; }
  .we-row > .grip { grid-area: g; align-self: start; padding-top: 6px; }
  .we-row > .kind { grid-area: k; justify-self: start; }
  .we-row > .we-acts { grid-area: a; }
  .we-row > .we-dur { grid-area: d; }
  .we-row > .we-tbtn { grid-area: t; }
  .we-row > .nt { grid-area: n; }
  .we-rep-in { padding-left: 4px; }
  .we-tools .sp { display: none; }
  .we-pop { width: calc(100vw - 64px); left: -4px; }
  .we-pop button.t { grid-template-columns: 72px minmax(0, 1fr); }
  .we-pop button.t svg { width: 72px; }
}`;

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const mmss = (s) => { s = Math.round(s || 0); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const parseSec = (v) => { const m = String(v).trim().match(/^(\d+)(?:[:：](\d{1,2}))?$/); return m ? +m[1] * 60 + +(m[2] || 0) : null; };
  // i18n (static/i18n/i18n.js t(key, fallback)): the keys live in the common namespace (common.workout.*,
  // inlined on every page); the zh-TW text is the fallback
  const tr = (k, fb, p) => (window.I18N && window.I18N.t ? window.I18N.t("common." + k, fb, p) : typeof fb === "string" ? fb : k);
  // (new strings: tr(key) / tr(key, params) with no Chinese fallback — the common catalog is always inlined)
  const KIND = { warm: tr("workout.kind.warm"), work: tr("workout.kind.work"), rest: tr("workout.kind.rest"), cool: tr("workout.kind.cool"), other: tr("workout.kind.other") };
  const TYPE = { auto: tr("workout.type.auto"), power: tr("workout.type.power"), hr: tr("workout.type.hr"), pace: tr("workout.type.pace"), rpe: "RPE", none: tr("workout.type.none") };
  // ≈ % CP of RPE 1–10 (workout_steps.RPE_FRAC; the mini chart's height only, 推估)
  const RPE_F = [0, 0.55, 0.62, 0.70, 0.76, 0.82, 0.88, 0.94, 1.00, 1.05, 1.10];
  const opt = (v, l, cur, extra = "") => `<option value="${esc(v)}"${String(v) === String(cur) ? " selected" : ""}${extra}>${esc(l)}</option>`;
  const q = (tip) => `<button type="button" class="qtip" aria-label="${esc(tr("workout.help"))}" data-tip="${esc(tip)}">?</button>`;
  const noTpaceText = () => tr("workout.no_tpace");
  // SP-64: the LTHR (or, under the %HRR / %HRmax 課表心率區間, the max HR) is not believable
  // (engine/threshold_confidence.warn_of → context.thresholds.thr_warn): HR targets get a badge
  const usesHr = (items) => (items || []).some((x) => x.kind === "repeat" ? usesHr(x.items) : ((x.target || {}).type === "hr"));
  const hrWarn = (ctx) => {
    const w = ((ctx || {}).thresholds || {}).thr_warn;
    if (!w) return "";
    return [w.lthr && w.lthr.low ? w.lthr.text : "", w.hrmax && w.hrmax.low ? w.hrmax.text : ""].filter(Boolean).join("；");
  };
  // where threshold pace is estimated (GET /steps/context tpace_link: the Friel pace-zone chart)
  const tpaceLink = (ctx) => {
    const u = (ctx || {}).tpace_link;
    return u ? ` <a class="we-tpl" href="${esc(u)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(tr("workout.no_tpace_link"))}</a>` : "";
  };
  const OPEN_W = 90;
  const kmTxt = (x) => (Math.round((x || 0) * 10) / 10).toString();
  // 時長類型 (SP-38): the options come from the push target's capabilities (context.provider:
  // sync/workout_targets describe() — end_conditions + end_labels); these only without one
  const END_DEFAULT = ["time", "distance", "open"];
  const END_LABEL = { time: tr("workout.end.time"), distance: tr("workout.end.distance"), open: tr("workout.end.open"), load: tr("workout.end.load") };
  const provCaps = (ctx) => (((ctx || {}).provider || {}).capabilities) || null;
  // 「負荷」 entered by feel (SP-57, engine/rpe_load.py): five levels (Borg CR-10 2/4/5/7/10) + minutes;
  // the server turns them into the step's TSS (dur.value) with the athlete's factor (推估)
  const RPE_LEVELS = ["easy", "moderate", "hard", "very_hard", "max"];
  const RPE_MIN = [1, 360];
  // the new dur of a 「負荷」 step after one of its fields changed (null = refuse the input); pure
  // (tests/test_rpe_load.py): lmode tss|rpe, lrpe a level, lmin minutes; estMin = the step's minutes now
  function loadDur(dur, f, v, estMin) {
    const d = { ...dur };
    if (f === "lmode") {
      if (v === "rpe") return d.rpe ? d : { type: "load", value: d.value, rpe: "hard", min: Math.max(RPE_MIN[0], Math.min(RPE_MIN[1], Math.round(estMin || 30))) };
      return { type: "load", value: d.value };
    }
    if (f === "lrpe") return RPE_LEVELS.includes(v) ? { ...d, rpe: v } : null;
    if (f === "lmin") { const m = Math.round(+v); return m >= RPE_MIN[0] && m <= RPE_MIN[1] ? { ...d, min: m } : null; }
    return null;
  }
  const TIP = {
    basis: tr("workout.tip.basis"),
    chart: tr("workout.tip.chart"),
    tss: tr("workout.tip.tss"),
    rules: tr("workout.tip.rules"),
    lastRest: tr("workout.tip.lastRest"),
    watch: tr("workout.tip.watch"),
    tpl: tr("workout.tip.tpl"),
    total: tr("workout.tip.total"),
    pacePct: tr("workout.tip.pacePct"),
    rpe: tr("workout.tip.rpe"),
  };

  let styled = false;
  function style() {
    if (styled) return;
    styled = true;
    const s = document.createElement("style");
    s.textContent = STYLE;
    document.head.appendChild(s);
  }

  let uidN = 0;
  const nid = () => `u${Date.now().toString(36).slice(-4)}${(++uidN).toString(36)}`;
  const clone = (it) => { const c = JSON.parse(JSON.stringify(it)); const re = (x) => { x.id = nid(); (x.items || []).forEach(re); }; re(c); return c; };

  // the 主課強度 filter of 插入範本, shared with the 範本 page (templates.html); storage may be blocked
  const TT_KEY = "templates.target_type";
  const ttLoad = () => { try { return localStorage.getItem(TT_KEY) || "all"; } catch (e) { return "all"; } };
  const ttSave = (v) => { try { localStorage.setItem(TT_KEY, v); } catch (e) { /* not kept */ } };

  async function req(method, url, body) {
    const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    const b = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, body: b };
  }

  class Editor {
    constructor(root, opts) {
      style();
      this.root = root;
      this.o = opts;
      this.doc = null; this.ctx = null; this.view = null;
      this.dirty = false; this.cleared = false; this.stored = false; this.ro = false;
      this.sel = null; this.openT = null; this.auto = {};
      this.seq = 0; this.timer = null; this.key = "";
      this.tpls = null; this.tplFull = true; this.tplCat = null; this.tplSub = null; this.tplTT = ttLoad();
      root.innerHTML = `<details class="we" id="we-box"><summary><b>結構</b><span class="we-sumtxt" id="we-sum">載入中…</span><button type="button" class="we-sumtpl" id="we-sumtpl">範本</button></summary>
        <div class="we-body">
          <div class="we-tools" id="we-tools">
            <span class="we-menu" id="we-tpl"><button class="btn primary we-tplbtn" type="button" data-a="tpl" id="we-tplbtn" aria-haspopup="true" aria-expanded="false">＋ 插入範本 ▾</button>${q(TIP.tpl)}
              <div class="we-pop" id="we-pop" hidden></div></span>
            <button class="btn" type="button" data-a="add-step">＋ 步驟</button>
            <button class="btn" type="button" data-a="add-rep">＋ 重複</button>
            <span class="sp"></span>
            <button class="btn" type="button" data-a="reset" id="we-reset" hidden>還原成系統排的</button>
            <button class="btn" type="button" data-a="savetpl" id="we-savetpl"${opts.saveTemplate === false ? " hidden" : ""}>${esc(tr("workout.save_tpl"))}</button>
            ${q(TIP.basis)}
          </div>
          <div class="we-save" id="we-save" hidden></div>
          <div class="we-chart" id="we-chart"><svg id="we-svg" role="img" aria-label="區段圖：強度隨時間"></svg><div class="we-tip" id="we-tip" hidden></div></div>
          <div class="we-legend" id="we-legend"></div>
          <div class="we-stats" id="we-stats" aria-live="polite"></div>
          <ul class="we-issues" id="we-issues" aria-live="polite"></ul>
          <div class="we-list" id="we-list" data-cont="root"></div>
          <details class="we-watch" id="we-watch"><summary>推到手錶會長這樣 ${q(TIP.watch)}</summary><div id="we-wbody"></div></details>
        </div></details>`;
      this.$ = (id) => root.querySelector("#" + id);
      this.bind();
      if (window.ResizeObserver) new ResizeObserver(() => this.chart()).observe(this.$("we-chart"));
    }

    // ---------------- lifecycle ----------------
    sess() { return this.o.session() || {}; }
    sig(s) { return JSON.stringify([s.kind, s.title, s.minutes, s.target, s.detail, s.protocol, s.variant_key, s.terrain]); }
    async open({ uid, ro, kind } = {}) {
      this.dirty = false; this.cleared = false; this.sel = null; this.openT = null; this.auto = {};
      this.ro = !!ro; this.uid = uid || null;
      this.root.classList.toggle("we-ro", this.ro);
      this.root.closest("dialog")?.classList.add("we-wide");
      this.$("we-box").open = true;     // 結構 is expanded by default
      await this.derive({});
    }
    async derive(extra) {
      const s = this.sess(), my = ++this.seq;
      this.key = this.sig(s);
      const r = await req("POST", `${this.o.api}/steps/derive`, { ...s, uid: this.uid, ...extra });
      if (my !== this.seq) return;
      if (!r.ok) { this.doc = null; this.$("we-sum").textContent = "結構讀取失敗"; return; }
      this.ctx = r.body.context; this.stored = !r.body.derived && !extra.rederive;
      this.doc = r.body.steps ? { origin: r.body.steps.origin, items: r.body.steps.items, tpl: r.body.steps.tpl, route: r.body.steps.route } : null;
      this.reason = r.body.reason || "";
      if (this.stored) this.$("we-box").open = true;
      this.$("we-reset").hidden = !this.stored || this.ro;
      await this.check();
    }
    refresh() {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => {
        const s = this.sess();
        if (!this.dirty && !this.stored && this.sig(s) !== this.key) this.derive({});
        else this.check();
      }, 220);
    }
    rederive(extra) { this.dirty = false; this.stored = false; return this.derive({ ...extra, rederive: true }); }
    touch() { this.dirty = true; this.cleared = false; this.render(); this.refreshSoon(); }
    refreshSoon() { clearTimeout(this.timer); this.timer = setTimeout(() => this.check(), 150); }
    payload() {
      if (this.cleared) return { steps: null };
      if (!this.dirty || !this.doc) return null;
      return { steps: this.stepsDoc() };
    }
    // the structure as sent: origin user, plus the user template it came from (its route GPX) and
    // the session's own copy of that route's profile
    stepsDoc() {
      const d = { origin: "user", items: this.doc.items };
      if (this.doc.tpl) d.tpl = this.doc.tpl;
      if (this.doc.route) d.route = this.doc.route;
      return d;
    }
    // the 範本 page: show a given structure (a template), with the context of a derive for the session fields
    async load(doc, { ro } = {}) {
      this.dirty = false; this.cleared = false; this.sel = null; this.openT = null; this.auto = {};
      this.ro = !!ro; this.uid = null;
      this.root.classList.toggle("we-ro", this.ro);
      this.$("we-box").open = true;
      const my = ++this.seq;
      const r = await req("POST", `${this.o.api}/steps/derive`, { ...this.sess() });
      if (my !== this.seq) return;
      this.ctx = r.ok ? r.body.context : null;
      this.stored = false; this.reason = "";
      this.doc = doc && (doc.items || []).length ? { origin: "user", items: JSON.parse(JSON.stringify(doc.items)), tpl: doc.tpl, route: doc.route } : null;
      this.$("we-reset").hidden = true;
      await this.check();
    }
    async check() {
      if (!this.doc) { this.view = null; this.render(); this.o.onView && this.o.onView(null, this); return; }
      const my = ++this.seq;
      const r = await req("POST", `${this.o.api}/steps/check`, { ...this.sess(), uid: this.uid, steps: this.stepsDoc() });
      if (my !== this.seq) return;
      if (r.ok) this.view = r.body;
      else this.view = { ...(this.view || {}), issues: ((r.body.detail || {}).errors || ["結構有誤"]).map((t) => ({ level: "err", text: t })) };
      this.render();
      this.o.onView && this.o.onView(this.view, this);
    }

    // ---------------- model helpers ----------------
    find(id, items = this.doc.items, parent = null) {
      for (let i = 0; i < items.length; i++) {
        if (items[i].id === id) return { arr: items, i, it: items[i], parent };
        if (items[i].kind === "repeat") { const f = this.find(id, items[i].items, items[i]); if (f) return f; }
      }
      return null;
    }
    isQ() { return (this.sess().kind || "") === "quality"; }
    newStep(kind = "work") {
      const t = kind === "work" ? (this.isQ() ? { type: "auto", intent: "band", lo: 0.9, hi: 0.95, cls: "Z3sub" } : { type: "auto", intent: "easy", plo: 0.75, phi: 0.8 })
        : kind === "rest" || kind === "other" ? { type: "auto", intent: "open" } : { type: "auto", intent: "easy" };
      return { id: nid(), kind, dur: { type: "time", value: kind === "rest" ? 120 : 300 }, target: t, note: "" };
    }
    newRep() {
      const w = this.newStep("work");
      w.dur.value = this.isQ() ? 180 : 20;
      if (this.isQ()) w.target = { type: "auto", intent: "band", lo: 1.06, hi: 1.12, cls: "Z5" };   // Palladino 5 區
      else { w.kind = "other"; w.target = { type: "auto", intent: "open" }; w.note = "快步跑"; }
      const r = this.newStep("rest"); r.dur.value = this.isQ() ? 180 : 40; r.note = "慢跑";
      return { id: nid(), kind: "repeat", times: 4, last_rest: true, note: "", items: [w, r] };
    }
    autoFor(st) {
      if (this.auto[st.id]) return this.auto[st.id];
      const t = st.target || {};
      if (st.kind === "work" && t.type === "power" && t.mode === "pct") return { type: "auto", intent: "band", lo: t.lo, hi: t.hi, cls: "" };
      if (st.kind === "work" && this.isQ()) return { type: "auto", intent: "band", lo: 0.9, hi: 0.95, cls: "Z3sub" };
      return st.kind === "rest" || st.kind === "other" ? { type: "auto", intent: "open" } : { type: "auto", intent: "easy" };
    }
    // the override's starting numbers: what the step resolves to now (auto-filled from the thresholds)
    fill(st, type) {
      const r = (this.view && this.view.resolved || {})[st.id] || {}, th = (this.ctx || {}).thresholds || {};
      const f = r.frac, rd = (x) => Math.round(x * 100) / 100;
      if (type === "power") {
        if (r.type === "power" && th.cp) return { type, mode: "pct", lo: rd(r.lo / th.cp), hi: rd(r.hi / th.cp) };
        if (f) return { type, mode: "pct", lo: rd(f - 0.03), hi: rd(f + 0.03) };
        return { type, mode: "zone", zone: st.kind === "work" ? "3A" : "1C" };
      }
      if (type === "hr") {
        if (r.type === "hr" && /輕鬆跑上限|AeT/.test(r.sub || "")) return { type, mode: "zone", zone: "aet" };
        if (r.type === "hr" && th.lthr) return { type, mode: "pct", lo: rd(r.lo / th.lthr), hi: rd(r.hi / th.lthr) };
        // 課表心率區間 (Z1–Z6) when the context has it, else the Friel ids
        const hz = (((this.ctx || {}).zones || {}).hr || []).some((x) => x.id === "Z4") ? "Z4" : "4";
        return { type, mode: "zone", zone: st.kind === "work" ? hz : "aet" };
      }
      if (type === "rpe") return { type, lo: st.kind === "work" ? 3 : 2, hi: st.kind === "work" ? 4 : 3 };
      if (type === "pace") {
        if (f && th.tpace) return { type, mode: "pct", lo: rd(1 / (f + 0.03)), hi: rd(1 / Math.max(0.3, f - 0.03)) };
        return { type, mode: "zone", zone: st.kind === "work" ? "4" : "2" };
      }
      return { type: "none" };
    }
    convert(st, mode) {
      const t = st.target, r = (this.view && this.view.resolved || {})[st.id] || {}, th = (this.ctx || {}).thresholds || {};
      const base = { power: th.cp, hr: th.lthr, pace: th.tpace }[t.type];
      const zones = ((this.ctx || {}).zones || {})[t.type] || [];
      if (mode === "abs") {
        const lo = r.lo ?? (t.lo != null && base ? t.lo * base : null), hi = r.hi ?? (t.hi != null && base ? t.hi * base : null);
        return { type: t.type, mode, lo: Math.round(lo || (t.type === "pace" ? 330 : t.type === "hr" ? 140 : 200)), hi: Math.round(hi || (t.type === "pace" ? 360 : t.type === "hr" ? 150 : 220)) };
      }
      if (mode === "pct") {
        if (r.lo != null && base) return { type: t.type, mode, lo: Math.round(r.lo / base * 100) / 100, hi: Math.round(r.hi / base * 100) / 100 };
        const z = zones.find((x) => x.id === t.zone && x.lo != null);
        return { type: t.type, mode, lo: z ? z.lo : 0.9, hi: z ? z.hi : 0.95 };
      }
      const mid = r.lo != null && base ? (r.lo + r.hi) / 2 / base : null;
      const cur = zones.filter((x) => !x.legacy);
      const z = cur.find((x) => x.lo != null && mid != null && mid >= x.lo && mid < x.hi) || cur.find((x) => x.lo != null) || { id: "aet" };
      return { type: t.type, mode: "zone", zone: z.id };
    }

    // ---------------- render ----------------
    render() {
      const v = this.view, d = this.doc;
      if (!d) {
        this.$("we-sum").textContent = this.reason || "沒有結構";
        ["we-list", "we-issues", "we-stats", "we-legend", "we-wbody"].forEach((x) => (this.$(x).innerHTML = ""));
        this.$("we-svg").innerHTML = "";
        this.$("we-list").innerHTML = this.ro ? "" : `<p class="we-empty">${esc(this.reason || "")} 用「＋ 步驟」「＋ 重複」或「插入範本」排一份。</p>`;
        return;
      }
      const t = (v && v.totals) || {};
      this.$("we-sum").textContent = `${v ? v.structure : ""}${t.sec != null ? ` · ${t.est ? "約 " : ""}${mmss(t.sec)}${t.open ? "＋「直到按下計圈」段" : ""}` : ""}` +
        (this.dirty ? " · 尚未儲存" : this.stored ? " · 你改過的結構" : "");
      this.$("we-list").innerHTML = d.items.map((it) => this.itemHtml(it, 0)).join("");
      this.legend();
      this.stats();
      this.issues();
      this.watch();
      this.chart();
    }
    zc(r, st) {
      if (!r || st.dur.type === "open" || r.type === "none" || !r.level) return "var(--bar)";
      return `var(--wz${r.level})`;
    }
    itemHtml(it, depth) { return it.kind === "repeat" ? this.repHtml(it, depth) : this.rowHtml(it); }
    rowHtml(st) {
      const r = ((this.view || {}).resolved || {})[st.id] || null, dis = this.ro ? " disabled" : "";
      const dt = st.dur.type;
      const durIn = dt === "time" ? `<input class="dur" data-f="sec" value="${mmss(st.dur.value)}" inputmode="numeric" aria-label="時間（分:秒）"${dis}>`
        : dt === "distance" ? `<input class="num" type="number" step="0.1" min="0.05" data-f="km" value="${st.dur.value / 1000}" aria-label="距離 km"${dis}><span class="faint">km</span>`
        : dt === "load" ? this.loadIn(st, r, dis)
        : st.dur.est ? `<span class="we-lap" title="直到按下計圈；總時間用課表寫的最短時間估">≈ ${mmss(st.dur.est)}</span>` : "";
      const ov = st.target && st.target.type !== "auto";
      const tb = r ? `<span class="src${ov ? " ov" : ""}">${ov ? "指定" : "自動"}</span><b>${r.type === "none" ? "不設目標" : `${esc(r.label)} ${esc(r.text)}`}</b>${r.sub ? `<span class="s">${esc(r.sub)}</span>` : ""}` +
        (r.err ? `<span class="e">✕ ${esc(r.err)}</span>` : r.warn ? `<span class="w">⚠ ${esc(r.warn)}</span>` : "") : `<span class="s">…</span>`;
      return `<div class="we-row${this.sel === st.id ? " sel" : ""}" draggable="${!this.ro}" tabindex="0" data-id="${st.id}" style="--zc:${this.zc(r, st)}">
        <span class="grip" aria-hidden="true" title="拖曳排序">⋮⋮</span>
        <select class="kind" data-f="kind" aria-label="類型"${dis}>${Object.entries(KIND).map(([k, l]) => opt(k, l, st.kind)).join("")}</select>
        <span class="we-dur"><select data-f="dtype" aria-label="時長類型"${dis}>${this.endOpts(st)}</select>${durIn}</span>
        <button type="button" class="we-tbtn${this.openT === st.id ? " on" : ""}" data-a="tgt" aria-expanded="${this.openT === st.id}" aria-label="這一段的目標（點一下改這一段）"${dis}>${tb}</button>
        <input class="note nt" data-f="note" value="${esc(st.note || "")}" maxlength="60" placeholder="名稱（手錶顯示）" aria-label="名稱"${dis}>
        <span class="we-acts"><button type="button" data-a="up" title="上移" aria-label="上移">↑</button><button type="button" data-a="down" title="下移" aria-label="下移">↓</button><button type="button" data-a="dup" title="複製" aria-label="複製">⧉</button><button type="button" data-a="wrap" title="包成重複" aria-label="包成重複">⟳</button><button type="button" data-a="del" title="刪除" aria-label="刪除">✕</button></span>
        ${this.openT === st.id && !this.ro ? this.tgHtml(st, r) : ""}
      </div>`;
    }
    // the 時長類型 options for one step: the provider's end conditions (「負荷」 on main-set steps
    // only); a stored type the provider lacks stays listed, marked
    endOpts(st) {
      const caps = provCaps(this.ctx), ends = (caps && caps.end_conditions) || END_DEFAULT;
      const labels = { ...END_LABEL, ...((caps && caps.end_labels) || {}) };
      const lk = (this.ctx || {}).load_kinds || ["work"], dt = st.dur.type;
      const list = ends.filter((k) => k !== "load" || lk.includes(st.kind));
      let h = list.map((k) => opt(k, labels[k] || k, dt)).join("");
      if (!list.includes(dt)) h += opt(dt, `${labels[dt] || dt}（${esc(((this.ctx || {}).provider || {}).label || "這個平台")}不支援）`, dt);
      return h;
    }
    // a 「負荷」 step: TSS in — or by feel, an RPE level + minutes (SP-57) — and the provider's
    // conversion next to it (COROS TL ± error, else the time; 推估)
    loadIn(st, r, dis) {
      const caps = provCaps(this.ctx), unit = caps ? caps.load_unit : "TL", L = r && r.load, byRpe = !!st.dur.rpe;
      const conv = !L ? "" : unit ? `≈ ${L.tl} ${esc(unit)}（推估 ±${L.err}）` : `≈ ${mmss(L.sec)}（推估）`;
      const tip = unit ? `這裡填 TSS；推到手錶換算成 ${unit}（用你同步的活動擬合，誤差約 ±20%）。圖表和總時間用 TSS ÷（這段強度 IF² × 100）換成時間：約 ${L ? mmss(L.sec) : "?"}`
        : "這個平台沒有負荷結束條件：推送時換成預估時間 TSS ÷（這段強度 IF² × 100）";
      const mode = `<select data-f="lmode" aria-label="${esc(tr("workout.load_mode"))}"${dis}>${opt("tss", "TSS", byRpe ? "rpe" : "tss")}${opt("rpe", tr("workout.load_by_rpe"), byRpe ? "rpe" : "tss")}</select>`;
      if (byRpe) {
        const R = (L && L.rpe) || {};
        const rtip = tr("workout.load_rpe_tip", { factor: R.factor != null ? R.factor : "?", src: tr(R.fitted ? "workout.load_rpe_mine" : "workout.load_rpe_default") });
        return mode + `<select data-f="lrpe" aria-label="RPE"${dis}>${RPE_LEVELS.map((k) => opt(k, tr("workout.rpe_" + k), st.dur.rpe)).join("")}</select>` +
          `<input class="num" type="number" step="1" min="${RPE_MIN[0]}" max="${RPE_MIN[1]}" data-f="lmin" value="${st.dur.min ?? ""}" aria-label="${esc(tr("workout.load_min"))}"${dis}><span class="faint">${esc(tr("workout.load_min"))}</span>` +
          (L ? `<span class="we-lap" title="${esc(rtip + "\n" + tip)}">≈ ${L.tss} TSS ${conv}</span>` : "");
      }
      return mode + `<input class="num" type="number" step="1" min="1" max="500" data-f="tss" value="${st.dur.value}" aria-label="負荷 TSS"${dis}><span class="faint">TSS</span>` +
        (conv ? `<span class="we-lap" title="${esc(tip)}">${conv}</span>` : "");
    }
    tgHtml(st, r) {
      const t = st.target || { type: "auto" }, ty = t.type, ctx = this.ctx || {}, th = ctx.thresholds || {};
      const zones = (ctx.zones || {})[ty] || [];
      const miss = { power: !th.cp && "沒有 CP", hr: !(th.lthr || th.aet) && "沒有 LTHR／AeT", pace: !th.tpace && "沒有閾值配速" };
      let h = `<div class="we-tg" data-tg="${st.id}"><select data-f="ttype" aria-label="目標類型">` +
        opt("auto", `自動（依課表類型）`, ty) + ["power", "hr", "pace"].map((k) => opt(k, TYPE[k] + (miss[k] ? `（${miss[k]}）` : ""), ty)).join("") +
        opt("rpe", "RPE＋爬升（技術地形／下坡）", ty) + opt("none", "無", ty) + `</select>`;
      if (ty === "rpe") {
        const n = (f, v, l, mx, stp) => `<input class="num" type="number" min="${f === "tlo" || f === "thi" ? 1 : 0}" max="${mx}" step="${stp}" data-f="${f}" value="${v ?? ""}" aria-label="${l}">`;
        h += `${n("tlo", t.lo, "RPE 下限", 10, 1)}–${n("thi", t.hi, "RPE 上限", 10, 1)}<span class="faint">/ 10</span>` +
          `<span class="faint">爬升</span>${n("tup", t.up, "爬升 m", 5000, 50)}<span class="faint">m</span>` +
          `<span class="faint">下降</span>${n("tdown", t.down, "下降 m", 5000, 50)}<span class="faint">m</span>${q(TIP.rpe)}`;
      }
      if (["power", "hr", "pace"].includes(ty)) {
        h += `<select data-f="tmode" aria-label="填法">${opt("zone", "區間", t.mode)}${opt("pct", ty === "power" ? "% CP" : ty === "hr" ? "% LTHR" : "% 閾值配速", t.mode)}${opt("abs", "自訂數字", t.mode)}</select>`;
        if (t.mode === "zone") h += `<select data-f="tzone" aria-label="區間">${zones.filter((z) => !z.legacy || z.id === t.zone).map((z) => opt(z.id, `${z.label}${z.text ? " · " + z.text : ""}`, t.zone)).join("")}</select>`;
        else if (t.mode === "pct") h += `<input class="num" type="number" step="1" data-f="tlo" value="${Math.round(t.lo * 100)}" aria-label="下限 %">–<input class="num" type="number" step="1" data-f="thi" value="${Math.round(t.hi * 100)}" aria-label="上限 %"><span class="faint">%</span>${ty === "pace" ? q(TIP.pacePct) : ""}`;
        else if (ty === "pace") h += `<input class="dur" data-f="tlo" value="${mmss(t.lo)}" aria-label="快的一端 分:秒/km">–<input class="dur" data-f="thi" value="${mmss(t.hi)}" aria-label="慢的一端 分:秒/km"><span class="faint">/km</span>`;
        else h += `<input class="num" type="number" step="1" data-f="tlo" value="${Math.round(t.lo)}" aria-label="下限">–<input class="num" type="number" step="1" data-f="thi" value="${Math.round(t.hi)}" aria-label="上限"><span class="faint">${ty === "power" ? "W" : "bpm"}</span>`;
      }
      h += r ? `<span class="res">＝ ${r.type === "none" ? "不設目標" : esc(`${r.label} ${r.text}`)}${r.sub ? " · " + esc(r.sub) : ""}</span>` : "";
      if (ty !== "auto") h += `<button type="button" class="btn" data-a="tauto">↺ 回到自動</button>`;
      return h + `</div>`;
    }
    repHtml(it, depth) {
      const dis = this.ro ? " disabled" : "";
      const sec = ((this.view || {}).order || []).filter((o) => o.rep.some((x) => x.id === it.id)).reduce((a, o) => a + (o.sec || 0), 0);
      return `<div class="we-rep" draggable="${!this.ro}" data-id="${it.id}">
        <div class="we-rep-h"><span class="grip" aria-hidden="true">⋮⋮</span><b>重複</b> ×
          <input class="num" type="number" min="1" max="99" data-f="times" value="${it.times}" aria-label="次數"${dis}>
          <label><input type="checkbox" data-f="lastrest"${it.last_rest === false ? " checked" : ""}${dis}>最後一趟不休息</label>${q(TIP.lastRest)}
          <span class="faint num">${sec ? mmss(sec) : ""}</span>
          <span class="sp"></span>
          <span class="we-acts"><button type="button" data-a="addin" title="在裡面加一步" aria-label="在裡面加一步">＋</button><button type="button" data-a="up" title="上移" aria-label="上移">↑</button><button type="button" data-a="down" title="下移" aria-label="下移">↓</button><button type="button" data-a="dup" title="複製" aria-label="複製">⧉</button><button type="button" data-a="unwrap" title="拆開" aria-label="拆開重複">⇲</button><button type="button" data-a="del" title="刪除" aria-label="刪除">✕</button></span></div>
        <div class="we-rep-in" data-cont="${it.id}">${it.items.map((x) => this.itemHtml(x, depth + 1)).join("")}</div>
      </div>`;
    }
    legend() {
      const z = [[1, "< 75% CP"], [2, "75–88%"], [3, "88–101%"], [4, "101–106%"], [5, "≥ 106%"]];
      this.$("we-legend").innerHTML = z.map(([c, l]) => `<span><i style="background:var(--wz${c})"></i>${l}</span>`).join("") +
        `<span><i style="background:var(--bar)"></i>不設目標</span><span><i style="background:repeating-linear-gradient(45deg,var(--bar) 0 3px,var(--panel) 3px 5px)"></i>直到按下計圈</span>${q(TIP.chart)}` +
        this.elevLegend();
    }
    elevLegend() {
      const ev = (this.view || {}).elev;
      if (!ev) return "";
      const km = (x) => (Math.round((x || 0) * 10) / 10).toString();
      const txt = tr("workout.elev_legend", { name: ev.name || "GPX", km: ev.complete ? km(ev.route_km) : `${km(ev.km)}/${km(ev.route_km)}` });
      const tip = `${ev.note || ""}` + (ev.complete ? "" : "\n" + tr("workout.elev_partial"));
      return `<span><i style="background:color-mix(in srgb, var(--we-elev) 30%, transparent);border-top:1px solid var(--we-elev)"></i>${esc(txt)}</span>${q(tip)}`;
    }
    stats() {
      const v = this.view || {}, t = v.totals || {}, c = this.ctx || {};
      const cap = c.cap ? `${Math.round(c.cap)} 分（${c.cap_mode === "hard" ? "硬上限" : "軟上限"}）` : "不限";
      this.$("we-stats").innerHTML = t.sec == null ? "" :
        `<span>總時間 <b>${t.est ? "約 " : ""}${mmss(t.sec)}</b>${t.open ? "＋「直到按下計圈」段" : ""}${t.est ? q(`總時間是估的。${t.est_note || ""}`) : ""}</span>` +
        `<span>≥ 88% CP <b>${mmss(t.hard_s)}</b></span>` + (t.z5_s ? `<span>≥ 106% CP（Palladino 5 區）<b>${mmss(t.z5_s)}</b></span>` : "") +
        `<span>TSS 估 <b>${Math.round(t.tss)}</b>${q(TIP.tss)}</span><span>這天上限 ${esc(cap)}</span>`;
    }
    issues() {
      const v = this.view || {}, list = v.issues || [];
      const ic = { err: "✕", warn: "!", info: "i" };
      const nt = noTpaceText(), T = this.tpls || {};
      const isNt = (x) => x === nt || x === T.no_tpace_text;
      const hw = this.doc && usesHr(this.doc.items) ? hrWarn(this.ctx) : "";
      this.$("we-issues").innerHTML = (hw ? `<li class="warn"><span class="ic">!</span><span>${esc(hw)}</span></li>` : "") + list.map((i) => `<li class="${i.level}"${i.id ? ` data-id="${esc(i.id)}"` : ""}><span class="ic">${ic[i.level] || "i"}</span><span>${esc(i.text)}${isNt(i.text) ? tpaceLink(this.ctx) : ""}</span></li>`).join("") +
        (list.length ? `<li class="info"><span class="ic"></span><span class="faint">檢查規則 ${q(TIP.rules)}</span></li>` : "");
    }
    watch() {
      const w = (this.view || {}).watch;
      if (!w) { this.$("we-wbody").innerHTML = ""; return; }
      const line = (l) => `<li>${esc(l.kind)} · ${esc(l.dur)} · ${esc(l.target)}${l.name ? ` <span class="nm">「${esc(l.name)}」</span>` : ""}</li>`;
      const body = w.lines.map((l) => l.group ? `<li>${esc(l.group)} × ${l.sets}<ol>${l.steps.map(line).join("")}</ol></li>` : line(l)).join("");
      this.$("we-wbody").innerHTML = `<ol>${body}</ol><ul class="we-lim">${w.limits.map((x) => `<li class="${x.hit ? "hit" : ""}">${esc(x.text)}</li>`).join("")}` +
        w.lost.map((x) => `<li class="hit">${esc(x)}${x === noTpaceText() || x === (this.tpls || {}).no_tpace_text ? tpaceLink(this.ctx) : ""}</li>`).join("") + `</ul>`;
    }
    chart() {
      const svg = this.$("we-svg"), v = this.view;
      if (!v || !v.order || !this.doc) { svg.innerHTML = ""; return; }
      const box = svg.getBoundingClientRect(), W = Math.max(280, box.width || 600), H = box.height || 124, top = 22, base = H - 16;
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
      const NS = "http://www.w3.org/2000/svg";
      const el = (n, a, p = svg) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); p.appendChild(e); return e; };
      svg.innerHTML = "";
      const defs = el("defs", {});
      const pat = el("pattern", { id: "we-hatch", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
      el("rect", { width: 6, height: 6, fill: "var(--bar)" }, pat);
      el("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: "var(--panel)", "stroke-width": 2 }, pat);
      // a route GPX: the distance axis (km, each step from elev.x); else time (a lap-button step OPEN_W s)
      const ev = v.elev && Array.isArray(v.elev.x) && v.elev.x.length === v.order.length + 1 ? v.elev : null;
      const span = (o, i) => ev ? ev.x[i + 1] - ev.x[i] : (o.open && !o.sec ? OPEN_W : o.sec);
      const total = (ev ? ev.x[ev.x.length - 1] : v.order.reduce((a, o, i) => a + span(o, i), 0)) || 1;
      const sx = (W - 2) / total, maxF = 1.3;
      const yCP = base - (1 / maxF) * (base - top);
      this.elev(el, ev, sx, base, top);
      el("line", { x1: 0, x2: W, y1: yCP, y2: yCP, stroke: "var(--faint)", "stroke-dasharray": "3 3", "stroke-width": 1 });
      el("text", { x: W - 2, y: yCP - 3, "text-anchor": "end", "font-size": 10.5, fill: "var(--muted)" }).textContent = "CP";
      el("line", { x1: 0, x2: W, y1: base, y2: base, stroke: "var(--line)" });
      const res = v.resolved || {}, spans = new Map();
      let x = 1, tAcc = 0;
      const ticks = [];
      const step = ev ? (total > 40 ? 10 : total > 15 ? 5 : total > 6 ? 2 : total > 2.5 ? 1 : 0.5)
        : total > 5400 ? 1800 : total > 2400 ? 600 : 300;
      v.order.forEach((o, i) => {
        const w = span(o, i) * sx;
        const f = Math.min(maxF, o.frac == null ? (o.kind === "rest" ? 0.5 : 0.6) : o.frac);
        const h = Math.max(6, (f / maxF) * (base - top));
        const fill = o.open ? "url(#we-hatch)" : o.frac == null || !o.level ? "var(--bar)" : `var(--wz${o.level})`;
        const rr = el("rect", { x: x + 1, y: base - h, width: Math.max(1, w - 2), height: h, rx: 2, fill, class: "blk", "data-id": o.id });
        if (this.sel === o.id) { rr.setAttribute("stroke", "var(--text)"); rr.setAttribute("stroke-width", 1.5); }
        const km = ev ? [ev.x[i], ev.x[i + 1]] : null;
        rr.addEventListener("mousemove", (e) => this.tip(e, o, res[o.id], km));
        rr.addEventListener("mouseleave", () => (this.$("we-tip").hidden = true));
        rr.addEventListener("click", () => this.select(o.id));
        for (const rp of o.rep) { const sp = spans.get(rp.id) || { a: x, b: x + w, n: rp.n, d: o.rep.indexOf(rp) }; sp.b = x + w; spans.set(rp.id, sp); }
        if (!ev && (!o.open || o.sec)) { const before = tAcc; tAcc += o.sec; for (let m = Math.ceil(before / step) * step || step; m <= tAcc; m += step) if (m > before) ticks.push(x + (m - before) * sx); }
        x += w;
      });
      if (ev) for (let m = step; m < total; m += step) ticks.push(1 + m * sx);
      const tick = (i) => ev ? `${+((i + 1) * step).toFixed(1)} km` : `${(i + 1) * step / 60}′`;
      ticks.forEach((px, i) => { if (px < W - 18) el("text", { x: px, y: H - 3, "text-anchor": "middle", "font-size": 10.5, fill: "var(--faint)" }).textContent = tick(i); });
      for (const { a, b, n, d } of spans.values()) {
        const y = 10 + d * 8;                     // a nested repeat's bracket sits under its parent's
        el("path", { d: `M${a + 2} ${y + 6} V${y} H${b - 2} V${y + 6}`, fill: "none", stroke: "var(--muted)", "stroke-width": 1 });
        if (b - a > 18) el("text", { x: (a + b) / 2, y: y - 2, "text-anchor": "middle", "font-size": 11, fill: "var(--text)", "font-weight": 600 }).textContent = `×${n}`;
      }
      svg.setAttribute("aria-label", `區段圖：${v.structure || ""}，總長 ${mmss((v.totals || {}).sec || 0)}` +
        (ev ? tr("workout.elev_aria", { lo: Math.round(ev.z_min), hi: Math.round(ev.z_max), km: kmTxt(ev.total_km) }) : ""));
    }
    // the route GPX's elevation (POST /steps/check elev: d = km on the chart's distance axis, z = m):
    // a light area + thin line behind the bars, its own scale (min–max of the route), labelled at the left
    elev(el, ev, sx, base, top) {
      if (!ev || !ev.d || ev.d.length < 2) return;
      const lo = ev.z_min, hi = Math.max(ev.z_max, lo + 10);
      const ey = (z) => base - 2 - ((z - lo) / (hi - lo)) * (base - top - 8);
      const ex = (t) => 1 + t * sx;
      const pts = ev.d.map((t, i) => `${ex(t).toFixed(1)},${ey(ev.z[i]).toFixed(1)}`);
      const a = ex(ev.d[0]).toFixed(1), b = ex(ev.d[ev.d.length - 1]).toFixed(1);
      el("path", { d: `M${a},${base} L${pts.join(" L")} L${b},${base} Z`, fill: "var(--we-elev)", "fill-opacity": 0.16, stroke: "none", "pointer-events": "none" });
      el("path", { d: `M${pts.join(" L")}`, fill: "none", stroke: "var(--we-elev)", "stroke-opacity": 0.6, "stroke-width": 1, "pointer-events": "none" });
      el("text", { x: 3, y: ey(hi) - 3, "font-size": 10, fill: "var(--muted)" }).textContent = `${Math.round(hi)} m`;
      el("text", { x: 3, y: base - 4, "font-size": 10, fill: "var(--muted)" }).textContent = `${Math.round(lo)} m`;
    }
    tip(e, o, r, km) {
      const t = this.$("we-tip"), box = this.$("we-chart").getBoundingClientRect(), f = this.find(o.id);
      const st = f ? f.it : { kind: o.kind, note: "" };
      const n = o.rep.length ? o.rep.map((x) => `第 ${x.i + 1}/${x.n} 趟`).join(" · ") + " · " : "";
      t.innerHTML = `<b>${n}${esc(KIND[o.kind] || o.kind)}</b> · ${o.open ? "直到按下計圈" : (o.load ? `負荷 ${o.load.tss} TSS · ` : "") + mmss(o.sec) + (o.est ? "（推估）" : "")}` +
        (km ? ` · ${esc(tr("workout.elev_km", { a: kmTxt(km[0]), b: kmTxt(km[1]) }))}` : "") + `<br>` +
        (r ? `${r.type === "none" ? "不設目標" : `${esc(r.label)} <b class="num">${esc(r.text)}</b>`}${r.sub ? ` <span class="meta">${esc(r.sub)}</span>` : ""}` : "") +
        (st.note ? `<br><span class="meta">${esc(st.note)}</span>` : "");
      t.hidden = false;
      t.style.left = Math.min(box.width - 100, Math.max(100, e.clientX - box.left)) + "px";
      t.style.top = (e.clientY - box.top - 10) + "px";
    }
    select(id) {
      this.sel = id;
      this.render();
      const r = this.root.querySelector(`.we-row[data-id="${CSS.escape(id)}"]`);
      if (r) r.scrollIntoView({ block: "nearest" });
    }

    // ---------------- events ----------------
    bind() {
      const L = this.$("we-list");
      // the editor lives inside the dialog's form: its edits are not the form's (no re-render of
      // the dialog on every change), and Enter in a field commits it instead of submitting
      this.root.addEventListener("change", (e) => e.stopPropagation());
      this.root.addEventListener("input", (e) => e.stopPropagation());
      this.root.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.target.matches("input")) { e.preventDefault(); e.target.blur(); }
      });
      L.addEventListener("change", (e) => this.onChange(e));
      L.addEventListener("click", (e) => this.onClick(e));
      L.addEventListener("keydown", (e) => this.onKey(e));
      this.$("we-issues").addEventListener("click", (e) => { const li = e.target.closest("li[data-id]"); if (li) this.select(li.dataset.id); });
      this.$("we-tools").addEventListener("click", (e) => this.onTool(e));
      this.$("we-pop").addEventListener("click", (e) => this.onTpl(e));
      // (a tab click re-renders the popup: its target is detached by the time this runs)
      document.addEventListener("click", (e) => { if (e.target.isConnected && !e.target.closest("#we-tpl, #we-sumtpl")) this.$("we-pop").hidden = true; });
      this.$("we-sumtpl").addEventListener("click", (e) => {
        e.preventDefault();                      // a button in <summary>: open the box, not toggle it
        if (this.ro) return;
        this.$("we-box").open = true;
        const b = this.$("we-tplbtn");
        this.$("we-pop").hidden = true;
        this.menu(b).then(() => b.scrollIntoView({ block: "nearest" }));
      });
      let drag = null;
      L.addEventListener("dragstart", (e) => {
        if (this.ro || e.target.closest("input,select,button:not(.grip)")) return;
        const h = e.target.closest("[data-id]"); if (!h) return;
        drag = h.dataset.id; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", drag); e.stopPropagation();
      });
      const clear = () => L.querySelectorAll(".drop").forEach((x) => x.classList.remove("drop"));
      L.addEventListener("dragover", (e) => {
        clear();
        const h = e.target.closest("[data-id]");
        if (!drag || !h || h.dataset.id === drag) return;
        e.preventDefault(); h.classList.add("drop");
      });
      L.addEventListener("dragend", () => { drag = null; clear(); });
      L.addEventListener("drop", (e) => {
        e.preventDefault(); clear();
        const h = e.target.closest("[data-id]");
        if (!drag || !h || h.dataset.id === drag) return;
        const intoHead = !!e.target.closest(".we-rep-h") && h.classList.contains("we-rep");
        this.move(drag, h.dataset.id, intoHead);
        drag = null;
      });
    }
    depthOf(id) { let d = 0, f = this.find(id); while (f && f.parent) { d++; f = this.find(f.parent.id); } return d; }
    move(src, dst, into) {
      const s = this.find(src); if (!s) return;
      if (this.find(dst, s.it.items || [])) return;                       // into itself
      const hasRep = s.it.kind === "repeat" && s.it.items.some((x) => x.kind === "repeat");
      const target = this.find(dst);
      const depth = into ? this.depthOf(dst) + 1 : this.depthOf(dst);
      if (s.it.kind === "repeat" && (depth > 1 || (hasRep && depth > 0))) return;     // two levels at most
      s.arr.splice(s.i, 1);
      if (into) target.it.items.unshift(s.it);
      else { const t = this.find(dst); t.arr.splice(t.i, 0, s.it); }
      this.touch();
    }
    onKey(e) {
      if (this.ro) return;
      const row = e.target.classList && (e.target.classList.contains("we-row") ? e.target : null);
      if (!row) return;
      const f = this.find(row.dataset.id); if (!f) return;
      if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();
        const j = f.i + (e.key === "ArrowUp" ? -1 : 1);
        if (j < 0 || j >= f.arr.length) return;
        [f.arr[f.i], f.arr[j]] = [f.arr[j], f.arr[f.i]];
        this.sel = f.it.id; this.touch();
        this.root.querySelector(`.we-row[data-id="${CSS.escape(f.it.id)}"]`)?.focus();
      } else if (e.key === "Delete") {
        e.preventDefault(); f.arr.splice(f.i, 1); this.touch();
      }
    }
    onChange(e) {
      if (this.ro) return;
      const host = e.target.closest("[data-id]"); if (!host) return;
      const f = this.find(host.dataset.id); if (!f) return;
      const it = f.it, k = e.target.dataset.f, v = e.target.value;
      if (k === "times") it.times = Math.max(1, Math.min(99, Math.round(+v) || 1));
      else if (k === "lastrest") it.last_rest = !e.target.checked;
      else if (k === "kind") {
        const was = it.kind; it.kind = v;
        if (it.target && it.target.type === "auto" && was !== v) it.target = this.autoFor({ ...it, target: {} });
        if (it.dur && it.dur.type === "load" && !((this.ctx || {}).load_kinds || ["work"]).includes(v)) it.dur = { type: "time", value: this.estSec(it) };
      } else if (k === "dtype") it.dur = v === "open" ? { type: "open" } : v === "distance" ? { type: "distance", value: 1000 }
        : v === "load" ? { type: "load", value: this.estTss(it) }
        : { type: "time", value: it.dur.value && it.dur.type === "time" ? it.dur.value : it.dur.type === "load" ? this.estSec(it) : 300 };
      else if (k === "lmode" || k === "lrpe" || k === "lmin") {
        // the TSS the server computed from the RPE (normalize) is the value kept when switching back to TSS
        const L = (((this.view || {}).resolved || {})[it.id] || {}).load;
        const nd = loadDur(L ? { ...it.dur, value: L.tss } : it.dur, k, v, this.estSec(it) / 60);
        if (nd) it.dur = nd; else { e.target.value = k === "lmin" ? (it.dur.min ?? "") : it.dur.rpe; return; }
      } else if (k === "tss") { const x = +v; if (x >= 1 && x <= 500) it.dur = { type: "load", value: Math.round(x * 10) / 10 }; else { e.target.value = it.dur.value; return; } }
      else if (k === "sec") { const s = parseSec(v); if (s && s >= 5) it.dur = { type: "time", value: s }; else { e.target.value = mmss(it.dur.value); return; } }
      else if (k === "km") { const m = Math.round(+v * 1000); if (m >= 50) it.dur = { type: "distance", value: m }; }
      else if (k === "note") it.note = v.slice(0, 60);
      else if (k === "ttype") {
        if (it.target && it.target.type === "auto") this.auto[it.id] = it.target;
        it.target = v === "auto" ? this.autoFor(it) : this.fill(it, v);
        this.openT = it.id;
      } else if (k === "tmode") { it.target = this.convert(it, v); this.openT = it.id; }
      else if (k === "tzone") it.target = { ...it.target, zone: v };
      else if (k === "tlo" || k === "thi") {
        const key = k === "tlo" ? "lo" : "hi", t = it.target;
        const n = t.type === "rpe" ? Math.max(1, Math.min(10, Math.round(+v) || 0)) : t.mode === "pct" ? (+v || 0) / 100 : t.type === "pace" ? parseSec(v) : +v;
        if (n) it.target = { ...t, [key]: n };
      } else if (k === "tup" || k === "tdown") {
        const key = k === "tup" ? "up" : "down", t = { ...it.target }, m = Math.round(+v);
        if (m > 0) t[key] = Math.min(5000, m); else delete t[key];
        it.target = t;
      } else return;
      if (!["note"].includes(k)) this.sel = it.id;
      this.touch();
    }
    // the step's run-order entry of the last check (seconds, ≈ % CP)
    orderOf(it) { return ((this.view || {}).order || []).find((o) => o.id === it.id) || null; }
    estTss(it) { const o = this.orderOf(it), f = (o && o.frac) || 1, s = (o && o.sec) || 300; return Math.max(1, Math.min(500, Math.round(s * f * f * 100 / 3600))); }
    estSec(it) { const o = this.orderOf(it); return Math.max(5, Math.round((o && o.sec) || 300)); }
    onClick(e) {
      const b = e.target.closest("button[data-a]");
      const host = e.target.closest("[data-id]");
      if (!host) return;
      if (!b) { if (!e.target.closest("select,input,label")) { this.sel = host.dataset.id; this.render(); } return; }
      if (b.classList.contains("qtip")) return;
      const f = this.find(host.dataset.id); if (!f) return;
      const a = b.dataset.a;
      if (a === "tgt") { this.openT = this.openT === f.it.id ? null : f.it.id; this.sel = f.it.id; this.render(); return; }
      if (this.ro) return;
      if (a === "del") f.arr.splice(f.i, 1);
      else if (a === "dup") f.arr.splice(f.i + 1, 0, clone(f.it));
      else if (a === "up" && f.i > 0) [f.arr[f.i - 1], f.arr[f.i]] = [f.arr[f.i], f.arr[f.i - 1]];
      else if (a === "down" && f.i < f.arr.length - 1) [f.arr[f.i + 1], f.arr[f.i]] = [f.arr[f.i], f.arr[f.i + 1]];
      else if (a === "wrap") {
        if (this.depthOf(f.it.id) >= 1 && f.parent && this.depthOf(f.parent.id) >= 1) return;
        const r = this.newStep("rest"); r.dur.value = 120;
        f.arr.splice(f.i, 1, { id: nid(), kind: "repeat", times: 3, last_rest: true, note: "", items: [f.it, r] });
      } else if (a === "unwrap") f.arr.splice(f.i, 1, ...f.it.items);
      else if (a === "addin") f.it.items.push(this.newStep("work"));
      else if (a === "tauto") { f.it.target = this.autoFor(f.it); delete this.auto[f.it.id]; }
      else return;
      this.touch();
    }
    insertAt() {
      // before the cool-down (the last top-level cool), else at the end
      const items = this.doc.items;
      let i = items.length;
      while (i > 0 && items[i - 1].kind === "cool") i--;
      return i;
    }
    onTool(e) {
      const b = e.target.closest("button[data-a]"); if (!b || this.ro) return;
      const a = b.dataset.a;
      if (a === "tpl") { this.menu(b); return; }
      if (a === "savetpl") { this.saveForm(); return; }
      if (!this.doc) this.doc = { origin: "user", items: [] };
      if (a === "add-step") { const s = this.newStep("work"); this.doc.items.splice(this.insertAt(), 0, s); this.sel = s.id; this.touch(); }
      else if (a === "add-rep") { this.doc.items.splice(this.insertAt(), 0, this.newRep()); this.touch(); }
      else if (a === "reset") { this.cleared = true; this.dirty = false; this.stored = false; this.$("we-reset").hidden = true; this.derive({ rederive: true }).then(() => { this.cleared = true; this.render(); this.o.onView && this.o.onView(this.view, this); }); }
    }
    // 插入範本: category tabs (default: this session's kind), 強度課 split 有氧間歇／VO2max 間歇／速度
    // (workout_templates.family_of), one row per template with a mini chart, its 訓練目的 and its source
    catOf(kind) { return { easy: "easy", long: "easy", quality: "quality", test: "test", hike: "trail" }[kind] || "easy"; }
    async menu(btn) {
      const pop = this.$("we-pop");
      if (!pop.hidden) { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); return; }
      if (!this.tpls) {
        const r = await req("GET", `${this.o.api}/steps/templates`);
        this.tpls = r.ok ? r.body : { cats: [], groups: [] };
      }
      await this.loadRecs();
      const k = this.sess().kind;
      if (!this.tplCat || this.tplKind !== k) { this.tplCat = this.catOf(k); this.tplSub = null; this.tplKind = k; }
      this.menuHtml();
      pop.hidden = false; btn.setAttribute("aria-expanded", "true");
    }
    // the 推薦 block: per tab the 3 best templates for this session (GET /steps/templates/recs,
    // engine/template_recs.py), fetched again when the day / type / minutes / terrain change
    async loadRecs() {
      const s = this.sess();
      const q = new URLSearchParams({ kind: s.kind || "easy" });
      if (s.day) q.set("day", s.day);
      if (this.uid) q.set("uid", this.uid);
      if (s.minutes) q.set("minutes", s.minutes);
      if (s.terrain) q.set("terrain", s.terrain);
      const key = q.toString();
      if (this.recsKey === key && this.recs) return;
      const r = await req("GET", `${this.o.api}/steps/templates/recs?${key}`);
      this.recs = r.ok ? r.body : null;
      this.recsKey = key;
    }
    menuHtml() {
      const T = this.tpls, cat = this.tplCat, cats = T.cats || [];
      const subs = (cats.find((c) => c.id === cat) || {}).subs || [];
      if (subs.length && !subs.some((s) => s.id === this.tplSub)) this.tplSub = subs[0].id;
      const where = {};                                      // row key -> "gi.i" in this tab
      (T.groups || []).forEach((g, gi) => { if (g.cat === cat) g.rows.forEach((r, i) => { where[r.key] ??= `${gi}.${i}`; }); });
      // 主課強度: the types this category's rows use; a kept choice none of them has shows 全部 (kept still)
      const avail = new Set((T.groups || []).filter((g) => g.cat === cat).flatMap((g) => g.rows.flatMap((r) => r.target_types || [])));
      const tt = avail.has(this.tplTT) ? this.tplTT : "all";
      const ttOk = (r) => tt === "all" || (r.target_types || []).includes(tt);
      const rowAt = (at) => { const [g, i] = at.split(".").map(Number); return T.groups[g].rows[i]; };
      const recs = ((this.recs || {}).cats || {})[cat]?.filter((x) => where[x.key] && ttOk(rowAt(where[x.key]))) || [];
      const recKeys = new Set(recs.map((x) => x.key));
      // 我的範本 with no sub-tab of its own (a 強度課 one with no interval family): in every sub-tab
      const gs = (T.groups || []).map((g, gi) => ({ g, gi })).filter(({ g }) => g.cat === cat && (!subs.length || g.sub === this.tplSub || (g.mine && !g.sub)));
      const tab = (k, id, l, on, tip) => `<button type="button" data-${k}="${esc(id)}" class="${on ? "on" : ""}" aria-pressed="${on}"${tip ? ` title="${esc(tip)}"` : ""}>${esc(l)}</button>`;
      // pace × threshold pace (Daniels / Canova / Billat …) with no threshold pace: badge it
      const noTp = !(((this.ctx || {}).thresholds || {}).tpace);
      const hw = hrWarn(this.ctx);
      const tpBadge = (r) => (r.needs_tpace && noTp
        ? ` <span class="we-tpb" title="${esc(T.no_tpace_text || noTpaceText())}">⚠ ${esc(tr("workout.no_tpace_badge"))}</span>` : "") +
        (hw && usesHr(r.full || r.items) ? ` <span class="we-tpb" title="${esc(hw)}">⚠ ${esc(tr("workout.thr_low_badge"))}</span>` : "");
      // 技術地形／下坡 rows: how the scheduler counts them (workout_steps.rpe_role)
      const fsub = (r) => (r.mine ? ` <span class="mine">${esc(tr("workout.mine"))}</span>` : "") + (r.gpx ? ` <span class="fam">▲ GPX</span>` : "") +
        (r.family && r.family.sub_label ? ` <span class="fam">${esc(r.family.sub_label)}</span>` : "") +
        (r.role ? ` <span class="fam">${r.role === "quality" ? "算強度課" : "算輕鬆課"}</span>` : "");
      const btn = (r, at, sub) => `<button type="button" class="t" data-t="${at}">${this.mini(r.full || r.items)}<span>${esc(r.label)}${fsub(r)}${r.src_kind === "推估" ? ` <span class="faint">（推估）</span>` : ""}${tpBadge(r)}</span>` +
        `${r.purpose ? `<span class="pur">${esc(r.purpose)}</span>` : ""}<span class="src${sub ? " why" : ""}">${esc(sub || r.src || "")}</span></button>`;
      const rec = recs.length ? `<div class="g rec">推薦 ${q((this.recs || {}).tip || "")}</div>` +
        recs.map((x, n) => btn(rowAt(where[x.key]), where[x.key], `${n + 1}. ${x.reason}`)).join("") : "";
      const others = gs.map(({ g, gi }) => {
        const rows = g.rows.map((r, i) => [r, i]).filter(([r]) => !recKeys.has(r.key) && ttOk(r));
        return rows.length ? (gs.length > 1 || g.title ? `<div class="g">${esc(g.title || g.group)}</div>` : "") + rows.map(([r, i]) => btn(r, `${gi}.${i}`)).join("") : "";
      }).join("");
      const subTabs = subs.length ? `<div class="tabs sub" role="group" aria-label="類別">${subs.map((s) => tab("sub", s.id, s.label, s.id === this.tplSub, s.tip)).join("")}</div>` : "";
      const list = subTabs + (others || `<p class="empty">${!gs.length ? "這一類還沒有範本"
        : gs.some(({ g }) => g.rows.some(ttOk)) ? "都在上面的推薦裡" : esc(tr("workout.tt_none"))}</p>`);
      const n = (T.groups || []).filter((g) => g.cat === cat).reduce((a, g) => a + g.rows.filter((r) => !recKeys.has(r.key) && ttOk(r)).length, 0);
      const tts = [{ id: "all", label: tr("workout.tt_all") }, ...(T.target_types || []).filter((x) => avail.has(x.id))];
      const ttTabs = tts.length > 2 ? `<div class="tabs sub" role="group" aria-label="${esc(tr("workout.tt"))}">${tts.map((x) => tab("tt", x.id, x.label, x.id === tt)).join("")}</div>` : "";
      this.$("we-pop").innerHTML = `<div class="tabs" role="group" aria-label="類型">${cats.map((c) => tab("cat", c.id, c.label, c.id === cat)).join("")}</div>` + ttTabs +
        (rec ? rec + `<details class="more" id="we-more"${this.tplMore ? " open" : ""}><summary>其他範本（${n}）</summary>${list}</details>` : list) +
        `<div class="mode" role="radiogroup" aria-label="插入方式"><label><input type="radio" name="we-tm" value="full"${this.tplFull ? " checked" : ""}>整份換（含暖身、緩和）</label><label><input type="radio" name="we-tm" value="main"${this.tplFull ? "" : " checked"}>只換主課</label></div>`;
      this.$("we-more")?.addEventListener("toggle", (e) => { this.tplMore = e.target.open; });
    }
    // a template's structure as a 96×26 sparkline: width = time, height + colour = intensity
    mini(items) {
      const rows = [];
      const walk = (xs) => xs.forEach((x) => {
        if (x.kind === "repeat") {
          for (let i = 0; i < x.times; i++) {
            let kids = x.items;
            if (i === x.times - 1 && x.last_rest === false) { kids = kids.slice(); while (kids.length && kids[kids.length - 1].kind === "rest") kids.pop(); }
            walk(kids);
          }
        } else rows.push(x);
      });
      walk(items || []);
      const f = (x) => {
        const t = x.target || {};
        if (t.type === "auto" && t.intent === "band") return (t.lo + t.hi) / 2;
        if (t.type === "auto" && t.intent === "easy") return t.plo != null ? (t.plo + t.phi) / 2 : 0.75;
        if (t.type === "power" && t.mode === "pct") return (t.lo + t.hi) / 2;
        if (t.type === "hr" && t.mode === "pct") { const m = (t.lo + t.hi) / 2; return m < .85 ? .7 : m < .9 ? .8 : m < .95 ? .88 : m < 1 ? .96 : m < 1.03 ? 1.03 : 1.1; }  // Friel → Palladino (推估, height only)
        if (t.type === "hr" && t.mode === "zone") return 0.75;
        if (t.type === "pace" && t.mode === "pct") return 2 / (t.lo + t.hi);
        if (t.type === "rpe") return (RPE_F[Math.round(t.lo)] + RPE_F[Math.round(t.hi)]) / 2 || null;
        return null;
      };
      const sec = (x) => x.dur.type === "time" ? x.dur.value : x.dur.type === "distance" ? x.dur.value * 0.36 : x.dur.type === "load" ? x.dur.value * 36 : (x.dur.est || 60);
      const tot = rows.reduce((a, x) => a + sec(x), 0) || 1, W = 96, H = 26;
      let xx = 0;
      const lv = (v) => v == null ? 0 : v < .75 ? 1 : v < .88 ? 2 : v < 1.01 ? 3 : v < 1.06 ? 4 : 5;
      const bars = rows.map((x) => {
        const v = f(x), w = sec(x) / tot * W, h = Math.max(3, Math.min(1.3, v == null ? (x.kind === "rest" ? .45 : .55) : v) / 1.3 * H);
        const fill = v == null ? "var(--bar)" : `var(--wz${lv(v)})`;
        const r = `<rect x="${xx.toFixed(1)}" y="${(H - h).toFixed(1)}" width="${Math.max(0.6, w - 0.6).toFixed(1)}" height="${h.toFixed(1)}" fill="${fill}"/>`;
        xx += w;
        return r;
      }).join("");
      return `<svg viewBox="0 0 ${W} ${H}" aria-hidden="true" preserveAspectRatio="none">${bars}</svg>`;
    }
    onTpl(e) {
      const m = e.target.closest('input[name="we-tm"]');
      if (m) { this.tplFull = m.value === "full"; return; }
      const c = e.target.closest("button[data-cat]");
      if (c) { this.tplCat = c.dataset.cat; this.tplSub = null; this.menuHtml(); return; }
      const sb = e.target.closest("button[data-sub]");
      if (sb) { this.tplSub = sb.dataset.sub; this.menuHtml(); return; }
      const tb = e.target.closest("button[data-tt]");
      if (tb) { this.tplTT = tb.dataset.tt; ttSave(this.tplTT); this.menuHtml(); return; }
      const b = e.target.closest("button[data-t]"); if (!b) return;
      const [g, i] = b.dataset.t.split(".").map(Number), row = this.tpls.groups[g].rows[i];
      this.applyRow(row);
    }
    // one template row into the structure (整份換 / 只換主課 by tplFull), as a click in 插入範本 does
    applyRow(row) {
      const fresh = (xs) => xs.map(clone);
      if (!this.doc) this.doc = { origin: "user", items: [] };
      const items = this.doc.items;
      if ((this.tplFull || !items.length) && row.full) this.doc.items = fresh(row.full);
      else {
        // replace the main set: everything between the leading warm-up (warm steps, strides) and the cool-down
        let a = 0;
        const lead = (x) => x.kind === "warm" || (x.kind === "repeat" && x.items.every((y) => y.kind !== "work"));
        while (a < items.length && lead(items[a])) a++;
        let z = items.length;
        while (z > a && items[z - 1].kind === "cool") z--;
        items.splice(a, z - a, ...fresh(row.items));
      }
      // a user template with a route GPX: the structure keeps its id (the chart's elevation; the
      // session's own copy of the profile is taken from it on save); another template replacing
      // the whole structure drops both
      if (row.mine && row.gpx) { this.doc.tpl = row.id; delete this.doc.route; }
      else if (this.tplFull || !items.length) { delete this.doc.tpl; delete this.doc.route; }
      this.$("we-pop").hidden = true;
      this.touch();
      this.o.onTemplate && this.o.onTemplate(row, this.tplFull);
    }
    // a template by its key (the 課表 page's ?add= deep link, SP-39 「安排課表」): the whole session
    // (warm-up and cool-down included); the row, or null when the key isn't a template
    async findKey(key) {
      if (!this.tpls) {
        const r = await req("GET", `${this.o.api}/steps/templates`);
        this.tpls = r.ok ? r.body : { cats: [], groups: [] };
      }
      for (const g of this.tpls.groups || []) {
        const row = (g.rows || []).find((r) => r.key === key);
        if (row) return { row, cat: g.cat };
      }
      return null;
    }
    async applyKey(key) {
      const f = await this.findKey(key);
      if (!f) return null;
      const row = f.row;
      this.tplFull = true;
      this.applyRow(row);
      return row;
    }
    // the session's total from outside (同負荷換算 changed the time): the longest top-level
    // timed step takes the difference
    setTotal(min) {
      if (!this.doc || this.ro) return false;
      const cur = ((this.view || {}).totals || {}).sec, want = Math.round(min * 60);
      if (cur == null || Math.abs(want - cur) < 30) return false;
      const ts = this.doc.items.filter((x) => x.kind !== "repeat" && x.dur.type === "time");
      if (!ts.length) return false;
      const st = ts.reduce((a, x) => (x.dur.value > a.dur.value ? x : a));
      st.dur = { type: "time", value: Math.max(60, st.dur.value + want - cur) };
      this.touch();
      return true;
    }
    // 儲存成範本 (SP-36): name + categories (built-in and the user's own, several), the current structure
    async saveForm() {
      const box = this.$("we-save");
      if (!box.hidden) { box.hidden = true; return; }
      if (!this.doc || !this.doc.items.length) { this.$("we-sum").textContent = tr("workout.save_tpl_empty"); return; }
      if (!this.tpls) {
        const r = await req("GET", `${this.o.api}/steps/templates`);
        this.tpls = r.ok ? r.body : { cats: [], groups: [] };
      }
      const s = this.sess(), def = this.catOf(s.kind);
      const cats = (this.tpls.cats || []).map((c) => `<label><input type="checkbox" value="${esc(c.id)}"${c.id === def ? " checked" : ""}>${esc(c.label)}</label>`).join("");
      box.innerHTML = `<div class="row"><b>${esc(tr("workout.save_tpl"))}</b><input class="nm" id="we-save-nm" maxlength="40" value="${esc(s.title || "")}" aria-label="${esc(tr("workout.tpl_name"))}" placeholder="${esc(tr("workout.tpl_name"))}"></div>
        <div class="cats" role="group" aria-label="${esc(tr("workout.tpl_cats"))}">${cats}</div>
        <div class="row"><button type="button" class="btn primary" data-s="ok">${esc(tr("workout.save"))}</button><button type="button" class="btn" data-s="no">${esc(tr("workout.cancel"))}</button>
          <span class="meta">${esc(tr("workout.save_tpl_hint"))}</span></div>
        <div class="msg" id="we-save-msg" aria-live="polite"></div>`;
      box.hidden = false;
      box.onclick = (e) => {
        const b = e.target.closest("button[data-s]"); if (!b) return;
        if (b.dataset.s === "no") { box.hidden = true; return; }
        this.saveTemplate();
      };
      this.$("we-save-nm").focus();
    }
    async saveTemplate() {
      const box = this.$("we-save"), m = this.$("we-save-msg");
      const name = this.$("we-save-nm").value.trim();
      const cats = [...box.querySelectorAll(".cats input:checked")].map((x) => x.value);
      const s = this.sess();
      const body = { name, cats, steps: this.stepsDoc(), target_basis: s.target_basis || undefined };
      const url = this.uid ? `${this.o.api}/sessions/${encodeURIComponent(this.uid)}/save-as-template` : `${this.o.api}/steps/templates/user`;
      const r = await req("POST", url, body);
      if (!r.ok) {
        const d = r.body.detail || {};
        m.className = "msg err";
        m.textContent = (d.errors || [typeof d === "string" ? d : (r.body.message || tr("workout.save_failed"))]).join(" · ");
        return;
      }
      this.tpls = null;                                 // the menu shows it next time
      m.className = "msg ok";
      m.innerHTML = `${esc(tr("workout.saved_tpl", { name: r.body.name }))} <a href="${esc(this.o.api)}/templates/page#${r.body.id}">${esc(tr("workout.to_templates"))}</a>`;
    }
    openBox() { this.$("we-box").open = true; const t = this.root.querySelector(".we-row input.dur"); if (t && !this.ro) t.focus(); }
  }

  // endOptions: the 時長類型 <option>s of one step for a context (pure; tests/test_static_steps.py)
  window.WorkoutEditor = { mount: (root, opts) => new Editor(root, opts), request: req,
    endOptions: (ctx, st) => Editor.prototype.endOpts.call({ ctx }, st),
    // SP-57 (tests/test_rpe_load.py): a 「負荷」 step's dur after an edit, and its input html
    loadDur, loadInput: (ctx, st, r) => Editor.prototype.loadIn.call({ ctx }, st, r, "") };
})();
