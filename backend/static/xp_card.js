/* 疊石 Cairn 開通小卡 (SP-763; research SP-571 §5.1 / §6.1, docs/research/sp571-cross-promotion.md).
 *
 * Only where a multi-day trip is planned: 賽事管理's editor (a race of 2 days or more) and the race
 * calculator once a multi-day result is drawn — one card a screen, not a pop-up. Before SSO (SP-329)
 * this app cannot tell whether someone has a Cairn account: the button opens Cairn's front page in a new
 * tab (Cairn makes a free account at the first Google sign-in). Hidden (GET /api/v1/app/xp/cairn says
 * why): after 「不用了」 (both places at once, kept with the account — every device; 設定 › 提示多寡 shows it
 * again), for 提示多寡「精簡」 (SP-482: an introduction, not a tip that changes what to do), for an account
 * linked with Cairn (only the links stay), and in the demo (no route there).
 *
 *   XpCard.show(el, { where: "races" | "calc", compact })   // draws into el, or hides it
 *   XpCard.hide(el)
 *
 * Counts (SP-765): xp-card-shown once a page per place, xp-card-click, and 「不用了」 (its own route).
 * Strings: common.xp.cairn.* (static/i18n/{zh-TW,en}/common.json).
 */
(() => {
  const API = "/api/v1/app/xp";
  const t = (k, p) => (window.I18N && I18N.t ? I18N.t(k, p) : k);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let stateP = null;
  const counted = new Set();             // places whose xp-card-shown went out on this page
  const mounted = new Set();

  function state() {
    if (!stateP) {
      stateP = fetch(`${API}/cairn`, { credentials: "same-origin" })
        .then((r) => (r.ok ? r.json() : { show: false, why: "unavailable" }))
        .catch(() => ({ show: false, why: "unavailable" }));
    }
    return stateP;
  }
  const event = (name) => fetch(`${API}/event`, { method: "POST", credentials: "same-origin", keepalive: true,
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ event: name }) }).catch(() => {});

  function hide(el) {
    if (!el) return;
    el.hidden = true;
    el.innerHTML = "";
    mounted.delete(el);
  }

  async function show(el, opts = {}) {
    if (!el) return;
    const st = await state();
    if (!st.show || !st.url) { hide(el); return; }
    const where = opts.where || "page";
    const open = `<a class="xp-go" href="${esc(st.url)}" target="_blank" rel="noopener" data-xp-go>${esc(t("common.xp.cairn.open"))}</a>`;
    const no = `<button type="button" class="xp-no" data-xp-no>${esc(t("common.xp.cairn.no"))}</button>`;
    el.innerHTML = opts.compact
      ? `<div class="xp-card xp-compact" role="note"><span class="xp-text">${esc(t("common.xp.cairn.short"))}</span>
           <span class="xp-acts">${open}${no}</span></div>`
      : `<div class="xp-card" role="note"><b class="xp-h">${esc(t("common.xp.cairn.title"))}</b>
           <p class="xp-text">${esc(t("common.xp.cairn.body"))}</p>
           <div class="xp-acts">${open}${no}</div>
           <p class="xp-small">${esc(t("common.xp.cairn.note"))}</p></div>`;
    el.hidden = false;
    mounted.add(el);
    el.querySelector("[data-xp-go]").addEventListener("click", () => event("xp-card-click"));
    el.querySelector("[data-xp-no]").addEventListener("click", dismiss);
    if (!counted.has(where)) { counted.add(where); event("xp-card-shown"); }
  }

  async function dismiss(e) {
    if (e) e.preventDefault();
    try {
      const r = await fetch(`${API}/cairn/dismiss`, { method: "POST", credentials: "same-origin" });
      if (r.ok) stateP = Promise.resolve(await r.json());
    } catch { /* offline: hidden on this page, asked again next time */ stateP = Promise.resolve({ show: false, why: "off" }); }
    [...mounted].forEach(hide);
  }

  if (typeof document !== "undefined" && document.head && !document.getElementById("xp-card-css")) {
    const s = document.createElement("style");
    s.id = "xp-card-css";
    s.textContent = `.xp-card{border:1px solid var(--line,#ddd);border-left:3px solid var(--accent,#2a78d6);border-radius:8px;padding:8px 12px;margin:8px 0;background:var(--panel,transparent);font-size:13.5px;line-height:1.55}
.xp-card .xp-h{display:block;margin-bottom:2px}.xp-card .xp-text{margin:0 0 6px;color:var(--text,inherit)}
.xp-card .xp-acts{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center}
.xp-card a.xp-go{display:inline-block;padding:3px 12px;border-radius:6px;background:var(--accent,#2a78d6);color:#fff;text-decoration:none;font-weight:600}
.xp-card button.xp-no{padding:3px 10px;border-radius:6px;border:1px solid var(--line,#ccc);background:transparent;color:var(--muted,#666);cursor:pointer;font:inherit}
.xp-card .xp-small{margin:6px 0 0;font-size:12px;color:var(--muted,#666)}
.xp-compact{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center}.xp-compact .xp-text{margin:0;flex:1 1 220px}`;
    document.head.appendChild(s);
  }

  window.XpCard = { show, hide, state };
})();
