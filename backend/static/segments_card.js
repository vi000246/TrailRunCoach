/* Single-activity view: a card listing the repeated segments and routes this
 * activity matched, with its rank on each ("第 2 快 / 7 次"), linking to the
 * 路線 page. Loaded by wko5_viewer.html with one script tag; everything else
 * lives here so the viewer stays untouched.
 *
 * The viewer rebuilds #grid on every load(); this watches #grid and appends
 * the card after the viewer's own cards whenever a workout is shown.
 * It reads the viewer's global state `S` (S.mode, S.workout).
 */
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const hms = (s) => {
    if (s == null) return "—";
    s = Math.round(s);
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
  };
  const cache = new Map();
  let pending = null;

  function state() {
    try { return typeof S !== "undefined" ? S : null; } catch { return null; }   // the viewer's global
  }

  async function fill(card, idx) {
    let res = cache.get(idx);
    if (!res) {
      try { res = await (await fetch(`/api/v1/wko5/workouts/${idx}/segments`)).json(); }
      catch { card.querySelector(".body").textContent = "讀取失敗"; return; }
      if (res.built) cache.set(idx, res);
    }
    const body = card.querySelector(".body");
    if (!res.segments?.length) {
      body.innerHTML = `<div class="meta">${res.built ? "這次沒有經過重複的路段" : "路段還在第一次整理中，稍後再看"}</div>`;
      return;
    }
    body.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:13px">` + res.segments.map((r) => {
      const rank = r.rank == null ? "—" : r.rank === 1 ? `<b style="color:var(--ok)">最快</b> / ${r.of} 次` : `第 ${r.rank} 快 / ${r.of} 次`;
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
    if (cur && +cur.dataset.w === s.workout) return;
    cur?.remove();
    const card = document.createElement("section");
    card.className = "card";
    card.id = "segcard";
    card.dataset.w = s.workout;
    card.innerHTML = `<h2>重複路段 <a href="/api/v1/routes/page" style="font-size:12px;font-weight:400;color:var(--accent);text-decoration:none">路線 →</a></h2><div class="body"><div class="loading">計算中…</div></div>`;
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
