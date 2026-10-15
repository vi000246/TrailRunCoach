"""SP-542: the 課表's week column says why each week is planned the way it is — this week's reasons
(week_plan's `why`, as the overview's 「詳細」) and the projected weeks' (projection `weeks[].why`, the
recovery week's 「the share is 推估」 line included), on GET /calendar's week_rows.

The plan inputs are cached once for every language (api/plan_sessions._inputs_key has no locale), so
the reasons are M_() texts (backend/i18n: the text plus its msgid) and the week rows re-render them in
the request's language (i18n.relocalize): an English page shows English even when a Chinese request
(or the background, zh-TW) built the inputs. Synthetic data only."""
from __future__ import annotations

from datetime import date

from backend.api import plan_sessions as API
from backend.engine import b2b as B2B
from backend.engine import overview as O
from backend.engine import projection as P
from backend.engine import reentry as RE
from backend.i18n import M_, Msg, relocalize, translate, use_locale
from backend.scripts.i18n_extract import CJK
from backend.tests.test_recovery_week import LIGHT, MON, _phases, _week


def _zh_inputs():
    """This week's plan and the projected weeks to the race, built in zh-TW (as the cache may be)."""
    with use_locale("zh-TW"):
        _, plan, wp = _week("2026-11-14", {4: LIGHT})
        weeks = P.project_weeks(wp, _phases(plan, date(2026, 9, 30)), date(2026, 11, 15))
    return wp, weeks


# ---- backend/i18n: M_ and relocalize ------------------------------------------------------------

def test_m_is_the_text_and_keeps_its_msgid():
    with use_locale("zh-TW"):
        m = M_("減量期：平常 {h:.1f} h × {share:.0%}", h=6.0, share=0.75)
    assert isinstance(m, Msg) and isinstance(m, str) and m == "減量期：平常 6.0 h × 75%"
    assert relocalize(m, "en") == "Taper: usual 6.0 h × 75%"
    assert relocalize(m, "zh-TW") == m
    assert relocalize("plain", "en") == "plain" and relocalize(None, "en") is None


def test_an_m_parameter_follows_the_language_too():
    with use_locale("zh-TW"):
        m = M_("{mode}：近 4 週的 {share:.0%}", mode=P.mode_label("transition"), share=0.65)
    assert m == "轉換期：近 4 週的 65%"
    assert relocalize(m, "en") == "Transition: 65% of the last 4 weeks"


def test_m_survives_copy_and_pickle():
    import copy
    import pickle
    with use_locale("zh-TW"):
        m = M_("比賽週：短、輕鬆")
    for c in (copy.deepcopy(m), pickle.loads(pickle.dumps(m))):
        assert isinstance(c, Msg) and c == m and relocalize(c, "en") == "Race week: short and easy"


# ---- the reasons: every week, in either language ---------------------------------------------------

def test_this_week_and_every_projected_week_have_reasons():
    wp, weeks = _zh_inputs()
    assert wp["why"] and all(isinstance(x, Msg) for x in wp["why"])
    assert weeks and all(w["why"] for w in weeks)
    kinds = {w["mode"] for w in weeks}
    assert {"recovery_week", "taper"} <= kinds, kinds          # the recovery and taper weeks are among them
    rec = next(w for w in weeks if w["mode"] == "recovery_week")
    assert rec["why"][-1] == O.recovery_share_note()          # 「這個比例是推估」, last


def test_in_english_no_reason_has_chinese():
    """Built in zh-TW, shown in English: every reason and mode label of this week and the projected ones."""
    wp, weeks = _zh_inputs()
    with use_locale("en"):
        texts = [relocalize(x) for w in [wp] + weeks for x in w["why"]] + \
                [relocalize(w["mode_label"]) for w in [wp] + weeks]
    bad = [t for t in texts if CJK.search(t)]
    assert not bad, bad
    rec = next(w for w in weeks if w["mode"] == "recovery_week")
    with use_locale("en"):
        assert relocalize(rec["why"][-1]).startswith("No study says how much a recovery week should cut")
        assert relocalize(rec["mode_label"]) == "Recovery week"


def test_the_projected_lines_that_were_untranslated():
    """The f-strings of projection.week_hours / project_weeks (SP-542): now msgids with English."""
    with use_locale("zh-TW"):
        _, _, why = P.week_hours("base", [5.0, 5.5, 6.0, 6.5], [False, True, True], ctl0=40.0, r=50.0, cc=42.0,
                                 days_to_a=None)
        _, _, taper = P.week_hours("taper", [6.0] * 6, [], ctl0=40.0, r=50.0, cc=42.0, days_to_a=20)
        _, _, event = P.week_hours("event", [6.0] * 4, [], ctl0=40.0, r=50.0, cc=42.0, days_to_a=3)
    assert why[0].startswith("長期訓練負荷（Chronic load）40 每週 +")
    with use_locale("en"):
        assert relocalize(why[0]).startswith("Chronic load 40 at +") and "capped at +10%" in relocalize(why[0])
        assert relocalize(taper[0]) == "Taper: usual 6.0 h × 75%"
        assert relocalize(event[0]) == "Race week: short and easy"


def test_b2b_and_reentry_reasons_translate():
    info = {"due": True}
    with use_locale("zh-TW"):
        due = B2B.tsb_exempt(info, -25.0)
        post = B2B.tsb_exempt({"post": {"start": "2026-10-10", "end": "2026-10-11"}}, -25.0)
        txt = RE.text_of(20, "14-28", date(2026, 10, 1), date(2026, 10, 21), stepped=True, transition_days=7)
    assert "負荷平衡" in due and "10/10–10/11" in post and "不含賽後恢復期／轉換期 7 天" in txt
    with use_locale("en"):
        for s in (due, post, txt):
            assert not CJK.search(relocalize(s)), relocalize(s)
        assert relocalize(post).startswith("Load balance -25: the expected drop from last weekend's B2B (10/10–10/11)")


# ---- GET /calendar's week rows -------------------------------------------------------------------

def test_week_rows_carry_this_and_the_projected_weeks_reasons():
    wp, weeks = _zh_inputs()
    start, end = wp["week"]["start"], weeks[2]["end"] if "end" in weeks[2] else weeks[2]["start"]
    with use_locale("zh-TW"):
        rows = API._week_rows(start, end, [], [], [], weeks, {}, MON.isoformat(), cur=wp)
    assert [r["start"] for r in rows] == [wp["week"]["start"]] + [w["start"] for w in weeks[:3]]
    assert rows[0]["why"] == list(wp["why"]) and rows[0]["mode_label"] == wp["mode_label"]
    assert rows[1]["why"] == list(weeks[0]["why"]) and rows[1]["provisional"] == weeks[0]["provisional"]
    with use_locale("en"):
        en = API._week_rows(start, end, [], [], [], weeks, {}, MON.isoformat(), cur=wp)
    assert en[0]["why"] == [translate(x.msgid, "en", **{k: relocalize(v, "en") for k, v in x.params.items()})
                            for x in wp["why"]]
    assert not [x for r in en for x in r["why"] + [r["mode_label"] or ""] if CJK.search(x)]


def test_week_rows_without_the_plan_have_no_reasons():
    """課表統計 (no `cur`): the rows as before, no `why`."""
    wp, weeks = _zh_inputs()
    rows = API._week_rows(wp["week"]["start"], weeks[1]["start"], [], [], [], weeks, {}, MON.isoformat())
    assert rows and not any("why" in r for r in rows)


# ---- the page: the phase pill's popup, 精簡 into 「詳細」 (schedule.html, in node) -------------------

from backend.engine import hints as H  # noqa: E402
from backend.tests.test_schedule_hints_sp484 import LEVELS, _run, needs_node  # noqa: E402

ROW = {"start": "2026-10-12", "end": "2026-10-18", "phase": "specific", "phase_label": "專項期", "mode_label": "恢復週",
       "provisional": True, "why": ["專項期賽前第 5 週是恢復週", "恢復週減多少沒有研究可依"]}
SETUP = "D.week_rows = [DATA.row]; D.week_why_hint = DATA.hint; D.plan_notes = [];"


def test_sc15_is_in_the_catalog():
    assert H.CATALOG["SC15"].cls == 2 and H.CATALOG["SC15"].show == "●●▷"


@needs_node
def test_the_pill_names_the_weeks_reasons_until_lean():
    for level in LEVELS:
        out = _run(f"(() => {{ {SETUP} renderNotes(); return {{ pill: phPill(DATA.row), box: BOX.innerHTML }}; }})()",
                   level=level, row=ROW, hint=H.flag("SC15"))
        if level == "lean":                       # ▷: no popup; the reasons in the week reminders' 「詳細」
            assert 'class="php"' in out["pill"] and "data-tip" not in out["pill"], out
            assert '<details class="pn-detail">' in out["box"] and "wk-why-d" in out["box"]
            assert "專項期 · 恢復週：這週為什麼這樣排" in out["box"] and "<li>恢復週減多少沒有研究可依</li>" in out["box"]
            assert "暫定：之後每週會照你實際的完成狀況重算" in out["box"]
        else:                                     # ●: the pill is a button with the popup text
            assert out["pill"].startswith('<button type="button" class="php wkq"'), out
            assert ('data-tip="專項期 · 恢復週：這週為什麼這樣排\n• 專項期賽前第 5 週是恢復週\n• 恢復週減多少沒有研究可依\n'
                    '暫定：之後每週會照你實際的完成狀況重算"') in out["pill"]
            assert "wk-why-d" not in out["box"]


@needs_node
def test_a_week_without_reasons_keeps_the_plain_pill():
    row = {**ROW, "why": None, "mode_label": None}
    out = _run(f"(() => {{ {SETUP} renderNotes(); return {{ pill: phPill(DATA.row), box: BOX.innerHTML }}; }})()",
               level="lean", row=row, hint=H.flag("SC15"))
    assert out["pill"] == '<span class="php" title="專項期">專項期</span>' and out["box"] == ""
