# =============================================================================
# Agent Service — Production Docker Image (Single-Stage ARM64)
# =============================================================================
FROM docker.io/library/python:3.12.3-slim@sha256:afc139a0a640942491ec481ad8dda10f2c5b753f5c969393b12480155fe15a63

# Set Python to run unbuffered and not write .pyc files
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

# Application configuration
ENV LOG_LEVEL=INFO \
    DEPLOYMENT_ENVIRONMENT=production \
    SERVICE_VERSION=1.0.0 \
    HOST=0.0.0.0 \
    PORT=8000

# Install only the packages needed for health checks and runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN groupadd -g 1000 appgroup \
    && useradd -m -u 1000 -g appgroup -s /usr/sbin/nologin appuser \
    && mkdir -p /app \
    && chown -R appuser:appgroup /app

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ /app/src/
COPY compiled/ /app/compiled/
COPY data/ /app/data/

# Copy Alembic migrations (needed for human_overrides table)
COPY alembic/ /app/alembic/
COPY alembic.ini /app/

# Security hardening
RUN find /app -type f -perm /6000 -exec chmod ug-s {} \; 2>/dev/null || true \
    && find /app -type d -perm /o+w -exec chmod o-w {} \; 2>/dev/null || true

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent --max-time 5 http://127.0.0.1:8000/healthz || exit 1

CMD ["python", "-u", "/app/src/main.py"]