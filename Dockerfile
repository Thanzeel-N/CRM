# ─────────────────────────────────────────────
# Meta Lead Ads CRM — Production Dockerfile
# ─────────────────────────────────────────────

FROM python:3.12-slim AS base

# Security: run as non-root
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

# Install system deps (needed for mysqlclient / bcrypt compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer-cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Optional: copy Google service account credentials (mount as secret in production)
# COPY credentials/google-service-account.json /app/credentials/

RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000

# Gunicorn with uvicorn workers — adjust --workers based on CPU cores (2*cores+1)
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
