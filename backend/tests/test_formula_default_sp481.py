"""SP-481: a runner who never chose a formula gets 我的算式's researched preset, not the
program's built-in defaults.

`EngineConfig.load()` with parity off: whatever the tenant's engine.json does not set (no file,
or a file without that key) comes from MOUNTAIN_PRESET — the set the 我的算式 button applies
(settings.html: PUT /config with GET /config's `mountain_preset`) — instead of the dataclass
defaults (TP's TSS, hrTSS over all recorded time, no climbing bonus). What the file sets wins.
WKO5 parity (a WKO5 folder and no stored choice, or the admin's choice) ignores the knobs and is
left exactly as it was, so its config — and every cache key holding it — does not move.
"""
from __future__ import annotations

import json

import pytest

from backend import tenancy
from backend.api import wko5views as WV
from backend.engine.wko5expr import config as C
from backend.engine.wko5expr.config import MOUNTAIN_PRESET, EngineConfig
from backend.tests.test_formula_admin_sp464 import (  # noqa: F401 — fixtures used by name
    _key_cfg, _user, _write, no_source, server, signin,
)

# the preset as a Dataset key / an API answer holds it (JSON: lists, not tuples)
PRESET_JSON = json.loads(json.dumps(MOUNTAIN_PRESET.to_dict()))
BUILT_IN = {"use_tp_tss": True, "hr_tss_moving_only": False, "elevation_tss_per_1000ft": 0.0}


@pytest.fixture
def no_wko5(monkeypatch, tmp_path):
    monkeypatch.setenv("WKO5_ATHLETE_DIR", str(tmp_path / "no-wko5"))


@pytest.fixture
def a_wko5_folder(monkeypatch, tmp_path):
    d = tmp_path / "wko5"
    d.mkdir()
    (d / "Someone.wko5athlete").write_bytes(b"")
    monkeypatch.setenv("WKO5_ATHLETE_DIR", str(d))


# ---------------------------------------------------------------------------- EngineConfig.load

def test_no_engine_json_is_the_my_formulas_preset(tmp_path, no_wko5):
    cfg = EngineConfig.load(tmp_path / "engine.json")
    assert cfg == MOUNTAIN_PRESET
    assert cfg.tp_tss is False and cfg.moving_hr_tss is True and cfg.elevation_tss_per_1000ft == 10.0
    assert cfg.elevation_bonus(1000 / C.FEET_PER_METRE, "trail running") == pytest.approx(10.0)
    assert EngineConfig() != MOUNTAIN_PRESET                     # the dataclass itself: unchanged


@pytest.mark.parametrize("stored", [{}, {"parity": False}, {"pack_weight_pct": 0.0}, {"power_spike_ratio": 0.0}])
def test_a_file_without_the_knobs_gets_the_preset_for_them(tmp_path, no_wko5, stored):
    p = tmp_path / "engine.json"
    p.write_text(json.dumps(stored), "utf-8")
    assert EngineConfig.load(p) == MOUNTAIN_PRESET


def test_what_the_runner_set_wins_knob_by_knob(tmp_path, no_wko5):
    p = tmp_path / "engine.json"
    p.write_text(json.dumps({"parity": False, "elevation_tss_per_1000ft": 5.0}), "utf-8")
    assert EngineConfig.load(p) == MOUNTAIN_PRESET.replace(elevation_tss_per_1000ft=5.0)
    # someone who set every knob to the old built-in values keeps them
    p.write_text(json.dumps({"parity": False, **BUILT_IN}), "utf-8")
    assert EngineConfig.load(p) == EngineConfig(parity=False, **BUILT_IN)
    p.write_text(json.dumps({"use_tp_tss": True}), "utf-8")         # one knob, no parity key, no WKO5
    assert EngineConfig.load(p) == MOUNTAIN_PRESET.replace(use_tp_tss=True)


def test_wko5_parity_is_left_exactly_as_it_was(tmp_path, a_wko5_folder):
    """A WKO5 folder and no stored choice: parity, with the dataclass knobs as before (they are
    ignored in parity; leaving them keeps the Dataset / estimate / chart keys of parity users)."""
    p = tmp_path / "engine.json"
    assert EngineConfig.load(p) == EngineConfig()                    # parity=True, built-in knobs
    p.write_text(json.dumps({"parity": True}), "utf-8")
    assert EngineConfig.load(p) == EngineConfig()
    p.write_text(json.dumps({"parity": False}), "utf-8")             # chose 我的算式 (old file, no knobs)
    assert EngineConfig.load(p) == MOUNTAIN_PRESET


def test_the_default_is_what_the_my_formulas_button_writes(server, no_source, no_wko5, monkeypatch):
    """The owner's app, no engine.json: GET /config already shows the button's set, and pressing
    我的算式 (PUT mountain_preset + parity off) changes nothing — not even the Dataset key."""
    with tenancy.use(tenancy.owner()):
        got = WV.get_config()
        assert got["config"] == got["mountain_preset"] == MOUNTAIN_PRESET.to_dict()
        before = WV._dataset_key()
        assert _key_cfg(before) == PRESET_JSON
        WV.put_config({**got["mountain_preset"], "parity": False})
        assert WV._dataset_key() == before


# ---------------------------------------------------------------------------- sign-in mode

def test_a_new_user_gets_the_preset(signin, no_source, no_wko5):
    t = _user(signin, "subscriber")
    with tenancy.use(t):
        assert EngineConfig.load() == MOUNTAIN_PRESET
        got = WV.get_config()
        assert got["can_switch"] is False and got["config"] == MOUNTAIN_PRESET.to_dict()
        assert _key_cfg(WV._dataset_key()) == PRESET_JSON
    assert not (t.root / "engine.json").exists()                       # nothing written for them


def test_a_users_old_parity_file_without_knobs_gets_the_preset(signin, no_source, no_wko5):
    """SP-464 turns their parity off; the knobs they never set are the preset, not the built-in ones."""
    t = _user(signin)
    _write(t, {"parity": True})
    with tenancy.use(t):
        assert EngineConfig.load() == MOUNTAIN_PRESET


def test_a_user_who_set_their_own_keeps_it(signin, no_source, no_wko5):
    t = _user(signin)
    _write(t, {"parity": False, **BUILT_IN})
    with tenancy.use(t):
        assert EngineConfig.load() == EngineConfig(parity=False, **BUILT_IN)
        assert _key_cfg(WV._dataset_key())["elevation_tss_per_1000ft"] == 0.0


def test_the_admins_parity_is_unchanged(signin, no_source, no_wko5):
    admin = tenancy.owner()
    with tenancy.use(admin):
        WV.put_config({"parity": True})
        assert EngineConfig.load().parity is True
        assert _key_cfg(WV._dataset_key(parity=False)) == PRESET_JSON   # ?parity=false: 我的算式
