FROM docker.io/library/python:3.12.3-slim@sha256:afc139a0a640942491ec481ad8dda10f2c5b753f5c969393b12480155fe15a63

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

ENV LOG_LEVEL=INFO \
    DEPLOYMENT_ENVIRONMENT=production \
    SERVICE_VERSION=1.0.0 \
    HOST=0.0.0.0 \
    PORT=8000

# Install runtime dependencies and patch OS packages for CVEs
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 appgroup \
    && useradd -m -u 1000 -g appgroup -s /usr/sbin/nologin appuser \
    && mkdir -p /app \
    && chown -R appuser:appgroup /app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/
COPY compiled/ /app/compiled/
COPY data/ /app/data/
COPY alembic/ /app/alembic/
COPY alembic.ini /app/

RUN find /app -type f -perm /6000 -exec chmod ug-s {} \; 2>/dev/null || true \
    && find /app -type d -perm /o+w -exec chmod o-w {} \; 2>/dev/null || true

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent --max-time 5 http://127.0.0.1:8000/healthz || exit 1

CMD ["python", "-u", "/app/src/main.py"]