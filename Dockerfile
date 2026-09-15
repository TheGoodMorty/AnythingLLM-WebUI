FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=1555

WORKDIR /app

RUN pip install --no-cache-dir flask requests waitress cheroot \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

COPY --chown=appuser:appuser server.py support.js sw.js manifest.webmanifest ./
COPY --chown=appuser:appuser ["AnythingLLM Console.dc.html", "./"]

USER appuser

EXPOSE 1555
VOLUME ["/data"]

CMD ["python", "server.py"]
