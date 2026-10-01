import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app import scheduler
from backend.app.api import actions, anomalies, audit, dashboard, metrics, resources, system
from backend.app.auth import require_viewer
from backend.app.config import settings
from backend.app.db.asyncpg_pool import close_db_pool, init_db_pool
from backend.app.logging_config import configure_logging
from backend.app.rate_limit import RateLimitMiddleware
from backend.app.services import runtime_config

configure_logging(settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


def _testing() -> bool:
    return os.getenv("CLOUDSENTRY_TESTING") == "1"


def validate_startup_config() -> None:
    """Refuse to boot with a configuration that cannot authenticate anyone or
    cannot reach the database: that is a silent outage, not a degraded mode."""
    problems = []
    if not settings.SUPABASE_JWT_SECRET and not (settings.SUPABASE_JWKS_URL or settings.SUPABASE_URL):
        problems.append("no JWT verification source (SUPABASE_JWT_SECRET or SUPABASE_URL/SUPABASE_JWKS_URL)")
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        problems.append("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY")
    if not settings.DATABASE_URL:
        problems.append("DATABASE_URL")
    if "*" in (settings.CORS_ORIGINS or ""):
        logger.warning("CORS_ORIGINS contains '*'; it is ignored (credentialed CORS requires explicit origins).")
    if settings.ML_ANOMALY_THRESHOLD is not None:
        logger.warning(
            "ML_ANOMALY_THRESHOLD is deprecated and ignored (it used the score_samples scale). "
            "Use ML_IF_DECISION_THRESHOLD (default 0.0)."
        )
    if problems:
        raise RuntimeError("CloudSentry misconfigured, missing: " + "; ".join(problems))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting CloudSentry API...")
    if not _testing():
        validate_startup_config()
    runtime_config.load_from_env()
    await run_in_threadpool(runtime_config.refresh_from_db)
    await init_db_pool()
    if not _testing() and settings.SCHEDULER_ENABLED:
        await scheduler.start()
    yield
    logger.info("Shutting down CloudSentry API...")
    await scheduler.shutdown()
    await close_db_pool()


_docs = settings.EXPOSE_API_DOCS
app = FastAPI(
    title="CloudSentry API",
    version="1.0.0",
    description="Autonomous cloud cost intelligence API",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    # JSON API: nothing should ever render or be cached by intermediaries.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        # The docs pages (when enabled) need their own CSP to load Swagger UI.
        if name == "Content-Security-Policy" and request.url.path in ("/docs", "/redoc"):
            continue
        response.headers.setdefault(name, value)
    return response


app.add_middleware(RateLimitMiddleware)
# Auth is a Bearer header, not cookies, so credentialed CORS is not needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak internals (AWS/Postgres error text) to clients.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Every router needs at least viewer; mutating routes add require_operator.
auth_deps = [Depends(require_viewer)]

app.include_router(resources.router, prefix="/api/v1/resources", tags=["resources"], dependencies=auth_deps)
app.include_router(metrics.router, prefix="/api/v1/metrics", tags=["metrics"], dependencies=auth_deps)
app.include_router(anomalies.router, prefix="/api/v1/anomalies", tags=["anomalies"], dependencies=auth_deps)
app.include_router(actions.router, prefix="/api/v1/actions", tags=["actions"], dependencies=auth_deps)
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"], dependencies=auth_deps)
app.include_router(system.router, prefix="/api/v1/system", tags=["system"], dependencies=auth_deps)
app.include_router(audit.router, prefix="/api/v1/audit-logs", tags=["audit-logs"], dependencies=auth_deps)


@app.get("/api/v1/health/live", tags=["system"])
async def liveness():
    """Process is up. Use for restart decisions; never depends on externals."""
    return {"status": "ok"}


@app.get("/api/v1/health", tags=["system"])
async def health_check():
    """Readiness: 503 when the database is unreachable."""
    from backend.app.services.health import collect_health
    body = await collect_health()
    code = 200 if body.get("status") in ("ok", "degraded") else 503
    return JSONResponse(content=body, status_code=code)
