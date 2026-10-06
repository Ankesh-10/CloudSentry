import hmac
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from backend.app import metrics as app_metrics
from backend.app import scheduler
from backend.app.api import actions, anomalies, audit, dashboard, metrics, resources, system
from backend.app.auth import require_viewer
from backend.app.config import settings
from backend.app.db.asyncpg_pool import close_db_pool, init_db_pool, is_transaction_pooler
from backend.app.db.schema import check_schema_version
from backend.app.logging_config import configure_logging
from backend.app.rate_limit import RateLimitMiddleware
from backend.app.request_context import REQUEST_ID_HEADER, RequestContextMiddleware
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
    elif is_transaction_pooler(settings.DATABASE_URL):
        # Session-level advisory locks (leader election) do not survive the
        # transaction pooler: two replicas could both run the scheduler.
        problems.append("DATABASE_URL points at a transaction pooler (port 6543 / pgbouncer); "
                        "use the session pooler (port 5432) or a direct connection")
    if settings.ALERT_WEBHOOK_URL and not settings.ALERT_WEBHOOK_URL.startswith("https://"):
        problems.append("ALERT_WEBHOOK_URL must be an https:// URL")
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
    await init_db_pool(required=not _testing())
    if not _testing():
        await check_schema_version()
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


def _route_template(request: Request) -> str:
    """Route template (never the raw path) so metric label cardinality stays
    bounded. Included routers report their path without the mount prefix, so
    re-attach the prefix the request path was served under."""
    route = getattr(request.scope.get("route"), "path", None)
    if route is None:
        return "unmatched"
    if route.startswith("/api/") or route.startswith("/metrics"):
        return route
    path = request.url.path
    prefix = next((p for p in ROUTER_PREFIXES if path == p or path.startswith(p + "/")), "")
    return prefix + route


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    start = time.perf_counter()
    status_class = "5xx"
    try:
        response = await call_next(request)
        status_class = f"{response.status_code // 100}xx"
        return response
    finally:
        route = _route_template(request)
        app_metrics.inc("cloudsentry_http_requests_total", method=request.method, route=route, status=status_class)
        app_metrics.observe("cloudsentry_http_request_duration_seconds", time.perf_counter() - start, route=route)


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
    allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
    expose_headers=[REQUEST_ID_HEADER],
)
# Added last = outermost: every response, including 429s, carries a request id.
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak internals (AWS/Postgres error text) to clients.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Every router needs at least viewer; mutating routes add require_operator.
auth_deps = [Depends(require_viewer)]

ROUTERS = [
    (resources.router, "/api/v1/resources", "resources"),
    (metrics.router, "/api/v1/metrics", "metrics"),
    (anomalies.router, "/api/v1/anomalies", "anomalies"),
    (actions.router, "/api/v1/actions", "actions"),
    (dashboard.router, "/api/v1/dashboard", "dashboard"),
    (system.router, "/api/v1/system", "system"),
    (audit.router, "/api/v1/audit-logs", "audit-logs"),
]
# Longest first so the most specific prefix wins in _route_template.
ROUTER_PREFIXES = sorted((p for _, p, _ in ROUTERS), key=len, reverse=True)
for _router, _prefix, _tag in ROUTERS:
    app.include_router(_router, prefix=_prefix, tags=[_tag], dependencies=auth_deps)


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request):
    """Disabled (404) unless METRICS_TOKEN is set; then bearer-token protected."""
    token = settings.METRICS_TOKEN
    if not token:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"},
                            headers={"WWW-Authenticate": "Bearer"})
    return PlainTextResponse(app_metrics.render(), media_type="text/plain; version=0.0.4")


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
