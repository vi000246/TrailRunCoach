# Plan: WKO5 MVP — Tab Navigation, Config Page, Season Date Filter

> **For agentic workers:** `/prp-implement` will route this plan to the matching skill based on `Metadata.Type` below. Steps use checkbox (`- [ ]`) syntax for tracking.

## Summary

Converts the existing single-page WKO5 Coach dashboard into a 4-tab SPA (Season / Activities / Config / AI) using a zustand store. Season tab gains a `DateRangePicker` that passes `date_from`/`date_to` into the existing PMC chart. Config tab provides FTP and LTHR input fields, backed by a new `GET /api/v1/athletes/{id}/settings` endpoint that returns computed power and HR zones.

## User Story

As an athlete, I want to switch between a Season overview with date-range filtering and a Config page where I can set FTP and LTHR, so that I can see training load for any period and keep my zones up to date.

## Problem → Solution

Single flat dashboard with no navigation and no settings UI → 4-tab layout with date-filtered PMC and a working FTP/LTHR config form.

## Metadata

- **Module**: frontend
- **Parent Plan**: N/A
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md`
- **Source SRS**: `docs/spec/wko5-frontend-enhancement.spec.md`
- **Source Linear Issue**: N/A
- **Type**: feature
- **Size**: M
- **Complexity**: Medium
- **Rigor**: balanced
- **Mode**: A — 快建
- **TDD**: off
- **Commit cadence**: per-task
- **Estimated Files**: 10 (5 new, 5 updated)

---

## UX Design

### Before

```
┌─────────────────────────────────────────────────────────┐
│ WKO5 Coach                               ⟳ Scan Files  │
├─────────────────────────────────────────────────────────┤
│  [Coros Sync panel]                                     │
│  [PMC Chart — always 1Y, no controls]                   │
│  [DashboardGrid widgets]                                │
└─────────────────────────────────────────────────────────┘
```

### After

```
┌─────────────────────────────────────────────────────────┐
│ WKO5 Coach                               ⟳ Scan Files  │
├──────────┬─────────────┬────────┬────────┬─────────────┤
│ Season ● │ Activities  │ Config │ AI     │             │
├─────────────────────────────────────────────────────────┤
│  (Season tab)                                           │
│  [Coros Sync panel]                                     │
│  [1M] [3M] [6M] [1Y]  YYYY-MM-DD — YYYY-MM-DD          │
│  [PMC Chart — filtered by chosen range]                 │
│  [DashboardGrid widgets]                                │
│                                                         │
│  (Config tab)                                           │
│  FTP (watts): [____]                                    │
│  LTHR (bpm):  [____]   [Save]                           │
│  Power Zones table                                      │
│  HR Zones table                                         │
└─────────────────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Tab bar | None | 4 tabs below header | Active tab has purple underline |
| PMC date range | Fixed 1Y | Preset buttons + custom inputs | State lives in `SeasonTab` |
| FTP/LTHR | No UI | Config tab form | Saves via existing PUT endpoint |
| Zone tables | No UI | Displayed below form after save | Computed server-side |
| Activities/AI tabs | N/A | Placeholder "coming soon" | Out of scope for this plan |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `frontend/src/pages/Dashboard.tsx` | 1–143 | Layout to extend with TabNav |
| P0 | `frontend/src/api/hooks.ts` | 1–83 | Hook pattern to follow exactly |
| P0 | `backend/api/athletes.py` | 48–76 | PUT pattern + SettingsUpdate model |
| P0 | `backend/api/pmc.py` | 14–47 | Async endpoint pattern with `Depends(get_db)` |
| P1 | `frontend/src/components/PmcChart.tsx` | 1–88 | Add optional date props |
| P1 | `frontend/src/api/client.ts` | 1–51 | Where to add new types |
| P1 | `backend/db/database.py` | 19–38 | `_migrate_schema` pattern (no changes needed here) |
| P2 | `backend/db/models.py` | 22–31 | `AthleteSettings` already has `lthr` column |
| P2 | `frontend/package.json` | all | zustand v5, recharts, react-query already installed |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| zustand v5 create | Installed | `create<Store>((set) => ...)` — same API as v4 |
| `<input type="date">` | MDN | `value` must be `YYYY-MM-DD`; use `min`/`max` to constrain |

---

## Patterns to Mirror

### NAMING_CONVENTION
```ts
// SOURCE: frontend/src/api/hooks.ts:21-18 and components/PmcChart.tsx:13
// Hooks: use + PascalCase noun. Components: PascalCase function exports.
export function usePmc(params?: { date_from?: string; date_to?: string }) { ... }
export function PmcChart() { ... }
```

### REACT_QUERY_HOOK
```ts
// SOURCE: frontend/src/api/hooks.ts:21-23
export function usePmc(params?: { date_from?: string; date_to?: string }) {
  return useQuery<{ series: PmcPoint[] }>({
    queryKey: ['pmc', params],
    queryFn: () => api.get('/pmc', { params }).then(r => r.data),
  })
}
```

### MUTATION_HOOK
```ts
// SOURCE: frontend/src/api/hooks.ts:46-53
export function useScan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/scan'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workouts'] }),
  })
}
```

### TAILWIND_CARD
```tsx
// SOURCE: frontend/src/pages/Dashboard.tsx:53
<div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
```

### TAILWIND_INPUT
```tsx
// SOURCE: frontend/src/pages/Dashboard.tsx:76-81
<input
  type="email"
  className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-48"
/>
```

### FASTAPI_ASYNC_ENDPOINT
```python
# SOURCE: backend/api/pmc.py:14-47
@router.get("")
async def get_pmc(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WorkoutFile.workout_date, WorkoutMetric.value)
        .join(...)
        .where(...)
    )
    rows = result.all()
    return {"series": rows}
```

### ZUSTAND_STORE
```ts
// Pattern: zustand v5 (installed, no existing stores in codebase)
import { create } from 'zustand'
interface MyStore { value: string; setValue: (v: string) => void }
export const useMyStore = create<MyStore>((set) => ({
  value: '',
  setValue: (v) => set({ value: v }),
}))
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `frontend/src/store/tabStore.ts` | CREATE | zustand tab state (activeTab + setTab) |
| `frontend/src/components/TabNav.tsx` | CREATE | Tab bar UI, reads from tabStore |
| `frontend/src/components/DateRangePicker.tsx` | CREATE | Preset buttons + date inputs |
| `frontend/src/tabs/SeasonTab.tsx` | CREATE | DateRangePicker + PmcChart with range |
| `frontend/src/tabs/ConfigTab.tsx` | CREATE | FTP/LTHR form + zone tables |
| `frontend/src/components/PmcChart.tsx` | UPDATE | Accept optional `dateFrom`/`dateTo` props |
| `frontend/src/pages/Dashboard.tsx` | UPDATE | Add TabNav, render active tab content |
| `frontend/src/api/client.ts` | UPDATE | Add `AthleteSettingsResponse`, `PowerZone`, `HrZone` types |
| `frontend/src/api/hooks.ts` | UPDATE | Add `useAthleteSettings`, `useUpdateSettings` |
| `backend/api/athletes.py` | UPDATE | Add GET settings endpoint; add `lthr` to SettingsUpdate |

## NOT Building

- Activities tab (placeholder "coming soon" only)
- AI tab (placeholder "coming soon" only)
- WeeklyLoadChart
- ActivityList / ActivityDetail
- MMP compare chart
- MCP server (`backend/mcp_server.py`)
- DB schema migration (AthleteSettings.lthr already exists in models.py:30)
- Multi-athlete support (athlete_id hardcoded to 1 throughout)

---

## Step-by-Step Tasks

### Task 1: Create zustand tab store

- **ACTION**: Create `frontend/src/store/tabStore.ts` with 4 tab IDs and active tab state.
- **IMPLEMENT**:
  ```ts
  // frontend/src/store/tabStore.ts
  import { create } from 'zustand'

  export type Tab = 'season' | 'activities' | 'config' | 'ai'

  interface TabStore {
    activeTab: Tab
    setTab: (tab: Tab) => void
  }

  export const useTabStore = create<TabStore>((set) => ({
    activeTab: 'season',
    setTab: (tab) => set({ activeTab: tab }),
  }))
  ```
- **MIRROR**: ZUSTAND_STORE pattern above.
- **IMPORTS**: `import { create } from 'zustand'` — package already installed.
- **GOTCHA**: zustand v5 uses `create` directly (no `createStore` wrapper needed). No `immer` middleware required here.
- **VALIDATE**: TypeScript compiles — `npx tsc --noEmit` passes.

---

### Task 2: Create TabNav component

- **ACTION**: Create `frontend/src/components/TabNav.tsx`. Renders 4 tab buttons; active tab gets purple bottom border.
- **IMPLEMENT**:
  ```tsx
  // frontend/src/components/TabNav.tsx
  import { useTabStore, type Tab } from '../store/tabStore'

  const TABS: { id: Tab; label: string }[] = [
    { id: 'season',     label: 'Season' },
    { id: 'activities', label: 'Activities' },
    { id: 'config',     label: 'Config' },
    { id: 'ai',         label: 'AI' },
  ]

  export function TabNav() {
    const { activeTab, setTab } = useTabStore()
    return (
      <nav className="flex gap-1 bg-gray-900 border-b border-gray-800 px-6">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm font-medium transition border-b-2 -mb-px ${
              activeTab === t.id
                ? 'border-purple-500 text-purple-400'
                : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>
    )
  }
  ```
- **MIRROR**: TAILWIND_CARD card style for surrounding containers; purple-400 / gray-500 color tokens already used in Dashboard.
- **GOTCHA**: `-mb-px` makes the active border-b cover the nav's own `border-b border-gray-800`, creating the classic "selected tab" effect.
- **VALIDATE**: Active tab shows purple underline; clicking changes tab.

---

### Task 3: Create DateRangePicker component

- **ACTION**: Create `frontend/src/components/DateRangePicker.tsx`. Four preset buttons (1M/3M/6M/1Y) plus two `<input type="date">` for custom range.
- **IMPLEMENT**:
  ```tsx
  // frontend/src/components/DateRangePicker.tsx
  export interface DateRange {
    from: string  // YYYY-MM-DD
    to: string
  }

  interface Props {
    value: DateRange
    onChange: (range: DateRange) => void
  }

  const toIso = (d: Date) => d.toISOString().slice(0, 10)
  const today = () => toIso(new Date())
  const daysAgo = (n: number) => {
    const d = new Date()
    d.setDate(d.getDate() - n)
    return toIso(d)
  }

  const PRESETS = [
    { label: '1M', days: 30 },
    { label: '3M', days: 90 },
    { label: '6M', days: 180 },
    { label: '1Y', days: 365 },
  ] as const

  export function DateRangePicker({ value, onChange }: Props) {
    return (
      <div className="flex items-center gap-3 flex-wrap">
        {PRESETS.map(p => (
          <button
            key={p.label}
            onClick={() => onChange({ from: daysAgo(p.days), to: today() })}
            className="px-3 py-1 text-xs bg-gray-800 hover:bg-gray-700 rounded transition text-gray-300"
          >
            {p.label}
          </button>
        ))}
        <input
          type="date"
          value={value.from}
          max={value.to}
          onChange={e => onChange({ ...value, from: e.target.value })}
          className="px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded text-gray-300"
        />
        <span className="text-xs text-gray-500">—</span>
        <input
          type="date"
          value={value.to}
          min={value.from}
          max={today()}
          onChange={e => onChange({ ...value, to: e.target.value })}
          className="px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded text-gray-300"
        />
      </div>
    )
  }
  ```
- **MIRROR**: TAILWIND_INPUT pattern; `bg-gray-800 border border-gray-700 rounded text-gray-100`.
- **GOTCHA**: `<input type="date">` value must be `YYYY-MM-DD` (ISO format). `max` on `from` input prevents selecting a start after end; `min` on `to` prevents selecting end before start.
- **VALIDATE**: Clicking "3M" updates both date inputs; editing inputs directly updates displayed dates.

---

### Task 4: Update PmcChart to accept date props

- **ACTION**: Modify `frontend/src/components/PmcChart.tsx` — add optional `dateFrom`/`dateTo` props and pass them to `usePmc`.
- **IMPLEMENT** (diff):
  ```tsx
  // frontend/src/components/PmcChart.tsx
  // Change line 13 from:
  export function PmcChart() {
    const { data, isLoading, isError } = usePmc()

  // To:
  interface Props {
    dateFrom?: string
    dateTo?: string
  }

  export function PmcChart({ dateFrom, dateTo }: Props = {}) {
    const params = dateFrom || dateTo
      ? { date_from: dateFrom, date_to: dateTo }
      : undefined
    const { data, isLoading, isError } = usePmc(params)
  ```
  Leave everything else in the file unchanged.
- **MIRROR**: REACT_QUERY_HOOK — `usePmc` already accepts `{ date_from?, date_to? }`.
- **GOTCHA**: Default parameter `= {}` means callers that don't pass props still work — backward compatible with existing usages in `DashboardGrid` widgets.
- **VALIDATE**: `PmcChart` without props still renders; `PmcChart dateFrom="2026-02-15" dateTo="2026-05-15"` triggers a query with those params (visible in Network tab).

---

### Task 5: Create SeasonTab

- **ACTION**: Create `frontend/src/tabs/SeasonTab.tsx`. Manages date range state; renders `DateRangePicker` + `PmcChart`.
- **IMPLEMENT**:
  ```tsx
  // frontend/src/tabs/SeasonTab.tsx
  import { useState } from 'react'
  import { DateRangePicker, type DateRange } from '../components/DateRangePicker'
  import { PmcChart } from '../components/PmcChart'

  const toIso = (d: Date) => d.toISOString().slice(0, 10)
  const daysAgo = (n: number) => {
    const d = new Date()
    d.setDate(d.getDate() - n)
    return toIso(d)
  }

  export function SeasonTab() {
    const [range, setRange] = useState<DateRange>({
      from: daysAgo(365),
      to: toIso(new Date()),
    })

    return (
      <div className="space-y-4">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <DateRangePicker value={range} onChange={setRange} />
        </div>
        <PmcChart dateFrom={range.from} dateTo={range.to} />
      </div>
    )
  }
  ```
- **MIRROR**: TAILWIND_CARD for the DateRangePicker wrapper.
- **GOTCHA**: `useState` default is computed once at mount. `daysAgo` uses `new Date()` at call time — correct behavior.
- **VALIDATE**: PMC chart re-fetches when preset or custom dates change (react-query key changes).

---

### Task 6: Add types and hooks for athlete settings

- **ACTION**: Update `frontend/src/api/client.ts` with settings types, then update `frontend/src/api/hooks.ts` with `useAthleteSettings` and `useUpdateSettings`.
- **IMPLEMENT** — client.ts additions (append to file):
  ```ts
  // Append to frontend/src/api/client.ts

  export interface PowerZone {
    zone: number
    label: string
    min_w: number
    max_w: number | null
  }

  export interface HrZone {
    zone: number
    label: string
    min_bpm: number
    max_bpm: number | null
  }

  export interface AthleteSettingsResponse {
    athlete_id: number
    effective_date: string
    ftp_w: number | null
    lthr: number | null
    weight_kg: number | null
    power_zones: PowerZone[]
    hr_zones: HrZone[]
  }

  export interface SettingsUpdatePayload {
    ftp_w?: number
    lthr?: number
    weight_kg?: number
  }
  ```
- **IMPLEMENT** — hooks.ts additions (append to file):
  ```ts
  // Append to frontend/src/api/hooks.ts
  import type { ..., AthleteSettingsResponse, SettingsUpdatePayload } from './client'

  export function useAthleteSettings(athleteId = 1) {
    return useQuery<AthleteSettingsResponse>({
      queryKey: ['athlete_settings', athleteId],
      queryFn: () => api.get(`/athletes/${athleteId}/settings`).then(r => r.data),
      retry: false,
    })
  }

  export function useUpdateSettings(athleteId = 1) {
    const qc = useQueryClient()
    return useMutation<{ saved: boolean }, Error, SettingsUpdatePayload>({
      mutationFn: (body) => api.put(`/athletes/${athleteId}/settings`, body).then(r => r.data),
      onSuccess: () => qc.invalidateQueries({ queryKey: ['athlete_settings', athleteId] }),
    })
  }
  ```
- **MIRROR**: REACT_QUERY_HOOK + MUTATION_HOOK patterns exactly.
- **GOTCHA**: `retry: false` on `useAthleteSettings` — if no settings exist yet the backend returns 404. Without `retry: false`, react-query retries 3× before surfacing the error, creating unnecessary delay for new users.
- **VALIDATE**: TypeScript compiles with no errors on these new types.

---

### Task 7: Add GET settings endpoint + lthr to PUT (backend)

- **ACTION**: Update `backend/api/athletes.py`:
  1. Add `lthr: Optional[int] = None` to `SettingsUpdate`.
  2. Add `if body.lthr is not None: s.lthr = body.lthr` in the PUT handler.
  3. Add new `GET /{athlete_id}/settings` endpoint that computes power + HR zones server-side.
- **IMPLEMENT**:
  ```python
  # backend/api/athletes.py — full file replacement shown; only the relevant additions:

  # 1. Add HTTPException import (line 1 imports block):
  from fastapi import APIRouter, Depends, HTTPException

  # 2. Updated SettingsUpdate model (replaces existing at line 48):
  class SettingsUpdate(BaseModel):
      ftp_w: Optional[float] = None
      lthr: Optional[int] = None
      weight_kg: Optional[float] = None
      effective_date: Optional[date] = None

  # 3. In PUT handler, add after `if body.weight_kg is not None:` block (after line 68):
      if body.lthr is not None:
          s.lthr = body.lthr

  # 4. New GET endpoint — add BEFORE the PUT handler so FastAPI matches correctly:
  @router.get("/{athlete_id}/settings")
  async def get_settings(athlete_id: int, db: AsyncSession = Depends(get_db)):
      result = await db.execute(
          select(AthleteSettings)
          .where(AthleteSettings.athlete_id == athlete_id)
          .order_by(AthleteSettings.effective_date.desc())
      )
      s = result.scalars().first()
      if s is None:
          raise HTTPException(status_code=404, detail="NO_SETTINGS")

      ftp = s.ftp_w
      lthr = s.lthr

      power_zones = []
      if ftp:
          # Coggan 7-zone collapsed to 5: breakpoints at 55/75/90/105 % FTP
          breakpoints = [int(ftp * p) for p in (0.55, 0.75, 0.90, 1.05)]
          labels = ["Recovery", "Endurance", "Tempo", "Threshold", "VO2max+"]
          for i in range(5):
              power_zones.append({
                  "zone": i + 1,
                  "label": labels[i],
                  "min_w": breakpoints[i - 1] if i > 0 else 0,
                  "max_w": breakpoints[i] - 1 if i < 4 else None,
              })

      hr_zones = []
      if lthr:
          # Friel 7-zone collapsed to 5: breakpoints at 85/90/95/100 % LTHR
          breakpoints = [int(lthr * p) for p in (0.85, 0.90, 0.95, 1.00)]
          labels = ["Recovery", "Aerobic", "Tempo", "Threshold", "Anaerobic"]
          for i in range(5):
              hr_zones.append({
                  "zone": i + 1,
                  "label": labels[i],
                  "min_bpm": breakpoints[i - 1] if i > 0 else 0,
                  "max_bpm": breakpoints[i] - 1 if i < 4 else None,
              })

      return {
          "athlete_id": athlete_id,
          "effective_date": s.effective_date.isoformat(),
          "ftp_w": ftp,
          "lthr": lthr,
          "weight_kg": s.weight_kg,
          "power_zones": power_zones,
          "hr_zones": hr_zones,
      }
  ```
- **MIRROR**: FASTAPI_ASYNC_ENDPOINT — `Depends(get_db)`, `await db.execute(select(...).where(...))`, `.scalars().first()`.
- **GOTCHA 1**: The new GET must be declared BEFORE the existing PUT in the file. FastAPI route ordering matters — both use `/{athlete_id}/settings` path; placing GET after PUT causes no issue in FastAPI (it matches by method), but ordering by resource convention is cleaner.
- **GOTCHA 2**: `AthleteSettings.lthr` is `Mapped[Optional[int]]` (models.py:30) — column already exists in the schema. No migration needed.
- **GOTCHA 3**: `int(ftp * 0.55)` truncates; this matches how WKO5/TrainingPeaks display zone boundaries as whole watts.
- **VALIDATE**: `curl http://localhost:8000/api/v1/athletes/1/settings` returns JSON with `power_zones` and `hr_zones` arrays (or 404 if no settings yet).

---

### Task 8: Create ConfigTab

- **ACTION**: Create `frontend/src/tabs/ConfigTab.tsx`. FTP + LTHR inputs; save via `useUpdateSettings`; display zone tables if settings exist.
- **IMPLEMENT**:
  ```tsx
  // frontend/src/tabs/ConfigTab.tsx
  import { useState, useEffect } from 'react'
  import { useAthleteSettings, useUpdateSettings } from '../api/hooks'
  import type { PowerZone, HrZone } from '../api/client'

  function ZoneTable({ zones, unitKey }: {
    zones: (PowerZone | HrZone)[]
    unitKey: 'min_w' | 'min_bpm'
  }) {
    const maxKey = unitKey === 'min_w' ? 'max_w' : 'max_bpm'
    const unit = unitKey === 'min_w' ? 'w' : 'bpm'
    return (
      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-500">
            <th className="text-left py-1 px-2 font-normal">Zone</th>
            <th className="text-left py-1 px-2 font-normal">Label</th>
            <th className="text-left py-1 px-2 font-normal">Min</th>
            <th className="text-left py-1 px-2 font-normal">Max</th>
          </tr>
        </thead>
        <tbody>
          {zones.map(z => (
            <tr key={z.zone} className="border-t border-gray-800">
              <td className="py-1 px-2 text-gray-400">Z{z.zone}</td>
              <td className="py-1 px-2 text-gray-300">{z.label}</td>
              <td className="py-1 px-2 text-gray-300">{(z as any)[unitKey]}{unit}</td>
              <td className="py-1 px-2 text-gray-300">
                {(z as any)[maxKey] != null ? `${(z as any)[maxKey]}${unit}` : '∞'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  export function ConfigTab() {
    const { data: settings, isLoading } = useAthleteSettings()
    const update = useUpdateSettings()

    const [ftp, setFtp] = useState('')
    const [lthr, setLthr] = useState('')
    const [saved, setSaved] = useState(false)

    useEffect(() => {
      if (settings) {
        setFtp(settings.ftp_w != null ? String(settings.ftp_w) : '')
        setLthr(settings.lthr != null ? String(settings.lthr) : '')
      }
    }, [settings])

    const handleSubmit = (e: React.FormEvent) => {
      e.preventDefault()
      update.mutate(
        {
          ftp_w: ftp ? parseFloat(ftp) : undefined,
          lthr: lthr ? parseInt(lthr, 10) : undefined,
        },
        {
          onSuccess: () => {
            setSaved(true)
            setTimeout(() => setSaved(false), 2000)
          },
        },
      )
    }

    return (
      <div className="space-y-4 max-w-lg">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 mb-3">Athlete Settings</h2>
          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">FTP (watts)</label>
              <input
                type="number"
                min="1"
                max="600"
                value={ftp}
                onChange={e => setFtp(e.target.value)}
                placeholder="e.g. 250"
                className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-32"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">LTHR (bpm)</label>
              <input
                type="number"
                min="1"
                max="220"
                value={lthr}
                onChange={e => setLthr(e.target.value)}
                placeholder="e.g. 162"
                className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-32"
              />
            </div>
            <button
              type="submit"
              disabled={update.isPending}
              className="px-4 py-1.5 text-sm bg-purple-700 hover:bg-purple-600 rounded transition disabled:opacity-50"
            >
              {update.isPending ? 'Saving...' : saved ? '✓ Saved' : 'Save'}
            </button>
          </form>
        </div>

        {isLoading && (
          <div className="text-gray-500 text-xs">Loading settings...</div>
        )}

        {settings?.power_zones && settings.power_zones.length > 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <h2 className="text-sm font-semibold text-gray-400 mb-2">
              Power Zones — FTP {settings.ftp_w}w
            </h2>
            <ZoneTable zones={settings.power_zones} unitKey="min_w" />
          </div>
        )}

        {settings?.hr_zones && settings.hr_zones.length > 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <h2 className="text-sm font-semibold text-gray-400 mb-2">
              HR Zones — LTHR {settings.lthr}bpm
            </h2>
            <ZoneTable zones={settings.hr_zones} unitKey="min_bpm" />
          </div>
        )}
      </div>
    )
  }
  ```
- **MIRROR**: TAILWIND_CARD, TAILWIND_INPUT; purple-700 button from Dashboard.tsx:64.
- **GOTCHA 1**: `useAthleteSettings` returns 404 for new users (no settings yet). The component handles this gracefully — `settings` is `undefined`, zone tables don't render, form shows empty inputs.
- **GOTCHA 2**: `parseInt(lthr, 10)` — always pass radix 10 to avoid octal parsing edge cases.
- **VALIDATE**: Save with FTP=250, LTHR=162 → zone tables appear with correct wattages and bpm values.

---

### Task 9: Update Dashboard to use TabNav + render active tab

- **ACTION**: Update `frontend/src/pages/Dashboard.tsx` — import TabNav + tab components; render active tab content.
- **IMPLEMENT** (full file replacement):
  ```tsx
  // frontend/src/pages/Dashboard.tsx
  import { useState } from 'react'
  import { useDashboard, useScan, useCorosStatus, useCorosLogin, useCorosSync } from '../api/hooks'
  import { DashboardGrid } from '../components/DashboardGrid'
  import { TabNav } from '../components/TabNav'
  import { SeasonTab } from '../tabs/SeasonTab'
  import { ConfigTab } from '../tabs/ConfigTab'
  import { useTabStore } from '../store/tabStore'

  function CorosPanel() {
    const { data: status, isLoading } = useCorosStatus()
    const login = useCorosLogin()
    const sync = useCorosSync()

    const [email, setEmail] = useState('')
    const [password, setPassword] = useState('')
    const [syncMsg, setSyncMsg] = useState<string | null>(null)

    const handleLogin = (e: React.FormEvent) => {
      e.preventDefault()
      login.mutate({ email, password })
    }

    const handleSync = async () => {
      setSyncMsg('Syncing...')
      try {
        const body = await sync.mutateAsync(undefined)
        if (!body) { setSyncMsg('Done'); return }
        const reader = body.getReader()
        const dec = new TextDecoder()
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const chunk = dec.decode(value)
          const lines = chunk.split('\n').filter(l => l.startsWith('data:'))
          for (const line of lines) {
            try {
              const evt = JSON.parse(line.slice(5))
              if (evt.status === 'complete') {
                setSyncMsg(`Done — ${evt.total_downloaded} downloaded, ${evt.total_checked} checked`)
              } else if (evt.status === 'downloading' || evt.status === 'downloaded') {
                setSyncMsg(`Downloading ${evt.activity_id}...`)
              } else if (evt.status === 'error') {
                setSyncMsg(`Error: ${evt.error}`)
              }
            } catch { /* skip malformed lines */ }
          }
        }
      } catch (e) {
        setSyncMsg(`Failed: ${e}`)
      }
    }

    if (isLoading) return null

    return (
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">Coros Sync</h2>
        {status?.authenticated ? (
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-xs text-green-400">● {status.email}</span>
            {status.last_sync && (
              <span className="text-xs text-gray-500">Last sync: {status.last_sync.slice(0, 10)}</span>
            )}
            <button
              onClick={handleSync}
              disabled={sync.isPending}
              className="px-3 py-1 text-xs bg-purple-700 hover:bg-purple-600 rounded transition disabled:opacity-50"
            >
              {sync.isPending ? 'Syncing...' : '↓ Sync Coros'}
            </button>
            {syncMsg && <span className="text-xs text-gray-400">{syncMsg}</span>}
          </div>
        ) : (
          <form onSubmit={handleLogin} className="flex items-end gap-2 flex-wrap">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">Email</label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-48"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">Password</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
                className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-36"
              />
            </div>
            <button
              type="submit"
              disabled={login.isPending}
              className="px-3 py-1 text-sm bg-purple-700 hover:bg-purple-600 rounded transition disabled:opacity-50"
            >
              {login.isPending ? 'Logging in...' : 'Login'}
            </button>
            {login.isError && (
              <span className="text-xs text-red-400">Login failed</span>
            )}
          </form>
        )}
      </div>
    )
  }

  export function Dashboard() {
    const { data: dashboard, isLoading } = useDashboard()
    const scan = useScan()
    const activeTab = useTabStore(s => s.activeTab)

    if (isLoading) {
      return (
        <div className="flex items-center justify-center h-screen bg-gray-950 text-gray-400">
          Loading...
        </div>
      )
    }

    return (
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center justify-between">
          <h1 className="text-lg font-semibold text-purple-400">WKO5 Coach</h1>
          <button
            onClick={() => scan.mutate()}
            disabled={scan.isPending}
            className="px-3 py-1 text-sm bg-gray-700 hover:bg-gray-600 rounded transition disabled:opacity-50"
          >
            {scan.isPending ? 'Scanning...' : '⟳ Scan Files'}
          </button>
        </header>
        <TabNav />
        <main className="p-4">
          {activeTab === 'season' && (
            <>
              <CorosPanel />
              <SeasonTab />
              {dashboard?.layout?.widgets && (
                <div className="mt-4">
                  <DashboardGrid widgets={dashboard.layout.widgets} />
                </div>
              )}
            </>
          )}
          {activeTab === 'config' && <ConfigTab />}
          {activeTab === 'activities' && (
            <div className="flex items-center justify-center h-64 text-gray-600 text-sm">
              Activities — coming soon
            </div>
          )}
          {activeTab === 'ai' && (
            <div className="flex items-center justify-center h-64 text-gray-600 text-sm">
              AI — coming soon
            </div>
          )}
        </main>
      </div>
    )
  }
  ```
- **MIRROR**: CorosPanel code is copied verbatim — no behavior change. `useTabStore(s => s.activeTab)` is the zustand selector form (avoids unnecessary re-renders if setTab changes).
- **GOTCHA**: `CorosPanel` stays on Season tab only. It could go in the header, but the SRS shows it as part of Season content and it requires a lot of horizontal space.
- **VALIDATE**: All 4 tabs clickable; Season shows PMC with date controls; Config shows form; Activities/AI show placeholder.

---

## Testing Strategy

### Unit Tests

No automated tests for this plan (Mode A, balanced rigor). Manual browser verification is the acceptance gate.

### Edge Cases Checklist
- [ ] No settings in DB yet → Config tab shows empty form (no crash)
- [ ] Set FTP only (no LTHR) → only power zones table shown
- [ ] Date `from` > `to` prevented by `min`/`max` attributes on date inputs
- [ ] PMC with very narrow range (1 day) → chart shows 1 data point or empty state
- [ ] Switching tabs and back → date range state preserved in Season tab (zustand is ephemeral in-memory)

---

## Validation Commands

### TypeScript
```bash
cd /Users/<user>/Projects/WKO5reverse/frontend && npx tsc --noEmit
```
EXPECT: Zero errors

### Lint
```bash
cd /Users/<user>/Projects/WKO5reverse/frontend && npm run lint
```
EXPECT: No errors or warnings

### Build
```bash
cd /Users/<user>/Projects/WKO5reverse/frontend && npm run build
```
EXPECT: Vite build succeeds, no TS errors

### Backend (manual check)
```bash
cd /Users/<user>/Projects/WKO5reverse && python -m uvicorn backend.main:app --reload --port 8000
```
Then: `curl http://localhost:8000/api/v1/athletes/1/settings`
EXPECT: `{"detail":"NO_SETTINGS"}` (404) if no settings exist, or full settings JSON if they do.

### Dev Server (browser validation)
```bash
cd /Users/<user>/Projects/WKO5reverse/frontend && npm run dev
```
Open http://localhost:5173

### Manual Validation
- [ ] Tab bar renders with 4 tabs; Season is active on load
- [ ] Clicking Config tab shows the settings form
- [ ] Entering FTP=250, LTHR=162 and clicking Save → "✓ Saved" appears
- [ ] After save, power zones table shows 5 rows with correct watt boundaries
- [ ] After save, HR zones table shows 5 rows with correct bpm boundaries
- [ ] Clicking "3M" preset on Season tab → PMC chart re-fetches with 90-day range
- [ ] Editing date inputs directly → PMC chart re-fetches
- [ ] Activities and AI tabs show "coming soon" placeholders
- [ ] Refreshing the page → activeTab resets to "season" (ephemeral state, expected)

---

## Acceptance Criteria

- [ ] 4-tab navigation visible and functional
- [ ] Season tab: DateRangePicker with 1M/3M/6M/1Y presets + custom date inputs
- [ ] Season tab: PmcChart re-fetches when date range changes
- [ ] Config tab: FTP and LTHR inputs save to DB via PUT endpoint
- [ ] Config tab: Power zones and HR zones tables render after save
- [ ] GET `/api/v1/athletes/1/settings` returns correct zone arrays
- [ ] TypeScript compiles with zero errors
- [ ] No lint errors

## Completion Checklist

- [ ] Code follows REACT_QUERY_HOOK and MUTATION_HOOK patterns
- [ ] All new components use `bg-gray-900 border border-gray-800 rounded-lg p-4` card style
- [ ] Backend GET endpoint mirrors pmc.py async pattern
- [ ] `lthr` field added to both SettingsUpdate model and PUT handler
- [ ] `PmcChart` backward-compatible (no props = same behavior as before)
- [ ] No hardcoded athlete_id except the default `athleteId = 1` parameter
- [ ] Activities and AI tabs are explicit placeholders, not omitted

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `<input type="date">` renders differently on Safari vs Chrome | L | L | Force ISO format value; test on both |
| First-run user has no athlete row → GET /athletes/1/settings 500s | M | M | ConfigTab `isLoading` shows gracefully; error should be 404 if row missing; verify athlete bootstrap |
| `useDashboard` 404 on fresh install → Loading spinner hangs | M | L | Already handled by existing code; no regression |

## Notes

- `App.tsx` (Vite default template) is unused — `main.tsx` renders `<Dashboard>` directly. Leave App.tsx as-is; it doesn't affect the build.
- `tabs/` directory is new; `mkdir -p frontend/src/tabs` before creating files.
- The `AthleteSettings.lthr` column already exists (`models.py:30`) — **no DB migration required**.
- Zone boundary formula uses `int()` (floor), consistent with how WKO5/TrainingPeaks display whole-watt boundaries.
