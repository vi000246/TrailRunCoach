# Feature Implementation Report: WKO5 MVP — Tab Navigation, Config Page, Season Date Filter

## Summary

Added 4-tab navigation (Season / Activities / Config / AI) via zustand store, a `DateRangePicker` that drives PMC date range filtering, and a Config tab with FTP/LTHR form backed by a new `GET /api/v1/athletes/{id}/settings` endpoint that computes and returns power + HR zones.

## Strategy Used

- Size: M
- Subagent count: none (single-agent sequential — plan provided complete code, subagent overhead not justified)
- Parallel batches: none

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | Create zustand tab store | done | |
| 2 | Create TabNav component | done | |
| 3 | Create DateRangePicker component | done | |
| 4 | Update PmcChart to accept date props | done | Backward-compatible via default `= {}` |
| 5 | Create SeasonTab | done | |
| 6 | Add AthleteSettings types + hooks | done | |
| 7 | Backend GET settings + lthr in PUT | done | |
| 8 | Create ConfigTab | done | Deviated — see below |
| 9 | Update Dashboard.tsx | done | |

## Integration Checks

| Check | Status | Notes |
|---|---|---|
| Type-check | pass | Zero errors |
| Lint | pass | 1 error fixed before passing |
| Unit tests | N/A | Mode A, no automated tests |
| Build | pass | 722 kB bundle (pre-existing size, not caused by this change) |
| Smoke test | pending | Requires live backend + browser |

## Files Changed

| File | Action | Notes |
|---|---|---|
| `frontend/src/store/tabStore.ts` | CREATED | zustand tab store |
| `frontend/src/components/TabNav.tsx` | CREATED | 4-tab nav bar |
| `frontend/src/components/DateRangePicker.tsx` | CREATED | Preset + custom date inputs |
| `frontend/src/tabs/SeasonTab.tsx` | CREATED | DateRangePicker + PmcChart |
| `frontend/src/tabs/ConfigTab.tsx` | CREATED | FTP/LTHR form + zone tables |
| `frontend/src/components/PmcChart.tsx` | UPDATED | Added optional dateFrom/dateTo props |
| `frontend/src/pages/Dashboard.tsx` | UPDATED | TabNav + tab-conditional rendering |
| `frontend/src/api/client.ts` | UPDATED | PowerZone, HrZone, AthleteSettingsResponse, SettingsUpdatePayload types |
| `frontend/src/api/hooks.ts` | UPDATED | useAthleteSettings, useUpdateSettings |
| `backend/api/athletes.py` | UPDATED | GET settings endpoint + lthr in SettingsUpdate + PUT handler |

## Deviations from Plan

**ConfigTab form approach changed**: Plan used `useState` + `useEffect` to sync form values from server data. This triggered ESLint `react-hooks/set-state-in-effect` error. Fixed by switching to uncontrolled inputs with `defaultValue` and `key={settings?.effective_date ?? 'empty'}` to reset the form when settings load. Behavior is identical; pattern is cleaner.

## Issues Encountered

- ESLint `react-hooks/set-state-in-effect` on original `ConfigTab` implementation. Resolved by using `FormData` + uncontrolled inputs — standard React pattern for async-initialized forms.

## Tests Written

None (Mode A — 快建, balanced rigor). Manual browser verification is the acceptance gate.

## Follow-ups

- [ ] Smoke test: open http://localhost:5173, verify all 4 tabs, save FTP/LTHR, confirm zone tables render
- [ ] Activities tab implementation (ActivityList + ActivityDetail)
- [ ] WeeklyLoadChart on Season tab
- [ ] MCP server (`backend/mcp_server.py`)
