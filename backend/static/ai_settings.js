// 設定 › 進階設定 › AI 問答 (SP-785 / SP-786; api/ai.py, backend/ai/config.py): the switch, the owner's API key
// (pasted here: sealed on the server, only its last 4 characters shown back) and the model / today's count.
// The owner only: GET /api/v1/ai/settings answers 403 to anyone else and the section stays hidden.
// Every text goes through t("settings.askai.*") (static/i18n/<locale>/settings.json).
(() => {
  const URL_ = "/api/v1/ai/settings";
  const el = (id) => document.getElementById(id);
  const say = (m) => (typeof window.toast === "function" ? window.toast(m) : alert(m));
  if (!el("askai")) return;

  async function api(opt) {
    const r = await fetch(URL_, { credentials: "same-origin", ...(opt || {}) });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      const e = new Error((body.detail && body.detail.code) || `${r.status}`);
      e.status = r.status; e.code = body.detail && body.detail.code;
      throw e;
    }
    return body;
  }
  const put = (data) => api({ method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

  function show(s) {
    el("askai").hidden = false;
    el("ai-on").checked = !!s.enabled;
    const k = s.key || {};
    el("ai-key-state").textContent = k.source === "env" ? t("settings.askai.key_env", { masked: k.masked || "" })
      : k.source === "saved" ? t("settings.askai.key_saved", { masked: k.masked || "" }) : t("settings.askai.key_none");
    // an env key wins: nothing to paste or clear here
    el("ai-key-row").hidden = k.source === "env";
    el("ai-key-clear").hidden = k.source !== "saved";
    el("ai-model").textContent = t("settings.askai.model", { model: s.model, n: s.used_today, limit: s.daily_limit });
  }

  async function load() {
    try { show(await api()); } catch (e) {
      if (e.status === 403 || e.status === 404) { el("askai").hidden = true; return; }
      say(t("settings.askai.failed", { msg: e.message.slice(0, 80) }));
    }
  }
  async function save(data) {
    try { show(await put(data)); say(t("settings.askai.saved")); }
    catch (e) { say(e.code === "BAD_KEY" ? t("settings.askai.bad_key") : t("settings.askai.failed", { msg: e.message.slice(0, 80) })); }
  }

  el("ai-on").onchange = (ev) => save({ enabled: ev.target.checked });
  el("ai-key-save").onclick = () => {
    const v = el("ai-key").value.trim();
    if (!v) return;
    el("ai-key").value = "";
    save({ api_key: v });
  };
  el("ai-key-clear").onclick = () => save({ api_key: "" });
  load();
})();
