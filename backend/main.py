import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.db.database import init_db
from backend.api import workouts, pmc, expr, dashboard, scan, sync, auth, athletes, analytics
from backend.api import ai, sports, wko5views
from backend.api import achievements as achievements_api
from backend.api import overview as overview_api
from backend.api import plan as plan_api
from backend.api import racepower as racepower_api
from backend.api import plan_sessions as plan_sessions_api
from backend.api import plan_auto as plan_auto_api
from backend.api import routes as routes_api
from backend.api import injuries as injuries_api
from backend.api import backup as backup_api
from backend.api import calib as calib_api


async def _ensure_athlete() -> None:
    """A runner without a WKO5 folder gets an empty athlete row on first start
    (db/current.py); with WKO5 the rows come from /athletes/bootstrap as before."""
    from backend.settings.paths import athlete_dir
    from backend.db.database import AsyncSessionLocal
    from backend.db.current import ensure_athlete
    try:
        if any(athlete_dir().glob("*.wko5athlete")):
            return
    except OSError:
        pass
    async with AsyncSessionLocal() as db:
        if await ensure_athlete(db):
            await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from backend.sync import scheduler
    await init_db()
    await _ensure_athlete()
    # Sync endpoints run in AnyIO's worker threads (40 by default). While a
    # Dataset builds, every chart request of a page waits in one (single
    # flight, wko5views._dataset); with 40 the static files and the other
    # pages queued behind them. Waiting threads cost nothing, so allow more.
    import anyio.to_thread
    anyio.to_thread.current_default_thread_limiter().total_tokens = int(os.getenv("WKO5COACH_THREADS", "200"))
    # build the active data source's Dataset now, not on the first page load
    # (FIT parsing in a process pool, fitcache.py); WKO5COACH_NO_WARMUP=1 disables
    wko5views.warm_up("startup")
    # daily auto-sync (sync.schedule.daily_time); WKO5COACH_NO_SCHEDULER=1 disables
    task = None if os.getenv("WKO5COACH_NO_SCHEDULER") else asyncio.create_task(scheduler.loop())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="WKO5 Coach", lifespan=lifespan)

_cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:8000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workouts.router)
app.include_router(pmc.router)
app.include_router(expr.router)
app.include_router(dashboard.router)
app.include_router(scan.router)
app.include_router(sync.router)
app.include_router(auth.router)
app.include_router(athletes.router)
app.include_router(analytics.router)
app.include_router(ai.router)
app.include_router(sports.router)
app.include_router(wko5views.router)
app.include_router(achievements_api.router)
app.include_router(plan_api.router)
app.include_router(plan_auto_api.router)
app.include_router(plan_sessions_api.router)
app.include_router(overview_api.router)
app.include_router(racepower_api.router)
app.include_router(racepower_api.share_router)      # /share/<id>: the only path meant to skip the tunnel's password
app.include_router(routes_api.router)
app.include_router(routes_api.workout_router)
app.include_router(injuries_api.router)          # 傷病紀錄 (404 in the demo mode)
app.include_router(backup_api.router)            # 備份 (settings page)
app.include_router(calib_api.router)             # 每人校正 (settings page, 進階設定)

# shared page assets (shell.js: the app-wide navigation every page includes)
app.mount("/api/v1/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="pages-static")

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
else:
    @app.get("/", include_in_schema=False)
    def _home():
        return RedirectResponse("/api/v1/overview/page")
