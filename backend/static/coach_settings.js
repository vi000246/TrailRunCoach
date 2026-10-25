/* SP-789: 設定 › 教練 — a coach and their students (api/coach.py; the rules in accounts/coaching.py).
 *
 *   我的教練   everyone signed in: my coach (or my request waiting for the coach) and 解除／取消; a coach's
 *             code typed here asks that coach (they accept); a coach's invite link
 *             (/api/v1/app/settings?coach_token=…#coach) asks me here — nothing happens until I press 接受
 *   我的學員   a coach (the admin page makes one): my code, my invite link (made, copied, stopped), my
 *             students and their requests (接受／拒絕／解除), how many I have of how many I may (SP-793)
 *
 * The token of an invite link leaves the address bar at once (history.replaceState) and is kept in this
 * page only. Owner mode / the demo (GET /api/v1/account/coach is not there): the tab stays hidden.
 * i18n: settings.coach.*.
 */
(() => {
  const ACCT = "/api/v1/account/coach", COACH = "/api/v1/coach";
  const $ = (id) => document.getElementById(id);
  const T = (k, p) => window.I18N.t("settings.coach." + k, p);
  const day = (iso) => (iso ? String(iso).slice(0, 10) : "");
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const btn = (text, cls, onclick) => { const b = el("button", cls, text); b.type = "button"; b.onclick = onclick; return b; };
  const section = $("coach"), tab = $("tab-coach");
  if (!section || !tab) return;

  // the invite link's token: out of the address bar now (kept in memory for this page only)
  const params = new URLSearchParams(location.search);
  let inviteToken = params.get("coach_token");
  if (inviteToken) {
    params.delete("coach_token");
    const q = params.toString();
    history.replaceState(null, "", location.pathname + (q ? "?" + q : "") + "#coach");
  }

  async function call(method, url, body) {
    const r = await fetch(url, { method, cache: "no-store", headers: body ? { "Content-Type": "application/json" } : {},
                                 body: body ? JSON.stringify(body) : undefined });
    let d = null;
    try { d = await r.json(); } catch { d = null; }
    if (!r.ok) {
      const det = d && d.detail;
      const e = new Error((det && det.message) || T("failed", { status: r.status }));
      e.status = r.status; e.code = det && det.code;
      throw e;
    }
    return d;
  }
  const say = (text, ok) => { const m = $("coach-msg"); m.textContent = text || ""; m.className = "meta" + (ok === false ? " err-text" : ok ? " ok-text" : ""); };

  function hideTab() {
    tab.hidden = true; section.hidden = true;
    const on = document.querySelector('.tabs [aria-selected="true"]');
    if (on && on.dataset.tab === "coach" && typeof showTab === "function") showTab("personal");
  }

  // ---- 我的教練 ------------------------------------------------------------------------------
  let mine = null;
  function renderMine() {
    const box = $("coach-mine");
    box.replaceChildren();
    const rows = (mine && mine.coaches) || [];
    if (!rows.length) box.append(el("p", "meta", T("none")));
    for (const c of rows) {
      const row = el("div", "sub coach-row");
      const head = el("div", "row");
      head.append(el("b", null, c.name),
        el("span", "meta", c.status === "active" ? T("since", { date: day(c.since) }) : T("waiting", { date: day(c.since) })));
      row.append(head);
      if (c.status === "active") {
        row.append(el("p", "meta", T("can_see", { what: c.can_see })));
        row.append(permsBox(c));
      }
      const act = el("div", "row");
      act.append(btn(T(c.status === "active" ? "end" : "cancel"), c.status === "active" ? "danger" : "", async (e) => {
        if (c.status === "active" && !confirm(T("confirm_end", { name: c.name }))) return;
        e.target.disabled = true;
        try { mine = await call("POST", `${ACCT}/${c.link_id}/end`); renderMine(); say(T(c.status === "active" ? "ended" : "cancelled", { name: c.name }), true); }
        catch (err) { e.target.disabled = false; say(err.message, false); }
      }));
      row.append(act);
      box.append(row);
    }
    $("coach-find").hidden = rows.length >= ((mine && mine.max) || 1);
    loadEdits();
  }

  // SP-792: what the coach may change, each switched by the student (off: refused from the coach's next request)
  function permsBox(c) {
    const box = el("div", "coach-perms");
    box.append(el("p", "meta", T("perms_h")));
    const labels = (mine && mine.perm_labels) || {};
    for (const k of Object.keys(c.perms || {})) {
      const lab = el("label", "coach-perm");
      const cb = el("input"); cb.type = "checkbox"; cb.checked = !!c.perms[k]; cb.dataset.perm = k;
      cb.onchange = async () => {
        cb.disabled = true;
        try {
          mine = await call("PUT", `${ACCT}/${c.link_id}/perms`, { [k]: cb.checked });
          renderMine();
          // SP-822: 「看傷病紀錄」 opens a read, not a change
          const see = k === "injury" ? "_see" : "";
          say(T((cb.checked ? "perm_on" : "perm_off") + see, { what: labels[k] || k }), true);
        } catch (err) { cb.checked = !cb.checked; cb.disabled = false; say(err.message, false); }
      };
      lab.append(cb, el("span", null, labels[k] || k));
      box.append(lab);
    }
    return box;
  }

  // SP-792: the coach's log — who, when, what (the latest 50), shown when there is any
  const FIELDS = ["day", "minutes", "title", "kind", "target", "detail", "steps", "terrain", "variant_key", "family"];
  function editText(e) {
    const p = e.params || {}, bits = [];
    const when = p.day || p.date;
    if (when) bits.push(String(when));
    if (p.name || p.title) bits.push(String(p.name || p.title));
    for (const [k, lab] of [["lthr", "LTHR"], ["aethr", "AeT"], ["cp", "CP"]]) if (p[k] != null) bits.push(`${lab} ${p[k]}`);
    if (p.max_hr != null || p.mhr != null) bits.push(T("f_max_hr", { n: p.max_hr ?? p.mhr }));
    if (p.rest_hr != null || p.rhr != null) bits.push(T("f_rest_hr", { n: p.rest_hr ?? p.rhr }));
    if (e.action === "session_edit" && p.fields) {
      const f = String(p.fields).split(",").filter((x) => FIELDS.includes(x)).map((x) => T("field." + x));
      if (f.length) bits.push(T("fields", { list: f.join(T("sep")) }));
    }
    const head = T("edit." + e.action);
    return bits.length ? head + T("paren", { text: bits.join(T("sep")) }) : head;
  }
  async function loadEdits() {
    const box = $("coach-edits");
    if (!box) return;
    let rows = [];
    try { rows = (await call("GET", `${ACCT}/edits?limit=50`)).edits || []; } catch { rows = []; }
    box.replaceChildren();
    box.hidden = !rows.length;
    if (!rows.length) return;
    const det = el("details", "coach-log");
    det.append(el("summary", "meta", T("log_h", { n: rows.length })));
    const ul = el("ul", "coach-log-list");
    for (const e of rows) {
      const li = el("li");
      let at = e.at;
      try { at = new Date(e.at).toLocaleString(window.I18N.locale || undefined, { year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); } catch { /* keep the ISO */ }
      li.append(el("span", "meta", at), " ", el("b", null, e.coach || T("someone")), " ", el("span", null, editText(e)));
      ul.append(li);
    }
    det.append(ul);
    det.open = location.hash === "#coach";
    box.append(det);
  }

  async function lookupCode() {
    const code = $("coach-code").value.trim(), found = $("coach-found");
    found.hidden = true; found.replaceChildren(); say("");
    if (!code) return;
    try {
      const d = await call("POST", `${ACCT}/code`, { code });
      found.append(el("span", null, T("found", { name: d.coach.name })), btn(T("ask"), "primary", async (e) => {
        e.target.disabled = true;
        try { mine = await call("POST", `${ACCT}/request`, { code }); renderMine(); found.hidden = true; $("coach-code").value = ""; say(T("asked", { name: d.coach.name }), true); }
        catch (err) { e.target.disabled = false; say(err.message, false); }
      }));
      found.hidden = false;
    } catch (err) { say(err.message, false); }
  }

  // a coach's invite link: who it is, what accepting lets them see, 接受 / 不要
  async function askInvite() {
    const box = $("coach-ask");
    if (!inviteToken) { box.hidden = true; return; }
    let d;
    try { d = await call("POST", `${ACCT}/invite`, { token: inviteToken }); }
    catch (err) { inviteToken = null; box.hidden = false; box.replaceChildren(el("p", "meta err-text", err.message)); return; }
    const name = d.coach.name;
    box.replaceChildren(el("b", null, T("invite_h", { name })), el("p", "meta", T("invite_body", { name })));
    const act = el("div", "row");
    act.append(btn(T("accept"), "primary", async (e) => {
      e.target.disabled = true;
      try {
        mine = await call("POST", `${ACCT}/invite/accept`, { token: inviteToken });
        inviteToken = null; box.hidden = true; renderMine(); say(T("accepted", { name }), true);
      } catch (err) { e.target.disabled = false; say(err.message, false); }
    }), btn(T("decline"), "", () => { inviteToken = null; box.hidden = true; say(T("declined")); }));
    box.append(act);
    box.hidden = false;
  }

  // ---- 我的學員 (a coach) ----------------------------------------------------------------------
  let side = null, lastUrl = "";
  function renderSide() {
    const head = $("coach-side-head"), list = $("coach-students");
    head.replaceChildren(); list.replaceChildren();
    if (!side) { $("coach-side").hidden = true; return; }
    $("coach-side").hidden = false;
    const count = el("p", side.full ? "meta err-text" : "meta", T(side.full ? "count_full" : "count", { used: side.used, max: side.max_students }));
    const code = el("div", "row");
    code.append(el("span", null, T("my_code")), el("code", "coach-code", side.code),
      btn(T("code_reset"), "", async () => {
        if (!confirm(T("confirm_code_reset"))) return;
        try { side = await call("POST", `${COACH}/code`); renderSide(); say(T("code_done"), true); } catch (err) { say(err.message, false); }
      }));
    const inv = el("div", "row");
    inv.append(el("span", null, side.invite ? T("invite_until", { date: day(side.invite.expires_at) }) : T("invite_none")),
      btn(T(side.invite ? "invite_new" : "invite_make"), side.invite ? "" : "primary", async () => {
        if (side.invite && !confirm(T("confirm_invite_new"))) return;
        try {
          const d = await call("POST", `${COACH}/invite`);
          lastUrl = d.url;
          side = await call("GET", COACH); renderSide();
          try { await navigator.clipboard.writeText(d.url); say(T("invite_copied"), true); } catch { say(T("invite_made"), true); }
        } catch (err) { say(err.message, false); }
      }));
    if (side.invite) inv.append(btn(T("invite_stop"), "", async () => {
      if (!confirm(T("confirm_invite_stop"))) return;
      try { side = await call("DELETE", `${COACH}/invite`); lastUrl = ""; renderSide(); say(T("invite_stopped"), true); } catch (err) { say(err.message, false); }
    }));
    head.append(count, code, inv);
    if (lastUrl && side.invite) {
      const u = el("div", "row");
      const input = el("input"); input.type = "text"; input.readOnly = true; input.value = lastUrl; input.className = "coach-url";
      input.setAttribute("aria-label", T("invite_link"));
      input.onfocus = () => input.select();
      u.append(input);
      head.append(u, el("p", "meta", T("invite_note")));
    }
    if (!side.students.length) list.append(el("p", "meta", T("no_students")));
    for (const s of side.students) {
      const row = el("div", "sub coach-row");
      const top = el("div", "row");
      top.append(el("b", null, s.name), el("span", "meta",
        s.status === "active" ? T("student_since", { date: day(s.since) }) : T("student_asks", { date: day(s.since) })));
      const act = el("div", "row");
      const go = (path, okKey, ask) => async (e) => {
        if (ask && !confirm(ask)) return;
        e.target.disabled = true;
        try { side = await call("POST", `${COACH}/${path}`); renderSide(); say(T(okKey, { name: s.name }), true); }
        catch (err) { e.target.disabled = false; say(err.message, false); }
      };
      if (s.status === "pending") act.append(btn(T("accept"), "primary", go(`requests/${s.link_id}/accept`, "student_accepted")),
                                             btn(T("decline"), "", go(`links/${s.link_id}/end`, "student_declined")));
      else {
        // SP-791: 代看 — this session sees the student's data (the banner on every page has 回到我自己)
        if (!s.disabled) act.append(btn(T("view"), "primary", async (e) => {
          e.target.disabled = true;
          try {
            const d = await call("POST", `${COACH}/view/${encodeURIComponent(s.student_id)}`);
            location.href = (d && typeof d.go === "string" && d.go.startsWith("/") && !d.go.startsWith("//")) ? d.go : "/api/v1/overview/page";
          } catch (err) { e.target.disabled = false; say(err.message, false); }
        }));
        act.append(btn(T("student_end"), "danger", go(`links/${s.link_id}/end`, "student_ended", T("confirm_student_end", { name: s.name }))));
      }
      row.append(top, act);
      list.append(row);
    }
    // SP-823: the shell's 「N 位學員想加入」 line goes once nobody is waiting any more
    if (!side.students.some((s) => s.status === "pending")) document.querySelectorAll(".an-creq").forEach((n) => n.remove());
  }

  async function load() {
    try { mine = await call("GET", ACCT); }
    catch (err) { if (err.status === 404 || err.status === 401) { hideTab(); return; } say(err.message, false); return; }
    tab.hidden = false; section.hidden = false;
    // an invite link (or #coach) asked for this tab before it was known to be there
    if (location.hash === "#coach" && typeof showTab === "function" && tab.getAttribute("aria-selected") !== "true") showTab("coach");
    renderMine();
    askInvite();
    if (mine.is_coach) {
      try { side = await call("GET", COACH); } catch (err) { side = null; say(err.message, false); }
    }
    renderSide();
  }

  $("coach-code-go").onclick = lookupCode;
  $("coach-code").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); lookupCode(); } });
  const css = document.createElement("style");
  css.textContent = `#coach .coach-row .row { margin-top: 4px; }
    #coach .coach-ask { border: 1px solid var(--accent); border-radius: 10px; padding: 10px 12px; margin-top: 10px; }
    #coach code.coach-code { font-size: 15px; letter-spacing: .08em; padding: 1px 6px; border: 1px solid var(--line); border-radius: 6px; }
    #coach input.coach-url { width: 100%; max-width: 40em; min-width: 0; font-size: 12.5px; }
    #coach .coach-perms { margin-top: 6px; display: grid; gap: 4px; }
    #coach .coach-perms p { margin: 0; }
    #coach .coach-perm { display: flex; gap: 6px; align-items: flex-start; font-size: 13.5px; }
    #coach .coach-perm input { margin-top: 3px; }
    #coach .coach-log { margin-top: 10px; }
    #coach .coach-log-list { margin: 6px 0 0; padding-left: 18px; font-size: 13px; display: grid; gap: 3px; overflow-wrap: anywhere; }`;
  document.head.append(css);
  load();
})();
