# Meridian review console.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY data/seed/ ./data/seed/

# Build the database into the image so a cold start serves content
# immediately, with no LLM call and no key required.
RUN python -m src.ticket_agent.seed --seed 42 --db /app/data/meridian.db

# Run as a non-root user. The data directory stays writable so the app can
# record decisions; on a platform with a mounted disk, point DB_PATH at it.
RUN useradd --create-home --uid 10001 meridian \
    && chown -R meridian:meridian /app/data
USER meridian

ENV DB_PATH=/app/data/meridian.db \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/healthz')"

CMD ["sh", "-c", "uvicorn src.ticket_agent.web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
