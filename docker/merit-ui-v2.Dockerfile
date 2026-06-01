FROM python:3.12-slim

LABEL org.opencontainers.image.title="MERIT ML Readiness UI" \
      org.opencontainers.image.description="Local MERIT web UI with bundled precomputed Workbench assessment cache" \
      org.opencontainers.image.source="https://github.com/<OWNER>/<REPO>" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MERIT_CACHE_ONLY=1 \
    MERIT_UI_PRECOMPUTED_ROOT=/opt/merit/merit-cache-workbench-full-v7 \
    PORT=8773

WORKDIR /opt/merit/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY merit-ui-v2/pyproject.toml merit-ui-v2/README.md ./
COPY merit-ui-v2/api ./api
COPY merit-ui-v2/merit ./merit
COPY merit-ui-v2/static ./static
COPY merit-ui-v2/Logo.png ./Logo.png

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir . gunicorn

COPY merit-cache-workbench-full-v7 /opt/merit/merit-cache-workbench-full-v7

EXPOSE 8773

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8773/healthz', timeout=3).read()"

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8773} --workers 2 --threads 8 --timeout 180 api.index:app"]
