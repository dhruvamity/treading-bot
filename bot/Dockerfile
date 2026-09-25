# Scout for a server: records every Arcus perp (best bid/offer, trades and, with --depth, the top 10 book levels)
# around the clock and backtests the whole strategy menu on every market every 30 minutes, at each market's maximum
# leverage and below it. Public market data only: no API keys, no orders. Everything it writes is under /app/data,
# /app/state and /app/logs (mount them; see docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=UTC

WORKDIR /app
COPY pyproject.toml README.md ./
COPY bot ./bot
RUN pip install --no-cache-dir -e .
COPY config ./config

# healthy while the recorder has written its status in the last 15 minutes
HEALTHCHECK --interval=5m --timeout=10s --start-period=10m CMD python -c "import json,sys,time; s=json.load(open('/app/data/scout/recorder.json')); sys.exit(0 if time.time()-s['ts']<900 and not s['paused_for_disk'] else 1)"

ENTRYPOINT ["bot"]
CMD ["scout", "run", "--depth", "--workers", "auto"]
