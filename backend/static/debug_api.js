// 設定 › 進階 › Debug API (SP-371, SP-373, api/debug.py admin_router, docs/debug-api.md): the switch,
// the personal tokens (made by the owner, no PIN — shown once —, revoke one / all) and the last 100
// calls; and 從正式站同步 (#upstream, engine/upstream.py) when this machine names a production server.
// Every text goes through t("settings.debug.*") / t("settings.upstream.*") (static/i18n/<locale>/settings.json).
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
    // SP-804: data-l = the column's header — on a phone the row is a card (settings.html #dbg-tokens
    // tr.tok) and each cell says what it is; the 撤銷 button sits beside the name instead of off the edge
    return `<tr class="tok"><td>${esc(k.name)} <span class="meta">${esc(k.prefix)}…</span></td>
      <td class="meta" data-l="${esc(t("settings.debug.col_scopes"))}">${k.scopes.map((s) => `<span title="${esc(scopeTitle(s))}">${esc(s)}</span>`).join(" ")}</td>
      <td class="meta" data-l="${esc(t("settings.debug.col_expires"))}">${k.never_expires ? esc(t("settings.debug.no_expiry")) : esc(new Date(k.expires_at).toLocaleDateString())}</td>
      <td class="meta" data-l="${esc(t("settings.debug.col_used"))}">${esc(when(k.last_used_at))}${k.last_ip ? " · " + esc(k.last_ip) : ""}${nip}</td><td>${btn}</td></tr>`;
  }

  function auditRow(a) {
    const nip = a.new_ip ? ` <span title="${esc(t("settings.debug.new_ip_tip"))}">⚠</span>` : "";
    return `<tr><td class="meta">${esc(when(a.at))}</td><td>${esc(a.token || "—")}</td>
      <td class="meta">${esc(a.path.replace("/api/v1/debug", ""))}${a.query ? "?" + esc(a.query) : ""}</td>
      <td>${esc(a.status)}</td><td class="meta">${esc(a.ip || "")}${nip}</td><td class="meta">${a.bytes ? esc(Math.round(a.bytes / 102.4) / 10 + " kB") : ""}</td></tr>`;
  }

  // SP-802: one write through /api/v1/debug/write/* — what changed (part · key · field: before → after)
  const val = (v) => (v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
  function writeRow(w) {
    const items = (w.diff || []).slice(0, 6).map((d) => `${d.part} · ${d.key}${d.field ? " · " + d.field : ""}: ${val(d.before)} → ${val(d.after)}`);
    if ((w.diff || []).length > 6) items.push(`… +${w.diff.length - 6}`);
    const tag = w.kind === "undo" ? t("settings.debug.undo_of", { id: w.undoes })
      : w.undone_at ? t("settings.debug.undone") : "";
    // the 已復原 / 復原第 n 次 tag under the time: a 4th column wrapped one letter a line on a phone
    return `<tr><td class="meta">#${esc(w.id)} ${esc(when(w.at))}${tag ? `<br><b>${esc(tag)}</b>` : ""}</td>
      <td>${esc(w.by || "—")}</td><td class="meta">${items.map(esc).join("<br>")}</td></tr>`;
  }

  function show(s) {
    state = s;
    const wh = `<tr><th>${esc(t("settings.debug.col_time"))}</th><th>${esc(t("settings.debug.col_name"))}</th>
      <th>${esc(t("settings.debug.col_change"))}</th></tr>`;
    el("dbg-writes").innerHTML = (s.writes || []).length ? wh + s.writes.map(writeRow).join("")
      : `<tr><td class="meta">${esc(t("settings.debug.writes_empty"))}</td></tr>`;
    el("dbg-on").checked = !!s.enabled;
    el("dbg-body").hidden = !s.enabled;
    const head = `<tr class="hd"><th>${esc(t("settings.debug.col_name"))}</th><th>${esc(t("settings.debug.col_scopes"))}</th>
      <th>${esc(t("settings.debug.col_expires"))}</th><th>${esc(t("settings.debug.col_used"))}</th><th></th></tr>`;
    el("dbg-tokens").innerHTML = s.tokens.length ? head + s.tokens.map(tokenRow).join("")
      : `<tr><td class="meta">${esc(t("settings.debug.no_tokens"))}</td></tr>`;
    const ah = `<tr><th>${esc(t("settings.debug.col_time"))}</th><th>${esc(t("settings.debug.col_name"))}</th>
      <th>${esc(t("settings.debug.col_path"))}</th><th>${esc(t("settings.debug.col_status"))}</th><th>IP</th>
      <th>${esc(t("settings.debug.col_size"))}</th></tr>`;
    el("dbg-audit").innerHTML = s.audit.length ? ah + s.audit.map(auditRow).join("")
      : `<tr><td class="meta">${esc(t("settings.debug.audit_empty"))}</td></tr>`;
    el("dbg-revoke-all").disabled = !s.tokens.some((k) => k.active);
    const d = s.dropped || {};
    el("dbg-stats").textContent = t("settings.debug.dropped", { rate: d.RATE_LIMITED || 0, blocked: d.BLOCKED || 0, busy: d.BUSY || 0 });
    const fh = `<tr><th>${esc(t("settings.debug.col_time"))}</th><th>IP</th><th>${esc(t("settings.debug.col_code"))}</th>
      <th>${esc(t("settings.debug.col_count"))}</th></tr>`;
    el("dbg-fails").innerHTML = (s.failures || []).length
      ? fh + s.failures.map((f) => `<tr><td class="meta">${esc(when(f.hour))}</td><td class="meta">${esc(f.ip)}</td>
          <td class="meta">${esc(f.code)}</td><td>${esc(f.count)}</td></tr>`).join("")
      : `<tr><td class="meta">${esc(t("settings.debug.failures_empty"))}</td></tr>`;
  }

  async function load() {
    try { show(await api("")); } catch (e) {
      // sign-in mode, not the owner (SP-373 / SP-406): the debug API is the owner's only — no block at all
      if (e.status === 403) { el("debugapi").hidden = true; return; }
      say(t("settings.debug.failed", { msg: e.message.slice(0, 80) }));
    }
  }

  el("dbg-on").onchange = async (ev) => {
    try { show(await api("", json("PUT", { enabled: ev.target.checked }))); }
    catch (e) { ev.target.checked = !ev.target.checked; say(t("settings.debug.failed", { msg: e.message.slice(0, 80) })); }
  };

  el("dbg-new").onclick = () => {
    const s = state || {};
    el("dbg-days").innerHTML = (s.days || [7, 30, 90, 365, 0]).map((n) =>
      `<option value="${n}"${n === s.default_days ? " selected" : ""}>${esc(n ? t("settings.debug.days_n", { n }) : t("settings.debug.no_expiry"))}</option>`).join("");
    el("dbg-scopes").innerHTML = (s.scopes || []).map((sc) =>
      `<label title="${esc(scopeTitle(sc))}"><input type="checkbox" name="dbg-scope" value="${esc(sc)}"${(s.default_scopes || []).includes(sc) ? " checked" : ""}> ${esc(sc)}</label>`).join(" ");
    el("dbg-msg").textContent = "";
    // SP-802: a token that can write lasts at most write_max_days (never 「不過期」)
    const ws = document.querySelector(`input[name="dbg-scope"][value="${s.write_scope || "write:config"}"]`);
    if (ws) ws.onchange = () => {
      const max = s.write_max_days || 7;
      [...el("dbg-days").options].forEach((o) => { const n = Number(o.value); o.disabled = ws.checked && !(n > 0 && n <= max); });
      if (ws.checked && el("dbg-days").selectedOptions[0]?.disabled) el("dbg-days").value = String(max);
      el("dbg-msg").textContent = ws.checked ? t("settings.debug.write_days") : "";
    };
    el("dbg-dlg").showModal(); el("dbg-name").focus();
  };
  el("dbg-cancel").onclick = () => el("dbg-dlg").close();
  el("dbg-create").onclick = async () => {
    const scopes = [...document.querySelectorAll('input[name="dbg-scope"]:checked')].map((x) => x.value);
    if (!scopes.length) { el("dbg-msg").textContent = t("settings.debug.need_scope"); return; }
    try {
      const r = await api("/tokens", json("POST", { name: el("dbg-name").value, scopes, days: Number(el("dbg-days").value) }));
      el("dbg-dlg").close();
      el("dbg-token").value = r.token; el("dbg-made").hidden = false; el("dbg-token").select();
      say(t("settings.debug.made"));
      await load();
    } catch (e) {
      el("dbg-msg").textContent = t("settings.debug.failed", { msg: e.message.slice(0, 80) });
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

  // ---- 從正式站同步 (SP-373): preview first, then apply what was previewed
  const up = el("upstream");
  function parts() { return [["config", "up-config"], ["plan", "up-plan"]].filter(([, id]) => el(id).checked).map(([p]) => p); }
  function lastText(lp) {
    return lp && lp.at ? t("settings.upstream.last", { when: when(lp.at) }) : t("settings.upstream.never");
  }
  function lines(r) {
    const out = [];
    if (r.config) {
      const c = r.config, pj = c.plan_json || {};
      out.push(t("settings.upstream.r_settings", { n: c.settings.changed.length, skipped: c.settings.skipped.length }));
      const diff = ["thresholds", "weights", "events", "phases"].filter((k) => pj[k] && !pj[k].same)
        .map((k) => t(`settings.upstream.k_${k}`) + ` ${pj[k].here} → ${pj[k].production}`);
      if (pj.profile && !pj.profile.same) diff.push(t("settings.upstream.k_profile"));
      out.push(diff.length ? t("settings.upstream.r_plan_json", { list: diff.join(t("settings.upstream.list_sep")) }) : t("settings.upstream.r_plan_json_same"));
      out.push(t("settings.upstream.r_injuries", { here: c.injuries.here, prod: c.injuries.production }));
      out.push(t("settings.upstream.r_tags", { here: c.activity_tags.here, prod: c.activity_tags.production }));
      if (c.terrain.production) out.push(t("settings.upstream.r_terrain", { n: c.terrain.changed, missing: c.terrain.missing_here }));
    }
    if (r.plan) out.push(t("settings.upstream.r_plan", { add: r.plan.added, del: r.plan.removed, chg: r.plan.changed, prod: r.plan.production }));
    if (r.apply && r.push_off) out.push(t("settings.upstream.r_push_off"));
    return out;
  }
  async function upLoad() {
    let s;
    try { s = await api("/upstream"); } catch { return; }
    if (!s.configured) return;
    up.hidden = false;
    el("up-host").textContent = t("settings.upstream.host", { host: s.host || "?" }) + " · " + lastText(s.last_pull);
  }
  async function upRun(apply) {
    const what = parts();
    if (!what.length) { el("up-msg").textContent = t("settings.upstream.need_part"); return; }
    if (apply && !confirm(t("settings.upstream.confirm"))) return;
    el("up-msg").textContent = t("settings.upstream.working");
    el("up-preview").disabled = el("up-apply").disabled = true;
    try {
      const r = await api("/upstream/pull", json("POST", { what, apply }));
      el("up-result").innerHTML = lines(r).map((x) => `<li>${esc(x)}</li>`).join("");
      el("up-msg").textContent = apply ? t("settings.upstream.done") : t("settings.upstream.previewed");
      el("up-apply").disabled = apply;
      if (apply) await upLoad();
    } catch (e) {
      el("up-msg").textContent = t("settings.debug.failed", { msg: e.message.slice(0, 160) });
    } finally {
      el("up-preview").disabled = false;
    }
  }
  if (up) {
    el("up-preview").onclick = () => upRun(false);
    el("up-apply").onclick = () => upRun(true);
    [el("up-config"), el("up-plan")].forEach((x) => { x.onchange = () => { el("up-apply").disabled = true; el("up-result").innerHTML = ""; }; });
    upLoad();
  }
})();
