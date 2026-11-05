# MERIT v2 native live Docker distribution, 2026-11-05.
FROM python:3.10.12-slim-bullseye@sha256:9e114b417be57d1c2b5ebb9770e415a9356f923c54d24827000378f625148363
LABEL org.opencontainers.image.title="MERIT-ML v2 live" \
      org.opencontainers.image.version="v2-live-20261105-r5" \
      org.opencontainers.image.source="https://github.com/BiosystemEngineeringLab-IITB/merit-ml" \
      org.opencontainers.image.description="Native live Workbench assessment; per-matrix resource isolation; no cohort result cache"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
    MERIT_PROJECT_GUARD_ROOT=/app MERIT_MATRIX_REUSE_MB=256
WORKDIR /app
COPY docker/merit-v2/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --uid 10001 --create-home merit
COPY --chown=merit:merit docker/merit-v2/runtime /app
RUN python relocate_bundle.py \
    && python -c "import numpy, scipy, sklearn, pandas, flask" \
    && mkdir -p /app/runs && chown merit:merit /app/runs
USER merit
VOLUME ["/app/runs"]
EXPOSE 8791
HEALTHCHECK --start-period=30s --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8791/healthz',timeout=4)"
ENTRYPOINT ["python", "v2_server.py", "--host", "0.0.0.0", "--port", "8791"]
