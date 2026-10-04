# Implementation Report: WKO5 Data Accuracy + Date Presets

## Summary
Implemented CTL/ATL seeding from WKO5 values, pace-based rTSS for GPS-only runs, WKO4 binary channel extraction for duration/distance, TSS backfill endpoint, calendar-aligned date preset buttons, and ConfigPage settings UI.

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Files Changed | ~10 | 10 |
| Test count | ~10 new | 9 new |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | DB schema — 3 new columns on athlete_settings | done | idempotent migration |
| 2 | SQLAlchemy model fields | done | |
| 3 | `compute_run_pmc()` seeding params | done | initial_ctl / initial_atl |
| 4 | `pace_rtss()` function | done | |
| 5 | athletes.py — settings GET/PUT + backfill-tss endpoint | done | |
| 6 | analytics.py run-load seeding | done | |
| 7 | test_run_pmc.py new tests | done | 5 new tests |
| 8 | wko4_reader.py — extract_wko4_metrics + test | done | multi-record-size probe with plausibility bounds |
| 9 | TypeScript types | done | AthleteSettingsResponse + BackfillResult |
| 10 | DateRangePicker — calendar presets | done | 本月/上月/近3月/近6月/今年/去年 with active highlight |
| 11 | hooks.ts — useBackfillTss | done | |
| 12 | ConfigPage — Run Load Settings + Backfill | done | |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (tsc) | pass | 0 errors |
| Unit Tests | pass | 19 tests, 0 failures |
| Build | pass | vite build success |
| Integration | N/A | dev server not started |
| Edge Cases | pass | WKO4 graceful None, pace_rtss zero guard |

## Files Changed

| File | Action | Notes |
|---|---|---|
| `backend/db/database.py` | UPDATED | 3 migration entries |
| `backend/db/models.py` | UPDATED | 3 AthleteSettings fields |
| `backend/engine/algorithms/metrics.py` | UPDATED | seeded pmc, pace_rtss |
| `backend/api/athletes.py` | UPDATED | settings ext + backfill-tss |
| `backend/api/analytics.py` | UPDATED | seeded run-load |
| `backend/files/wko4_reader.py` | UPDATED | extract_wko4_metrics |
| `backend/files/file_service.py` | UPDATED | use wko4 duration/distance |
| `backend/tests/test_migration.py` | CREATED | |
| `backend/tests/test_run_pmc.py` | UPDATED | 5 new tests |
| `backend/tests/test_wko4_extractor.py` | CREATED | |
| `frontend/src/api/client.ts` | UPDATED | 3 new types |
| `frontend/src/api/hooks.ts` | UPDATED | useBackfillTss |
| `frontend/src/components/DateRangePicker.tsx` | UPDATED | calendar presets |
| `frontend/src/pages/ConfigPage.tsx` | UPDATED | Run Load Settings card |

## Deviations from Plan

- **WKO4 record format**: Plan assumed 10-byte records (2-byte TS + 8-byte f64). Actual file had count × 10 > file size. Switched to probing multiple record sizes (10, 8, 6, 4) with plausibility range filtering. Returns None gracefully when no valid value found.

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `test_migration.py` | 2 | schema migration + ORM fields |
| `test_run_pmc.py` | 5 new | seeded PMC, pace_rtss |
| `test_wko4_extractor.py` | 4 | missing file, invalid, metrics, propagation |

## Next Steps
- [ ] Update Source SRS at `docs/srs/completed/wko5-data-accuracy-date-presets.srs.md` to reflect WKO4 record-size deviation
- [ ] Manually verify ConfigPage Run Load Settings in browser after dev server start
- [ ] Manual smoke test: set threshold pace, run backfill, check run-load chart reflects CTL seed
