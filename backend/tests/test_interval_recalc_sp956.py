"""SP-956: the recomputation after 「當作間歇判讀」.

Measured on the demo athlete (backend/scripts/perf_sp956.py): after the mark the 間歇 page asks for its six
cards four at a time; each card reads interval_eval.card_cached, all four missed the new key and each ran
card() — the same evaluation four times over. And card()'s 同功率下的心率 (peers) evaluated every earlier
session of 120 days with its 「5 Min PDC」, each scanning 90 days of samples for a number peers never reads.
Now card() runs once for the concurrent requests, and peers' evaluations skip the PDC; the cards say the same."""
import datetime as dt
import threading
import time

from backend.engine import interval_eval as IE
from backend.tests.test_interval_eval import _planned, _v1a_run
from backend.tests.test_quality_gate import TODAY, _ds, _z3_run


def test_concurrent_requests_compute_the_card_once(monkeypatch):
    ds = _ds([_v1a_run(TODAY - dt.timedelta(days=3))])
    w = ds.workouts[0]
    real = IE.card
    calls = []

    def slow(ds_, w_):
        calls.append(w_.idx)
        time.sleep(0.2)                     # long enough for the other requests to arrive meanwhile
        return real(ds_, w_)
    monkeypatch.setattr(IE, "card", slow)
    got, start = [], threading.Barrier(4)

    def ask():
        start.wait()
        got.append(IE.card_cached(ds, w))
    ts = [threading.Thread(target=ask) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert len(calls) == 1 and len(got) == 4
    assert all(g is got[0] for g in got)            # every request has the one evaluation
    assert not IE._CARD_LOCKS                        # nothing left behind
    assert IE.card_cached(ds, w) is got[0] and len(calls) == 1


def test_a_failed_card_is_not_kept_and_the_next_request_computes_it(monkeypatch):
    ds = _ds([_v1a_run(TODAY - dt.timedelta(days=3))])
    w = ds.workouts[0]
    real = IE.card
    n = []

    def flaky(ds_, w_):
        n.append(1)
        if len(n) == 1:
            raise RuntimeError("boom")
        return real(ds_, w_)
    monkeypatch.setattr(IE, "card", flaky)
    try:
        IE.card_cached(ds, w)
    except RuntimeError:
        pass
    assert not IE._CARD_LOCKS
    assert IE.card_cached(ds, w)["ok"] and len(n) == 2


def test_peers_skip_the_5min_pdc_and_read_the_same(monkeypatch):
    days = [TODAY - dt.timedelta(days=d) for d in (20, 13, 6)]
    ds = _planned(_ds([_z3_run(d) for d in days]), "t2a")
    w = ds.workouts[-1]
    real = IE.best_5min
    asked = []

    def spy(ds_, w_, days=90):
        asked.append(w_.idx)
        return real(ds_, w_, days)
    monkeypatch.setattr(IE, "best_5min", spy)
    e = IE.evaluate(ds, w)
    assert asked == [w.idx]                          # this session's PDC only, none for its peers
    assert [p["current"] for p in e["peers"]] == [False, False, True]
    # the earlier sessions read the same with or without their PDC
    for x in ds.workouts[:-1]:
        full, light = IE.evaluate(ds, x, with_peers=False), IE.evaluate(ds, x, with_peers=False, pdc5=False)
        assert "pdc5" in full and "pdc5" not in light
        assert {k: v for k, v in full.items() if k != "pdc5"} == light
