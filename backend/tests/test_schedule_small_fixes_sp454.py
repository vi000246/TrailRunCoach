"""
SP-454: small things on the 課表 page.

* 交換中: the ⇄ was an ::after drawn at the card's top right, over the ring and the watch's 未推送 icon. It is
  an element in the card's right-hand row now (before the ring), so nothing covers it and it covers nothing.
* A phone has no Esc key: the picking / 「不能交換」 words point at the toast's 取消 there.
* The 賽事計算機's 「匯出至課表」 writes the session as the user's own (edited, so reconcile keeps it): the
  dialog said 「已修改」 and the structure 「你改過的結構」 on a session nobody touched. The calendar now says
  whether it was changed on the 課表 page since the export (`ext_edited`, from the export's fingerprint);
  the dialog names its source, 已修改 only when ext_edited, and the editor says 「賽事計算機匯出的結構」.
* The week's 週統計: 「負荷 5 / 254」 and 「完成 TSS 5」 (SP-401) showed the same done number twice; a week
  with the 完成 TSS row drops the 負荷 row and its bar (the plan is in that row's hover).
Synthetic.
"""
import json
import re
from pathlib import Path

from backend.tests.test_race_day_sp504 import _cal

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "schedule.html"


def _src() -> str:
    return PAGE.read_text("utf-8")


# ---- 已修改 on a calculator export -------------------------------------------------------------

def test_an_export_is_not_edited_until_changed_on_the_schedule(monkeypatch):
    from backend.engine import plan_store as PS
    from backend.tests.test_coros_workouts import run
    from backend.tests.test_plan_store import API, KEY, race_data
    with _cal(monkeypatch) as e:
        e.c.get(f"{API}/calendar?start=2026-10-05&end=2026-10-11")
        run(PS.upsert_external(e.db, KEY, race_data(), "2026-09-30"))          # 2026-10-10
        cal = e.c.get(f"{API}/calendar?start=2026-10-05&end=2026-10-11").json()
        ex = next(s for s in cal["sessions"] if s.get("ext_key") == KEY)
        assert ex["edited"] is True and ex["ext_edited"] is False          # kept by reconcile, not changed by anyone
        assert all("ext_edited" not in s for s in cal["sessions"] if not s.get("ext_key"))
        r = e.c.patch(f"{API}/sessions/{ex['uid']}", json={"title": "賽事 測試越野（改過）"})
        assert r.status_code == 200, r.text
        cal = e.c.get(f"{API}/calendar?start=2026-10-05&end=2026-10-11").json()
        assert next(s for s in cal["sessions"] if s.get("ext_key") == KEY)["ext_edited"] is True


def test_the_dialog_and_the_editor_name_the_exports_source():
    src = _src()
    sub = re.search(r"\nfunction subOrigin\(s\) \{\n(.*?)\n\}\n", src, re.S)
    assert sub, "subOrigin not found"
    body = sub.group(1)
    # the words through the catalog since SP-839 (zh-TW: 已修改 / 自訂 / 賽事計算機匯出的結構 / 你改過的結構)
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "schedule.json").read_text("utf-8"))
    common = json.loads((STATIC / "i18n" / "zh-TW" / "common.json").read_text("utf-8"))
    assert zh["sd.edited"] == "已修改" and zh["sd.custom"] == "自訂"
    assert common["workout.from_racecalc"] == "賽事計算機匯出的結構" and common["workout.yours"] == "你改過的結構"
    assert 'if (fromCalc(s)) return ` · ${tt("sd.from_calc")}${s.ext_edited ? " · " + tt("sd.edited") : ""}`;' in body
    assert 'return s.origin === "custom" ? " · " + tt("sd.custom") : s.edited ? " · " + tt("sd.edited") : "";' in body      # the rest as before
    assert '${subOrigin(s)}`;' in src and 'const fromCalc = (s) => !!(s && String(s.ext_key || "").startsWith("racecalc:"));' in src
    assert 'src: fromCalc(s) && !s.ext_edited ? "racecalc" : null' in src
    we = (STATIC / "workout_editor.js").read_text("utf-8")
    assert 'this.stored ? " · " + (this.src === "racecalc" ? tr("workout.from_racecalc") : tr("workout.yours"))' in we
    assert "touch() { this.dirty = true; this.cleared = false; this.src = null;" in we          # an edit: yours now
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "schedule.json").read_text("utf-8"))
        assert cat.get("sd.from_calc"), loc


# ---- ⇄ while swapping, Esc on a phone ------------------------------------------------------------

def test_the_swap_mark_is_in_the_cards_row_not_over_it():
    src = _src()
    assert ".chip.swap-lock::after" not in src
    assert re.search(r'<span class="c-r">\$\{lock \? `<span class="c-lock" aria-hidden="true">\$\{pend \? "↻" : "⇄"\}</span>` : ""\}'
                     r"\$\{SessChip\.ring\(", src)


def test_a_phone_is_not_told_to_press_esc():
    src = _src()
    assert 'const NO_KEYS = () => matchMedia("(hover: none) and (pointer: coarse)").matches;' in src
    assert 'tt(NO_KEYS() ? "swap.pick_touch" : "swap.pick", { title: s.title })' in src
    assert 'const nt = NO_KEYS() ? "swap.not_this_touch" : "swap.not_this";' in src
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "schedule.json").read_text("utf-8"))
        for k in ("swap.pick_touch", "swap.not_this_touch"):
            assert cat.get(k) and "Esc" not in cat[k], (loc, k)
        assert "Esc" in cat["swap.pick"] and "Esc" in cat["swap.not_this"]               # a keyboard keeps it
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "schedule.json").read_text("utf-8"))
    assert "取消" in zh["swap.pick_touch"] and zh["swap.cancel"] == "取消"                  # the toast's button


# ---- the week's done TSS once ----------------------------------------------------------------------

def test_the_week_sum_shows_the_done_load_once():
    src = _src()
    s = re.search(r"\nfunction sumHtml\(w\) \{\n(.*?)\n\}\n", src, re.S).group(1)
    # the 負荷 row and its bar only without the 完成 TSS row (a future week, a week without compliance)
    assert re.search(r'\$\{w\.compliance && !future \? "" : `<div class="r"><span>\$\{esc\(tt\("load\.col"\)\)\}', s)
    assert "${compW(w)}" in s
    ag = re.search(r"\nfunction agendaHtml\(a, ss, acts\) \{\n(.*?)\n\}\n", src, re.S).group(1)
    assert re.search(r'\$\{w\.compliance && !future \? "" : `<span>\$\{esc\(tt\("load\.short"\)\)\}', ag)
    assert 'esc(tt("wk.done_tss"))' in ag
