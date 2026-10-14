// 設定 › 資料同步 › 體重同步 (SP-472, api/weight.py): the address the iOS Shortcut / Health Auto Export
// posts to, the weight-only tokens (make one — shown once —, revoke one / all), the nightly sort-out's
// rules and last run, the latest measurements received and the setup steps (in the page).
// Every text goes through t("settings.wsync.*") (static/i18n/<locale>/settings.json).
(() => {
  const URL_ = "/api/v1/weight";
  const el = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const say = (m) => (typeof window.toast === "function" ? window.toast(m) : alert(m));
  const lang = document.documentElement.lang || undefined;      // the page's language, not the browser's
  const when = (iso) => (iso ? new Date(iso).toLocaleString(lang, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })
    : t("settings.wsync.never"));
  // the athlete's wall time as the server wrote it (…T07:12+08:00): 10/14 07:12, not re-zoned by the browser
  const wall = (s) => `${Number(s.slice(5, 7))}/${Number(s.slice(8, 10))} ${s.slice(11, 16)}`;
  if (!el("weightsync")) return;

  async function api(path, opt) {
    const r = await fetch(URL_ + path, opt);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = body.detail;
      throw new Error((d && (d.message || d.code)) || (typeof d === "string" ? d : `${r.status}`));
    }
    return body;
  }
  const json = (method, data) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });

  function tokenRow(k) {
    const btn = k.active ? `<button style="white-space:nowrap" data-ws-revoke="${k.id}" data-name="${esc(k.name)}">${esc(t("settings.wsync.revoke"))}</button>`
      : `<span class="meta">${esc(t("settings.wsync.revoked"))}</span>`;
    return `<tr><td>${esc(k.name)} <span class="meta">${esc(k.prefix)}…</span></td>
      <td class="meta">${esc(when(k.created_at))}</td><td class="meta">${esc(when(k.last_used_at))}</td><td>${btn}</td></tr>`;
  }
  function status(m, rules) {
    if (m.status === "skipped") {
      const k = m.note === "manual" ? "st_manual" : m.note === "same" ? "st_same" : "st_small";
      return t(`settings.wsync.${k}`, { d: rules.min_diff_kg, g: rules.max_gap_days });
    }
    return t(`settings.wsync.st_${m.status}`);
  }
  function measRow(m, rules) {
    const src = t(`settings.wsync.src_${m.source}`) + (m.device ? ` · ${m.device}` : "");
    return `<tr><td class="meta">${esc(wall(m.measured_at))}</td><td>${esc(Number(m.kg).toFixed(1))} kg</td>
      <td class="meta">${esc(src)}</td><td class="meta">${esc(status(m, rules))}</td></tr>`;
  }
  function show(s) {
    el("ws-url").value = location.origin + s.path;
    const live = s.tokens.filter((k) => k.active);
    const head = `<tr><th>${esc(t("settings.wsync.col_name"))}</th><th>${esc(t("settings.wsync.col_created"))}</th>
      <th>${esc(t("settings.wsync.col_used"))}</th><th></th></tr>`;
    el("ws-tokens").innerHTML = live.length ? head + live.map(tokenRow).join("")
      : `<tr><td class="meta">${esc(t("settings.wsync.no_tokens"))}</td></tr>`;
    el("ws-revoke-all").hidden = !live.length;
    el("ws-new").disabled = live.length >= s.max_active;
    const r = s.rules;
    el("ws-rules").textContent = t("settings.wsync.rules", { at: r.apply_at, d: r.min_diff_kg, g: r.max_gap_days });
    const st = s.state || {};
    const what = st.status === "nothing" ? t("settings.wsync.last_nothing")
      : st.status === "applied" ? t("settings.wsync.last_applied", { n: st.added })
      : t("settings.wsync.last_unchanged");
    el("ws-state").textContent = [t("settings.wsync.next", { next: when(s.next_apply) }),
      s.pending ? t("settings.wsync.pending", { n: s.pending }) : "",
      st.at ? t("settings.wsync.last", { at: when(st.at), what }) : ""].filter(Boolean).join("　");
    el("ws-meas").innerHTML = s.measurements.length ? `<tr><th>${esc(t("settings.wsync.col_time"))}</th><th>${esc(t("settings.wsync.col_kg"))}</th>
      <th>${esc(t("settings.wsync.col_src"))}</th><th>${esc(t("settings.wsync.col_status"))}</th></tr>` + s.measurements.map((m) => measRow(m, r)).join("")
      : `<tr><td class="meta">${esc(t("settings.wsync.m_none"))}</td></tr>`;
    if (s.measurements.length) el("ws-m-box").open = true;
    else el("ws-sc").open = true;               // nothing received yet: the steps are what to read
  }
  async function load() {
    try { show(await api("/status")); } catch (e) { el("ws-state").textContent = t("settings.wsync.failed", { msg: e.message.slice(0, 80) }); }
  }
  function copy(id) {
    const v = el(id).value;
    (navigator.clipboard ? navigator.clipboard.writeText(v) : Promise.reject()).catch(() => { el(id).select(); document.execCommand("copy"); });
  }
  el("ws-url-copy").onclick = () => copy("ws-url");
  el("ws-copy").onclick = () => copy("ws-token");
  el("ws-new").onclick = async () => {
    try {
      const r = await api("/tokens", json("POST", { name: el("ws-name").value }));
      el("ws-token").value = r.token; el("ws-made").hidden = false;
      load();
    } catch (e) { say(t("settings.wsync.failed", { msg: e.message.slice(0, 80) })); }
  };
  el("ws-revoke-all").onclick = async () => {
    if (!confirm(t("settings.wsync.confirm_all"))) return;
    try { await api("/tokens/revoke-all", json("POST")); el("ws-made").hidden = true; load(); }
    catch (e) { say(t("settings.wsync.failed", { msg: e.message.slice(0, 80) })); }
  };
  el("ws-tokens").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-ws-revoke]");
    if (!b || !confirm(t("settings.wsync.confirm_revoke", { name: b.dataset.name }))) return;
    try { await api(`/tokens/${b.dataset.wsRevoke}`, { method: "DELETE" }); load(); }
    catch (err) { say(t("settings.wsync.failed", { msg: err.message.slice(0, 80) })); }
  });
  document.addEventListener("weight:changed", load);
  load();
})();
