/* 首次精靈 (generalize-athlete plan §2, engine/athlete_profile.py; SP-211): on any page, once,
 * while the app does not know the weight, the sex or the age — what the calculations are
 * calibrated with and the data cannot tell (docs/research/cold-start.md §1.3, §4.3). The three
 * are required to save, each with what it is used for; height and power source are optional.
 * Pre-filled from COROS (weight) and the data (power source). 「稍後再說」 also counts as done;
 * everything stays editable on 設定 → 個人資料. Text: catalog common.setup.* (every page has it).
 * Loaded by shell.js; styled from the page's own tokens.
 *
 * Extension hook (for settings owned elsewhere, e.g. 主要訓練項目 on
 * feat/primary-sport — one setting, not a second copy here): a script loaded
 * before this one may push
 *   (window.SetupWizardHooks ||= []).push({ render(el, profile) {…}, async save(form) {…} })
 * `render` fills the .sw-extra slot; `save` runs after the profile is saved.
 */
(() => {
  const HOOKS = window.SetupWizardHooks || (window.SetupWizardHooks = []);
  const P = "/api/v1/plan/profile";
  const T = (k, p) => (window.I18N ? window.I18N.t("common.setup." + k, p) : k);
  const j = (u, o) => fetch(u, o).then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))));
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const done = () => j(`${P}/setup`, { method: "POST", headers: { "Content-Type": "application/json" }, body: '{"done":true}' });

  j(P).then((prof) => {
    if (!prof.setup || !prof.setup.needed || prof.setup.done) return;
    const e = prof.effective, pre = prof.setup.prefill || {};
    const CSS = `
    .sw-dlg { border: 1px solid var(--line, #e1e5ea); border-radius: 10px; background: var(--panel, #fff);
      color: var(--text, #111); max-width: 400px; width: calc(100% - 32px); padding: 16px 18px;
      font: 14px/1.5 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif; }
    .sw-dlg::backdrop { background: rgba(0,0,0,.35); }
    .sw-dlg h3 { margin: 0 0 4px; font-size: 15px; }
    .sw-dlg p { margin: 0 0 10px; color: var(--muted, #667); font-size: 12.5px; }
    .sw-dlg label { display: grid; grid-template-columns: 76px 1fr; align-items: center; gap: 8px; margin: 6px 0 0; font-size: 13px; }
    .sw-dlg label b { color: var(--err, #b91c1c); font-weight: 600; }
    .sw-dlg .sw-why { margin: 1px 0 4px 84px; color: var(--muted, #667); font-size: 12px; line-height: 1.4; }
    .sw-dlg input, .sw-dlg select { font: inherit; color: inherit; background: var(--panel, #fff);
      border: 1px solid var(--line, #e1e5ea); border-radius: 6px; padding: 3px 6px; min-width: 0; }
    .sw-dlg .sw-b { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
    .sw-dlg button { font: inherit; border-radius: 6px; padding: 4px 12px; cursor: pointer;
      border: 1px solid var(--line, #e1e5ea); background: var(--panel, #fff); color: inherit; }
    .sw-dlg button.sw-ok { background: var(--accent, #2563eb); border-color: var(--accent, #2563eb); color: #fff; }
    .sw-dlg .sw-err { color: var(--err, #b91c1c); font-size: 12.5px; min-height: 1em; margin-top: 6px; }
    @media (max-width: 420px) { .sw-dlg .sw-why { margin-left: 0; } }`;
    const st = document.createElement("style");
    st.textContent = CSS;
    document.head.appendChild(st);
    const d = document.createElement("dialog");
    d.className = "sw-dlg";
    const w0 = e.weight ?? pre.weight ?? "";
    const req = `<b aria-hidden="true">*</b>`;
    d.innerHTML = `<form method="dialog" novalidate>
      <h3>${esc(T("title"))}</h3>
      <p>${esc(T("intro"))}</p>
      <label><span>${esc(T("sex"))}${req}</span> <select name="sex" required><option value="">${esc(T("pick"))}</option>
        <option value="male">${esc(T("male"))}</option><option value="female">${esc(T("female"))}</option></select></label>
      <div class="sw-why">${esc(T("sex_why"))}</div>
      <label><span>${esc(T("age"))}${req}</span> <span><input name="age" type="number" min="10" max="100" step="1" required value="${esc(e.age ?? "")}" style="width:72px"> ${esc(T("age_unit"))}</span></label>
      <div class="sw-why">${esc(T("age_why"))}</div>
      <label><span>${esc(T("weight"))}${req}</span> <span><input name="kg" type="number" step="0.1" min="25" max="250" required value="${esc(w0)}" style="width:80px"> kg
        ${pre.weight_source ? `<small style="color:var(--muted,#667)">（${esc(pre.weight_source)}）</small>` : ""}</span></label>
      <div class="sw-why">${esc(T("weight_why"))}</div>
      <label>${esc(T("height"))} <span><input name="h" type="number" min="100" max="250" step="1" value="${esc(e.height_cm ?? "")}" style="width:80px"> cm</span></label>
      <label>${esc(T("power"))} <select name="ps"><option value="">${esc(T("power_auto"))}</option><option value="stryd">Stryd</option>
        <option value="watch">${esc(T("power_watch"))}</option><option value="none">${esc(T("power_none"))}</option></select></label>
      <div class="sw-extra"></div>
      <div class="sw-err" role="alert"></div>
      <div class="sw-b"><button type="button" class="sw-later">${esc(T("later"))}</button><button class="sw-ok" value="ok">${esc(T("save"))}</button></div>
    </form>`;
    document.body.appendChild(d);
    const f = d.querySelector("form");
    for (const fn of HOOKS) if (fn.render) fn.render(d.querySelector(".sw-extra"), prof);
    f.sex.value = e.sex || "";
    f.ps.value = e.power_source || "";
    j(`${P}/detect`).then((x) => {
      const s = x.power_source && x.power_source.source;
      if (s) f.ps.options[0].textContent = T("power_detected", { src: x.labels[s] });
      if (!f.kg.value && x.weight) f.kg.value = x.weight;
    }).catch(() => {});
    d.querySelector(".sw-later").onclick = () => { done().catch(() => {}); d.close(); };
    const err = (k, el) => { d.querySelector(".sw-err").textContent = T(k); if (el) el.focus(); };
    f.onsubmit = async (ev) => {
      ev.preventDefault();
      const kg = Number(f.kg.value), age = Number(f.age.value);
      if (!f.sex.value) return err("need_sex", f.sex);
      if (!(Number.isInteger(age) && age >= 10 && age <= 100)) return err("need_age", f.age);
      if (!(kg >= 25 && kg <= 250)) return err("need_weight", f.kg);
      const today = new Date().toISOString().slice(0, 10);
      const weights = (prof.weights || []).filter((w) => w.date !== today).concat([{ date: today, kg }]);
      const p = prof.profile || {};
      const body = { weights, sex: f.sex.value, age,
        height_cm: f.h.value ? Number(f.h.value) : (p.height_cm ?? null),
        power_source: f.ps.value || null, power_meter: f.ps.value ? null : (p.power_meter ?? null) };
      try {
        await j(P, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        // hook: other settings asked here (e.g. 主要訓練項目) save themselves
        for (const fn of HOOKS) if (fn.save) await fn.save(f);
        await done();
        d.close();
        location.reload();
      } catch (e2) { d.querySelector(".sw-err").textContent = T("save_failed", { msg: e2.message }); }
    };
    d.showModal();
  }).catch(() => {});
})();
