"""同一堂間歇課前後被當成不同來源判讀（課表 vs 偵測到的趟） (SP-520).

Reported from the demo (SP-516): 10/6 「間歇 6×4 分」 read as planned on the server's first call and as
「偵測到 6 趟（超閾值）（你標了「當作間歇」：用偵測到的趟）」 after. Not reproduced: on a fresh demo server
the first and every later call read the detected reps — that run has no planned session (the demo stores
the 課表 of the current week only) and the demo build itself adds the 「當作間歇」 tag to its generated
interval / tempo / hill runs (backend/demo/build.py, 「as a user would」). The first reading's
「目標下限 101% CP」 is the detected 超閾值 band's floor, the later 263–276 W the same band in watts.

What these lock: a session with a plan is judged on its plan on the first call, the memoised call and a
fresh dataset (a restarted server) alike, marked or not; the only change of source is a plan matched
later (a sync), from then on; 「你標了「當作間歇」」 is said only with the tag, and judging never writes it."""
import datetime as dt

from backend.engine import interval_eval as IE
from backend.engine import workout_review as WR
from backend.tests.test_interval_eval import _planned, _v1a_run
from backend.tests.test_quality_gate import TODAY, _ds

MARK = "當作間歇"


def _source(ds, w) -> tuple:
    """What a reader compares between two calls: the source, the band and the badge's subtitle."""
    e = IE.card_cached(ds, w)
    v = WR.review(ds, w, "interval_verdict")
    return (e["kind"], e["planned"], e["label"], round(e["lo"], 4), round(e["hi"], 4), round(e["floor"], 1),
            v["badge"]["sub"])


def _fresh_planned():
    ds = _planned(_ds([_v1a_run(TODAY - dt.timedelta(days=3))]), "v1a")
    return ds, ds.workouts[0]


def test_a_planned_session_reads_its_plan_on_the_first_and_every_later_call():
    ds, w = _fresh_planned()
    first = _source(ds, w)
    assert first[0] == "plan" and first[1] and MARK not in first[-1], first
    assert _source(ds, w) == first                       # the memoised answer (a page reload)
    ds2, w2 = _fresh_planned()                           # a fresh dataset and memo (a restarted server)
    assert _source(ds2, w2) == first


def test_a_planned_session_marked_as_interval_still_reads_its_plan(monkeypatch):
    monkeypatch.setattr(IE, "flagged", lambda w: True)
    ds, w = _fresh_planned()
    first = _source(ds, w)
    assert first[0] == "plan" and first[1] and MARK not in first[-1], first
    assert _source(ds, w) == first
    ds2, w2 = _fresh_planned()
    assert _source(ds2, w2) == first


def test_a_plan_matched_later_is_used_from_then_on():
    """A sync's plan match comes after the dataset was built: the run reads its detected reps until then,
    its plan after (card_cached's memo key holds the matched session), never the other way round."""
    ds = _ds([_v1a_run(TODAY - dt.timedelta(days=3))])
    w = ds.workouts[0]
    ds.plan_rows = {}
    before = _source(ds, w)
    assert before[0] == "detected" and not before[1]
    _planned(ds, "v1a")
    after = _source(ds, w)
    assert after[0] == "plan" and after[1]
    assert _source(ds, w) == after


def test_the_mark_is_named_only_when_the_tag_is_there(tmp_path, monkeypatch):
    from backend.engine import activity_tags as AT
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _ds([_v1a_run(TODAY - dt.timedelta(days=3), reps=3)])      # an easy run with a set (SP-519)
    w = ds.workouts[0]
    ds.plan_rows = {}
    # no tag: shown by itself (SP-519), not 「你標了」 — and judging never adds the tag
    for _ in range(2):
        v = WR.review(ds, w, "interval_verdict")
        assert MARK not in v["badge"]["sub"] and "自動判讀" in v["badge"]["sub"], v["badge"]
    assert not IE.flagged(w) and IE.FLAG_TAG not in AT.tags_of(AT.user_of(w))
    # the user's 「當作間歇判讀」 (the tag): named, on the first call and after
    AT.upsert(db, start_local=AT.key_of(w.entry.start), file=w.entry.file, tags=[IE.FLAG_TAG])
    for _ in range(2):
        v = WR.review(ds, w, "interval_verdict")
        assert v["badge"]["sub"].endswith("（你標了「當作間歇」：用偵測到的趟）"), v["badge"]
