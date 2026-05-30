# =============================================================================
# MCP Server — Production Docker Image (Single-Stage)
# =============================================================================
FROM python:3.13.12-slim@sha256:f1927c75e81efd1e091dbd64b6c0ecaa5630b38635a3d1c04034ac636e1f94c8

# Set Python to run unbuffered and not write .pyc files
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

# Application configuration (no OpenTelemetry)
ENV LOG_LEVEL=INFO \
    DEPLOYMENT_ENVIRONMENT=production \
    SERVICE_VERSION=1.0.0 \
    HOST=0.0.0.0 \
    PORT=8001

# Install only the packages needed for health checks and runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non‑root user
RUN groupadd -g 1000 appgroup \
    && useradd -m -u 1000 -g appgroup -s /usr/sbin/nologin appuser \
    && mkdir -p /app \
    && chown -R appuser:appgroup /app

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY src/ /app/src/

# Remove setuid/setgid bits and world‑writable directories for security
RUN find /app -type f -perm /6000 -exec chmod ug-s {} \; 2>/dev/null || true \
    && find /app -type d -perm /o+w -exec chmod o-w {} \; 2>/dev/null || true

USER appuser
EXPOSE 8001

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --fail --silent --max-time 5 http://127.0.0.1:8001/healthz || exit 1

CMD ["python", "-u", "/app/src/main.py"]