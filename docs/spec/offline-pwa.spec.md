# Module Spec: offline-pwa

> **Last Updated**: 2026-10-13
> **Status**: Active
> **Domain Layer**: Supporting

## Overview

The web app installs to a phone's home screen and keeps a **read-only offline copy** of what a
runner needs without signal (on a mountain): today's and the next weeks' sessions (課表), the
week and the training status (總覽), and — when it fits — the activity list. Offline, the pages
open from the copy, a bar says **from when** the data is, and every change is refused with the
reason (never queued). Back online the copy refreshes by itself. One copy per browser, of the
person signed in there; it goes on sign-out and when someone else signs in. Not in the demo.

Tickets: SP-314 (this), SP-319 (the tiers and the 20 MB budget, `docs/research/fit-on-device.md`
§9.2, §9.3, §12 #1), SP-307 (`docs/research/local-first-sync.md` §7, §10, §11 #7), SP-322 (設定 ›
這台裝置的離線資料: size, tiers, persistence, the home-screen hint; §3, §12 #2). Follow-up: SP-325
(charts drawn on the device — the API the empty p2 tier waits for).

## Architecture

```
 shell.js (every page) ──register("/sw.js", scope "/")──► sw.js (service worker; thin)
      │  postMessage {session | sync | logout | status}            │ importScripts
      │  fetch wrapper: writes refused offline,                    ▼
      │  x-trc-cache → the offline bar                    offline_core.js  (rules + engine, node-tested)
      │                                                     classify · userKeyOf · warmDerived ·
      ▼                                                     pickFallback · planEviction · createEngine
 window.AppOffline {active, slow, at, clear(), status()}            │ Cache Storage
      ▲ schedule.html editable()                                    ▼
                                         trc-app-<build>  ·  trc-u-<person>-p0|p1|p2  ·  trc-meta
 backend/api/pwa.py: GET /sw.js (build id = digest of static/), GET /manifest.webmanifest (i18n)
 backend/accounts/login.py PUBLIC_PATHS += /sw.js, /manifest.webmanifest (exact)
```

| Layer | Responsibility | Entry point |
|---|---|---|
| Routes | `/sw.js` (no-cache, `__TRC_BUILD__` → `build_id()`), `/manifest.webmanifest` (name in the request's language) | `backend/api/pwa.py` |
| Worker | install / activate / fetch / message → the engine | `backend/static/sw.js` |
| Rules + engine | what is kept, whose, when refilled, what is dropped | `backend/static/offline_core.js` |
| Pages | register, offline bar, write guard, locked controls, sign-out clear, 加到主畫面 hint, manifest links | `backend/static/shell.js` (`// ---- offline copy (SP-314)`) |
| Fallback page | a page not kept, offline | `backend/static/offline.html` (+ `i18n/*/offline.json`) |
| Settings block | 設定 › 資料同步 › 這台裝置的離線資料 (SP-322) | `backend/static/device_storage.js` (`window.DeviceStorage`), `settings.html` `#device` (+ `settings.device.*`) |
| Icons | any (rounded), maskable, apple-touch | `backend/static/icons/` |

## What is kept (tiers, SP-319)

One fixed budget per browser: **20 MB** (`BUDGET`), program included.

| Tier | Contents | Refill | Status |
|---|---|---|---|
| app | the cached pages' scripts + what shell.js loads, `i18n.js`, ECharts 5.5.0 (CORS) | at install (each new build) and on use | done |
| **p0** | pages 總覽, 課表, the offline page; `status`, `weekplan`, `z5`, `b2b`, `summary` (week 16 / month 12 / year 6), `pmc` (7 / 42 / 90 days to the server's today), `plan/calendar` (總覽's week, this + next 2 weeks, this + next month grid), `plan/prefs`, `plan/prefs/gate`, `plan/equivalence`, `sync/settings`, `region` | every 15 min, at once when its cache is gone, forced on `online` | done |
| p1 | 活動列表 page, `wko5/activities` (every activity's summary), `activities/stats`, `achievements?include_hidden=true` | every 6 h / when gone | done (3-year summaries); CTL/TSB for 3 years, the 90-day chart data and tracks wait for SP-325 |
| p2 | — | daily / when gone | the tier exists (refill order, eviction, quota); FIT and 1-year chart data need an API first |

- **Only GET, only the listed paths** (`classify`): `API`, `PAGES`, the static files (not `*.html`
  under `/api/v1/static/`, which carry their reader's session), the listed CDN file. Everything
  else — `/api/v1/session`, `/fresh`, `dataset/status`, sign-in, `/share/*` — is never kept.
  Writes are left to the browser untouched (the shell refuses them offline).
- The day-dependent URLs are built from the server's `today` (GET `/status`) and week (GET
  `/weekplan`) just kept (`warmDerived`), exactly as the pages ask for them.
- Another day offline (`pickFallback`): pages — the same path, newest; data — the same path and
  non-date parameters; a range asked for → the narrowest copy covering it, else the most overlap;
  **a calendar only from a copy covering the days asked** (never an empty week shown as real).

## Answering (network first)

1. The network answers → returned; a 200 that wasn't redirected is kept (headers minus
   `content-length`, `content-encoding`, `set-cookie`, plus `x-trc-stored-at`).
2. No answer, a 5xx (the tunnel's 502 / 530 while the server is down) or slower than 6 s (pages,
   program) / 12 s (data) **and a copy exists** → the copy, with `x-trc-cache: offline | slow`;
   a page from the copy gets `window.TRC_OFFLINE_PAGE = {at, reason}` before its scripts.
   A late network answer still refreshes the copy (`waitUntil`).
3. A page not kept, offline → `offline.html` from p0 (links to the pages that are); nothing kept →
   the browser's own error.

## Whose copy (multi-tenant)

- Key `userKeyOf(session)`: `u<id>` signed in, `owner` in the single-user app; signed out, demo,
  unknown → none (nothing is kept, nothing answers from a copy).
- The worker asks GET `/api/v1/session` at every `sync`; the page tells it its `TRC_SESSION` on
  load. **Another key than the copy's → every person's caches are deleted first** (`setUser` →
  `clearUser`); work in flight for the previous person stops (`gen`), and an entry put meanwhile
  is removed again.
- **Sign-out** (shell.js 登出): the worker is told, and the page deletes `trc-u-*` and `trc-meta`
  itself, before POST `/api/v1/account/logout` (at most 1.5 s).
- A **401**, or a cached page answered by a redirect (to `/login` / `/signup`), deletes the copy.
- The server's answers keep `private, no-store` (no shared cache); the Cache Storage copy is the
  browser's own, per origin.
- Safari (iOS): a Safari tab and a home-screen app are **separate** storage and separate cookies:
  each has its own session and its own copy; signing out in one does not touch the other.

## Over the budget / storage full

- After each store, `planEviction`: total over 20 MB → drop **p2** entries (oldest first), then
  **p1**; p0 and the program are never dropped.
- `QuotaExceededError` on a put: a p0 entry may push out p2, then p1, retrying after each; a p1
  entry only p2; a p2 entry nothing (it's not kept).
- Cleared by the browser (Safari's 7 days, storage pressure): the tier whose cache is gone is
  refilled at the next sync, **p0 → p1 → p2**. An installed app (`display-mode: standalone`) asks
  `navigator.storage.persist()`.

## Pages (shell.js)

- Registration on `load`, outside the demo, secure contexts only; `updateViaCache: "none"`.
- **Offline bar** (`.an-offline`, under the nav): 「離線：顯示 {at} 存下的資料，不能修改」(the oldest
  `x-trc-stored-at` on the page, else 「打開這頁時的資料」); slow: 「連線很慢：先顯示 {at} 存下的資料」
  (no lock); back online after showing the copy: 「已連上網路 · 重新整理」 and the worker refreshes p0.
- **Writes offline**: same-origin non-GET → 503 `{code: "OFFLINE"}` without sending; a toast with
  the reason when the user just clicked. Edit controls (`OFFLINE_LOCK`: submit buttons, file
  inputs, 推送 / 抓活動 / 重排 / 套用 / 同意 / 拒絕 / 復原 / suggestion 排入 … and any
  `[data-offline-lock]`) are dimmed (`html[data-trc-offline]`) and a click only shows the reason.
- 課表: `editable()` is false offline — sessions open read-only, nothing drags.
- 加到主畫面: manifest + icons + `apple-mobile-web-app-*` added to `<head>` by the shell; on an
  iPhone / iPad in Safari (not installed), 總覽 shows the hint once until 「知道了」 (`trc.a2hs`).
- `AppOffline.status()` → `{user, version, budget, bytes: {app, p0, p1, p2}, total, tiers, present: {p0, p1, p2}}`
  (`present`: the tier's cache still exists — the browser may delete one while `tiers` still has its record).

## 設定 › 這台裝置的離線資料 (SP-322)

`settings.html` `#device` on the 資料同步 tab; `device_storage.js` reads, builds a model, renders
(`read` → `view` → `html`; `mount` wires it to the page). Device-local, nothing sent to the server.

| Row | Source | Shown |
|---|---|---|
| 用量 | `navigator.storage.estimate().usage` (the whole origin) | `{mb} MB／預算 20 MB` + a bar (capped at 100 %, warn colour and a note over the budget) + the copy's own `status().total` |
| P0 / P1 / P2 | `AppOffline.status()` (the worker's `tiers`, `bytes`, `present`; never recomputed) | 已存好 · MB / 部分存好（n 項沒存到）/ 沒存到 (ok 0, failed > 0) / 空間不夠，已先清掉 (ok > 0, 0 bytes) / 目前沒有要存的資料 (ok 0, failed 0: P2 until SP-325) / 還沒存 (no record, cache gone, nobody signed in) |
| 持久保存 | `navigator.storage.persisted()` | 是 / 否 + 「要求持久保存」(`persist()`; the answer: 已取得 / 瀏覽器沒有同意 / 不能要求) |
| 最後更新 | the newest `tiers[*].at` | device-local `YYYY-MM-DD HH:mm`, 還沒存過, + 「重新讀取」 |
| 加到主畫面 | `isIosTab`: iPhone / iPod / iPad (incl. iPadOS as `MacIntel` + touch), not `navigator.standalone`, not `display-mode: standalone` | a note: Safari clears a tab's storage after 7 days without the site; the home-screen app is exempt and keeps its own copy (sign in again there) |

- The budget is fixed at 20 MB (`DeviceStorage.BUDGET` = `offline_core.js` `BUDGET`, tested); the
  user does not choose it (SP-319).
- Any API missing, throwing, rejecting or answering garbage → that row says 無法取得; the worker
  not answering (demo, no controller yet, `status()` null) → the three tiers and 最後更新 say
  無法取得. Nothing throws out of `read` / `view` / `mount`.
- settings.html is not a cached page (not in `PAGES`), so `device_storage.js` is not in `APP_FILES`.

## Updates

`/sw.js` carries `build_id()` — a digest of every file under `backend/static/` (re-read only when
a size / mtime changed). A deploy changing any static file → new bytes → the browser installs the
new worker on the next navigation (at least every 24 h), `skipWaiting` + `clients.claim`, the old
`trc-app-<build>` is deleted, the people's copies stay.

## API

| Method | Path | Auth | Answer |
|---|---|---|---|
| GET | `/sw.js` | public (exact) | the worker, `text/javascript`, `Cache-Control: no-cache`; 404 in the demo |
| GET | `/manifest.webmanifest` | public (exact) | `application/manifest+json`: name = `shell.brand`, `start_url` `/api/v1/overview/page`, `scope` `/`, standalone, icons; 404 in the demo |
| GET | `/api/v1/static/offline.html` | public prefix (as every static page) | the fallback page, rendered in the request's language |

## Testing

| Test | Covers |
|---|---|
| `backend/tests/test_offline_pwa.py` (node, fake Cache Storage + server) | `classify` (only listed GETs; writes, sign-in, shares untouched), `userKeyOf`, `warmDerived`, `pickFallback`, `planEviction`, `markOfflineHtml`; offline reads the copy / online refreshes it; slow network; 5xx; pages offline + offline page; nobody known → nothing kept; sync p0 → p1 → p2, throttles, a cleared tier refilled, forced p0; another person never sees the copy; sign-out / signed out / 401 / redirect delete it; writes never kept; budget and quota eviction; install / new version; CDN with CORS |
| `backend/tests/test_pwa_sp314.py` | the routes (owner mode), the public paths exact in sign-in mode (signed out / in), the demo mounts neither, `build_id`, the pre-cached program = what the cached pages load, every listed path is a GET route, the shell wiring (sign-out clears first), the texts in both languages |
| `backend/tests/test_static_js_syntax.py` | `sw.js`, `offline_core.js`, `device_storage.js` parse |
| `backend/tests/test_device_storage_sp322.py` (node, fake `navigator.storage` / `AppOffline`) | usage vs the 20 MB budget, the bar cap, each tier state, nobody / never refilled, persisted + asking (granted / denied / no API), every API missing / throwing / garbage → 無法取得, the iPhone-tab hint (Safari tab, `navigator.standalone`, `display-mode: standalone`, iPadOS, Mac, Android), the page wiring, both languages, the budget equal to the worker's |
| `backend/tests/test_offline_pwa.py::test_status_says_which_tiers_are_still_there` | `status().present` per tier |

## Domain Model

### Bounded Context
The device side of the app: what a browser keeps for its signed-in person, never the server's data model.

### Ubiquitous Language
| Term | Meaning |
|---|---|
| 離線資料 / the copy | the Cache Storage entries of one person on one browser (`trc-u-<key>-<tier>`) |
| tier (P0 / P1 / P2) | SP-319's priority: P0 must be there, P1 should, P2 when there is room |
| 存下的時間 (`x-trc-stored-at`) | when the entry was kept; the bar shows the oldest on the page |
| program (`trc-app-<build>`) | the scripts, shared by everyone on the browser (no data) |

### Domain Events
None stored server side.

## Decisions Log

| Date | Decision | By |
|---|---|---|
| 2026-10-07 | Offline = read-only; changes wait for the network (SP-307 Q1) | user |
| 2026-10-07 | Tiers P0 / P1 / P2, a fixed 20 MB, refill P0 → P1 → P2, drop from P2; ask iPhone users to add to the home screen (SP-319) | user |
| 2026-10-13 | Network first everywhere (a deploy is never hidden behind an old copy); the copy only on failure / 5xx / timeout | SP-314 |
| 2026-10-13 | Writes refused in the page, never queued (no sync of offline edits: SP-307 option B not chosen) | SP-314 |
| 2026-10-13 | `/sw.js` and `/manifest.webmanifest` public, exact match; the demo mounts neither | SP-314 |

## Open Questions

- P1's 3-year CTL/TSB, 90-day chart data and tracks, and all of P2 (FIT, 1-year chart data) need
  device-friendly APIs (SP-325).
- `Clear-Site-Data` on sign-out was not added (it would also clear the browser's other storage,
  e.g. page preferences); the shell clears the copy itself.

## Change History

| Date | Change |
|---|---|
| 2026-10-13 | Created (SP-314): service worker, tiers, offline bar, write guard, manifest + icons, multi-tenant clearing |
| 2026-10-13 | SP-322: 設定 › 這台裝置的離線資料 (`device_storage.js`, `#device` on 資料同步): usage vs 20 MB, P0/P1/P2 state, persistence + ask, last refresh, iPhone-tab home-screen hint, 無法取得 without the APIs; `status()` adds `present` per tier |
| 2026-10-13 | SP-470: `sesspop.js` (the 課表 card hover) added to `APP_FILES` — the offline 課表 page loads it |
