/* 首次精靈 (generalize-athlete plan §2, engine/athlete_profile.py): on any page,
 * once, while the app knows no weight or no sex — the few 一般設定 the data
 * cannot tell. Pre-filled from COROS (weight) and the data (power source).
 * 「稍後再說」 also counts as done; everything stays editable on the 設定 page.
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
  const j = (u, o) => fetch(u, o).then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))));
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const done = () => j(`${P}/setup`, { method: "POST", headers: { "Content-Type": "application/json" }, body: '{"done":true}' });

  j(P).then((prof) => {
    if (!prof.setup || !prof.setup.needed || prof.setup.done) return;
    const e = prof.effective, pre = prof.setup.prefill || {};
    const CSS = `
    .sw-dlg { border: 1px solid var(--line, #e1e5ea); border-radius: 10px; background: var(--panel, #fff);
      color: var(--text, #111); max-width: 380px; width: calc(100% - 32px); padding: 16px 18px;
      font: 14px/1.5 system-ui, -apple-system, "Segoe UI", "Noto Sans TC", sans-serif; }
    .sw-dlg::backdrop { background: rgba(0,0,0,.35); }
    .sw-dlg h3 { margin: 0 0 4px; font-size: 15px; }
    .sw-dlg p { margin: 0 0 10px; color: var(--muted, #667); font-size: 12.5px; }
    .sw-dlg label { display: grid; grid-template-columns: 76px 1fr; align-items: center; gap: 8px; margin: 6px 0; font-size: 13px; }
    .sw-dlg input, .sw-dlg select { font: inherit; color: inherit; background: var(--panel, #fff);
      border: 1px solid var(--line, #e1e5ea); border-radius: 6px; padding: 3px 6px; min-width: 0; }
    .sw-dlg .sw-b { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
    .sw-dlg button { font: inherit; border-radius: 6px; padding: 4px 12px; cursor: pointer;
      border: 1px solid var(--line, #e1e5ea); background: var(--panel, #fff); color: inherit; }
    .sw-dlg button.sw-ok { background: var(--accent, #2563eb); border-color: var(--accent, #2563eb); color: #fff; }
    .sw-dlg .sw-err { color: var(--err, #b91c1c); font-size: 12.5px; min-height: 1em; }`;
    const st = document.createElement("style");
    st.textContent = CSS;
    document.head.appendChild(st);
    const d = document.createElement("dialog");
    d.className = "sw-dlg";
    const w0 = e.weight ?? pre.weight ?? "";
    d.innerHTML = `<form method="dialog">
      <h3>先填幾項基本資料</h3>
      <p>只問資料推不出來的。其他數字（門檻、熱、爬坡…）都會用你的紀錄自動估算。之後都能在「設定 → 一般設定」改。</p>
      <label>體重 <span><input name="kg" type="number" step="0.1" min="25" max="250" required value="${esc(w0)}" style="width:80px"> kg
        ${pre.weight_source ? `<small style="color:var(--muted,#667)">（${esc(pre.weight_source)}）</small>` : ""}</span></label>
      <label>性別 <select name="sex"><option value="">（未填）</option><option value="male">男</option><option value="female">女</option></select></label>
      <label>身高 <span><input name="h" type="number" min="100" max="250" step="1" value="${esc(e.height_cm ?? "")}" style="width:80px"> cm</span></label>
      <label>出生年 <input name="by" type="number" min="1920" max="${new Date().getFullYear() - 10}" step="1" value="${esc(e.birth_year ?? "")}" style="width:90px"></label>
      <label>功率來源 <select name="ps"><option value="">自動（依資料）</option><option value="stryd">Stryd</option>
        <option value="watch">手錶推估功率</option><option value="none">沒有功率計</option></select></label>
      <div class="sw-extra"></div>
      <div class="sw-err"></div>
      <div class="sw-b"><button type="button" class="sw-later">稍後再說</button><button class="sw-ok" value="ok">儲存</button></div>
    </form>`;
    document.body.appendChild(d);
    const f = d.querySelector("form");
    for (const fn of HOOKS) if (fn.render) fn.render(d.querySelector(".sw-extra"), prof);
    f.sex.value = e.sex || "";
    f.ps.value = e.power_source || "";
    j(`${P}/detect`).then((x) => {
      const s = x.power_source && x.power_source.source;
      if (s) f.ps.options[0].textContent = `自動（偵測到：${x.labels[s]}）`;
      if (!f.kg.value && x.weight) f.kg.value = x.weight;
    }).catch(() => {});
    d.querySelector(".sw-later").onclick = () => { done().catch(() => {}); d.close(); };
    f.onsubmit = async (ev) => {
      ev.preventDefault();
      const kg = Number(f.kg.value);
      if (!(kg >= 25 && kg <= 250)) { d.querySelector(".sw-err").textContent = "請填體重（25–250 kg）"; return; }
      const today = new Date().toISOString().slice(0, 10);
      const weights = (prof.weights || []).filter((w) => w.date !== today).concat([{ date: today, kg }]);
      const p = prof.profile || {};
      const body = { weights, sex: f.sex.value || p.sex || null,
        height_cm: f.h.value ? Number(f.h.value) : (p.height_cm ?? null),
        birth_year: f.by.value ? Number(f.by.value) : (p.birth_year ?? null),
        power_source: f.ps.value || null, power_meter: f.ps.value ? null : (p.power_meter ?? null) };
      try {
        await j(P, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        // hook: other settings asked here (e.g. 主要訓練項目) save themselves
        for (const fn of HOOKS) if (fn.save) await fn.save(f);
        await done();
        d.close();
        location.reload();
      } catch (err) { d.querySelector(".sw-err").textContent = "儲存失敗：" + err.message; }
    };
    d.showModal();
  }).catch(() => {});
})();
