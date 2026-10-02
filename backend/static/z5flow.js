// 5 區開放流程 as a quest-style step flow (quality_gate.z5_flow = z5_card()["flow"]). Shared by the
// 總覽 card (overview.html) and the 基礎期 panel (wko5_viewer.html, kind "z5gate"); no chart, no time axis.
// Stages run top-to-bottom in a narrow box (phone, the overview card) and left-to-right once the box is
// wide (container query). Each stage: a badge (✓ / its number), a tag word (state is never colour alone),
// a checklist ☑ / ☐ with one short 「what to do」 line, and what finishing it unlocks. Done and locked
// stages fold to their header in the narrow layout (tap to open). Sources / details behind ? buttons.
//   Z5Flow.html(flow, { q: "q" | "qtip" })  -> HTML string
//   Z5Flow.bind(root)                         -> the fold toggles (once per page; delegated)
(function () {
  const L = window.I18N || { t: (k) => k };
  const T = (k, p) => L.t("common.z5flow." + k, p);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function style() {
    if (document.getElementById("zf-style")) return;
    const st = document.createElement("style");
    st.id = "zf-style";
    st.textContent = `
.zf { --zf-good: var(--good, var(--ok, #1a7f37)); --zf-warn: var(--watch, var(--warn, #b45309));
  --zf-soft: var(--soft, var(--accent-soft, #e8efff)); --zf-faint: var(--faint, var(--muted));
  container-type: inline-size; display: grid; gap: 10px; min-width: 0; }
.zf-here { margin: 0; padding: 9px 12px; border-radius: 10px; background: var(--zf-soft); border-left: 4px solid var(--accent);
  font-size: 13.5px; line-height: 1.5; display: grid; gap: 2px; }
.zf-here .w { font-size: 11.5px; font-weight: 700; color: var(--accent); letter-spacing: .02em; }
.zf-here .nx { font-weight: 650; }
.zf-here .al { font-size: 12.5px; color: var(--muted); }
.zf-path { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
.zf-st { position: relative; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); min-width: 0; }
.zf-st + .zf-st::before { content: ""; position: absolute; left: 22px; top: -9px; height: 8px; border-left: 2px solid var(--line); }
.zf-st.done + .zf-st::before { border-left-color: color-mix(in srgb, var(--zf-good) 70%, var(--line)); }
.zf-st.current { border: 2px solid var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 14%, transparent); }
.zf-st.parallel { border: 1.5px dashed var(--accent); }
.zf-st.locked { background: color-mix(in srgb, var(--line) 22%, var(--panel)); }
.zf-st.locked .zf-hd, .zf-st.locked .zf-body { opacity: .62; }
.zf-hd { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto auto; gap: 8px; align-items: center; width: 100%;
  padding: 8px 10px; background: none; border: 0; font: inherit; color: var(--text); text-align: left; cursor: pointer; }
.zf-hd .bd { width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; font-size: 13px;
  border: 2px solid var(--line); color: var(--muted); background: var(--panel); }
.zf-st.done .bd { border-color: var(--zf-good); background: var(--zf-good); color: #fff; }
.zf-st.current .bd { border-color: var(--accent); background: var(--accent); color: #fff; }
.zf-st.parallel .bd { border-color: var(--accent); color: var(--accent); }
.zf-hd .tl { min-width: 0; }
.zf-hd .tl b { font-size: 13.5px; }
.zf-hd .tl small { display: block; font-size: 11.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.zf-tag { font-size: 11px; font-weight: 700; border-radius: 10px; padding: 1px 8px; white-space: nowrap; border: 1px solid currentColor; }
.zf-st.done .zf-tag { color: var(--zf-good); } .zf-st.current .zf-tag { color: #fff; background: var(--accent); border-color: var(--accent); }
.zf-st.parallel .zf-tag { color: var(--accent); } .zf-st.locked .zf-tag { color: var(--muted); }
.zf-hd .cv { font-size: 10px; color: var(--zf-faint); width: 10px; }
.zf-st.open .zf-hd .cv::before { content: "▾"; } .zf-st:not(.open) .zf-hd .cv::before { content: "▸"; }
.zf-body { padding: 0 10px 9px 46px; display: grid; gap: 6px; font-size: 12.5px; }
.zf-st:not(.open) .zf-body { display: none; }
.zf-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 5px; }
.zf-list li { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; gap: 6px; align-items: start; }
.zf-ck { width: 16px; height: 16px; margin-top: 1px; border-radius: 4px; border: 1.5px solid var(--muted); display: grid; place-items: center;
  font-size: 11px; font-weight: 800; line-height: 1; color: #fff; }
.zf-list li.y .zf-ck { background: var(--zf-good); border-color: var(--zf-good); }
.zf-list li.u .zf-ck { border-style: dashed; }
.zf-st.locked .zf-ck { border-color: var(--zf-faint); }
.zf-list .tx { min-width: 0; }
.zf-list .tx > span { font-weight: 600; }
.zf-list li.y .tx > span { color: var(--muted); font-weight: 500; }
.zf-list .vl { color: var(--muted); font-size: 11.5px; margin-left: 4px; }
.zf-list .td { display: block; color: var(--accent); font-weight: 600; font-size: 12px; }
.zf-st.locked .zf-list .td { color: var(--muted); font-weight: 500; }
.zf-any { border: 1px dashed var(--line); border-radius: 8px; padding: 6px 8px; display: grid; gap: 5px; }
.zf-any > small { font-size: 11px; color: var(--muted); font-weight: 700; }
.zf-note { margin: 0; color: var(--zf-warn); font-size: 12px; white-space: pre-line; }
.zf-unl { margin: 0; color: var(--muted); font-size: 11.5px; }
.zf-unl b { color: var(--text); font-weight: 600; }
.zf .q, .zf .qtip { margin: 0; }
@container (min-width: 860px) {
  .zf-path { grid-template-columns: repeat(var(--zf-n, 5), minmax(0, 1fr)); gap: 14px; align-items: stretch; }
  .zf-st + .zf-st::before { left: -13px; top: 22px; height: 0; width: 11px; border-left: 0; border-top: 2px solid var(--line); }
  .zf-st.done + .zf-st::before { border-top-color: color-mix(in srgb, var(--zf-good) 70%, var(--line)); }
  .zf-hd { cursor: default; grid-template-columns: 28px minmax(0, 1fr) auto; }
  .zf-hd .cv { display: none; }
  .zf-hd .zf-tag { grid-column: 2 / -1; grid-row: 2; justify-self: start; }
  .zf-st:not(.open) .zf-body { display: grid; }
  .zf-body { padding: 0 10px 10px; }
}`;
    document.head.appendChild(st);
  }

  const qb = (cls, s) => s ? `<button type="button" class="${cls}" aria-label="${esc(T("help"))}" data-tip="${esc(s)}">?</button>` : "";

  function items(list, cls, locked) {
    return `<ul class="zf-list">${list.map((i) => {
      const k = i.ok === true ? "y" : i.ok === false ? "n" : "u";
      const word = i.ok === true ? T("ok") : i.ok === false ? T("todo") : T("unknown");
      // not done yet (ok null): the 「→ what to do」 line says it; the value (「還沒做過…」) goes behind ?
      // and so does a long one (a failed test's full reasons): the card keeps one short line per item
      const inline = i.value && i.ok !== null && i.ok !== undefined && (i.ok === true || i.value.length <= 30);
      const tip = [inline ? "" : i.value, i.tip].filter(Boolean).join("\n");
      return `<li class="${k}"><span class="zf-ck" role="img" aria-label="${esc(word)}">${i.ok === true ? "✓" : ""}</span>
        <span class="tx"><span>${esc(i.text)}</span>${inline ? `<span class="vl">${esc(i.value)}</span>` : ""}
        ${i.todo && !locked ? `<span class="td">→ ${esc(i.todo)}</span>` : ""}</span>${qb(cls, tip)}</li>`;
    }).join("")}</ul>`;
  }

  function html(flow, opt = {}) {
    style();
    if (!flow || !flow.stages) return "";
    const cls = opt.q || "q", H = flow.here || {};
    const st = flow.stages.map((s, i) => {
      const locked = s.status === "locked";
      const open = s.status === "current" || s.status === "parallel";
      const any = (s.any || []).length ? `<div class="zf-any">${s.any_label ? `<small>${esc(s.any_label)}</small>` : ""}${items(s.any, cls, locked)}</div>` : "";
      return `<li class="zf-st ${esc(s.status)}${open ? " open" : ""}" data-zf="${esc(s.key)}">
        <button type="button" class="zf-hd" aria-expanded="${open}">
          <span class="bd" aria-hidden="true">${s.status === "done" ? "✓" : i + 1}</span>
          <span class="tl"><b>${esc(s.title)}</b>${s.sub ? `<small>${esc(s.sub)}</small>` : ""}</span>
          <span class="zf-tag">${esc(T("tag." + s.status))}</span><span class="cv" aria-hidden="true"></span></button>
        <div class="zf-body">
          ${s.items.length ? items(s.items, cls, locked) : ""}${any}
          ${s.note ? `<p class="zf-note">${esc(s.note)}${qb(cls, s.note_tip)}</p>` : ""}
          <p class="zf-unl">${esc(T(s.status === "done" ? "unlocked" : "unlocks"))} <b>${esc(s.unlocks)}</b>${qb(cls, s.tip)}</p>
        </div></li>`;
    }).join("");
    const here = `<p class="zf-here" role="status"><span class="w">${esc(T("here"))}：${esc(H.title || "")}</span>
      <span class="nx">${esc(T("next"))}：${esc(H.next || "")}${H.full && H.full !== H.next ? " " + qb(cls, H.full) : ""}</span>
      ${H.also ? `<span class="al">${esc(T("also", { stage: H.also_title }))}：${esc(H.also)}</span>` : ""}</p>`;
    return `<div class="zf" aria-label="${esc(T("aria"))}">${here}<ol class="zf-path" style="--zf-n:${flow.stages.length}">${st}</ol></div>`;
  }

  let bound = false;
  function bind() {
    if (bound) return;
    bound = true;
    document.addEventListener("click", (e) => {
      const b = e.target.closest?.(".zf-hd");
      if (!b) return;
      const li = b.closest(".zf-st");
      const on = !li.classList.contains("open");
      li.classList.toggle("open", on);
      b.setAttribute("aria-expanded", String(on));
    });
  }

  window.Z5Flow = { html, bind };
})();
