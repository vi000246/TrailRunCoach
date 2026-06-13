import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.db.database import init_db
from backend.api import workouts, pmc, expr, dashboard, scan, sync, auth, athletes, analytics
from backend.api import ai, sports


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


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

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
