# Real-data suite (opt-in)

The default run (`pytest backend/tests`) uses synthetic data (`fit_builder.py`,
`wko5_fakes.py`) and small frozen fixtures (`backend/tests/fixtures/`) only. It
never reads the WKO5 athlete folder (`~/WKO5`) or the app's data folder
(`~/.wko5coach`): `backend/tests/conftest.py` points home at a temp folder and
an audit hook fails any test that opens, lists or writes a path under either.

The tests here compare our numbers with WKO5's own (WKO5 is the comparison
baseline, not the test data), so they need the real athlete folder:

```
WKO5COACH_REALDATA=1 pytest backend/tests/realdata
```

(PowerShell: `$env:WKO5COACH_REALDATA = '1'; pytest backend/tests/realdata`.)

- The athlete folder is `WKO5_ATHLETE_DIR` if set, else the first folder in
  `~/WKO5` holding a `*.wko5athlete`. Without one every test skips.
- Home is still a temp folder: the dataset caches, plan.json and the rest are
  rebuilt there, so the run is slow but leaves `~/.wko5coach` untouched.
  Reads are allowed (the athlete folder; the TP leak guard reads
  `~/.wko5coach/tp_client.json`); any write under `~/WKO5` or `~/.wko5coach`
  fails the test (the guard is `backend/tests/_guard.py`).
- Without `WKO5COACH_REALDATA=1` this folder is not collected (and skips if
  named explicitly).

| file | moved from | needs |
|---|---|---|
| test_real_blackouts.py | test_blackouts.py | week_plan on the athlete's dataset |
| test_real_chart_metrics.py | test_chart_metrics.py | view expressions vs an independent recomputation |
| test_real_cp_protocols.py | test_cp_protocols.py | week_plan per CP protocol |
| test_real_drift_basis.py | test_drift_basis.py | drift card / season charts on three real runs |
| test_real_equivalence.py | test_equivalence.py | backtest on the athlete's trail / hike runs |
| test_real_fit_to_channels.py | test_fit_to_channels.py | FIT → channels parity with WKO5's imports |
| test_real_plan_prefs.py | test_plan_prefs.py | week_plan with prefs |
| test_real_racepower_backtest2.py | test_racepower_backtest2.py | classification on real activities |
| test_real_tp_client_sealed.py | test_tp_client_sealed.py | leak guard: reads the real `~/.wko5coach/tp_client.json` |
| test_real_wko4_extractor.py | test_wko4_extractor.py | one real .wko4 |
| test_real_wko4_file.py | test_wko4_file.py (whole file) | .wko4 channel stats vs WKO5's |
| test_real_wko5_{elevation,hr,meanmax,pace,power,time,perf}.py | test_wko5_*.py | each metric vs WKO5's stored value |
| test_real_wko5_pipeline.py | test_wko5_pipeline_golden.py (whole file) | FIT → NP / hrTSS / PMC vs WKO5 |

Before this suite existed, `test_cp_protocols.py::test_real_2026_09_30_test_falls_back_to_one_bout`
read the COROS FIT from `~/.wko5coach/fit`; it now uses the frozen
`fixtures/cp_test_2026-09-30.json.gz` (1-s power + HR only, ~4 KB) and stays
in the default run.
