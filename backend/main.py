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
from backend.api import workouts, expr, sync, auth
from backend.api import wko5views
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
from backend.api import calendar_feed as calendar_api
from backend.api import altitude_nights as altitude_nights_api
from backend.api import debug as debug_api


# pages the demo never serves: the WKO5 comparison, the settings and the injury log
DEMO_HIDDEN_PAGES = {"compare", "settings", "injuries"}

# the removed React SPA's routes that have a static page (bookmarks keep working)
OLD_SPA_PATHS = {
    "/overview": "/api/v1/overview/page",
    "/activities": "/api/v1/wko5/activities/page",
    "/achievements": "/api/v1/achievements/page",
    "/sync": "/api/v1/wko5/settings",
    "/config": "/api/v1/wko5/settings",
}


async def _ensure_athlete() -> None:
    """The install's athlete row, created on first start (db/current.py): pointing
    at the WKO5 athlete folder when there is one, else empty (a COROS / TP-only
    runner). Replaces POST /api/v1/athletes/bootstrap, removed with the React SPA
    that was its only caller (2026-10-04)."""
    from backend.settings.paths import athlete_dir
    from backend.db.database import AsyncSessionLocal
    from backend.db.current import ensure_athlete
    data_dir = ""
    try:
        d = athlete_dir()
        if any(d.glob("*.wko5athlete")):
            data_dir = str(d)
    except OSError:
        pass
    async with AsyncSessionLocal() as db:
        if await ensure_athlete(db, data_dir):
            await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from backend.sync import scheduler
    demo = bool(getattr(app.state, "demo", False))
    # exceptions and performance to stderr + <data folder>/logs/app.log (SP-215, backend/applog.py)
    from backend import applog
    applog.setup()
    if demo:
        from backend.demo import instance as DI
        DI.startup_checks()          # refuses to start next to the owner's data (§3.1)
    await init_db()
    await _ensure_athlete()
    # 賽事大小 (SP-111): planning.event_size reads the race calculator and the personal climb
    # divisor; the engine stays pure (and its tests) until the app installs them
    from backend.engine import planning as _planning
    _planning.install_size_inputs()
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
        # quitting: the rain backfill (api/rain_backfill.py, SP-299) stops before its next call
        from backend.api import rain_backfill
        rain_backfill.STOP.set()
        # …and the charts' background renders (SP-336): queued ones dropped, running ones not waited for
        from backend.engine.wko5expr import render_cache
        render_cache.shutdown_refresh()
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
    connect / upload / backup routers, the demo landing at /demo, and the
    tenant middleware enforcing the demo rules. Default: WKO5COACH_MODE."""
    from backend import tenancy
    from backend.tenancy_mw import TenancyMiddleware
    from backend.api import session as session_api
    if demo is None:
        demo = tenancy.demo_mode()
    app = FastAPI(title="WKO5 Coach", lifespan=lifespan)

    _cors_origins = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:8000",
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

    owner_only = {id(r) for r in (sync.router, auth.router,
                                  plan_auto_api.router, injuries_api.router, backup_api.router, calib_api.router,
                                  calendar_api.router, calendar_api.feed_router,
                                  debug_api.router, debug_api.admin_router)}
    routers = [
        workouts.router, expr.router,
        sync.router, auth.router,        # owner only (sync, connect)
        wko5views.router, achievements_api.router,
        plan_api.router, plan_auto_api.router, plan_sessions_api.router, overview_api.router,
        altitude_nights_api.router,      # 睡在高處的紀錄 (SP-259, the 課表 calendar)
        racepower_api.router,
        racepower_api.share_router,      # /share/<id>: the only path meant to skip the tunnel's password
        routes_api.router, routes_api.workout_router,
        injuries_api.router,             # 傷病紀錄 (hidden in the demo)
        backup_api.router,               # 備份 (settings page)
        calib_api.router,                # 每人校正 (settings page, 進階設定)
        region_api.router,               # 地區 tw | intl (static/region.js)
        calendar_api.router,             # 課表訂閱 address (settings page; owner only)
        calendar_api.feed_router,        # /share/calendar/<token>.ics: public, token-only (owner only)
        session_api.router,              # GET /api/v1/session (the shell), POST /api/v1/demo/reset
        # debug API for AI agents (SP-371, docs/debug-api.md): Bearer token only, 404 unless turned on;
        # its settings block (設定 › 進階). Owner only: the demo never mounts either
        debug_api.router, debug_api.admin_router,
    ]
    for r in routers:
        # the demo never mounts sync / connect / auto-plan / backup / injuries (§3.1 item 4),
        # nor the public share pages or the 課表訂閱 feed (it creates no shares)
        if demo and (id(r) in owner_only or r is racepower_api.share_router):
            continue
        app.include_router(r)
    # the tenant of each request (and the demo's rules): outermost, so every route sees it
    app.add_middleware(TenancyMiddleware)
    # slow requests, 5xx and unhandled exceptions (with traceback) to the app log (SP-215);
    # outside the tenant middleware so its time counts too
    from backend.applog import RequestLogMiddleware
    app.add_middleware(RequestLogMiddleware)
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
    else:
        @app.get("/", include_in_schema=False)
        def _home():
            return RedirectResponse("/api/v1/overview/page")

        # bookmarks of the removed React SPA (frontend/, 2026-10-04) → their static page;
        # its pages without one (/running, /trail, /ai) are gone and 404
        def _to(target: str):
            return lambda: RedirectResponse(target)

        for old, target in OLD_SPA_PATHS.items():
            app.add_api_route(old, _to(target), methods=["GET"], include_in_schema=False)
        app.add_api_route("/activities/{workout_id}", _to(OLD_SPA_PATHS["/activities"]),
                          methods=["GET"], include_in_schema=False)
    return app


app = build_app()
