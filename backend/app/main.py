import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from backend.app.scheduler import scheduler
from backend.app.api import dashboard, resources, metrics, anomalies, actions, system, audit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from backend.app.db.asyncpg_pool import init_db_pool, close_db_pool
from backend.app.scheduler import setup_scheduler, scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting CloudSentry API...")
    await init_db_pool()
    # Initialize scheduler
    setup_scheduler()
    scheduler.start()
    yield
    logger.info("Shutting down CloudSentry API...")
    scheduler.shutdown()
    await close_db_pool()

app = FastAPI(
    title="CloudSentry API",
    version="1.0.0",
    description="Autonomous cloud cost intelligence API",
    lifespan=lifespan
)

app.include_router(resources.router, prefix="/api/v1/resources", tags=["resources"])
app.include_router(metrics.router, prefix="/api/v1/metrics", tags=["metrics"])
app.include_router(anomalies.router, prefix="/api/v1/anomalies", tags=["anomalies"])
app.include_router(actions.router, prefix="/api/v1/actions", tags=["actions"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])
app.include_router(system.router, prefix="/api/v1/system", tags=["system"])
app.include_router(audit.router, prefix="/api/v1/audit-logs", tags=["audit-logs"])

@app.get("/api/v1/health", tags=["system"])
async def health_check():
    return {
        "status": "ok",
        "db": "pending",
        "ml_model": "pending",
        "aws_connectivity": "pending",
        "telemetry": "pending",
        "automation": "pending"
    }
