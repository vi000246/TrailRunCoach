/* 首次精靈 (generalize-athlete plan §2, engine/athlete_profile.py; SP-211): on any page, once,
 * while the app does not know the weight, the sex or the age — what the calculations are
 * calibrated with and the data cannot tell (docs/research/cold-start.md §1.3, §4.3). The three
 * are required to save, each with what it is used for; height and power source are optional.
 * Pre-filled from COROS (weight) and the data (power source). 「稍後再說」 skips it for
 * REMIND_DAYS (a week), then it asks again while something is still missing (owner 2026-10-06);
 * everything stays editable on 設定 → 個人資料. Text: catalog common.setup.* (every page has it).
 * Loaded by shell.js; styled from the page's own tokens.
 *
 * 跑步經驗問卷 (SP-290, engine/experience.py): four more questions on the same 精靈, every one
 * skippable, the same 「稍後再說」 and reminder — asked until saved once, never when the data
 * already has 4 complete weeks with ≥ 3 days of running / hiking (GET /profile/detect
 * has_history). Saved with PUT /profile/experience; the race goes to the shared race list.
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
  const done = (later) => j(`${P}/setup`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ done: !later, later: !!later }) });
  const num = (v) => (v === "" || v == null ? null : Number(v));
  const fmtT = (s) => `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

  j(P).then(async (prof) => {
    if (!prof.setup || !prof.setup.remind) return;
    const det = j(`${P}/detect`).catch(() => ({}));
    let askSurvey = !!prof.setup.survey_pending;
    if (!prof.setup.needed) {
      // only the questionnaire is pending: not for a runner whose data already has 4 good weeks
      if (!askSurvey || (await det).has_history === true) return;
    }
    const e = prof.effective, pre = prof.setup.prefill || {};
    const CSS = `
    .sw-dlg { border: 1px solid var(--line, #e1e5ea); border-radius: 10px; background: var(--panel, #fff);
      color: var(--text, #111); max-width: 440px; width: calc(100% - 32px); max-height: calc(100vh - 32px);
      overflow: auto; padding: 16px 18px;
      font: 14px/1.5 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif; }
    .sw-dlg::backdrop { background: rgba(0,0,0,.35); }
    .sw-dlg h3 { margin: 0 0 4px; font-size: 15px; }
    .sw-dlg h4 { margin: 14px 0 2px; font-size: 14px; }
    .sw-dlg p { margin: 0 0 10px; color: var(--muted, #667); font-size: 12.5px; }
    .sw-dlg label { display: grid; grid-template-columns: 76px 1fr; align-items: center; gap: 8px; margin: 6px 0 0; font-size: 13px; }
    .sw-dlg label.sw-q { display: block; margin-top: 8px; }
    .sw-dlg label.sw-q > span:first-child { display: block; margin-bottom: 2px; }
    .sw-dlg label b { color: var(--err, #b91c1c); font-weight: 600; }
    .sw-dlg .sw-why { margin: 1px 0 4px 84px; color: var(--muted, #667); font-size: 12px; line-height: 1.4; }
    .sw-dlg input, .sw-dlg select { font: inherit; color: inherit; background: var(--panel, #fff);
      border: 1px solid var(--line, #e1e5ea); border-radius: 6px; padding: 3px 6px; min-width: 0; }
    .sw-dlg .sw-b { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
    .sw-dlg button { font: inherit; border-radius: 6px; padding: 4px 12px; cursor: pointer;
      border: 1px solid var(--line, #e1e5ea); background: var(--panel, #fff); color: inherit; }
    .sw-dlg button.sw-ok { background: var(--accent, #2563eb); border-color: var(--accent, #2563eb); color: #fff; }
    .sw-dlg .sw-err { color: var(--err, #b91c1c); font-size: 12.5px; min-height: 1em; margin-top: 6px; }
    .sw-dlg .sw-race { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: center; font-size: 13px; }
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
      <p>${esc(T("remind", { days: prof.setup.remind_days || 7 }))}</p>
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
      <div class="sw-survey"></div>
      <div class="sw-extra"></div>
      <div class="sw-err" role="alert"></div>
      <div class="sw-b"><button type="button" class="sw-later">${esc(T("later"))}</button><button class="sw-ok" value="ok">${esc(T("save"))}</button></div>
    </form>`;
    document.body.appendChild(d);
    const f = d.querySelector("form");
    // 跑步經驗問卷 (SP-290): four questions, each skippable; filled from an earlier answer
    const survey = () => {
      const x = prof.experience || {}, r = prof.survey_race || {};
      d.querySelector(".sw-survey").innerHTML = `<h4>${esc(T("exp_title"))}</h4>
        <p>${esc(T("exp_intro"))}</p>
        <p>${esc(T("exp_import"))}</p>
        <label class="sw-q"><span>${esc(T("exp_q1"))}</span>
          <span><input name="xn" type="number" min="0" max="14" step="1" value="${esc(x.runs_per_week ?? "")}" style="width:56px"> ${esc(T("exp_times"))}
          × <input name="xm" type="number" min="5" max="600" step="5" value="${esc(x.minutes_per_run ?? "")}" style="width:64px"> ${esc(T("exp_min"))}</span></label>
        <label class="sw-q"><span>${esc(T("exp_q2"))}</span>
          <span><input name="xl" type="number" min="5" max="1440" step="5" value="${esc(x.longest_min ?? "")}" style="width:64px"> ${esc(T("exp_min"))}</span></label>
        <label class="sw-q"><span>${esc(T("exp_q3"))}</span>
          <select name="x30"><option value="">${esc(T("exp_skip"))}</option><option value="yes">${esc(T("exp_yes"))}</option>
          <option value="no">${esc(T("exp_no"))}</option></select></label>
        <label class="sw-q"><span>${esc(T("exp_q4"))}</span>
          <span class="sw-race"><span><input name="rk" type="number" min="0.4" max="1000" step="0.1" value="${esc(r.distance_km ?? "")}" style="width:64px" aria-label="${esc(T("exp_race_km"))}"> km</span>
          <input name="rt" type="text" inputmode="numeric" placeholder="h:mm:ss" value="${esc(r.time_s ? fmtT(r.time_s) : "")}" style="width:80px" aria-label="${esc(T("exp_race_time"))}">
          <input name="rd" type="date" value="${esc(r.date ?? "")}" aria-label="${esc(T("exp_race_date"))}">
          <span><input name="rtr" type="checkbox" ${r.trail ? "checked" : ""}> ${esc(T("exp_race_trail"))}</span></span></label>`;
      f.x30.value = x.can_run_30 === true ? "yes" : x.can_run_30 === false ? "no" : "";
    };
    if (askSurvey) {
      survey();
      // shown with the basics right away; dropped again if the data turns out to have 4 good weeks
      det.then((x) => { if (x && x.has_history === true) { askSurvey = false; d.querySelector(".sw-survey").innerHTML = ""; } });
    }
    for (const fn of HOOKS) if (fn.render) fn.render(d.querySelector(".sw-extra"), prof);
    f.sex.value = e.sex || "";
    f.ps.value = e.power_source || "";
    det.then((x) => {
      const s = x.power_source && x.power_source.source;
      if (s) f.ps.options[0].textContent = T("power_detected", { src: x.labels[s] });
      if (!f.kg.value && x.weight) f.kg.value = x.weight;
    }).catch(() => {});
    d.querySelector(".sw-later").onclick = () => { done(true).catch(() => {}); d.close(); };
    const err = (k, el) => { d.querySelector(".sw-err").textContent = T(k); if (el) el.focus(); };
    // the questionnaire's answers (blank = skipped); null + an error key when the race is half filled
    const surveyBody = () => {
      const rk = num(f.rk.value), rt = f.rt.value.trim(), rd = f.rd.value;
      const race = rk != null || rt || rd ? { distance_km: rk, time: rt, date: rd, trail: f.rtr.checked } : null;
      if (race && !(rk >= 0.4 && rk <= 1000 && /^\d{1,3}:\d{2}(:\d{2})?$/.test(rt) && rd
                    && rd <= new Date().toISOString().slice(0, 10))) return { bad: "need_race" };
      return { runs_per_week: num(f.xn.value), minutes_per_run: num(f.xm.value), longest_min: num(f.xl.value),
        can_run_30: f.x30.value === "yes" ? true : f.x30.value === "no" ? false : null, race };
    };
    f.onsubmit = async (ev) => {
      ev.preventDefault();
      const kg = Number(f.kg.value), age = Number(f.age.value);
      if (!f.sex.value) return err("need_sex", f.sex);
      if (!(Number.isInteger(age) && age >= 10 && age <= 100)) return err("need_age", f.age);
      if (!(kg >= 25 && kg <= 250)) return err("need_weight", f.kg);
      const sv = askSurvey ? surveyBody() : null;
      if (sv && sv.bad) return err(sv.bad, f.rk);
      const today = new Date().toISOString().slice(0, 10);
      const weights = (prof.weights || []).filter((w) => w.date !== today).concat([{ date: today, kg }]);
      const p = prof.profile || {};
      const body = { weights, sex: f.sex.value, age,
        height_cm: f.h.value ? Number(f.h.value) : (p.height_cm ?? null),
        power_source: f.ps.value || null, power_meter: f.ps.value ? null : (p.power_meter ?? null) };
      try {
        await j(P, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        if (sv) await j(`${P}/experience`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(sv) });
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
