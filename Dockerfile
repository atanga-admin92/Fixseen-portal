# ─── Stage 1: Python dependencies ────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# System deps for pikepdf (needs qpdf) and psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libqpdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ─── Stage 2: Runtime image ───────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

# Runtime libs only (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libqpdf28t64 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy project source
COPY . .

# Collect static files
RUN python manage.py collectstatic --noinput

# Create non-root user for security
RUN useradd --system --no-create-home fixseen && \
    chown -R fixseen:fixseen /app
USER fixseen

EXPOSE 8000

# ─── Entrypoint ───────────────────────────────────────────────────────────────
# Coolify will override this CMD per service:
#   Web:    gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
#   Worker: celery -A config worker --concurrency=2 --loglevel=info
#   Beat:   celery -A config beat --loglevel=info
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
