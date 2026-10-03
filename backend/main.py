import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.db.database import init_db
from backend.i18n import UserError
from backend.i18n.pages import STATIC as PAGES_DIR, render_page
from backend.request_context import RequestContextMiddleware
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
from backend.api import region as region_api


# pages the demo never serves: the WKO5 comparison, the settings and the injury log
DEMO_HIDDEN_PAGES = {"compare", "settings", "injuries"}


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
    demo = bool(getattr(app.state, "demo", False))
    if demo:
        from backend.demo import instance as DI
        DI.startup_checks()          # refuses to start next to the owner's data (§3.1)
    await init_db()
    await _ensure_athlete()
    # 資料來源: an old 自動 / unset setting becomes the source it picked, before
    # the synchronous readers (chart Dataset, CP scan) look at it
    try:
        from backend.sync import primary as P
        await P.migrate()
    except Exception as e:               # noqa: BLE001 — readers fall back to COROS
        import logging
        logging.getLogger(__name__).warning("data source migration failed: %s", type(e).__name__)
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
    if demo:
        # no sync in the demo; its janitor (expired sandboxes) and weekly data rebuild instead
        task = asyncio.create_task(DI.loop())
    else:
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


async def _user_error(request, exc: UserError):
    """UserError (backend/i18n): {"detail": {"code", "message" (translated), "params"}}."""
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail()})


def build_app(demo: bool | None = None) -> FastAPI:
    """The app. demo=True: the demo instance (auth-and-demo plan §3): no sync /
    connect / upload / AI / backup routers, the demo landing at /demo, and the
    tenant middleware enforcing the demo rules. Default: WKO5COACH_MODE."""
    from backend import tenancy
    from backend.tenancy_mw import TenancyMiddleware
    from backend.api import session as session_api
    if demo is None:
        demo = tenancy.demo_mode()
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
    # the request's language (and later its tenant) in contextvars: backend/request_context.py
    app.add_middleware(RequestContextMiddleware)


    app.add_exception_handler(UserError, _user_error)

    owner_only = {id(r) for r in (scan.router, sync.router, auth.router, athletes.router, ai.router,
                                  plan_auto_api.router, injuries_api.router, backup_api.router, calib_api.router)}
    routers = [
        workouts.router, pmc.router, expr.router, dashboard.router,
        scan.router, sync.router, auth.router, athletes.router,          # owner only (sync, connect, scan)
        analytics.router, ai.router, sports.router, wko5views.router, achievements_api.router,
        plan_api.router, plan_auto_api.router, plan_sessions_api.router, overview_api.router,
        racepower_api.router,
        racepower_api.share_router,      # /share/<id>: the only path meant to skip the tunnel's password
        routes_api.router, routes_api.workout_router,
        injuries_api.router,             # 傷病紀錄 (hidden in the demo)
        backup_api.router,               # 備份 (settings page)
        calib_api.router,                # 每人校正 (settings page, 進階設定)
        region_api.router,               # 地區 tw | intl (static/region.js)
        session_api.router,              # GET /api/v1/session (the shell), POST /api/v1/demo/reset
    ]
    for r in routers:
        # the demo never mounts sync / connect / scan / AI / auto-plan / backup / injuries (§3.1 item 4),
        # nor the public share pages (it creates no shares)
        if demo and (id(r) in owner_only or r is racepower_api.share_router):
            continue
        app.include_router(r)
    # the tenant of each request (and the demo's rules): outermost, so every route sees it
    app.add_middleware(TenancyMiddleware)
    app.state.demo = demo

    @app.get("/api/v1/static/{name}.html", include_in_schema=False)
    def _static_page(name: str):
        """A page opened by its file name (compare.html): rendered like the routed pages
        (language, inlined catalog), not served raw by the mount below."""
        if not name.replace("_", "").isalnum() or not (PAGES_DIR / f"{name}.html").is_file():
            raise HTTPException(404, "page not found")
        if demo and name in DEMO_HIDDEN_PAGES:
            raise HTTPException(404, "page not found")
        return render_page(name)


    # shared page assets (shell.js: the app-wide navigation every page includes)
    class _RevalidatingStatic(StaticFiles):
        """Page scripts / styles: the browser must revalidate on every load (ETag → 304 when
        unchanged), so a deploy is never hidden behind a heuristically cached old shell.js."""

        def file_response(self, *args, **kwargs):
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "no-cache"
            return resp


    app.mount("/api/v1/static", _RevalidatingStatic(directory=str(Path(__file__).parent / "static")), name="pages-static")

    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if demo:
        @app.get("/demo", include_in_schema=False)
        def _demo_landing():
            return render_page("demo")

        @app.get("/", include_in_schema=False)
        def _demo_home():
            return RedirectResponse("/demo")

        @app.get("/robots.txt", include_in_schema=False)
        def _robots():
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse("User-agent: *\nAllow: /demo$\nDisallow: /\n")

        @app.get("/healthz", include_in_schema=False)
        def _healthz():
            return {"ok": True}
    elif frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
    else:
        @app.get("/", include_in_schema=False)
        def _home():
            return RedirectResponse("/api/v1/overview/page")
    return app


app = build_app()
