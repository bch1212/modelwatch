"""ModelWatch — Continuous behavioral drift monitoring for LLM applications."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.models.database import get_engine, Base
from app.services.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables + start scheduler on startup, clean up on shutdown."""
    eng = get_engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")

    start_scheduler()
    yield
    stop_scheduler()
    await eng.dispose()


app = FastAPI(
    title="ModelWatch API",
    description="Continuous behavioral drift monitoring for LLM-powered applications",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # lock down in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routers ────────────────────────────────────────────────

from app.routers import auth, workspaces, endpoints, specs, dashboard, billing, badge  # noqa: E402

app.include_router(auth.router)
app.include_router(workspaces.router)
app.include_router(endpoints.router)
app.include_router(specs.router)
app.include_router(dashboard.router)
app.include_router(billing.router)
app.include_router(badge.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "modelwatch"}


@app.get("/")
async def root():
    return {
        "service": "ModelWatch",
        "version": "0.1.0",
        "description": "Continuous behavioral drift monitoring for LLM applications",
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
