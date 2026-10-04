/*
 * The 課表 page's structured workout editor (docs/plans/workout-editor.plan.md §3.3,
 * engine/workout_steps.py, api/plan_sessions.py /steps/*).
 *
 *   const we = WorkoutEditor.mount(el, { api, session: () => ({uid, kind, title, …}), onView });
 *   we.open({ uid, ro, kind })      derive (stored or from the text; nothing stored)
 *   we.refresh()                    the dialog's fields changed (目標用, kind, minutes …)
 *   we.rederive(extra)              a new library template was chosen in the dialog
 *   we.payload()                    {steps} to save, {steps: null} to clear, or null (untouched)
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
.we-ro .we-acts, .we-ro .we-tools, .we-ro .grip, .we-ro .we-sumtpl { display: none; }
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
  const KIND = { warm: "暖身", work: "主課", rest: "休息", cool: "緩和", other: "其他" };
  const TYPE = { auto: "自動", power: "功率", hr: "心率", pace: "配速", rpe: "RPE", none: "無" };
  // ≈ % CP of RPE 1–10 (workout_steps.RPE_FRAC; the mini chart's height only, 推估)
  const RPE_F = [0, 0.55, 0.62, 0.70, 0.76, 0.82, 0.88, 0.94, 1.00, 1.05, 1.10];
  const opt = (v, l, cur, extra = "") => `<option value="${esc(v)}"${String(v) === String(cur) ? " selected" : ""}${extra}>${esc(l)}</option>`;
  const q = (tip) => `<button type="button" class="qtip" aria-label="說明" data-tip="${esc(tip)}">?</button>`;
  // i18n (static/i18n/i18n.js t(key, fallback)); the zh-TW text is the fallback
  const tr = (k, fb, p) => (window.I18N && window.I18N.t ? window.I18N.t(k, fb, p) : fb);
  const noTpaceText = () => tr("workout.no_tpace", "沒有閾值配速：這段推到手錶不會有配速目標");
  // where threshold pace is estimated (GET /steps/context tpace_link: the Friel pace-zone chart)
  const tpaceLink = (ctx) => {
    const u = (ctx || {}).tpace_link;
    return u ? ` <a class="we-tpl" href="${esc(u)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(tr("workout.no_tpace_link", "看閾值配速怎麼估"))}</a>` : "";
  };
  const OPEN_W = 90;
  const TIP = {
    basis: "每一段自己決定用功率、心率還是配速：點那一段的目標就能改（標「指定」）。標「自動」的段依課表類型（路跑輕鬆／長跑看功率、心率以輕鬆跑上限為上限；越野看心率；間歇看功率）。數字依目前的 CP、LTHR、輕鬆跑上限、閾值配速帶入。",
    chart: "橫軸是時間（「直到按下計圈」的段畫成固定寬度、斜線），高度和顏色都是強度（約當 % CP）。心率段換算成功率高度是推估，只影響這張圖。點一段可以選到下面那一步。",
    tss: "TSS 估＝Σ 秒 × IF² × 100 ÷ 3600，IF＝目標中點 ÷ CP；心率段用 Friel 心率區對到 Palladino 功率區，沒有目標的段依類型給固定值。都是推估，跑步 rTSS 和這個公式的差距未驗證。",
    rules: "即時檢查：5 區每趟至少 2 分鐘（台灣教練）；5 區休息不超過最短一趟、也不超過 3 分鐘（Buchheit）；3 區每趟至少 3 分鐘（Haugen 2022 下緣）；這天的時間上限（課表偏好，軟上限只提醒、硬上限擋下）；選了功率卻沒有 CP 之類的錯誤。強度課另外和這一階的標準課表比，看算不算進階。",
    lastRest: "最後一趟做完不休息、直接接下一段。COROS 的間歇群組做不到，推送時會攤平成一段一段（每段一個 lap）。",
    watch: "COROS 手錶的限制：跑步的功率只收絕對瓦數（沒有 % CP）；每段只能設一個目標；沒有漸進（ramp）步驟。下面是實際會送出的步驟。",
    tpl: "有出處的課表（作者／書／網址寫在每一份下面），依這堂的類型篩選。強度課再分三類，先看主課強度、再看每趟長度：有氧間歇（≤ 101% CP；長 tempo 每趟 15–30 分、巡航間歇 6–15 分，更短的也算巡航）、VO2max 間歇（高於閾值、每趟 2–5 分、休息約 1:1；30/30 這種短趟短休也在這裡）、速度（每趟 ≤ 2 分、休息 ≥ 2 倍，例如 R、加速跑、短坡衝刺）。越野跑分三類：結構化爬升（階梯、坡度穩定的路線，心率／功率上下限照設）、技術地形（時間＋爬升＋RPE，不設心率、功率目標；RPE ≥ 7 算強度課）、下坡技術／離心（時間＋下降量）。每一份下面有一行訓練目的。每段用來源自己的目標：Palladino／Stryd 看功率，Friel、Uphill Athlete、Pfitzinger 看心率，Daniels、Canova、Billat 看配速（只在來源的數字 app 沒有時才換算，標推估）。插入後就是一般步驟，可以再改；改過的強度課用它自己的趟數和強度判斷算不算進階。每一類最上面是這堂課的「推薦」前三名（強度課第一名＝間歇階梯的下一步），其他收在下面。",
    total: "總時間由下面的步驟加總：要改時間就改步驟（點這格會打開結構）。",
    pacePct: "配速的 % 是閾值配速的倍數：數字大＝慢（例：114–129% 是 Friel 2 區）。",
    rpe: "RPE 用 0–10 量表（Foster）：3 中等、5 吃力、7 很累、10 極限。技術地形、下坡的心率上不去、功率不準，所以只看 RPE 和爬升／下降；手錶上這段不設目標，RPE 和爬升寫在步驟名稱。最高到 7 以上這堂算強度課（和其他強度課隔 48 小時）。負荷照手錶記錄算。",
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
      this.tpls = null; this.tplFull = true; this.tplCat = null; this.tplSub = null;
      root.innerHTML = `<details class="we" id="we-box"><summary><b>結構</b><span class="we-sumtxt" id="we-sum">載入中…</span><button type="button" class="we-sumtpl" id="we-sumtpl">範本</button></summary>
        <div class="we-body">
          <div class="we-tools" id="we-tools">
            <span class="we-menu" id="we-tpl"><button class="btn primary we-tplbtn" type="button" data-a="tpl" id="we-tplbtn" aria-haspopup="true" aria-expanded="false">＋ 插入範本 ▾</button>${q(TIP.tpl)}
              <div class="we-pop" id="we-pop" hidden></div></span>
            <button class="btn" type="button" data-a="add-step">＋ 步驟</button>
            <button class="btn" type="button" data-a="add-rep">＋ 重複</button>
            <span class="sp"></span>
            <button class="btn" type="button" data-a="reset" id="we-reset" hidden>還原成系統排的</button>
            ${q(TIP.basis)}
          </div>
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
      this.doc = r.body.steps ? { origin: r.body.steps.origin, items: r.body.steps.items } : null;
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
      return { steps: { origin: "user", items: this.doc.items } };
    }
    async check() {
      if (!this.doc) { this.view = null; this.render(); this.o.onView && this.o.onView(null, this); return; }
      const my = ++this.seq;
      const r = await req("POST", `${this.o.api}/steps/check`, { ...this.sess(), uid: this.uid, steps: { origin: "user", items: this.doc.items } });
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
        : st.dur.est ? `<span class="we-lap" title="直到按下計圈；總時間用課表寫的最短時間估">≈ ${mmss(st.dur.est)}</span>` : "";
      const ov = st.target && st.target.type !== "auto";
      const tb = r ? `<span class="src${ov ? " ov" : ""}">${ov ? "指定" : "自動"}</span><b>${r.type === "none" ? "不設目標" : `${esc(r.label)} ${esc(r.text)}`}</b>${r.sub ? `<span class="s">${esc(r.sub)}</span>` : ""}` +
        (r.err ? `<span class="e">✕ ${esc(r.err)}</span>` : r.warn ? `<span class="w">⚠ ${esc(r.warn)}</span>` : "") : `<span class="s">…</span>`;
      return `<div class="we-row${this.sel === st.id ? " sel" : ""}" draggable="${!this.ro}" tabindex="0" data-id="${st.id}" style="--zc:${this.zc(r, st)}">
        <span class="grip" aria-hidden="true" title="拖曳排序">⋮⋮</span>
        <select class="kind" data-f="kind" aria-label="類型"${dis}>${Object.entries(KIND).map(([k, l]) => opt(k, l, st.kind)).join("")}</select>
        <span class="we-dur"><select data-f="dtype" aria-label="時長類型"${dis}>${opt("time", "時間", dt)}${opt("distance", "距離", dt)}${opt("open", "直到按下計圈", dt)}</select>${durIn}</span>
        <button type="button" class="we-tbtn${this.openT === st.id ? " on" : ""}" data-a="tgt" aria-expanded="${this.openT === st.id}" aria-label="這一段的目標（點一下改這一段）"${dis}>${tb}</button>
        <input class="note nt" data-f="note" value="${esc(st.note || "")}" maxlength="60" placeholder="名稱（手錶顯示）" aria-label="名稱"${dis}>
        <span class="we-acts"><button type="button" data-a="up" title="上移" aria-label="上移">↑</button><button type="button" data-a="down" title="下移" aria-label="下移">↓</button><button type="button" data-a="dup" title="複製" aria-label="複製">⧉</button><button type="button" data-a="wrap" title="包成重複" aria-label="包成重複">⟳</button><button type="button" data-a="del" title="刪除" aria-label="刪除">✕</button></span>
        ${this.openT === st.id && !this.ro ? this.tgHtml(st, r) : ""}
      </div>`;
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
        `<span><i style="background:var(--bar)"></i>不設目標</span><span><i style="background:repeating-linear-gradient(45deg,var(--bar) 0 3px,var(--panel) 3px 5px)"></i>直到按下計圈</span>${q(TIP.chart)}`;
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
      this.$("we-issues").innerHTML = list.map((i) => `<li class="${i.level}"${i.id ? ` data-id="${esc(i.id)}"` : ""}><span class="ic">${ic[i.level] || "i"}</span><span>${esc(i.text)}${isNt(i.text) ? tpaceLink(this.ctx) : ""}</span></li>`).join("") +
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
      const total = v.order.reduce((a, o) => a + (o.open && !o.sec ? OPEN_W : o.sec), 0) || 1;
      const sx = (W - 2) / total, maxF = 1.3;
      const yCP = base - (1 / maxF) * (base - top);
      el("line", { x1: 0, x2: W, y1: yCP, y2: yCP, stroke: "var(--faint)", "stroke-dasharray": "3 3", "stroke-width": 1 });
      el("text", { x: W - 2, y: yCP - 3, "text-anchor": "end", "font-size": 10.5, fill: "var(--muted)" }).textContent = "CP";
      el("line", { x1: 0, x2: W, y1: base, y2: base, stroke: "var(--line)" });
      const res = v.resolved || {}, spans = new Map();
      let x = 1, tAcc = 0;
      const ticks = [];
      const step = total > 5400 ? 1800 : total > 2400 ? 600 : 300;
      for (const o of v.order) {
        const w = (o.open && !o.sec ? OPEN_W : o.sec) * sx;
        const f = Math.min(maxF, o.frac == null ? (o.kind === "rest" ? 0.5 : 0.6) : o.frac);
        const h = Math.max(6, (f / maxF) * (base - top));
        const fill = o.open ? "url(#we-hatch)" : o.frac == null || !o.level ? "var(--bar)" : `var(--wz${o.level})`;
        const rr = el("rect", { x: x + 1, y: base - h, width: Math.max(1, w - 2), height: h, rx: 2, fill, class: "blk", "data-id": o.id });
        if (this.sel === o.id) { rr.setAttribute("stroke", "var(--text)"); rr.setAttribute("stroke-width", 1.5); }
        rr.addEventListener("mousemove", (e) => this.tip(e, o, res[o.id]));
        rr.addEventListener("mouseleave", () => (this.$("we-tip").hidden = true));
        rr.addEventListener("click", () => this.select(o.id));
        for (const rp of o.rep) { const sp = spans.get(rp.id) || { a: x, b: x + w, n: rp.n, d: o.rep.indexOf(rp) }; sp.b = x + w; spans.set(rp.id, sp); }
        if (!o.open || o.sec) { const before = tAcc; tAcc += o.sec; for (let m = Math.ceil(before / step) * step || step; m <= tAcc; m += step) if (m > before) ticks.push(x + (m - before) * sx); }
        x += w;
      }
      ticks.forEach((px, i) => { if (px < W - 18) el("text", { x: px, y: H - 3, "text-anchor": "middle", "font-size": 10.5, fill: "var(--faint)" }).textContent = `${(i + 1) * step / 60}′`; });
      for (const { a, b, n, d } of spans.values()) {
        const y = 10 + d * 8;                     // a nested repeat's bracket sits under its parent's
        el("path", { d: `M${a + 2} ${y + 6} V${y} H${b - 2} V${y + 6}`, fill: "none", stroke: "var(--muted)", "stroke-width": 1 });
        if (b - a > 18) el("text", { x: (a + b) / 2, y: y - 2, "text-anchor": "middle", "font-size": 11, fill: "var(--text)", "font-weight": 600 }).textContent = `×${n}`;
      }
      svg.setAttribute("aria-label", `區段圖：${v.structure || ""}，總長 ${mmss((v.totals || {}).sec || 0)}`);
    }
    tip(e, o, r) {
      const t = this.$("we-tip"), box = this.$("we-chart").getBoundingClientRect(), f = this.find(o.id);
      const st = f ? f.it : { kind: o.kind, note: "" };
      const n = o.rep.length ? o.rep.map((x) => `第 ${x.i + 1}/${x.n} 趟`).join(" · ") + " · " : "";
      t.innerHTML = `<b>${n}${esc(KIND[o.kind] || o.kind)}</b> · ${o.open ? "直到按下計圈" : mmss(o.sec) + (o.est ? "（推估）" : "")}<br>` +
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
      } else if (k === "dtype") it.dur = v === "open" ? { type: "open" } : v === "distance" ? { type: "distance", value: 1000 } : { type: "time", value: it.dur.value && it.dur.type === "time" ? it.dur.value : 300 };
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
      const recs = ((this.recs || {}).cats || {})[cat]?.filter((x) => where[x.key]) || [];
      const recKeys = new Set(recs.map((x) => x.key));
      const gs = (T.groups || []).map((g, gi) => ({ g, gi })).filter(({ g }) => g.cat === cat && (!subs.length || g.sub === this.tplSub));
      const tab = (k, id, l, on, tip) => `<button type="button" data-${k}="${esc(id)}" class="${on ? "on" : ""}" aria-pressed="${on}"${tip ? ` title="${esc(tip)}"` : ""}>${esc(l)}</button>`;
      // pace × threshold pace (Daniels / Canova / Billat …) with no threshold pace: badge it
      const noTp = !(((this.ctx || {}).thresholds || {}).tpace);
      const tpBadge = (r) => r.needs_tpace && noTp
        ? ` <span class="we-tpb" title="${esc(T.no_tpace_text || noTpaceText())}">⚠ ${esc(tr("workout.no_tpace_badge", "沒有閾值配速"))}</span>` : "";
      // 技術地形／下坡 rows: how the scheduler counts them (workout_steps.rpe_role)
      const fsub = (r) => (r.family && r.family.sub_label ? ` <span class="fam">${esc(r.family.sub_label)}</span>` : "") +
        (r.role ? ` <span class="fam">${r.role === "quality" ? "算強度課" : "算輕鬆課"}</span>` : "");
      const btn = (r, at, sub) => `<button type="button" class="t" data-t="${at}">${this.mini(r.full || r.items)}<span>${esc(r.label)}${fsub(r)}${r.src_kind === "推估" ? ` <span class="faint">（推估）</span>` : ""}${tpBadge(r)}</span>` +
        `${r.purpose ? `<span class="pur">${esc(r.purpose)}</span>` : ""}<span class="src${sub ? " why" : ""}">${esc(sub || r.src || "")}</span></button>`;
      const rowAt = (at) => { const [g, i] = at.split(".").map(Number); return T.groups[g].rows[i]; };
      const rec = recs.length ? `<div class="g rec">推薦 ${q((this.recs || {}).tip || "")}</div>` +
        recs.map((x, n) => btn(rowAt(where[x.key]), where[x.key], `${n + 1}. ${x.reason}`)).join("") : "";
      const others = gs.map(({ g, gi }) => {
        const rows = g.rows.map((r, i) => [r, i]).filter(([r]) => !recKeys.has(r.key));
        return rows.length ? (gs.length > 1 || g.title ? `<div class="g">${esc(g.title || g.group)}</div>` : "") + rows.map(([r, i]) => btn(r, `${gi}.${i}`)).join("") : "";
      }).join("");
      const subTabs = subs.length ? `<div class="tabs sub" role="group" aria-label="類別">${subs.map((s) => tab("sub", s.id, s.label, s.id === this.tplSub, s.tip)).join("")}</div>` : "";
      const list = subTabs + (others || `<p class="empty">${gs.length ? "都在上面的推薦裡" : "這一類還沒有範本"}</p>`);
      const n = (T.groups || []).filter((g) => g.cat === cat).reduce((a, g) => a + g.rows.filter((r) => !recKeys.has(r.key)).length, 0);
      this.$("we-pop").innerHTML = `<div class="tabs" role="group" aria-label="類型">${cats.map((c) => tab("cat", c.id, c.label, c.id === cat)).join("")}</div>` +
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
      const sec = (x) => x.dur.type === "time" ? x.dur.value : x.dur.type === "distance" ? x.dur.value * 0.36 : (x.dur.est || 60);
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
      const b = e.target.closest("button[data-t]"); if (!b) return;
      const [g, i] = b.dataset.t.split(".").map(Number), row = this.tpls.groups[g].rows[i];
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
      this.$("we-pop").hidden = true;
      this.touch();
      this.o.onTemplate && this.o.onTemplate(row, this.tplFull);
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
    openBox() { this.$("we-box").open = true; const t = this.root.querySelector(".we-row input.dur"); if (t && !this.ro) t.focus(); }
  }

  window.WorkoutEditor = { mount: (root, opts) => new Editor(root, opts), request: req };
})();
