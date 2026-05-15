# Feature Implementation Report: WKO5 Training Load Charts

## Summary

Implemented 5 WKO5-style training analytics charts for running: run-specific PMC (CTL/ATL/TSB/ACWR), Daily % of CTL, CTL Ramp Rate, Intensity Load (≥95%/≥103% FTP), and Running Volume Log. Reverse-engineered all formulas from WKO5 binary `.wko5chart` files using EWMA with CTL τ=42 and ATL τ=7.

## Strategy Used
- Size: L
- Subagent count: none (executed inline due to context continuity after compaction)
- Parallel batches: Tasks 3/4/5 logically parallel; Tasks 6–10 created sequentially

## Tasks Completed

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Algorithm: `compute_run_pmc` + `compute_intensity_load_series` | done | Added to `metrics.py` |
| 2 | Tests for new algorithms | done | 8 tests, all pass; fixed path collision with sys.path |
| 3 | DB schema: `elevation_gain_m` column | done | Idempotent migration via PRAGMA |
| 4 | FIT importer: elevation gain + intensity metrics | done | `high_intensity_95pct_s`, `high_intensity_103pct_s` keys |
| 5 | API endpoints: `/run-load`, `/intensity-load`, `/run-volume` | done | All in `analytics.py` |
| 6 | `RunLoadChart.tsx` | done | CTL/ATL/TSB + ACWR; `ReferenceArea` for risk zones |
| 7 | `DailyPctCtlChart.tsx` | done | Color-coded bars via `Cell` |
| 8 | `RampRateChart.tsx` | done | `ReferenceLine` at y=0 and y=7 |
| 9 | `IntensityLoadChart.tsx` | done | 4-series lines (chronic/acute × 95%/103%) |
| 10 | `RunVolumeLog.tsx` | done | Weekly bar chart + monthly table |
| 11 | `SeasonTab.tsx` updated | done | 4 collapsible sections with all charts |

## Integration Checks

| Check | Status | Notes |
|---|---|---|
| Type-check | pass | `tsc -b` zero errors |
| Lint | n/a | ESLint not run (no errors expected, no new lint rules) |
| Unit tests | pass | 22/22 tests pass (8 new) |
| Build | pass | `vite build` success, 208ms |
| Smoke test | deferred | Backend requires live DB with running workouts; UI renders empty-state cards correctly |

## Files Changed

| File | Action |
|---|---|
| `backend/engine/algorithms/metrics.py` | UPDATED — added `compute_run_pmc`, `compute_intensity_load_series` |
| `backend/tests/test_run_pmc.py` | CREATED — 8 unit tests |
| `backend/db/models.py` | UPDATED — `elevation_gain_m` field on `WorkoutFile` |
| `backend/db/database.py` | UPDATED — migration entry for `elevation_gain_m` |
| `backend/files/file_service.py` | UPDATED — elevation gain + intensity metrics in `_import_one_file` |
| `backend/api/analytics.py` | UPDATED — 3 new GET endpoints |
| `frontend/src/api/client.ts` | UPDATED — 5 new TypeScript interfaces |
| `frontend/src/api/hooks.ts` | UPDATED — 3 new React Query hooks |
| `frontend/src/components/charts/RunLoadChart.tsx` | CREATED |
| `frontend/src/components/charts/DailyPctCtlChart.tsx` | CREATED |
| `frontend/src/components/charts/RampRateChart.tsx` | CREATED |
| `frontend/src/components/charts/IntensityLoadChart.tsx` | CREATED |
| `frontend/src/components/charts/RunVolumeLog.tsx` | CREATED |
| `frontend/src/tabs/SeasonTab.tsx` | UPDATED — wired all 5 charts in collapsible sections |

## Deviations from Plan

- **`ReferenceBand` → `ReferenceArea`**: Recharts 3.8.1 does not export `ReferenceBand`; used `ReferenceArea` with `y1`/`y2` + `yAxisId="acwr"` for ACWR risk zones. Visually equivalent.
- **Test path fix**: `backend/tests/test_run_pmc.py` imports use full package path (`from backend.engine.algorithms.metrics import ...`) to avoid shadowing by `src/metrics.py` when both test directories run together.
- **ACWR test duration**: 90-day steady load gives ACWR ≈ 1.13 (CTL not converged); test updated to 365 days.
- **EWMA convergence test**: corrected input to 3600 s/day (1 hr) over 252 days (6τ) to test minutes output correctly.

## Follow-ups

- [ ] Smoke test with live running workout data once COROS sync is available
- [ ] Code-split Recharts to reduce 827 kB bundle (pre-existing issue)
- [ ] Consider adding `athlete_id` param support to SeasonTab for multi-athlete use
