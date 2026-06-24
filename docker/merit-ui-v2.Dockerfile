FROM python:3.12-slim

LABEL org.opencontainers.image.title="MERIT ML Readiness UI" \
      org.opencontainers.image.description="Thin local MERIT web UI; assessment artifacts are fetched from the hosted MERIT endpoint/R2 at runtime" \
      org.opencontainers.image.source="https://github.com/BiosystemEngineeringLab-IITB/merit-ml" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MERIT_CACHE_ONLY=1 \
    MERIT_UI_PRECOMPUTED_ROOT=https://pub-acf151eb41e04ee795a86a8049d54039.r2.dev/merit-cache/releases/v7.2026-04-30-190939.metabatch-annotation-compatibility/ \
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

EXPOSE 8773

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8773/healthz', timeout=3).read()"

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8773} --workers 2 --threads 8 --timeout 180 api.index:app"]
