// 設定 › 進階 › Debug API (SP-371, api/debug.py admin_router, docs/debug-api.md): the switch, the
// token list (make one after the server PIN — shown once —, revoke one / all) and the last 100 calls.
// Every text goes through t("settings.debug.*") (static/i18n/<locale>/settings.json).
(() => {
  const URL_ = "/api/v1/settings/debug-api";
  const el = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const say = (m) => (typeof window.toast === "function" ? window.toast(m) : alert(m));
  const when = (iso) => (iso ? new Date(iso).toLocaleString() : t("settings.debug.never"));
  if (!el("debugapi")) return;

  async function api(path, opt) {
    const r = await fetch(URL_ + path, opt);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = body.detail;
      const e = new Error((d && (d.message || d.code)) || (typeof d === "string" ? d : `${r.status}`));
      e.detail = d; e.status = r.status;
      throw e;
    }
    return body;
  }
  const json = (method, data) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

  let state = null;

  function scopeTitle(s) { return t(`settings.debug.scope_${s.replace(":", "_")}`); }

  function tokenRow(k) {
    const status = k.revoked_at ? t("settings.debug.revoked") : k.expired ? t("settings.debug.expired") : "";
    const nip = k.new_ip_at && k.active
      ? ` <span class="meta" title="${esc(t("settings.debug.new_ip_tip"))}">⚠ ${esc(t("settings.debug.new_ip"))}</span>` : "";
    const btn = k.active ? `<button data-dbg-revoke="${k.id}" data-name="${esc(k.name)}">${esc(t("settings.debug.revoke"))}</button>` : esc(status);
    return `<tr><td>${esc(k.name)} <span class="meta">${esc(k.prefix)}…</span></td>
      <td class="meta">${k.scopes.map((s) => `<span title="${esc(scopeTitle(s))}">${esc(s)}</span>`).join(" ")}</td>
      <td class="meta">${esc(new Date(k.expires_at).toLocaleDateString())}</td>
      <td class="meta">${esc(when(k.last_used_at))}${k.last_ip ? " · " + esc(k.last_ip) : ""}${nip}</td><td>${btn}</td></tr>`;
  }

  function auditRow(a) {
    const nip = a.new_ip ? ` <span title="${esc(t("settings.debug.new_ip_tip"))}">⚠</span>` : "";
    return `<tr><td class="meta">${esc(when(a.at))}</td><td>${esc(a.token || "—")}</td>
      <td class="meta">${esc(a.path.replace("/api/v1/debug", ""))}${a.query ? "?" + esc(a.query) : ""}</td>
      <td>${esc(a.status)}</td><td class="meta">${esc(a.ip || "")}${nip}</td><td class="meta">${a.bytes ? esc(Math.round(a.bytes / 102.4) / 10 + " kB") : ""}</td></tr>`;
  }

  function show(s) {
    state = s;
    el("dbg-nopin").hidden = !!s.pin_configured;
    el("dbg-on").checked = !!s.enabled;
    el("dbg-on").disabled = !s.pin_configured && !s.enabled;
    el("dbg-body").hidden = !s.enabled;
    const head = `<tr><th>${esc(t("settings.debug.col_name"))}</th><th>${esc(t("settings.debug.col_scopes"))}</th>
      <th>${esc(t("settings.debug.col_expires"))}</th><th>${esc(t("settings.debug.col_used"))}</th><th></th></tr>`;
    el("dbg-tokens").innerHTML = s.tokens.length ? head + s.tokens.map(tokenRow).join("")
      : `<tr><td class="meta">${esc(t("settings.debug.no_tokens"))}</td></tr>`;
    const ah = `<tr><th>${esc(t("settings.debug.col_time"))}</th><th>${esc(t("settings.debug.col_name"))}</th>
      <th>${esc(t("settings.debug.col_path"))}</th><th>${esc(t("settings.debug.col_status"))}</th><th>IP</th>
      <th>${esc(t("settings.debug.col_size"))}</th></tr>`;
    el("dbg-audit").innerHTML = s.audit.length ? ah + s.audit.map(auditRow).join("")
      : `<tr><td class="meta">${esc(t("settings.debug.audit_empty"))}</td></tr>`;
    el("dbg-revoke-all").disabled = !s.tokens.some((k) => k.active);
    const d = s.dropped || {}, p = s.pin || {};
    el("dbg-stats").textContent = [
      t("settings.debug.pin_stats", { n: p.wrong_total || 0, streak: p.wrong_in_a_row || 0, locks: p.locks || 0 }),
      t("settings.debug.dropped", { rate: d.RATE_LIMITED || 0, blocked: d.BLOCKED || 0, busy: d.BUSY || 0 }),
    ].join(" · ");
    const fh = `<tr><th>${esc(t("settings.debug.col_time"))}</th><th>IP</th><th>${esc(t("settings.debug.col_code"))}</th>
      <th>${esc(t("settings.debug.col_count"))}</th></tr>`;
    el("dbg-fails").innerHTML = (s.failures || []).length
      ? fh + s.failures.map((f) => `<tr><td class="meta">${esc(when(f.hour))}</td><td class="meta">${esc(f.ip)}</td>
          <td class="meta">${esc(f.code)}</td><td>${esc(f.count)}</td></tr>`).join("")
      : `<tr><td class="meta">${esc(t("settings.debug.failures_empty"))}</td></tr>`;
  }

  async function load() {
    try { show(await api("")); } catch (e) { say(t("settings.debug.failed", { msg: e.message.slice(0, 80) })); }
  }

  el("dbg-on").onchange = async (ev) => {
    try { show(await api("", json("PUT", { enabled: ev.target.checked }))); }
    catch (e) { ev.target.checked = !ev.target.checked; say(t("settings.debug.failed", { msg: e.message.slice(0, 80) })); }
  };

  el("dbg-new").onclick = () => {
    const s = state || {};
    el("dbg-days").innerHTML = (s.days || [1, 7, 30, 90]).map((n) =>
      `<option value="${n}"${n === s.default_days ? " selected" : ""}>${esc(t("settings.debug.days_n", { n }))}</option>`).join("");
    el("dbg-scopes").innerHTML = (s.scopes || []).map((sc) =>
      `<label title="${esc(scopeTitle(sc))}"><input type="checkbox" name="dbg-scope" value="${esc(sc)}"${(s.default_scopes || []).includes(sc) ? " checked" : ""}> ${esc(sc)}</label>`).join(" ");
    el("dbg-pin").value = ""; el("dbg-msg").textContent = "";
    el("dbg-dlg").showModal(); el("dbg-pin").focus();
  };
  el("dbg-cancel").onclick = () => el("dbg-dlg").close();
  el("dbg-create").onclick = async () => {
    const scopes = [...document.querySelectorAll('input[name="dbg-scope"]:checked')].map((x) => x.value);
    if (!scopes.length) { el("dbg-msg").textContent = t("settings.debug.need_scope"); return; }
    try {
      const r = await api("/tokens", json("POST", { pin: el("dbg-pin").value, name: el("dbg-name").value,
                                                    scopes, days: Number(el("dbg-days").value) }));
      el("dbg-pin").value = "";
      el("dbg-dlg").close();
      el("dbg-token").value = r.token; el("dbg-made").hidden = false; el("dbg-token").select();
      say(t("settings.debug.made"));
      await load();
    } catch (e) {
      el("dbg-pin").value = "";
      el("dbg-msg").textContent = e.detail && e.detail.code === "PIN_LOCKED"
        ? t("settings.debug.locked", { m: Math.max(1, Math.ceil((e.detail.retry_s || 60) / 60)) })
        : t("settings.debug.failed", { msg: e.message.slice(0, 80) });
    }
  };
  el("dbg-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(el("dbg-token").value); say(t("settings.cal.copied")); }
    catch { el("dbg-token").select(); say(t("settings.cal.copy_fail")); }
  };
  el("dbg-tokens").onclick = async (ev) => {
    const b = ev.target.closest("[data-dbg-revoke]"); if (!b) return;
    if (!confirm(t("settings.debug.confirm_revoke", { name: b.dataset.name }))) return;
    try { const r = await api(`/tokens/${b.dataset.dbgRevoke}`, { method: "DELETE" }); show(r);
      say(t("settings.debug.done_revoke", { n: r.revoked })); }
    catch (e) { say(t("settings.debug.failed", { msg: e.message.slice(0, 80) })); }
  };
  el("dbg-revoke-all").onclick = async () => {
    if (!confirm(t("settings.debug.confirm_revoke_all"))) return;
    try { const r = await api("/tokens/revoke-all", { method: "POST" }); show(r); el("dbg-made").hidden = true;
      say(t("settings.debug.done_revoke", { n: r.revoked })); }
    catch (e) { say(t("settings.debug.failed", { msg: e.message.slice(0, 80) })); }
  };
  load();
})();
