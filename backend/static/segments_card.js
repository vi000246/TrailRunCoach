/* Single-activity view: a card listing the repeated segments and routes this
 * activity matched, with its rank on each ("第 2 快 / 7 次"), linking to the
 * 路線 page. Loaded by charts.html with one script tag; everything else
 * lives here so the viewer stays untouched.
 *
 * The viewer rebuilds #grid on every load(); this watches #grid and appends
 * the card after the viewer's own cards whenever a workout is shown.
 * It reads the viewer's global state `S` (S.mode, S.workout, S.dash, S.hint).
 *
 * 提示多寡 (SP-487, AN12 ●◐◐; docs/research/ui-hint-audit.md §1.5): 完整 = on every page, 「沒有重複路段」
 * included (as before); 標準／精簡 = only on the default page (本次重點, dashboard 0), and no card at all
 * when the activity matched no segment.
 */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const hms = (s) => {
    if (s == null) return "—";
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
  };
  const T = (k, p) => (window.I18N ? window.I18N.t("viewer.seg." + k, p) : k);   // i18n: viewer.seg.* (the viewer's catalog)
  const cache = new Map();
  const none = new Set();       // workouts with no segment, card put away (標準／精簡): not added again
  let pending = null;

  function state() {
    try { return typeof S !== "undefined" ? S : null; } catch { return null; }   // the viewer's global
  }
  // ---- segments card level (SP-487) ----
  const AN12 = { id: "AN12", cls: 3, show: "●◐◐" };          // engine/hints.py CATALOG
  const level = (s) => { const l = s && s.hint && s.hint.level; return ["full", "standard", "lean"].includes(l) ? l : "standard"; };
  const sym = (s) => (window.Hints ? Hints.sym(AN12, level(s)) : "●");
  /** the card on this page? (◐: the default page only) */
  const onPage = (s) => { const y = sym(s); return y === "●" || (y === "◐" && !((s.dash || {}).workout > 0)); };
  /** 「沒有重複路段」 shown, or no card (◐) */
  const keepNone = (s) => sym(s) === "●";
  // ---- end segments card level ----

  async function fill(card, idx) {
    let res = cache.get(idx);
    if (!res) {
      try { res = await (await fetch(`/api/v1/app/workouts/${idx}/segments`)).json(); }
      catch { card.querySelector(".body").textContent = T("failed"); return; }
      if (res.built) cache.set(idx, res);
    }
    const body = card.querySelector(".body");
    if (!res.segments?.length) {
      if (res.built && !keepNone(state())) { none.add(idx); card.remove(); return; }
      body.innerHTML = `<div class="meta">${esc(T(res.built ? "none" : "building"))}</div>`;
      return;
    }
    body.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:13px">` + res.segments.map((r) => {
      const rank = r.rank == null ? "—" : r.rank === 1 ? `<b style="color:var(--ok)">${esc(T("fastest"))}</b> / ${esc(T("of_n", { n: r.of }))}`
        : esc(T("rank", { rank: r.rank, n: r.of }));
      const km = (r.length_m / 1000).toFixed(r.length_m < 10000 ? 2 : 1);
      const g = r.kind === "route" ? "" : r.gain_m >= 0 ? ` ↑${Math.round(r.gain_m)}` : ` ↓${Math.round(-r.gain_m)}`;
      return `<tr style="border-bottom:1px solid var(--line)">
        <td style="padding:5px 4px"><a href="/api/v1/routes/page#${esc(r.id)}" style="color:var(--accent);text-decoration:none">${esc(r.name)}</a>
          <div class="meta" style="font-size:11.5px">${esc(r.kind_zh)} ${km} km${g}</div></td>
        <td style="padding:5px 4px;text-align:right;white-space:nowrap">${rank}</td>
        <td style="padding:5px 4px;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap">${hms(r.time_s)}
          <div class="meta" style="font-size:11.5px">${r.delta_best_s ? "+" + hms(r.delta_best_s) : r.rank === 1 ? "" : "—"}</div></td></tr>`;
    }).join("") + `</table>`;
  }

  function ensure() {
    const s = state(), grid = document.getElementById("grid");
    if (!s || !grid || s.mode !== "workout" || s.workout == null) return;
    if (!grid.querySelector("section.card:not(#segcard)")) return;       // viewer still starting its cards
    const cur = grid.querySelector("#segcard");
    if (!onPage(s) || (none.has(s.workout) && !keepNone(s))) { cur?.remove(); return; }
    const page = String((s.dash || {}).workout ?? 0);
    if (cur && +cur.dataset.w === s.workout && cur.dataset.p === page) return;
    cur?.remove();
    const card = document.createElement("section");
    card.className = "card";
    card.id = "segcard";
    card.dataset.w = s.workout;
    card.dataset.p = page;
    card.innerHTML = `<h2>${esc(T("title"))} <a href="/api/v1/routes/page" style="font-size:12px;font-weight:400;color:var(--accent);text-decoration:none">${esc(T("routes"))}</a></h2><div class="body"><div class="loading">${esc(T("loading"))}</div></div>`;
    grid.appendChild(card);
    fill(card, s.workout);
  }

  function watch() {
    const grid = document.getElementById("grid");
    if (!grid) return;
    new MutationObserver(() => {
      if (pending) return;
      pending = setTimeout(() => { pending = null; ensure(); }, 120);
    }).observe(grid, { childList: true });
    ensure();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watch); else watch();
})();
