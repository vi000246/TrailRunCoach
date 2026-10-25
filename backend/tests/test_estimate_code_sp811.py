"""SP-811: EngineConfig.load is not part of the estimate / PD code hashes.

codehash follows a method by name on the classes it reached: Dataset.__init__ builds an
EngineConfig and other reached code calls a `.load()` (json.load, activity_tags.load …), so
EngineConfig.load was hashed into estimate_code() / pd_code() although neither runs it — the
Dataset gets its config already loaded. An edit to it (SP-464, SP-481) refit every tenant's
estimates and PD. The config's values are in those keys by themselves; these tests pin both.

Synthetic FIT files only."""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timedelta, timezone

import pytest

from backend.engine import codehash as CH
from backend.engine.wko5expr import config as CFG
from backend.engine.wko5expr import fitdataset as FD
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.tests.fit_builder import build_run

TODAY = dt.date(2026, 9, 30)
SETTINGS = [{"effective_date": "2020-01-01", "weight_kg": 60, "run_ftp_w": 250,
             "threshold_pace_s_per_km": 300}]


def _edited_load():
    """EngineConfig.load as a deploy that edits its body would leave it (same name)."""
    def load(cls, path=None):
        return cls(parity=False)
    load.__module__ = CFG.__name__
    load.__qualname__ = "EngineConfig.load"
    return classmethod(load)


def test_editing_engine_config_load_keeps_the_estimate_and_pd_code(monkeypatch):
    CH._MEMO.clear()
    est, pd, own = FD.estimate_code(), FD.pd_code(), CH.code_hash(EngineConfig.load)
    monkeypatch.setattr(EngineConfig, "load", _edited_load())
    CH._MEMO.clear()
    assert CH.code_hash(EngineConfig.load) != own            # the edit is a real code change
    assert FD.estimate_code() == est
    assert FD.pd_code() == pd
    CH._MEMO.clear()


def test_the_estimate_and_pd_closures_leave_out_engine_config_load_and_what_only_it_reaches():
    est = CH.closure(*FD.estimate_roots(), skip=FD.CODE_SKIP)
    pd = CH.closure(*FD.pd_roots(), skip=FD.CODE_SKIP)
    for parts in (est, pd):
        assert "backend.engine.wko5expr.config.EngineConfig.load" not in parts
        assert "backend.engine.wko5expr.config.parity_allowed" not in parts          # only load calls it
        assert "class:backend.engine.wko5expr.config.EngineConfig" in parts           # the config object stays
        assert "backend.engine.wko5expr.config.EngineConfig.to_dict" in parts
    assert "backend.engine.thresholds.estimate" in est
    assert "backend.engine.racepower.athlete._pd_mftp" in pd


@pytest.fixture
def fit_root(tmp_path):
    root = tmp_path / "fit" / "coros"
    first = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
    for i in range(6):
        start = first + timedelta(days=9 * i)
        p = root / "2026" / f"{i:03d}.fit"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(build_run(start, seconds=1800, power=[230 + i] * 1800, hr=150, speed_m_s=3.0,
                                stryd=True))
    return root


def _ds(root, cfg):
    return FitFolderDataset(root, config=cfg, today=TODAY, classifications={},
                            athlete_settings=SETTINGS, estimate_thresholds=False, source="coros")


def test_a_changed_setting_still_changes_the_estimate_and_pd_keys(fit_root, monkeypatch):
    from backend.engine import power_source as PS
    monkeypatch.setattr(PS, "read_setting", lambda default=False: False)   # watch power off
    own = _ds(fit_root, EngineConfig(parity=False))
    knob = _ds(fit_root, EngineConfig(parity=False, pack_weight_pct=5.0))
    parity = _ds(fit_root, EngineConfig(parity=True))
    assert own._estimate_global()[0] != knob._estimate_global()[0]     # engine.json is in the key
    assert own._estimate_global()[0] != parity._estimate_global()[0]
    # the PD refit reads the config through what it uses: parity accepts watch power
    assert own.pd_memo.inputs()[1] != parity.pd_memo.inputs()[1]
