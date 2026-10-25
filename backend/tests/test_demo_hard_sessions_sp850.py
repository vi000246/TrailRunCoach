"""SP-850: the demo data's hard sessions look hard, the planned trail run is built, and the e2e athlete has
a ≥ 10-min Zone 3 stretch.

1. The tempo and interval runs' heart rate: an HR slow component (signals.slow_hr) on the hard sessions, a
   10′ warm-up and ~10′ cool-down, the tempo at 0.95 CP, the hill reps 2′ at 1.15 — their average HR is over
   the easy-run line (AeT 150 + 3), so the classifier (session_stimulus.verdict) no longer reads them
   「平均心率 ≤ 輕鬆跑上限+3」 = an easy run. The small build keeps their main set whole (6×4′, 2×12′).
2. The linked trail run (linked.pick_sessions): with no trail session on the anchor day, the week's long
   trail session is done early on a free morning up to the anchor.
3. The small build's tempo runs carry two 12′ Zone 3 stretches: the 閾值 tab has real data.
Synthetic only: tmp_path, never ~/.wko5coach."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from backend.demo import athlete as A
from backend.demo import generate as G
from backend.demo import linked as L
from backend.demo import signals as SG
from backend.engine import hr_profile as HP

EASY_LINE = A.AETHR + HP.EASY_TOL_BPM          # the demo's measured AeT + 3: over it is not an easy run
MONDAY = dt.date(2026, 10, 19)


# ---- 1. heart rate ---------------------------------------------------------------------------------------

def test_slow_hr_only_above_the_line_and_it_lingers():
    assert not SG.slow_hr(np.full(3600, 0.78)).any()                 # an easy run, an AeT test: nothing
    fs = np.concatenate([np.full(720, 0.92), np.full(600, 0.62)])     # 12′ at threshold, then 10′ jog
    s = SG.slow_hr(fs)
    assert 4.0 < s[719] < 10.0                                        # a few bpm after 12′
    assert s[-1] > 0.5 * s[719]                                       # still up after the 10′ jog


def test_the_small_build_keeps_a_hard_sessions_main_set():
    segs = G._segments("interval", 57)
    small = G._shrink(segs, 0.25, keep_main=True)
    assert [s.dur_s for s in small if s.label.startswith("rep")] == [240] * 6
    assert small[0].dur_s == segs[0].dur_s // 4 and small[-1].dur_s == segs[-1].dur_s // 4
    assert [s.dur_s for s in G._shrink(G._segments("easy", 40), 0.25, keep_main=False)] == [600]


@pytest.fixture(scope="module")
def season(tmp_path_factory):
    """52 weeks; each activity also gets `s120`, the highest HR held 120 s of what the FIT is written with."""
    from backend.engine import threshold_confidence as TC
    held, real = [], G.FW.encode_activity

    def spy(*a, **k):
        hr = k.get("hr")
        held.append(TC.held_peak(np.asarray(hr, dtype=float), TC.HOLD_LONG_S) if hr is not None and len(hr) else None)
        return real(*a, **k)
    G.FW.encode_activity = spy
    try:
        out = G.generate(tmp_path_factory.mktemp("sp850"), anchor=MONDAY, weeks=52)
    finally:
        G.FW.encode_activity = real
    for a, h in zip(out["activities"], held):
        a["s120"] = h
    return out


def _avg(season, kind):
    return [a["hr_avg"] for a in season["activities"] if a["kind"] == kind]


@pytest.mark.parametrize("kind", ["tempo", "interval", "interval_short", "hill"])
def test_hard_sessions_average_over_the_easy_line(season, kind):
    xs = _avg(season, kind)
    assert xs and min(xs) > EASY_LINE, (kind, min(xs))


def test_the_max_hr_evidence_is_unchanged(season):
    """threshold_confidence.hrmax_check: the CP test is the lone top 120-s hold (≥ 5 bpm over the next run), and
    that next run is > 8 bpm under the demo's 188 — the 設定 › 最大心率 card stays (SP-850 Q1-2). HRMAX − 6 for the
    long reps took tempo / 6×4′ holds to ~180 and the card went (e2e test_settings_threshold_confidence)."""
    from backend.engine import threshold_confidence as TC
    top = {k: max(a["s120"] for a in season["activities"] if a["kind"] == k and a.get("s120"))
           for k in ("cp_test", "tempo", "interval", "interval_short", "hill")}
    hard = max(v for k, v in top.items() if k != "cp_test")
    assert top["cp_test"] - hard > TC.HRMAX_OUTLIER_BPM, top
    assert A.HRMAX - hard > TC.HRMAX_GAP_BPM, top


def test_easy_runs_stay_easy(season):
    assert max(_avg(season, "easy")) < 146                            # under the lowest easy cap of the year


def test_the_weeks_other_runs_do_not_move_with_the_hard_sessions_length(monkeypatch):
    """The easy / long / trail runs stretch to the week's hours (season._share): they are counted against the
    hard sessions' length before SP-850 (SHARE_MINUTES), so shortening a hard session leaves them as they
    were — same minutes, so the same HR (a 52-week build compared with main: every easy, recovery, long,
    LSD, trail, hike run and the AeT test identical)."""
    from backend.demo import season as S
    kinds = ["easy", "tempo", "easy", "interval", "recovery", "long", "hill"]
    before = S._share(kinds, 6.0)
    monkeypatch.setitem(S.MINUTES, "tempo", (30, 30))
    monkeypatch.setitem(S.MINUTES, "interval", (40, 40))
    after = S._share(kinds, 6.0)
    flex = [i for i, k in enumerate(kinds) if k in S.FLEX]
    assert [after[i] for i in flex] == [before[i] for i in flex]
    assert after[1] == 30 and after[3] == 40                          # the hard sessions get their own length


# ---- 2. the linked trail run -----------------------------------------------------------------------------

def _s(day, kind, title, minutes=50, climb=None, state="active"):
    return {"day": day.isoformat(), "kind": kind, "title": title, "minutes": minutes, "climb_m": climb,
            "state": state, "uid": f"{day}-{kind}"}


def test_the_trail_session_of_the_anchor_day_is_taken_as_before():
    wed = MONDAY + dt.timedelta(days=2)
    p = L.pick_sessions([_s(wed, "easy", "越野跑", climb=400)], wed)
    assert p["trail_plan"][0]["title"] == "越野跑" and p["trail_plan"][1] == wed


def test_without_one_the_weeks_long_trail_run_is_done_early_on_a_free_morning():
    tue = MONDAY + dt.timedelta(days=1)
    sat = MONDAY + dt.timedelta(days=5)
    ss = [_s(tue, "easy", "輕鬆跑"), _s(sat, "long", "LSD（山路）", 160, 1400)]
    p = L.pick_sessions(ss, tue, frozenset())
    assert p["trail_plan"][0]["title"] == "LSD（山路）" and p["trail_plan"][1] == MONDAY   # Tuesday has its easy run
    assert "trail_plan" not in L.pick_sessions(ss, tue, frozenset({MONDAY.isoformat()}))   # Monday has an activity


def test_on_the_interval_sessions_day_both_runs_go_there():
    wed = MONDAY + dt.timedelta(days=2)
    ss = [_s(wed, "quality", "恢復週 fartlek 4×1 分", 37), _s(wed + dt.timedelta(days=3), "long", "LSD（山路）", 50, 400)]
    p = L.pick_sessions(ss, wed, frozenset({(wed - dt.timedelta(days=k)).isoformat() for k in (1, 2)}))
    assert p["trail_plan"][1] == wed and p["interval_plan"][1] == wed
    assert p["trail_plan"][2] < p["interval_plan"][2]                 # the trail run in the morning


# ---- the small build (anchor today, as the e2e athlete) ------------------------------------------------------

PROBE = r'''
import json, os, sys
from pathlib import Path
base = Path(sys.argv[1])
from backend.demo.build import child_env
os.environ.update(child_env(base))
from backend.api import wko5views as V
from backend.engine import threshold_segments as TS
from backend.engine import workout_review as WR
man = json.loads((base / "demo_manifest.json").read_text("utf-8"))
kinds = {Path(a["file"]).name: a["kind"] for a in man["activities"]}
ds = V._dataset(None)
out = []
for w in ds.workouts:
    k = kinds.get(Path(str(w.entry.file)).name)
    if k in ("tempo", "interval", "interval_short", "hill"):
        c = WR.classify(ds, w)
        segs = TS.segments(ds, w).get("segs") or []
        out.append({"kind": k, "type": c["type"], "why": (c.get("stim") or {}).get("why"),
                    "z3_min": [round(s["duration_s"] / 60, 1) for s in segs]})
print("PROBE" + json.dumps(out, ensure_ascii=False))
'''


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    from backend.demo import build as B
    root = tmp_path_factory.mktemp("sp850small")
    base = B.build(root, anchor=dt.date.today(), weeks=8, small=True, warm=False, switch=False)
    r = subprocess.run([sys.executable, "-c", PROBE, str(base)], env=B.child_env(base), capture_output=True, encoding="utf-8",
                       text=True, cwd=str(Path(B.__file__).resolve().parents[2]), timeout=600)
    line = next((x for x in r.stdout.splitlines() if x.startswith("PROBE")), None)
    assert line, r.stderr[-2000:]
    return json.loads((base / "demo_manifest.json").read_text("utf-8")), json.loads(line[5:])


def test_small_build_tempo_and_interval_runs_are_hard(small):
    _, rows = small
    hard = [r for r in rows if r["kind"] in ("tempo", "interval")]
    assert hard and all(r["type"] in ("quality", "hard_long", "test_cp") for r in hard), hard
    assert not any("輕鬆跑上限" in (r["why"] or "") for r in rows), rows


def test_small_build_has_a_ten_minute_zone_3_stretch(small):
    _, rows = small
    assert any(m >= 10 for r in rows if r["kind"] == "tempo" for m in r["z3_min"]), rows


def test_small_build_has_the_linked_trail_run(small):
    m, _ = small
    if dt.date.today().weekday() == 6:
        pytest.skip("on a Sunday the week's long run was done on Monday (a generated LSD): nothing is left to link")
    tr = next((a for a in m["activities"] if a.get("kind") == "trail_plan"), None)
    assert tr, "no linked trail run"
    assert tr.get("linked") and tr["linked"]["title"] == tr["session"]["title"], tr
