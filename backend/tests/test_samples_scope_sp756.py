"""SP-756: GET /workouts/{idx}/samples (the per-point HR, power and GPS behind the route map and
the hover sync) reaches only as far back as the plan's charts — the same rule as 活動判讀 and
活動比較 (SP-713, membership.require_workout_age)."""
from __future__ import annotations

from types import SimpleNamespace

from backend.tests.test_membership_sp330 import _user, server  # noqa: F401 — fixtures
from backend.tests.test_quota_rules_sp713 import F, P, _fresh_site, client  # noqa: F401 — fixtures


def _ds(days_back):
    ws = [SimpleNamespace(day=1000.0 - d, idx=i) for i, d in enumerate(days_back)]
    reads = []

    def channel(i, name):
        reads.append((i, name))
        return None
    return SimpleNamespace(today=1000.0, workouts=ws, channel=channel, reads=reads)


def test_samples_follow_the_charts_range(client, monkeypatch):  # noqa: F811
    from backend.api import wko5views as WV
    ds = _ds([10, 89, 200, 800])
    monkeypatch.setattr(WV, "_dataset", lambda parity=None, source=None: ds)
    for i in (0, 1):                                        # inside 90 days: the free tier gets them
        r = client.get(f"/api/v1/app/workouts/{i}/samples", headers=F)
        assert r.status_code == 200 and r.json()["workout"] == i
    ds.reads.clear()
    for i in (2, 3):                                        # older: refused, nothing read
        r = client.get(f"/api/v1/app/workouts/{i}/samples", headers=F)
        d = r.json()["detail"]
        assert r.status_code == 403 and d["code"] == "SCOPE_REQUIRED" and d["upgrade"] is True
    assert ds.reads == []
    for i in range(4):                                      # a subscriber: every activity
        assert client.get(f"/api/v1/app/workouts/{i}/samples", headers=P).status_code == 200


def test_samples_still_404_an_unknown_workout(client, monkeypatch):  # noqa: F811
    from backend.api import wko5views as WV
    ds = _ds([10])
    monkeypatch.setattr(WV, "_dataset", lambda parity=None, source=None: ds)
    assert client.get("/api/v1/app/workouts/5/samples", headers=F).status_code == 404
