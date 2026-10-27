"""SP-909 R1: the reasons the 課表 page shows for a change — the automatic adjustment's (adapt: the ↻ marker
and the 自動調整紀錄) and the 重算課表 / 不排課日期 previews' (reconcile, plan_match: moved, removed, missed,
unmatched) — in English on the English page.

They are stored / compared in zh-TW as before (「不排課」 is read back by reconcile); engine/stored_text reads
them back: a weekday right before a title (「{day}{title}取消：…」), a user's label kept as written inside a
text inside a reason (「在不排課日期內（連假出遊），移到 10/2」). Synthetic data only."""
from __future__ import annotations

from datetime import date

import pytest

from backend import request_context as RC
from backend.engine import adapt as A
from backend.engine import plan_match as PM
from backend.engine import quality_gate as QG
from backend.engine import reconcile as R
from backend.engine import stored_text as ST
from backend.scripts.i18n_extract import CJK
from backend.sync import coros_workouts as CW
from backend.tests.test_adapt import HARD_Z3, _fatigue_adj, _run, ctx, g, week
from backend.tests.test_adapt_rpe import c as rpe_ctx, rated, rpe_adj, wk as rpe_week
from backend.tests.test_blackouts import BL_LIST, BEnv, S, bmap, bo
from backend.tests.test_plan_store import API

REAL = QG.DOSE[1][1]            # a quality title the generator writes today (not a pre-SP-79 「閾值 3×10 分」)


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


def _en(t: str) -> str:
    ST._MEMO.clear()
    ST._KEPT.clear()
    return ST.shown(t, "en")


# ---- the automatic adjustment (adapt rule E, rule D's self-rating, the B2B exemption) -------------------

def test_fatigue_cancelled_and_stepped_down_read_in_english():
    gw = week([g("quality", "quality", "2026-10-01", 60, title=REAL), g("easy1", "easy", "2026-10-02", 50),
               g("long", "long", "2026-10-04", 120)])
    _out, adj, _ = A.adapt(gw, [], ctx(load={"ramp": 8.5}))
    gone = next(a for a in _fatigue_adj(adj) if a["action"] == "removed")
    assert gone["reason"].startswith(f"週四{REAL}取消：") and gone["reason"].endswith("，本週減量")   # zh-TW as before
    en = _en(gone["reason"])
    assert not CJK.search(en), en
    assert en.startswith(f"Thursday {_en(REAL)} cancelled: Chronic load ramp") and en.endswith("volume cut this week")
    # two too-hard easy runs: the quality steps down — 「{day}{old}改成{became}：…（週一、週二）…」
    gw = week([_run("easy1", "2026-09-28", 1), _run("easy2", "2026-09-29", 2),
               g("quality", "quality", "2026-10-02", 60, tss=70, title=REAL), g("easy3", "easy", "2026-10-03", 50),
               g("long", "long", "2026-10-04", 120, tss=100)])
    _out, adj, _ = A.adapt(gw, [], ctx(reviews={1: HARD_Z3, 2: HARD_Z3}))
    down = next(a for a in _fatigue_adj(adj) if a["action"] == "downgraded")
    assert down["reason"].startswith(f"週五{REAL}改成")
    en = _en(down["reason"])
    assert not CJK.search(en), en
    assert en.startswith(f"Friday {_en(REAL)} became ") and "(Monday, Tuesday)" in en      # a weekday keeps its capital
    trimmed = next(a for a in _fatigue_adj(adj) if a["action"] == "trimmed")
    assert "(Monday, Tuesday)" in _en(trimmed["reason"])


def test_a_self_rating_reason_reads_in_english():
    # 「{was} {title}延到{to}：{day}{what}自評 {label}，…」: the weekday right before 輕鬆跑 / 長跑
    _out, adj, _ = A.adapt(rpe_week(quality_title=REAL), [], rpe_ctx(rpe=rated(4)))
    r = rpe_adj(adj)[0]["reason"]
    assert r == f"週四 {REAL}延到週五：週三輕鬆跑自評 Hard，間隔不到 48 小時"
    en = _en(r)
    assert not CJK.search(en), en
    assert en.startswith(f"Thursday {_en(REAL)} moved to Friday: Wednesday ") and "rated Hard" in en


def test_the_b2b_exemption_reads_in_english():
    for info, want in (({"due": True}, "a B2B you scheduled is this week"),
                       ({"post": {"start": "2026-09-26", "end": "2026-09-27", "until": "2026-10-01"}},
                        "an easy day after last weekend's B2B (9/26–9/27)")):
        gw = week([g("quality", "quality", "2026-10-01", 60, title=REAL), g("long", "long", "2026-10-04", 120)])
        _out, adj, _ = A.adapt(gw, [], ctx(load={"tsb": -34, "ctl": 30}, b2b=info))
        note = next(a for a in adj if a["rule"] == "b2b")
        en = _en(note["reason"])
        assert not CJK.search(en) and want in en, en


# ---- the 重算課表 / 不排課日期 previews (reconcile, plan_match) ------------------------------------------

def test_reconcile_reasons_read_in_english_with_the_users_label_as_written():
    ss = [S("long", "long", "2026-10-25"), S("easy1", "easy", "2026-10-24"), S("easy2", "easy", "2026-10-21")]
    _new, ch = R.reconcile(ss, [], [], "2026-10-20", None, blocked=bmap(bo("2026-10-24", "2026-10-25")))
    moved = next(c for c in ch if c["uid"] == "long")
    assert moved["reason"] == "在不排課日期內（連假出遊），移到 10/23"                 # zh-TW as before
    assert _en(moved["reason"]) == "On a blocked date (連假出遊), moved to 10/23"
    _new, ch = R.reconcile(ss, [], [], "2026-10-20", None, blocked=bmap(bo("2026-10-19", "2026-10-25")))
    assert {_en(c["reason"]) for c in ch} == {"On a blocked date (連假出遊), no free day this week"}
    # an edited session: a conflict until the user decides, then their choice
    ss = [S("long", "long", "2026-10-25", edited=True), S("mine", "hike", "2026-10-24", origin="custom")]
    blocked = bmap(bo("2026-10-24", "2026-10-25"))
    _new, ch = R.reconcile(ss, [], [], "2026-10-20", None, blocked=blocked)
    assert {_en(c["reason"]) for c in ch} == {"On a blocked date (連假出遊)"}
    _new, ch = R.reconcile(ss, [], [], "2026-10-20", None, blocked=blocked, decisions={"long": "move", "mine": "delete"})
    assert {c["uid"]: _en(c["reason"]) for c in ch} == {
        "long": "On a blocked date (連假出遊), moved to 10/23 as you chose",
        "mine": "On a blocked date (連假出遊), deleted as you chose"}
    # a 休息日 (the app's own word in the label) and the texts with no field
    assert _en(R._why("休息日")) == "On a blocked date (Rest day)"
    for zh in ("重新計算後不需要", "超出排程範圍", "這週改成恢復／減量，你改過的這堂課和它衝突",
               "和你安排的課同一天，移到空的一天", "和你安排的課同一天，本週沒有空的日子", "沒有對應的活動"):
        assert not CJK.search(_en(zh)), zh


def test_match_reasons_read_in_english():
    out = [{"uid": "a", "week_start": "2026-09-28", "day": "2026-09-29", "kind": "easy", "title": "輕鬆跑",
            "state": "active", "done_by": None, "origin": "auto", "edited": False, "minutes": 40},
           {"uid": "b", "week_start": "2026-09-28", "day": "2026-09-29", "kind": "long", "title": "LSD（山路）",
            "state": "active", "done_by": None, "origin": "auto", "edited": False, "minutes": 120}]
    acts = [{"index": 1, "date": "2026-09-29", "category": "road", "minutes": 45, "tss": 30}]
    ch = PM.assign(out, acts, "2026-10-01", {})
    texts = [c["reason"] for c in ch if c.get("reason")]
    assert texts and not [t for t in texts if CJK.search(_en(t))], [(t, _en(t)) for t in texts]
    # a title the user typed stays as written inside the reason
    assert _en("同一筆活動已算給「早上跟朋友跑」") == "The same activity already counts for “早上跟朋友跑”"
    assert _en("同一筆活動已算給「LSD（山路）」") == "The same activity already counts for “LSD (trail)”"


def test_the_blackout_preview_answers_in_english_and_keeps_the_users_blackouts(monkeypatch):
    with BEnv(monkeypatch) as e:
        e.c.app.add_middleware(RC.RequestContextMiddleware)
        zh = e.c.post(f"{API}/blackouts/preview?lang=zh-TW", json={"blackouts": BL_LIST}).json()
        en = e.c.post(f"{API}/blackouts/preview?lang=en", json={"blackouts": BL_LIST}).json()
    no_id = lambda bl: [{k: v for k, v in b.items() if k != "id"} for b in bl]      # (a new id each preview)
    assert no_id(en["blackouts"]) == no_id(zh["blackouts"])            # the user's own, as sent
    said = [c for c in zh["changes"] if c.get("reason")]
    assert said and all("在不排課日期內" in c["reason"] for c in said)
    for c in en["changes"]:
        if c.get("reason"):
            assert c["reason"].startswith("On a blocked date (連假出遊)"), c["reason"]
    days = [d for d in en["by_day"] for c in en["by_day"][d] if c.get("reason")]
    assert days and all(c["reason"].startswith("On a blocked date") for d in days for c in en["by_day"][d]
                        if c.get("reason"))
