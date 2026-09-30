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


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from backend.sync import scheduler
    await init_db()
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
app.include_router(overview_api.router)
app.include_router(racepower_api.router)

# shared page assets (shell.js: the app-wide navigation every page includes)
app.mount("/api/v1/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="pages-static")

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
else:
    @app.get("/", include_in_schema=False)
    def _home():
        return RedirectResponse("/api/v1/overview/page")
