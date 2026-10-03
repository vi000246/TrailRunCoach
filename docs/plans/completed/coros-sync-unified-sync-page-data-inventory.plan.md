---
linear_issue: null
---
# Plan: 統一同步頁面 + 已載入資料盤點（TP 接 UI）

> **For agentic workers:** `/prp-implement` 依 `Metadata.Type` 路由。Mode B（任務先測）。前端無測試框架 → tsc + build + 後端整合 smoke 為 gate。

## Summary

新增一個 `/sync/inventory` 後端端點（依來源/運動別筆數、日期範圍、各來源最後同步時間，無 schema 變更），並把現有 COROS-only 的 `CorosPage` 重構為統一 `SyncPage`：盤點面板 + COROS 同步區（既有）+ TrainingPeaks 同步區（接既有但未在 UI 暴露的 `/auth/tp/login`、`/sync/start`）。`/coros` 重導到 `/sync`，nav 改「同步」。

## User Story
As 想一頁完成同步並確認資料進度的訓練者，
I want 一個同步頁能跑 COROS 與 TrainingPeaks 同步、並看到已載入哪些資料，
So that 我知道圖表背後的資料來源與覆蓋範圍，不用猜。

## Problem → Solution
TP 後端已可下載但前端只接 COROS、且看不到已載入資料盤點 → 統一同步頁 + 盤點端點。

## Metadata
- **Module**: coros-sync
- **Parent Plan**: N/A
- **Source PRD**: docs/prd/wko5-trail-multipage-sync-coach.prd.md
- **Source Feature SRS**: docs/srs/coros-sync-unified-sync-page-data-inventory.srs.md
- **Source Module Spec**: docs/spec/wko5-coros-sync.spec.md
- **Source Linear Issue**: N/A
- **Type**: feature
- **Size**: M
- **Complexity**: Medium
- **Rigor**: balanced
- **Mode**: B — 任務先測（前端以 tsc + build + smoke 替代）
- **TDD**: on（後端）/ off（前端，無框架）
- **Commit cadence**: per-task
- **Estimated Files**: ~8

---

## UX Design

### Before
```
nav: … Coros
CorosPage：只有 COROS 帳號 + 同步 log
（TP 後端可用但沒有任何 UI 入口；看不到已載入資料盤點）
```

### After
```
nav: … 同步
SyncPage (/sync)：
  ┌ 已載入資料盤點 ── 總 N 筆 · coros N / local N · YYYY-MM → YYYY-MM ┐
  │   依運動：跑步 N · 健走 N · 單車 N …                                        │
  │   最後同步：COROS YYYY-MM-DD · TP 從未                                     │
  ├ COROS 同步（帳號 + Start Sync + log）── 既有                              │
  └ TrainingPeaks 同步（帳號 + Start Sync + log）── 新接                      │
/coros → 重導 /sync
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| nav | Coros | 同步 | 標籤改名 |
| TP 同步 | 無 UI | 帳號+同步+log | 接既有後端 |
| 盤點 | 無 | 面板 | 新端點 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `docs/srs/coros-sync-unified-sync-page-data-inventory.srs.md` | all | AC、端點合約 |
| P0 | `docs/spec/wko5-coros-sync.spec.md` | all | 既有同步架構 |
| P0 | `backend/api/sync.py` | 1-70 | SSE 同步端點模式（coros + TP start 已存在） |
| P0 | `backend/api/auth.py` | 19-97 | TP/COROS login+status 端點（已存在，前端要接 TP） |
| P0 | `frontend/src/pages/CorosPage.tsx` | 1-207 | 要重構的頁面 + SSE log 解析模式 |
| P0 | `frontend/src/api/hooks.ts` | 42-90 | useSync(TP)/useCorosStatus/Login/Sync 既有 hook |
| P1 | `backend/db/models.py` | 41-60, 97-110 | WorkoutFile.source/sport, SyncState last-sync 欄位 |
| P1 | `backend/api/sports.py` | 全 | facets 聚合模式（盤點比照） |

## External Documentation
No external research needed — 沿用既有 FastAPI SSE / React Query / SSE 解析模式。

---

## Patterns to Mirror

### INVENTORY_AGGREGATION (比照 facets)
```python
# SOURCE: backend/api/sports.py:20-40
q = (select(WorkoutFile.sport, func.count(WorkoutFile.id))
     .where(WorkoutFile.athlete_id == athlete_id)
     .group_by(WorkoutFile.sport))
rows = (await db.execute(q)).all()
```

### SSE_LOG_PARSE (前端讀同步串流)
```tsx
// SOURCE: frontend/src/pages/CorosPage.tsx:57-71
const reader = (body as ReadableStream).getReader()
const dec = new TextDecoder()
while (true) {
  const { done, value } = await reader.read()
  if (done) break
  for (const line of dec.decode(value).split('\n')) {
    const trimmed = line.startsWith('data:') ? line.slice(5).trim() : ''
    if (trimmed) appendLog(JSON.parse(trimmed))
  }
}
```

### COROS_HOOKS (既有；TP 比照)
```ts
// SOURCE: frontend/src/api/hooks.ts:65-90
export function useCorosStatus(athleteId = 1) {
  return useQuery({ queryKey: ['coros_status', athleteId],
    queryFn: () => api.get(`/auth/coros/status?athlete_id=${athleteId}`).then(r => r.data) })
}
export function useCorosSync(athleteId = 1) {
  return useMutation({ mutationFn: (since?: string) =>
    fetch(`/api/v1/sync/coros/start?athlete_id=${athleteId}${since ? `&since=${since}` : ''}`,
      { method: 'POST' }).then(r => r.body) })
}
```

### TP_SYNC_HOOK (既有 useSync → TP)
```ts
// SOURCE: frontend/src/api/hooks.ts:42-51 — already points at TP /sync/start
export function useSync() {  // TP sync
  return useMutation({ mutationFn: () =>
    fetch('/api/v1/sync/start', { method: 'POST' }).then(r => r.body), ... })
}
```

### ROUTER_PATTERN
```python
# SOURCE: backend/api/sync.py:14-17
router = APIRouter(prefix="/api/v1/sync", tags=["sync"])
@router.get("/status")
async def sync_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)): ...
```

### TEST_STRUCTURE
```python
# SOURCE: backend/tests/test_sports_facets.py — in-memory DB + call handler directly
async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    ...
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/api/sync.py` | UPDATE | 加 `/inventory` 端點 |
| `backend/tests/test_sync_inventory.py` | CREATE | 盤點端點測試 |
| `frontend/src/api/client.ts` | UPDATE | SyncInventory 型別 |
| `frontend/src/api/hooks.ts` | UPDATE | useSyncInventory + useTpStatus/useTpLogin（TP login/status hook） |
| `frontend/src/components/ProviderSyncCard.tsx` | CREATE | 抽 CorosPage 帳號+同步 log 為可重用（吃 provider 設定） |
| `frontend/src/components/SyncInventoryPanel.tsx` | CREATE | 盤點面板 |
| `frontend/src/pages/SyncPage.tsx` | CREATE | 組裝盤點 + COROS + TP |
| `frontend/src/pages/CorosPage.tsx` | DELETE | 由 SyncPage 取代 |
| `frontend/src/App.tsx` | UPDATE | /sync 路由 + /coros 重導 |
| `frontend/src/layouts/AppShell.tsx` | UPDATE | nav 「同步」 |

## NOT Building
- 寫回/上傳 TP（PRD 排除）
- 自動排程同步
- intervals.icu 中介
- schema 變更（盤點純讀）

---

## Step-by-Step Tasks

### Task 1: `/sync/inventory` 端點
- **ACTION**: 在 `sync.py` 加 GET `/inventory`，回總筆數、依 source、依 sport、日期 min/max、各來源 last-sync。
- **TEST FIRST**: `backend/tests/test_sync_inventory.py`
  ```python
  import sys, os, asyncio
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
  from datetime import date, datetime
  from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

  def _run(c): return asyncio.get_event_loop().run_until_complete(c)

  async def _session():
      engine = create_async_engine("sqlite+aiosqlite:///:memory:")
      from backend.db.models import Base, Athlete, WorkoutFile, SyncState
      async with engine.begin() as conn:
          await conn.run_sync(Base.metadata.create_all)
      S = async_sessionmaker(engine, expire_on_commit=False); s = S()
      s.add(Athlete(id=1, name="a", data_dir="/tmp")); await s.flush()
      s.add_all([
          WorkoutFile(athlete_id=1, file_path="/a", file_format="fit", source="coros",
                      sport="running", workout_date=date(2025,1,1)),
          WorkoutFile(athlete_id=1, file_path="/b", file_format="fit", source="local",
                      sport="cycling", workout_date=date(2026,5,1)),
      ])
      s.add(SyncState(athlete_id=1, coros_last_sync_at=datetime(2026,5,15)))
      await s.commit(); return s

  def test_inventory_groups_by_source_and_sport():
      async def _i():
          from backend.api.sync import sync_inventory
          s = await _session()
          r = await sync_inventory(athlete_id=1, db=s)
          assert r["total"] == 2
          assert r["by_source"]["coros"] == 1 and r["by_source"]["local"] == 1
          assert r["by_sport"]["running"] == 1
          assert r["date_min"] == "2025-01-01" and r["date_max"] == "2026-05-01"
          assert r["last_sync"]["coros"] is not None
      _run(_i())
  ```
  Run: `/opt/homebrew/bin/pytest backend/tests/test_sync_inventory.py -q` — expect FAIL
- **IMPLEMENT**: `sync.py`
  ```python
  from sqlalchemy import func
  from backend.db.models import WorkoutFile

  @router.get("/inventory")
  async def sync_inventory(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
      base = WorkoutFile.athlete_id == athlete_id
      total = (await db.execute(
          select(func.count(WorkoutFile.id)).where(base))).scalar() or 0
      by_source = {k or "unknown": v for k, v in (await db.execute(
          select(WorkoutFile.source, func.count(WorkoutFile.id)).where(base)
          .group_by(WorkoutFile.source))).all()}
      by_sport = {k or "unknown": v for k, v in (await db.execute(
          select(WorkoutFile.sport, func.count(WorkoutFile.id)).where(base)
          .group_by(WorkoutFile.sport))).all()}
      dr = (await db.execute(
          select(func.min(WorkoutFile.workout_date), func.max(WorkoutFile.workout_date))
          .where(base))).first()
      st = (await db.execute(
          select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
      return {
          "total": total, "by_source": by_source, "by_sport": by_sport,
          "date_min": dr[0].isoformat() if dr and dr[0] else None,
          "date_max": dr[1].isoformat() if dr and dr[1] else None,
          "last_sync": {
              "coros": st.coros_last_sync_at.isoformat() if st and st.coros_last_sync_at else None,
              "tp": st.last_sync_at.isoformat() if st and st.last_sync_at else None,
          },
      }
  ```
- **MIRROR**: INVENTORY_AGGREGATION, ROUTER_PATTERN
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(coros-sync): GET /sync/inventory endpoint`

### Task 2: 前端型別 + hooks（盤點 + TP login/status）
- **ACTION**: client.ts 加 `SyncInventory` 型別；hooks.ts 加 `useSyncInventory`、`useTpStatus`、`useTpLogin`（TP sync 用既有 `useSync`）。
- **TEST FIRST (gate)**: `cd frontend && npx tsc -b` 綠。
- **IMPLEMENT**: client.ts
  ```ts
  export interface SyncInventory {
    total: number
    by_source: Record<string, number>
    by_sport: Record<string, number>
    date_min: string | null
    date_max: string | null
    last_sync: { coros: string | null; tp: string | null }
  }
  ```
  hooks.ts（MIRROR COROS_HOOKS）：
  ```ts
  export function useSyncInventory(athleteId = 1) {
    return useQuery<SyncInventory>({
      queryKey: ['sync_inventory', athleteId],
      queryFn: () => api.get('/sync/inventory', { params: { athlete_id: athleteId } }).then(r => r.data),
    })
  }
  export function useTpStatus(athleteId = 1) {
    return useQuery({ queryKey: ['tp_status', athleteId],
      queryFn: () => api.get(`/auth/tp/status?athlete_id=${athleteId}`).then(r => r.data) })
  }
  export function useTpLogin() {
    const qc = useQueryClient()
    return useMutation<unknown, Error, { username: string; password: string }>({
      mutationFn: (b) => api.post('/auth/tp/login', b).then(r => r.data),
      onSuccess: () => qc.invalidateQueries({ queryKey: ['tp_status'] }),
    })
  }
  ```
  （`SyncInventory` 加入 client.ts import；`useSync` 既有作 TP sync。）
- **MIRROR**: COROS_HOOKS, QUERY_HOOK
- **VALIDATE**: `npx tsc -b` 綠
- **COMMIT**: `feat(coros-sync): inventory + TP login/status hooks`

### Task 3: ProviderSyncCard 可重用元件
- **ACTION**: 把 CorosPage 的「帳號登入/狀態 + 同步按鈕 + SSE log」抽成 `ProviderSyncCard`，吃 provider 設定（title、status hook data、login fn、sync fn）。
- **TEST FIRST (gate)**: tsc 綠 + 瀏覽器 COROS 卡片行為不變。
- **IMPLEMENT**: 建 `ProviderSyncCard.tsx`，props：
  ```tsx
  interface ProviderSyncCardProps {
    title: string
    authenticated: boolean
    accountLabel?: string
    lastSync?: string | null
    loginPending: boolean
    loginError: string | null
    onLogin: (email: string, password: string) => void
    onSync: (since?: string) => Promise<ReadableStream | null | undefined>
    onSynced: () => void   // invalidate inventory
  }
  ```
  內部沿用 CorosPage 的 `handleSync`/SSE 解析（MIRROR SSE_LOG_PARSE）與 log 渲染；`onSynced()` 在 complete 後呼叫。
- **MIRROR**: SSE_LOG_PARSE + CorosPage 整體
- **GOTCHA**: TP login 欄位是 `username`（非 email）；COROS 是 `email`。用通用 `(id, password)` 簽章，呼叫端決定欄位名。
- **VALIDATE**: tsc + 瀏覽器
- **COMMIT**: `feat(coros-sync): reusable ProviderSyncCard`

### Task 4: SyncInventoryPanel
- **ACTION**: 建盤點面板，吃 `useSyncInventory`，顯示總筆數、source/sport 分佈、日期範圍、各來源 last-sync。
- **TEST FIRST (gate)**: tsc 綠 + 瀏覽器顯示真實數字。
- **IMPLEMENT**: `SyncInventoryPanel.tsx`
  ```tsx
  import { useSyncInventory } from '../api/hooks'
  import { Card, CardHeader, CardTitle, CardContent } from './ui/card'
  export function SyncInventoryPanel({ athleteId = 1 }: { athleteId?: number }) {
    const { data } = useSyncInventory(athleteId)
    if (!data) return null
    return (
      <Card><CardHeader><CardTitle>已載入資料</CardTitle></CardHeader>
      <CardContent className="space-y-2 text-xs text-[#a9b6c8]">
        <div>總 <b className="text-[#e8edf5]">{data.total}</b> 筆 · {data.date_min} → {data.date_max}</div>
        <div>來源：{Object.entries(data.by_source).map(([k,v]) => `${k} ${v}`).join(' · ')}</div>
        <div>運動：{Object.entries(data.by_sport).slice(0,8).map(([k,v]) => `${k} ${v}`).join(' · ')}</div>
        <div>最後同步：COROS {data.last_sync.coros ?? '從未'} · TP {data.last_sync.tp ?? '從未'}</div>
      </CardContent></Card>
    )
  }
  ```
- **MIRROR**: SmartDashboardSection 卡片語彙
- **VALIDATE**: tsc + 瀏覽器
- **COMMIT**: `feat(coros-sync): sync inventory panel`

### Task 5: SyncPage 組裝 + 路由 + nav + 清理
- **ACTION**: 建 `SyncPage` 組裝盤點 + COROS ProviderSyncCard + TP ProviderSyncCard；App.tsx 加 `/sync` + `/coros` 重導；AppShell nav 改「同步」；刪 CorosPage。
- **TEST FIRST (gate)**: `tsc -b` + `npm run build` 綠；瀏覽器 /sync 三區塊、/coros 重導。
- **IMPLEMENT**:
  - `SyncPage.tsx`：用 `useCorosStatus`+`useCorosLogin`+`useCorosSync` 餵 COROS 卡；`useTpStatus`+`useTpLogin`+`useSync` 餵 TP 卡；`useQueryClient().invalidateQueries(['sync_inventory'])` 作 `onSynced`。頂部 `<SyncInventoryPanel />`。
  - `App.tsx`：`import { SyncPage }`；`<Route path="sync" element={<SyncPage />} />`；`<Route path="coros" element={<Navigate to="/sync" replace />} />`；移除 CorosPage import。
  - `AppShell.tsx`：NAV_ITEMS 的 `{ to: '/coros', label: 'Coros' }` → `{ to: '/sync', label: '同步' }`。
  - 刪 `pages/CorosPage.tsx`。
- **MIRROR**: CorosPage 組裝、App.tsx 既有路由、ROUTE/NAV_ITEM
- **GOTCHA**: TP 帳號用 username 欄位；COROS 用 email。ProviderSyncCard 的 `onLogin(id, pw)` 由各自 hook 映射。
- **VALIDATE**: `npm run build` 綠 + 手測 /sync 與 /coros 重導
- **COMMIT**: `feat(coros-sync): unified SyncPage + retire CorosPage`

---

## Testing Strategy

### Unit Tests
| Test | Input | Expected | Edge? |
|---|---|---|---|
| sync_inventory | coros+local 各1 | total 2, by_source 正確 | N |
| sync_inventory | 空 athlete | total 0, date None | Y |

### Edge Cases Checklist
- [ ] 空 DB → total 0、date None、last_sync 皆 None 不崩
- [ ] TP 未登入 → TP 卡顯示登入表單
- [ ] 同步完成 → 盤點 invalidate 重抓

---

## Validation Commands

### Backend
```bash
cd "<repo>reverse"
/opt/homebrew/bin/pytest backend/tests/ -q
```
EXPECT: 全 pass（含新 test_sync_inventory）

### Frontend
```bash
cd "<repo>reverse/frontend"
npx tsc -b && npm run build
```
EXPECT: 型別零錯誤、build 成功

### Integration (smoke)
```bash
cd "<repo>reverse"
/opt/homebrew/bin/python3.12 -m uvicorn backend.main:app --port 8021 &
sleep 3
curl -s "http://localhost:8021/api/v1/sync/inventory?athlete_id=1" | head -c 300
```
EXPECT: 回真實盤點 JSON

### Manual
- [ ] /sync 顯示盤點 + COROS + TP 三區塊
- [ ] /coros 重導 /sync
- [ ] TP 登入後可見同步區（不實跑同步也可）

---

## Acceptance Criteria
- [ ] AC-1 盤點端點（test_sync_inventory）
- [ ] AC-2 TP 同步端點存在並接既有 client（既有 /sync/start，本計畫接 UI）
- [ ] AC-3 同步頁三區塊 + /coros 重導
- [ ] AC-4 同步後盤點 invalidate 更新
- [ ] tsc + build + 後端測試全綠

## Completion Checklist
- [ ] 沿用既有 SSE / React Query / Card 模式
- [ ] 無 schema 變更
- [ ] 無殘留 CorosPage 死連結
- [ ] 自足

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| TP token 已失效 | M | L | 失效則 TP 卡顯示登入；不影響 COROS 與盤點 |
| ProviderSyncCard 抽象漏掉 COROS 既有行為 | M | M | 先確保 COROS 卡行為等價再接 TP |

## Notes
TP 後端（login/status/sync）與 `useSync` hook 皆已存在；本計畫主要是 UI 接線 + 盤點端點，非新後端能力。盤點純讀，零 migration。
