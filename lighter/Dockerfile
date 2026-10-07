# The Lighter scout for a server: records every Lighter perp and scans every 30 minutes. No keys, no orders.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 LBOT_ROOT=/app
COPY pyproject.toml README.md ./
COPY lighter_bot ./lighter_bot
COPY config ./config
RUN pip install --no-cache-dir .
HEALTHCHECK --interval=5m --timeout=10s --start-period=10m \
  CMD python -c "import json,time,sys; d=json.load(open('/app/data/recorder.json')); sys.exit(0 if time.time()-d['t']<900 else 1)"
CMD ["lighter", "scout", "run"]
