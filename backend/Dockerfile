FROM python:3.11-slim

WORKDIR /app

# No compiler or libpq in the runtime image: the compiled dependencies
# (asyncpg, numpy, pandas, scipy, scikit-learn, cryptography, grpcio) all ship
# manylinux wheels for this Python, and asyncpg speaks the Postgres protocol
# itself (no psycopg/libpq). Fewer tools in the image, less to patch.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY ml /app/ml
COPY data /app/data
COPY db /app/db
COPY scripts/apply_migrations.py /app/scripts/apply_migrations.py
RUN mkdir -p /app/models

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness only (no DB): a database outage should not get the container killed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=4).status == 200 else 1)"

# --proxy-headers: take the client IP from X-Forwarded-For, but only from
# proxies listed in FORWARDED_ALLOW_IPS (uvicorn default: 127.0.0.1). The
# per-IP rate limit depends on it.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
