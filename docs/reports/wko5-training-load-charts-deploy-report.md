# Deploy Report: wko5-training-load-charts

## Metadata
- **Deployed at**: 2026-05-15T15:12Z
- **Environment**: localhost (Docker Compose)
- **Mode**: LIVE
- **Deploy Type**: Docker image rebuild (./deploy.sh)
- **Status**: SUCCESS
- **Git commit deployed**: 074cfdcfd368d0825045458acdf6560f68c61911

## Source Artifacts
- Deploy Guide: docs/deploy/wko5coach.deploy.md
- Implementation Report: docs/reports/wko5-training-load-charts-feature-report.md
- Source Linear Issue: N/A

## Steps Executed

| # | Step | Command | Exit | Status |
|---|------|---------|------|--------|
| 1 | Git commit | `git commit -m "feat: add WKO5 training load charts..."` | 0 | done |
| 2 | Docker rebuild + restart | `./deploy.sh` | 0 | done |

## Verification Results

| Item | Result |
|---|---|
| Container started | `wko5coach Started` (Docker output) |
| `/api/v1/analytics/run-load` | 200 OK — returns PMC series with date/ctl/atl/tsb/acwr |
| `/api/v1/analytics/intensity-load` | 200 OK — returns empty series (no running power data yet) |
| `/api/v1/analytics/run-volume` | 200 OK — returns weekly/monthly volume data |
| Frontend build | TypeScript 0 errors, vite built in 236ms |

## Notes
- `intensity-load` returns empty series until running workouts with power data are synced (COROS or .fit with power channel)
- Bundle size warning (>500 kB) is pre-existing from Recharts — not introduced by this change
