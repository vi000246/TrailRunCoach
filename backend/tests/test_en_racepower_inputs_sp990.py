"""SP-990: the race calculator's 「你的數據」 card in English — the line under it read 「Sex male (設定頁) · …
(plus 6: 手錶推估功率（未採用）)」: where the sex came from (cp_protocols.sex_or_default), the label of the
watch-power runs left out (power_source.UNUSED_LABEL) and the power meter word were plain zh-TW strings in
athlete.derive's answer, which is cached for every language. They are M_() texts now: the same Chinese,
English when shown to an English request (api/racepower._py relocalizes the cache).

Synthetic data only: the demo athlete (backend/demo/build.py, small mode) built into tmp_path."""
from __future__ import annotations

import datetime as dt
import re

import pytest

from backend.i18n import Msg, relocalize, use_locale

CJK = re.compile(r"[㐀-鿿]")


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory):
    from backend.demo import build as B
    r = tmp_path_factory.mktemp("demo-sp990")
    B.build(r, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    return r


@pytest.fixture()
def ds(demo_root, monkeypatch):
    from backend.tests import demo_fixtures as F
    F.demo_env(monkeypatch, demo_root)
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    return wko5views._dataset()


def _fields(d):
    ps = d["power_source"]
    return {"sex_source": d["profile"]["sex_source"], "power_meter": d["profile"]["power_meter"],
            "unused_label": ps["unused_label"], **{f"unused[{i}].label_power": u["label_power"]
                                                    for i, u in enumerate(ps["unused"][:3])}}


def test_the_inputs_cards_words_follow_the_language(ds, tmp_path, monkeypatch):
    from backend.engine.racepower import athlete as A
    monkeypatch.setattr(A, "TRAINING_ENV_CACHE", tmp_path / "env.json")
    zh = A.derive(ds, dt.date.today(), fetch_weather=False)        # the cache: made in zh-TW, shown to anyone
    f = _fields(zh)
    assert f["sex_source"] == "設定頁" and f["unused_label"] == "手錶推估功率（未採用）", f
    assert zh["power_source"]["unused"], "the demo has watch-power runs left out"
    for k, v in f.items():
        assert isinstance(v, Msg) or not CJK.search(v), (k, v)
        shown = relocalize(v, "en")
        assert not CJK.search(shown), (k, shown)
        assert relocalize(v, "zh-TW") == v, k                    # the Chinese unchanged
    assert relocalize(f["sex_source"], "en") == "Settings page"
    assert relocalize(f["unused_label"], "en") == "watch-estimated power (not used)"


def test_the_sex_source_without_a_sex_on_file_and_the_comparison_folder(monkeypatch):
    from backend.engine import cp_protocols as CPP
    plan = type("P", (), {"profile": {}})()
    monkeypatch.setattr(CPP, "athlete_sex", lambda ds: None)
    sex, src = CPP.sex_or_default(type("D", (), {"plan": plan})())
    assert (sex, src) == ("male", "預設（男，推估）") and relocalize(src, "en") == "default (male, estimate)"
    monkeypatch.setattr(CPP, "athlete_sex", lambda ds: "female")
    sex, src = CPP.sex_or_default(type("D", (), {"plan": plan})())
    assert (sex, src) == ("female", "對照資料夾") and relocalize(src, "en") == "Comparison folder"
    with use_locale("en"):
        assert CPP.sex_or_default(type("D", (), {"plan": plan})())[1] == "Comparison folder"
