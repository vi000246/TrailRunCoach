// 設定 › 進階設定 › AI 問答 (SP-785 / SP-786; api/ai.py, backend/ai/config.py): the switch and the sources
// a question is answered from (SP-997, mock A): one card each, in the order they are tried — the owner's
// local model (an OpenAI-compatible endpoint, e.g. Ollama on their Mac), a Claude (Anthropic) key, a Gemini
// key. Keys are pasted here, sealed on the server; only their last 4 characters come back. Each card has
// 「測試」 (nothing billed). The owner orders the cards with ↑↓; a member (SP-883, `who` member) has their own
// Claude and Gemini keys only — no local model, no order (Claude first, then Gemini).
// GET /api/v1/ai/settings answers 403 to anyone else and the section stays hidden.
// Every text goes through t("settings.askai.*") (static/i18n/<locale>/settings.json).
(() => {
  const URL_ = "/api/v1/ai/settings";
  const el = (id) => document.getElementById(id);
  const say = (m) => (typeof window.toast === "function" ? window.toast(m) : alert(m));
  if (!el("askai")) return;

  let state = null;
  const editing = { local: false, anthropic: false, gemini: false };   // 「編輯」／「換 key」 opened

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

  // the member's wording on the `?` (SP-883): their key, their bill; no server key to mention
  function words(member) {
    const pairs = [[document.querySelector("#askai h2 .q"), "help"], [el("ai-key-q"), "key_help"], [el("ai-gem-q"), "gemini_help"]];
    for (const [b, k] of pairs) {
      if (!b) continue;
      const key = `settings.askai.${k}${member ? "_member" : ""}`;
      b.setAttribute("data-i18n-attr", `aria-label:settings.thr.q_aria;title:${key}`);
      b.title = t(key);
    }
    el("ai-member-note").hidden = !member;
  }

  // SP-997: an answer from an older server (or a test's stub) has no `sources`: the Claude key alone
  function sourcesOf(s) {
    if (Array.isArray(s.sources) && s.sources.length) return s.sources;
    const k = s.key || {};
    return [{ kind: "anthropic", ready: k.source !== "none" && !!k.source, source: k.source || "none", masked: k.masked, model: s.model }];
  }

  const CARD = { local: "ai-src-local", anthropic: "ai-src-anthropic", gemini: "ai-src-gemini" };

  function keyState(src) {
    return src.source === "env" ? t("settings.askai.key_env", { masked: src.masked || "" })
      : src.source === "saved" ? t("settings.askai.key_saved", { masked: src.masked || "" }) : t("settings.askai.key_none");
  }

  // one key card (Claude, Gemini): ids ai-key-* (SP-785's) or ai-gem-*
  function keyCard(src, ids) {
    el(ids.model).textContent = src.model ? `${src.model} · ` : "";
    el(ids.state).textContent = keyState(src);
    const env = src.source === "env", none = src.source !== "saved" && !env;
    el(ids.acts).hidden = none;                                    // 測試 / 換 key / 清除 once there is a key
    el(ids.change).hidden = env;                                   // an env key wins: nothing to paste or clear
    el(ids.clear).hidden = src.source !== "saved";
    el(ids.row).hidden = env || (!none && !editing[src.kind]);     // the field: no key yet, or 換 key
  }

  function localCard(src) {
    el("ai-loc-state").textContent = src.ready ? `${src.model} · ${src.url}` : t("settings.askai.local_none");
    el("ai-loc-acts").hidden = !src.ready;
    el("ai-loc-row").hidden = src.ready && !editing.local;
    if (src.ready && !editing.local) { el("ai-loc-url").value = src.url || ""; el("ai-loc-model").value = src.model || ""; }
  }

  function show(s) {
    state = s;
    el("askai").hidden = false;
    const member = s.who === "member";
    words(member);
    el("ai-on").checked = !!s.enabled;
    const list = sourcesOf(s);
    const box = el("ai-sources");
    el("ai-order-note").hidden = member || list.length < 2;
    for (const k of Object.keys(CARD)) el(CARD[k]).hidden = true;
    list.forEach((src, i) => {
      const card = el(CARD[src.kind]);
      if (!card) return;
      card.hidden = false;
      box.appendChild(card);                                       // the order they are tried in
      card.classList.toggle("off", !src.ready);
      card.querySelector(".ai-src-n").textContent = String(i + 1);
      const moves = card.querySelector(".ai-src-moves");
      moves.hidden = member || list.length < 2;
      moves.querySelector(".ai-up").disabled = i === 0;
      moves.querySelector(".ai-down").disabled = i === list.length - 1;
      if (src.kind === "local") localCard(src);
      else if (src.kind === "anthropic") keyCard(src, { model: "ai-key-model", state: "ai-key-state", acts: "ai-test-row", change: "ai-key-change", clear: "ai-key-clear", row: "ai-key-row" });
      else keyCard(src, { model: "ai-gem-model", state: "ai-gem-state", acts: "ai-gem-acts", change: "ai-gem-change", clear: "ai-gem-clear", row: "ai-gem-row" });
    });
    const local = list.some((x) => x.kind === "local" && x.ready);
    el("ai-model").textContent = t(local ? "settings.askai.used_local" : "settings.askai.used", { n: s.used_today, limit: s.daily_limit });
    // SP-883: an open 「問 AI」 on this page unlocks (or locks) as a key is saved or cleared
    window.dispatchEvent(new CustomEvent("askai:key", { detail: { has: list.some((x) => x.ready) } }));
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
  const SAVE_ERR = { BAD_KEY: "bad_key", BAD_LOCAL: "bad_local" };
  async function save(data, kind) {
    for (const id of ["ai-key-test-msg", "ai-gem-test-msg", "ai-loc-test-msg"]) { el(id).textContent = ""; el(id).className = "meta ai-src-st"; }
    try {
      const s = await put(data);
      if (kind) editing[kind] = false;
      show(s);
      say(t("settings.askai.saved"));
    } catch (e) {
      say(SAVE_ERR[e.code] ? t(`settings.askai.${SAVE_ERR[e.code]}`) : t("settings.askai.failed", { msg: e.message.slice(0, 80) }));
    }
  }

  // 「測試」 (SP-883, SP-997): that source against its model list — nothing billed
  const TEST_ERR = {
    anthropic: { AI_KEY_INVALID: "test_invalid", AI_NO_CREDIT: "test_no_credit", AI_BUSY: "test_busy", _: "test_failed" },
    gemini: { AI_KEY_INVALID: "test_invalid_gemini", AI_NO_MODEL: "test_no_model_gemini", AI_BUSY: "test_busy_gemini", _: "test_failed_gemini" },
    local: { AI_UNREACHABLE: "test_unreachable", AI_NO_MODEL: "test_no_model", AI_KEY_INVALID: "test_invalid_local", AI_BUSY: "test_busy_local", _: "test_failed_local" },
  };
  const TEST_OK = { anthropic: "test_ok", gemini: "test_ok_gemini", local: "test_ok_local" };
  const TEST_IDS = { anthropic: ["ai-key-test", "ai-key-test-msg"], gemini: ["ai-gem-test", "ai-gem-test-msg"], local: ["ai-loc-test", "ai-loc-test-msg"] };
  async function test(kind) {
    const [bid, mid] = TEST_IDS[kind];
    const b = el(bid), msg = el(mid);
    const src = sourcesOf(state || {}).find((x) => x.kind === kind) || {};
    b.disabled = true;
    msg.className = "meta ai-src-st";
    msg.textContent = t("settings.askai.test_running");
    try {
      await api({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind }) }, `${URL_}/test`);
      msg.textContent = "✓ " + t(`settings.askai.${TEST_OK[kind]}`, { model: src.model || "" });
      msg.classList.add("ok");
    } catch (e) {
      const k = e.code === "AI_TEST_LIMIT" ? "test_limit" : e.code === "AI_NO_KEY" ? "test_no_key" : (TEST_ERR[kind][e.code] || TEST_ERR[kind]._);
      msg.textContent = t(`settings.askai.${k}`, { model: src.model || "" });
      msg.classList.add("bad");
    } finally { b.disabled = false; }
  }

  // ↑↓ (the owner): swap with the neighbour, save the whole order
  function move(kind, by) {
    const kinds = sourcesOf(state || {}).map((x) => x.kind);
    const i = kinds.indexOf(kind), j = i + by;
    if (i < 0 || j < 0 || j >= kinds.length) return;
    [kinds[i], kinds[j]] = [kinds[j], kinds[i]];
    save({ order: kinds });
  }
  for (const k of Object.keys(CARD)) {
    const card = el(CARD[k]);
    card.querySelector(".ai-up").onclick = () => move(k, -1);
    card.querySelector(".ai-down").onclick = () => move(k, 1);
  }

  el("ai-on").onchange = (ev) => save({ enabled: ev.target.checked });
  // Claude
  el("ai-key-save").onclick = () => {
    const v = el("ai-key").value.trim();
    if (!v) return;
    el("ai-key").value = "";
    el("ai-key-row").classList.remove("link-asked");
    save({ api_key: v }, "anthropic");
  };
  el("ai-key-clear").onclick = () => save({ api_key: "" }, "anthropic");
  el("ai-key-change").onclick = () => { editing.anthropic = !editing.anthropic; show(state); if (editing.anthropic) el("ai-key").focus(); };
  el("ai-key-test").onclick = () => test("anthropic");
  // Gemini
  el("ai-gem-save").onclick = () => {
    const v = el("ai-gem-key").value.trim();
    if (!v) return;
    el("ai-gem-key").value = "";
    save({ gemini_key: v }, "gemini");
  };
  el("ai-gem-clear").onclick = () => save({ gemini_key: "" }, "gemini");
  el("ai-gem-change").onclick = () => { editing.gemini = !editing.gemini; show(state); if (editing.gemini) el("ai-gem-key").focus(); };
  el("ai-gem-test").onclick = () => test("gemini");
  // the local model (the owner)
  el("ai-loc-save").onclick = () => {
    const url = el("ai-loc-url").value.trim(), model = el("ai-loc-model").value.trim(), key = el("ai-loc-key").value.trim();
    if (!url || !model) { say(t("settings.askai.bad_local")); return; }
    el("ai-loc-key").value = "";
    save({ local: key ? { url, model, api_key: key } : { url, model } }, "local");
  };
  el("ai-loc-clear").onclick = () => { el("ai-loc-url").value = ""; el("ai-loc-model").value = ""; save({ local: null }, "local"); };
  el("ai-loc-edit").onclick = () => { editing.local = !editing.local; show(state); if (editing.local) el("ai-loc-url").focus(); };
  el("ai-loc-test").onclick = () => test("local");
  load();
})();
