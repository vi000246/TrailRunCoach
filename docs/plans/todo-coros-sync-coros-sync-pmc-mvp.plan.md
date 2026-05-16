# Plan: Coros Sync + PMC Chart MVP

> **For agentic workers:** `/prp-implement` → Type=feature → `implementing-features` skill.

## Summary

最小可行實作：從 Coros 非官方 API 下載 .fit 到 `~/.wko5coach/fits/`，在 Web UI 顯示 PMC（CTL/ATL/TSB）圖表。目的是驗證 Coros API 可串接、FIT 解析流程正確、PMC 指標可計算與顯示。

## User Story

As 個人運動員，I want 按一個按鈕就從 Coros 下載最新訓練並在網頁看到 PMC 圖表，So that 我可以確認整個 pipeline 通了。

## Problem → Solution

現狀：App.tsx 是 placeholder，Coros sync 不存在，Dashboard 沒有 PMC 圖。
目標：Coros 登入 → sync FIT → 自動計算 TSS → PMC 圖在 Web UI 顯示。

## Metadata

- **Module**: coros-sync
- **Parent Plan**: N/A
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md` — Milestone 4 + 5
- **Source SRS**: `docs/spec/wko5-coros-sync.spec.md`
- **Source Linear Issue**: N/A
- **Type**: feature
- **Size**: M
- **Complexity**: Medium
- **Rigor**: lite
- **Mode**: A — 快建
- **TDD**: off
- **Commit cadence**: per-task
- **Estimated Files**: 10

---

## UX Design

### Before
```
localhost:8000 → Vite placeholder (Count: 0)
No sync, no PMC, no data
```

### After
```
┌─────────────────────────────────────────────────────┐
│ WKO5 Coach        [⟳ Scan] [Coros Login] [↓ Sync]  │
├─────────────────────────────────────────────────────┤
│  PMC — CTL / ATL / TSB (last 365 days)              │
│  ┌─────────────────────────────────────────────┐    │
│  │  CTL ─── ATL ─── TSB ···                   │    │
│  │  (Recharts LineChart, log X-axis)           │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  Workout List (last 10)                             │
│  2026-05-14  Cycling  3h00  TSS 98                  │
│  2026-05-12  Cycling  1h30  TSS 52                  │
└─────────────────────────────────────────────────────┘
```

### Interaction Changes

| Touchpoint | Before | After |
|---|---|---|
| App.tsx | Vite placeholder | `<Dashboard />` |
| Header | TP sync button | Coros Login + Coros Sync buttons |
| Main content | DashboardGrid (empty) | PMC chart + recent workouts list |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/sync/tp_client.py` | 1-120 | SSE + httpx pattern to mirror in coros_client.py |
| P0 | `backend/api/sync.py` | all | SSE EventSourceResponse pattern |
| P0 | `backend/api/auth.py` | all | Login endpoint pattern |
| P0 | `backend/db/models.py` | all | Model field pattern (Mapped[Optional[str]]) |
| P0 | `backend/db/database.py` | all | init_db + create_all approach |
| P0 | `backend/files/file_service.py` | 55-120 | `_import_one_file` signature |
| P1 | `backend/api/pmc.py` | all | PMC endpoint (already works) |
| P1 | `frontend/src/pages/Dashboard.tsx` | all | Dashboard structure to extend |
| P1 | `frontend/src/api/hooks.ts` | all | React Query hook pattern |
| P1 | `frontend/src/api/client.ts` | all | Axios client + TypeScript types |

---

## Patterns to Mirror

### ERROR_HANDLING
```python
# SOURCE: backend/api/auth.py:31-36
except Exception as e:
    detail = str(e)
    if "invalid_grant" in detail or "401" in detail:
        raise HTTPException(401, f"COROS_LOGIN_FAILED: {detail}")
    raise HTTPException(502, f"COROS_LOGIN_ERROR: {detail}")
```

### SSE_PATTERN
```python
# SOURCE: backend/api/sync.py:17-26
async def generate():
    async for event in sync_workouts(db, athlete_id, since=since):
        yield {"event": "sync_progress", "data": json.dumps(event)}
return EventSourceResponse(generate())
```

### DB_QUERY
```python
# SOURCE: backend/api/auth.py:42-44
state_result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
state = state_result.scalar_one_or_none()
```

### MODEL_FIELD
```python
# SOURCE: backend/db/models.py:73-74
tp_access_token: Mapped[Optional[str]] = mapped_column(Text)
tp_refresh_token: Mapped[Optional[str]] = mapped_column(Text)
```

### HTTPX_CLIENT
```python
# SOURCE: backend/sync/tp_client.py:79-84
async with httpx.AsyncClient(timeout=15) as client:
    resp = await client.post(url, content=body, headers={...})
if resp.status_code != 200:
    raise ValueError(f"Login failed (HTTP {resp.status_code}): {resp.text[:400]!r}")
```

### REACT_HOOK
```typescript
// SOURCE: frontend/src/api/hooks.ts:23-28
export function usePmc(params?: { date_from?: string; date_to?: string }) {
  return useQuery<{ series: PmcPoint[] }>({
    queryKey: ['pmc', params],
    queryFn: () => api.get('/pmc', { params }).then(r => r.data),
  })
}
```

### MUTATION_HOOK
```typescript
// SOURCE: frontend/src/api/hooks.ts:38-46
export function useScan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post('/scan'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workouts'] }),
  })
}
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/db/models.py` | UPDATE | Add Coros fields to `SyncState` + `WorkoutFile` |
| `backend/db/database.py` | UPDATE | Add `_migrate_schema()` for ALTER TABLE on existing DB |
| `backend/sync/coros_client.py` | CREATE | Coros API login + list + download + sync |
| `backend/api/auth.py` | UPDATE | Add `/auth/coros/login`, `/auth/coros/status` |
| `backend/api/sync.py` | UPDATE | Add `/sync/coros/start` SSE endpoint |
| `backend/files/file_service.py` | UPDATE | Add `coros_activity_id` param to `_import_one_file` |
| `backend/main.py` | UPDATE | No change needed (auth + sync already imported) |
| `frontend/src/App.tsx` | UPDATE | Replace Vite placeholder with `<Dashboard />` |
| `frontend/src/pages/Dashboard.tsx` | UPDATE | Add PMC chart + Coros sync button |
| `frontend/src/components/PmcChart.tsx` | CREATE | Recharts PMC line chart (CTL/ATL/TSB) |
| `frontend/src/api/client.ts` | UPDATE | Add `CorosLoginResponse` type |
| `frontend/src/api/hooks.ts` | UPDATE | Add `useCorosLogin`, `useCorosSync` |

## NOT Building

- Rate limiting / exponential backoff (add in production milestone)
- Coros token refresh flow (re-login on expiry for now)
- Running dynamics / Activity Detail page
- iLevels / Training Levels
- TrainingPeaks sync changes
- Dashboard widget drag-and-drop
- React Router / multi-page navigation (single page for MVP)
- Error toast UI (console.error for now)

---

## Step-by-Step Tasks

### Task 1: DB Schema — Add Coros columns

**ACTION**: Add Coros fields to `SyncState` and `WorkoutFile`, plus a `_migrate_schema()` function that safely adds missing columns to the existing SQLite DB (since `create_all` won't add new columns to existing tables).

**IMPLEMENT** in `backend/db/models.py` — add to `WorkoutFile`:
```python
# after tp_workout_id line
coros_activity_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
coros_sport_type: Mapped[Optional[int]]
```
Add to `SyncState`:
```python
# after last_sync_cursor line
coros_access_token: Mapped[Optional[str]] = mapped_column(Text)
coros_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime)
coros_last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
coros_email: Mapped[Optional[str]] = mapped_column(String(200))
```

**IMPLEMENT** in `backend/db/database.py` — add after `init_db`:
```python
async def _migrate_schema():
    """Add columns that didn't exist in earlier schema versions."""
    new_cols = [
        ("workout_files", "coros_activity_id", "TEXT"),
        ("workout_files", "coros_sport_type", "INTEGER"),
        ("sync_state", "coros_access_token", "TEXT"),
        ("sync_state", "coros_token_expires", "DATETIME"),
        ("sync_state", "coros_last_sync_at", "DATETIME"),
        ("sync_state", "coros_email", "TEXT"),
    ]
    async with engine.begin() as conn:
        for table, col, col_type in new_cols:
            # SQLite: check if column exists via PRAGMA
            result = await conn.execute(
                text(f"PRAGMA table_info({table})")
            )
            existing = {row[1] for row in result.fetchall()}
            if col not in existing:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                )

async def init_db():
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_schema()
```

Note: move `from sqlalchemy import text` to top of `database.py`.

**VALIDATE**: Start the server — check logs show no errors. `curl http://localhost:8000/api/v1/auth/coros/status` should return 200.

---

### Task 2: coros_client.py — Coros API Client

**ACTION**: Create `backend/sync/coros_client.py` with login, list_activities, download_fit, sync_workouts.

**IMPLEMENT** full file:
```python
"""
Coros unofficial API client.
API base: https://teamcnapi.coros.com
Auth: POST /account/login with MD5-hashed password.
Reference implementations: xballoy/coros-api, cygnusb/coros-mcp
"""
import hashlib
import json
import logging
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional, AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.models import SyncState, Athlete, WorkoutFile
from backend.files.file_service import _import_one_file

log = logging.getLogger(__name__)

COROS_BASE = "https://teamcnapi.coros.com"
COROS_FITS_ROOT = Path.home() / ".wko5coach" / "fits"

SPORT_NAMES = {
    100: "cycling",
    200: "run",
    300: "swim",
    400: "triathlon",
    0: "other",
}


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _parse_coros_time(t: int) -> Optional[date]:
    """Parse YYYYMMDDHHmmss int to date."""
    try:
        return datetime.strptime(str(t)[:8], "%Y%m%d").date()
    except Exception:
        return None


async def login(email: str, password: str, db: AsyncSession, athlete_id: int = 1) -> dict:
    """MD5-hash password, POST /account/login, persist token."""
    payload = {"account": email, "passwd": _md5(password), "accountType": 2}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{COROS_BASE}/account/login",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    if resp.status_code != 200:
        raise ValueError(f"Coros login HTTP {resp.status_code}: {resp.text[:400]!r}")
    data = resp.json()
    if str(data.get("apiCode")) != "1":
        raise ValueError(f"Coros login failed: {data.get('message', data)!r}")

    result = data["result"]
    token = result["accessToken"]
    user_id = str(result["userId"])
    expires_ms = int(result.get("tokenExpiry", 0))
    expires_at = (
        datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc)
        if expires_ms
        else None
    )

    # Persist to sync_state
    state_res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_res.scalar_one_or_none()
    if not state:
        state = SyncState(athlete_id=athlete_id)
        db.add(state)
    state.coros_access_token = token
    state.coros_token_expires = expires_at
    state.coros_email = email
    await db.commit()

    log.info("Coros login OK user_id=%s", user_id)
    return {
        "authenticated": True,
        "coros_user_id": user_id,
        "email": email,
        "token_expires": expires_at.isoformat() if expires_at else None,
    }


async def _get_token(db: AsyncSession, athlete_id: int = 1) -> str:
    """Return valid token from DB, raise if missing/expired."""
    res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = res.scalar_one_or_none()
    if not state or not state.coros_access_token:
        raise ValueError("COROS_AUTH_REQUIRED: not logged in")
    if state.coros_token_expires:
        if datetime.now(timezone.utc) >= state.coros_token_expires:
            raise ValueError("COROS_AUTH_REQUIRED: token expired, please login again")
    return state.coros_access_token


async def _list_page(token: str, start_day: str, end_day: str, page: int, size: int = 20) -> dict:
    headers = {"accessToken": token}
    params = {"size": size, "pageNumber": page, "startDay": start_day, "endDay": end_day}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{COROS_BASE}/activity/query", headers=headers, params=params)
    if resp.status_code != 200:
        raise ValueError(f"Coros list_activities HTTP {resp.status_code}")
    data = resp.json()
    if str(data.get("apiCode")) != "1":
        raise ValueError(f"Coros list_activities error: {data.get('message')}")
    return data.get("result", {})


async def _download_fit(token: str, activity: dict) -> bytes:
    """Download FIT bytes: try fitUrl first, fallback to /activity/fit/url."""
    fit_url = activity.get("fitUrl")
    if fit_url:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(fit_url)
            if resp.status_code == 200 and resp.content:
                return resp.content

    # Fallback endpoint
    label_id = activity["labelId"]
    sport_type = activity.get("sportType", 0)
    headers = {"accessToken": token}
    async with httpx.AsyncClient(timeout=60) as client:
        url_resp = await client.get(
            f"{COROS_BASE}/activity/fit/url",
            params={"labelId": label_id, "sportType": sport_type},
            headers=headers,
        )
    if url_resp.status_code != 200:
        raise ValueError(f"fit/url HTTP {url_resp.status_code}")
    url_data = url_resp.json()
    if str(url_data.get("apiCode")) != "1":
        raise ValueError(f"fit/url error: {url_data.get('message')}")
    download_url = url_data["result"]["fileUrl"]
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(download_url)
    if resp.status_code != 200:
        raise ValueError(f"FIT download HTTP {resp.status_code}")
    return resp.content


async def sync_workouts(
    db: AsyncSession,
    athlete_id: int = 1,
    since: Optional[str] = None,
) -> AsyncIterator[dict]:
    """Full sync: list Coros activities → download new .fit → import → yield progress."""
    token = await _get_token(db, athlete_id)

    # Get athlete name for path
    ath_res = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = ath_res.scalar_one_or_none()
    athlete_name = athlete.name if athlete else "default"

    since_date = since or "20200101"
    if "-" in since_date:
        since_date = since_date.replace("-", "")
    end_day = datetime.now().strftime("%Y%m%d")

    yield {"status": "started", "since": since_date, "until": end_day}

    page, total_checked, total_downloaded = 1, 0, 0
    while True:
        result = await _list_page(token, since_date, end_day, page)
        activities = result.get("dataList", [])
        if not activities:
            break

        for act in activities:
            label_id = str(act.get("labelId", ""))
            act_date = _parse_coros_time(act.get("startTime", 0))
            sport_type = act.get("sportType", 0)
            sport_name = SPORT_NAMES.get(sport_type, "other")
            total_checked += 1

            yield {"status": "checking", "activity_id": label_id,
                   "date": act_date.isoformat() if act_date else None}

            # Skip already imported
            dup = await db.execute(
                select(WorkoutFile).where(WorkoutFile.coros_activity_id == label_id)
            )
            if dup.scalar_one_or_none():
                yield {"status": "skipped", "activity_id": label_id, "reason": "already_imported"}
                continue

            # Download FIT
            try:
                fit_bytes = await _download_fit(token, act)
            except Exception as e:
                log.warning("Coros FIT download failed %s: %s", label_id, e)
                yield {"status": "error", "activity_id": label_id, "error": str(e)}
                continue

            # Save to disk
            year = act_date.year if act_date else "unknown"
            dest_dir = COROS_FITS_ROOT / athlete_name / str(year)
            dest_dir.mkdir(parents=True, exist_ok=True)
            date_str = act_date.isoformat() if act_date else "unknown"
            filename = f"{label_id}_{date_str}_{sport_name}.fit"
            dest = dest_dir / filename
            dest.write_bytes(fit_bytes)

            # Import to DB
            try:
                wf = await _import_one_file(
                    db, athlete_id, dest,
                    source="coros",
                    coros_activity_id=label_id,
                )
                await db.commit()
                total_downloaded += 1
                yield {"status": "downloaded", "activity_id": label_id,
                       "file": filename, "sport": sport_name}
            except Exception as e:
                log.warning("Import failed for %s: %s", filename, e)
                yield {"status": "error", "activity_id": label_id, "error": f"import_failed: {e}"}

        if len(activities) < 20:
            break
        page += 1

    # Update last_sync_at
    state_res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = state_res.scalar_one_or_none()
    if state:
        state.coros_last_sync_at = datetime.now(timezone.utc)
        await db.commit()

    yield {"status": "complete", "total_downloaded": total_downloaded, "total_checked": total_checked}
```

**VALIDATE**: `python3 -c "from backend.sync.coros_client import login; print('OK')"` should not error.

---

### Task 3: Backend API Endpoints — Auth + Sync

**ACTION**: Add Coros auth endpoints to `auth.py` and Coros sync endpoint to `sync.py`.

**IMPLEMENT** — add to `backend/api/auth.py` (after existing imports, before first `@router`):
```python
from backend.sync import coros_client

class CorosLoginRequest(BaseModel):
    email: str
    password: str
    athlete_id: int = 1

@router.post("/coros/login")
async def coros_login(body: CorosLoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await coros_client.login(body.email, body.password, db, body.athlete_id)
        return result
    except Exception as e:
        detail = str(e)
        if "apiCode" in detail or "Invalid" in detail.lower():
            raise HTTPException(401, f"COROS_LOGIN_FAILED: {detail}")
        raise HTTPException(502, f"COROS_LOGIN_ERROR: {detail}")

@router.get("/coros/status")
async def coros_auth_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = res.scalar_one_or_none()
    return {
        "authenticated": bool(state and state.coros_access_token),
        "email": state.coros_email if state else None,
        "token_expires": state.coros_token_expires.isoformat() if (state and state.coros_token_expires) else None,
    }

@router.post("/coros/logout")
async def coros_logout(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
    state = res.scalar_one_or_none()
    if state:
        state.coros_access_token = None
        state.coros_token_expires = None
        await db.commit()
    return {"logged_out": True}
```

**IMPLEMENT** — add to `backend/api/sync.py`:
```python
from backend.sync import coros_client as _coros

@router.post("/coros/start")
async def start_coros_sync(
    athlete_id: int = 1,
    since: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger Coros sync. Streams SSE progress."""
    async def generate():
        try:
            async for event in _coros.sync_workouts(db, athlete_id, since=since):
                yield {"event": "sync_progress", "data": json.dumps(event)}
        except Exception as e:
            yield {"event": "sync_progress", "data": json.dumps({"status": "error", "error": str(e)})}
    return EventSourceResponse(generate())
```

**VALIDATE**:
```bash
curl -s http://localhost:8000/api/v1/auth/coros/status | python3 -m json.tool
# → { "authenticated": false, "email": null, ... }
```

---

### Task 4: file_service.py — Add coros_activity_id param

**ACTION**: Extend `_import_one_file` to accept and persist `coros_activity_id`.

**IMPLEMENT** — change signature and body in `backend/files/file_service.py`:
```python
async def _import_one_file(
    db: AsyncSession,
    athlete_id: int,
    path: Path,
    source: str = "local",
    tp_workout_id: Optional[int] = None,
    coros_activity_id: Optional[str] = None,    # NEW
) -> Optional[WorkoutFile]:
```

Inside the function, where `wf = WorkoutFile(...)` is created, add:
```python
    wf.coros_activity_id = coros_activity_id   # NEW
```
(Find the existing `WorkoutFile(...)` constructor call and add this field.)

**VALIDATE**: `python3 -c "from backend.files.file_service import _import_one_file; print('OK')"` should not error.

---

### Task 5: App.tsx — Wire Dashboard

**ACTION**: Replace Vite placeholder with `<Dashboard />`.

**IMPLEMENT** — replace entire `frontend/src/App.tsx` with:
```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Dashboard } from './pages/Dashboard'

const queryClient = new QueryClient()

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Dashboard />
    </QueryClientProvider>
  )
}
```

**GOTCHA**: Check if `main.tsx` already wraps with `QueryClientProvider`. If so, remove it from `App.tsx` to avoid nesting. Check `frontend/src/main.tsx`:
```tsx
// If main.tsx has: <QueryClientProvider ...>, remove it from App.tsx
```

**VALIDATE**: `cd frontend && npm run dev` → browser shows Dashboard (not Vite placeholder).

---

### Task 6: PmcChart.tsx — Recharts PMC Line Chart

**ACTION**: Create `frontend/src/components/PmcChart.tsx`.

**IMPLEMENT**:
```tsx
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceLine
} from 'recharts'
import { usePmc } from '../api/hooks'

export function PmcChart() {
  const { data, isLoading, error } = usePmc()

  if (isLoading) return (
    <div className="h-64 flex items-center justify-center text-gray-500 text-sm">
      Loading PMC...
    </div>
  )
  if (error || !data?.series?.length) return (
    <div className="h-64 flex items-center justify-center text-gray-600 text-sm">
      No PMC data — sync workouts to see CTL/ATL/TSB
    </div>
  )

  const series = data.series

  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <h2 className="text-sm font-medium text-gray-400 mb-3">
        Performance Management Chart — CTL / ATL / TSB
      </h2>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={series} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="date"
            tick={{ fill: '#9ca3af', fontSize: 11 }}
            tickFormatter={(v: string) => v.slice(5)}  // MM-DD
            interval="preserveStartEnd"
          />
          <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} />
          <Tooltip
            contentStyle={{ background: '#1f2937', border: '1px solid #374151', fontSize: 12 }}
            labelStyle={{ color: '#e5e7eb' }}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: '#9ca3af' }} />
          <ReferenceLine y={0} stroke="#4b5563" strokeDasharray="3 3" />
          <Line type="monotone" dataKey="ctl" stroke="#7c3aed" dot={false} name="CTL" strokeWidth={2} />
          <Line type="monotone" dataKey="atl" stroke="#dc2626" dot={false} name="ATL" strokeWidth={2} />
          <Line type="monotone" dataKey="tsb" stroke="#059669" dot={false} name="TSB" strokeWidth={1} strokeDasharray="4 2" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
```

**VALIDATE**: Import in Dashboard and confirm chart renders with data.

---

### Task 7: Dashboard.tsx — Add PMC chart + Coros sync

**ACTION**: Replace TP sync button with Coros sync, add PMC chart to main content.

**IMPLEMENT** — replace `frontend/src/pages/Dashboard.tsx`:
```tsx
import { useState } from 'react'
import { useDashboard, useScan } from '../api/hooks'
import { useCorosLogin, useCorosSync, useCorosStatus } from '../api/hooks'
import { PmcChart } from '../components/PmcChart'

export function Dashboard() {
  const { data: dashboard, isLoading } = useDashboard()
  const scan = useScan()
  const corosSync = useCorosSync()
  const { data: corosStatus } = useCorosStatus()
  const [showLoginForm, setShowLoginForm] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const corosLogin = useCorosLogin()

  if (isLoading) return (
    <div className="flex items-center justify-center h-screen bg-gray-950 text-gray-400">Loading...</div>
  )

  const isAuthenticated = corosStatus?.authenticated

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    await corosLogin.mutateAsync({ email, password })
    setShowLoginForm(false)
    setEmail('')
    setPassword('')
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-purple-400">WKO5 Coach</h1>
        <div className="flex gap-3">
          <button
            onClick={() => scan.mutate()}
            disabled={scan.isPending}
            className="px-3 py-1 text-sm bg-gray-700 hover:bg-gray-600 rounded transition disabled:opacity-50"
          >
            {scan.isPending ? 'Scanning...' : '⟳ Scan'}
          </button>
          {!isAuthenticated ? (
            <button
              onClick={() => setShowLoginForm(v => !v)}
              className="px-3 py-1 text-sm bg-purple-700 hover:bg-purple-600 rounded transition"
            >
              Coros Login
            </button>
          ) : (
            <button
              onClick={() => corosSync.mutate()}
              disabled={corosSync.isPending}
              className="px-3 py-1 text-sm bg-blue-700 hover:bg-blue-600 rounded transition disabled:opacity-50"
            >
              {corosSync.isPending ? 'Syncing...' : '↓ Sync Coros'}
            </button>
          )}
        </div>
      </header>

      {showLoginForm && (
        <div className="bg-gray-800 border-b border-gray-700 px-6 py-4">
          <form onSubmit={handleLogin} className="flex gap-3 items-end max-w-md">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Coros Email</label>
              <input value={email} onChange={e => setEmail(e.target.value)}
                type="email" required
                className="px-2 py-1 text-sm bg-gray-700 rounded border border-gray-600 text-white" />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Password</label>
              <input value={password} onChange={e => setPassword(e.target.value)}
                type="password" required
                className="px-2 py-1 text-sm bg-gray-700 rounded border border-gray-600 text-white" />
            </div>
            <button type="submit" disabled={corosLogin.isPending}
              className="px-3 py-1 text-sm bg-purple-600 hover:bg-purple-500 rounded transition disabled:opacity-50">
              {corosLogin.isPending ? 'Logging in...' : 'Login'}
            </button>
            {corosLogin.isError && (
              <span className="text-red-400 text-xs">{String(corosLogin.error)}</span>
            )}
          </form>
        </div>
      )}

      {corosSync.isPending && (
        <div className="bg-blue-900/30 border-b border-blue-800 px-6 py-2 text-sm text-blue-300">
          Syncing from Coros...
        </div>
      )}

      <main className="p-4 space-y-4">
        <PmcChart />
        {/* Existing dashboard widgets if configured */}
        {dashboard?.layout?.widgets && dashboard.layout.widgets.length > 0 && (
          <div className="text-xs text-gray-600 italic">— Custom widgets below —</div>
        )}
      </main>
    </div>
  )
}
```

**IMPLEMENT** — add to `frontend/src/api/hooks.ts`:
```typescript
import type { CorosLoginRequest, CorosLoginResponse, CorosStatus } from './client'

export function useCorosStatus(athleteId = 1) {
  return useQuery<CorosStatus>({
    queryKey: ['coros_status', athleteId],
    queryFn: () => api.get(`/auth/coros/status?athlete_id=${athleteId}`).then(r => r.data),
    refetchInterval: 60_000,
  })
}

export function useCorosLogin() {
  const qc = useQueryClient()
  return useMutation<CorosLoginResponse, Error, CorosLoginRequest>({
    mutationFn: (body) => api.post('/auth/coros/login', body).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['coros_status'] }),
  })
}

export function useCorosSync(athleteId = 1) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const resp = await fetch(`/api/v1/sync/coros/start?athlete_id=${athleteId}`, { method: 'POST' })
      // Drain SSE stream
      const reader = resp.body?.getReader()
      if (!reader) return
      while (true) {
        const { done } = await reader.read()
        if (done) break
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workouts'] })
      qc.invalidateQueries({ queryKey: ['pmc'] })
    },
  })
}
```

**IMPLEMENT** — add to `frontend/src/api/client.ts`:
```typescript
export interface CorosLoginRequest {
  email: string
  password: string
  athlete_id?: number
}

export interface CorosLoginResponse {
  authenticated: boolean
  coros_user_id: string
  email: string
  token_expires: string | null
}

export interface CorosStatus {
  authenticated: boolean
  email: string | null
  token_expires: string | null
}
```

**VALIDATE**: Browser shows PMC chart. Coros Login button appears. After login, "Sync Coros" button appears.

---

## Validation Commands

### Backend
```bash
# Syntax check
cd /Users/<user>/Projects/WKO5reverse
python3 -m py_compile backend/sync/coros_client.py backend/api/auth.py backend/api/sync.py backend/files/file_service.py backend/db/models.py backend/db/database.py
echo "SYNTAX OK"

# Start server
./start.sh  # or: docker compose up -d --build

# Auth status (expect: authenticated: false)
curl -s http://localhost:8000/api/v1/auth/coros/status | python3 -m json.tool

# PMC endpoint (expect: { series: [...] } or empty)
curl -s "http://localhost:8000/api/v1/pmc" | python3 -m json.tool | head -20
```

### Frontend
```bash
cd /Users/<user>/Projects/WKO5reverse/frontend
npm run build   # should succeed with no TS errors
```

### Manual Browser Validation
- [ ] `http://localhost:8000` shows WKO5 Coach header (not Vite placeholder)
- [ ] PMC chart renders (even if empty — shows "No PMC data" message)
- [ ] "Coros Login" button visible in header
- [ ] Click Coros Login → inline form appears (email + password fields)
- [ ] After login → "Sync Coros" button replaces login button
- [ ] Click "Sync Coros" → "Syncing..." state visible
- [ ] After sync → PMC chart refreshes with new data points
- [ ] FIT files appear in `~/.wko5coach/fits/{athlete_name}/{year}/`

---

## Acceptance Criteria

- [ ] Backend starts without errors after schema migration
- [ ] `GET /api/v1/auth/coros/status` returns `{ authenticated: false }` before login
- [ ] `POST /api/v1/auth/coros/login` with valid Coros credentials returns `{ authenticated: true }`
- [ ] `POST /api/v1/sync/coros/start` streams SSE events and downloads .fit files to `~/.wko5coach/fits/`
- [ ] PMC chart visible at `http://localhost:8000` with CTL/ATL/TSB lines
- [ ] After sync, PMC chart shows updated data

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Coros API endpoint 不存在 / 已改版 | Medium | High | 先用 curl 手動測 `/account/login` |
| FIT bytes 下載為 0 / 格式錯誤 | Medium | Medium | `dest.write_bytes(fit_bytes)` 後確認 `wc -c` 非零 |
| `create_all` 已有 DB 不新增欄位 | High | High | `_migrate_schema()` 用 PRAGMA + ALTER TABLE 解決 |
| `QueryClientProvider` 雙層包裝 | Low | Low | 檢查 main.tsx，如已有則移除 App.tsx 的 |
| TSS 未計算導致 PMC 空白 | Medium | Medium | PMC 空白是正常（需先有 power FIT），不影響驗收 |

## Notes

- `COROS_FITS_ROOT = Path.home() / ".wko5coach" / "fits"` — Docker volume 已掛載 `~/.wko5coach/`，新子目錄 `fits/` 自動在 volume 內
- PMC 圖表需要 FIT 檔案中有 `power` channel 才有 TSS。若 Coros 活動沒有功率計，PMC 可能仍為空 — 這不是 bug，是 feature（no power, no TSS）
- 如果 Coros `fitUrl` 回傳的 .fit 是壓縮格式，`fit_reader.py` 會報錯 — 先確認下載的 bytes 是合法 FIT（magic bytes: `0x0E 0x10 0x44 0x09` 開頭）
