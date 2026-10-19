"""
SP-712: a settings change that rebuilds the Datasets (thresholds, weight, HR zones, a data
correction …) drops only the tenant's own cached Datasets (the key's last field is the tenant's
shared root, wko5views._dataset_key) — never another user's. The single-user app (the owner's
tenant only) rebuilds as before.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend import tenancy
from backend.api import wko5views as WV


class _Fake:
    workouts: list = []


def _tenant(root: Path, n: int) -> tenancy.Tenant:
    d = root / "users" / str(n)
    d.mkdir(parents=True, exist_ok=True)
    return tenancy.Tenant(id=f"u{n}", kind=tenancy.USER, root=d, shared=d, caps=tenancy.USER_CAPS)


def _key(t: tenancy.Tenant, source: str = "coros", stamp: str = "s1") -> tuple:
    return ('{"cfg": 1}', source, stamp, str(t.shared))


@pytest.fixture
def cache(monkeypatch):
    c = WV._DatasetCache(lambda *a: _Fake())
    monkeypatch.setattr(WV, "_dataset_cfg", c)

    def fill(*keys):
        with c._lock:                      # no put(): its budget must not evict anything here
            for k in keys:
                c._d[k] = _Fake()
    c.fill = fill
    return c


def test_a_threshold_change_drops_only_that_users_datasets(cache, tmp_path):
    a, b = _tenant(tmp_path, 2), _tenant(tmp_path, 3)
    ka, ka_wko5, kb = _key(a), _key(a, "wko5", "w1"), _key(b)
    cache.fill(ka, ka_wko5, kb)
    with tenancy.use(a):
        WV.plan_changed(True)
    assert cache.entries() == [kb]                         # B keeps its Dataset: no 50 s rebuild
    with tenancy.use(b):
        assert cache.peek(kb) is not None


def test_the_settings_save_path_and_a_weight_entry_drop_only_their_own(cache, tmp_path, monkeypatch):
    """What the endpoints call: api/plan._notify(True) (門檻、心率區間、配速、套用估算值) and
    api/weight._notify() (體重)."""
    from backend.api import plan as plan_api, weight as weight_api
    from backend.engine import plan_auto as PA
    monkeypatch.setattr(PA, "after_thresholds", lambda *a, **k: None)
    a, b = _tenant(tmp_path, 2), _tenant(tmp_path, 3)
    for notify in (lambda: plan_api._notify(True), weight_api._notify):
        cache.fill(_key(a), _key(b))
        with tenancy.use(a):
            notify()
        assert cache.entries() == [_key(b)], notify
        cache.cache_clear()


def test_an_event_edit_drops_nothing(cache, tmp_path):
    a, b = _tenant(tmp_path, 2), _tenant(tmp_path, 3)
    cache.fill(_key(a), _key(b))
    with tenancy.use(a):
        WV.plan_changed(False)
    assert cache.entries() == [_key(a), _key(b)]


def test_a_data_correction_drops_only_that_users_datasets(cache, tmp_path, monkeypatch):
    """The corrections file is the tenant's own (corrections.corrections_path: tenancy.base_path)."""
    from backend.engine.wko5expr import corrections as C
    a, b = _tenant(tmp_path, 2), _tenant(tmp_path, 3)
    monkeypatch.setattr(C, "CORRECTIONS_PATH", None)
    cache.fill(_key(a), _key(b))
    with tenancy.use(a):
        WV.approve_corrections(WV.ApproveBody(proposals=[]))
    assert cache.entries() == [_key(b)]
    cache.fill(_key(a))
    with tenancy.use(a):
        with pytest.raises(Exception):
            WV.undo_correction("nope")                     # 404: nothing removed, nothing dropped
    assert set(cache.entries()) == {_key(a), _key(b)}


def test_the_single_user_app_still_rebuilds_after_a_change(cache, tmp_path, monkeypatch):
    """No accounts: the owner's tenant is the only one — its Datasets go, the next request builds."""
    monkeypatch.setenv(tenancy.ENV_HOME, str(tmp_path / "home"))
    owner = tenancy.owner()
    built = []
    cache._build = lambda *k: built.append(k) or _Fake()
    with tenancy.use(owner):
        k = _key(owner)
        cache(*k)
        assert built == [k]
        cache(*k)
        assert built == [k]                                 # cached
        WV.plan_changed(True)
        assert cache.entries() == []
        cache(*k)
        assert built == [k, k]                              # rebuilt after the change
