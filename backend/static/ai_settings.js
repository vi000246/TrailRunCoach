// 設定 › 進階設定 › AI 問答 (SP-785 / SP-786; api/ai.py, backend/ai/config.py): the switch, the API key
// (pasted here: sealed on the server, only its last 4 characters shown back), 「測試」 and the model / today's count.
// The owner, or a signed-in member with their own key while the server allows it (SP-883: `who` member — the
// member's wording: their own Anthropic account pays, no server key). GET /api/v1/ai/settings answers 403 to
// anyone else and the section stays hidden.
// Every text goes through t("settings.askai.*") (static/i18n/<locale>/settings.json).
(() => {
  const URL_ = "/api/v1/ai/settings";
  const el = (id) => document.getElementById(id);
  const say = (m) => (typeof window.toast === "function" ? window.toast(m) : alert(m));
  if (!el("askai")) return;

  async function api(opt, url) {
    const r = await fetch(url || URL_, { credentials: "same-origin", ...(opt || {}) });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      const e = new Error((body.detail && body.detail.code) || `${r.status}`);
      e.status = r.status; e.code = body.detail && body.detail.code;
      throw e;
    }
    return body;
  }
  const put = (data) => api({ method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

  // the member's wording on the two `?` (SP-883): their key, their bill; no server key to mention
  function words(member) {
    const pairs = [[document.querySelector("#askai h2 .q"), "help"], [el("ai-key-q"), "key_help"]];
    for (const [b, k] of pairs) {
      if (!b) continue;
      const key = `settings.askai.${k}${member ? "_member" : ""}`;
      b.setAttribute("data-i18n-attr", `aria-label:settings.thr.q_aria;title:${key}`);
      b.title = t(key);
    }
    el("ai-member-note").hidden = !member;
  }

  function show(s) {
    el("askai").hidden = false;
    words(s.who === "member");
    el("ai-on").checked = !!s.enabled;
    const k = s.key || {};
    el("ai-key-state").textContent = k.source === "env" ? t("settings.askai.key_env", { masked: k.masked || "" })
      : k.source === "saved" ? t("settings.askai.key_saved", { masked: k.masked || "" }) : t("settings.askai.key_none");
    // an env key wins: nothing to paste or clear here
    el("ai-key-row").hidden = k.source === "env";
    el("ai-key-clear").hidden = k.source !== "saved";
    el("ai-test-row").hidden = k.source === "none";
    el("ai-model").textContent = t("settings.askai.model", { model: s.model, n: s.used_today, limit: s.daily_limit });
    // SP-883: an open 「問 AI」 on this page unlocks (or locks) as the key is saved or cleared
    window.dispatchEvent(new CustomEvent("askai:key", { detail: { has: k.source !== "none" } }));
  }

  // SP-883: 「問 AI」's 「到設定填 API key」 lands on #ai-key-row — the section is hidden until GET answers, so the
  // row is scrolled to, outlined and its field focused here (the outline goes once a key is saved)
  function asked() {
    if (location.hash !== "#ai-key-row" || el("ai-key-row").hidden) return;
    el("ai-key-row").classList.add("link-asked");
    el("ai-key-row").scrollIntoView({ block: "center" });
    el("ai-key").focus({ preventScroll: true });
  }

  async function load() {
    try { show(await api()); asked(); } catch (e) {
      if (e.status === 403 || e.status === 404) { el("askai").hidden = true; return; }
      say(t("settings.askai.failed", { msg: e.message.slice(0, 80) }));
    }
  }
  async function save(data) {
    el("ai-key-test-msg").textContent = "";
    try { show(await put(data)); say(t("settings.askai.saved")); }
    catch (e) { say(e.code === "BAD_KEY" ? t("settings.askai.bad_key") : t("settings.askai.failed", { msg: e.message.slice(0, 80) })); }
  }

  // 「測試」 (SP-883): the key in use against the API's model list — nothing billed
  const TEST_ERR = { AI_KEY_INVALID: "test_invalid", AI_NO_CREDIT: "test_no_credit", AI_TEST_LIMIT: "test_limit",
                     AI_NO_KEY: "test_no_key", AI_BUSY: "test_busy" };
  async function test() {
    const b = el("ai-key-test"), msg = el("ai-key-test-msg");
    b.disabled = true;
    msg.textContent = t("settings.askai.test_running");
    try {
      await api({ method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, `${URL_}/test`);
      msg.textContent = t("settings.askai.test_ok");
    } catch (e) {
      msg.textContent = t(`settings.askai.${TEST_ERR[e.code] || "test_failed"}`);
    } finally { b.disabled = false; }
  }

  el("ai-on").onchange = (ev) => save({ enabled: ev.target.checked });
  el("ai-key-save").onclick = () => {
    const v = el("ai-key").value.trim();
    if (!v) return;
    el("ai-key").value = "";
    el("ai-key-row").classList.remove("link-asked");
    save({ api_key: v });
  };
  el("ai-key-clear").onclick = () => save({ api_key: "" });
  el("ai-key-test").onclick = test;
  load();
})();
