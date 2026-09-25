#!/bin/sh
# Wait until the Arcus API answers, then start the paper farm (paper only: it records public data and simulates
# orders). Used where the network may open later, e.g. a cloud session whose network policy is being changed.
#   nohup sh scripts/farm_when_reachable.sh 15 > logs/farm_wait.out 2>&1 &
# Arguments: hours (default 15), then any extra `bot farm run` options.
HOURS="${1:-15}"
[ $# -gt 0 ] && shift
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
while :; do
    code=$(curl -sS -m 10 -o /dev/null -w "%{http_code}" https://api.arcus.xyz/v1/markets 2>/dev/null || true)
    if [ -n "$code" ] && [ "$code" != "000" ]; then
        echo "$(date -u +%FT%TZ) arcus reachable (HTTP $code): starting the farm for $HOURS h"
        exec .venv/bin/bot farm run --hours "$HOURS" "$@" > logs/farm.out 2>&1
    fi
    sleep 60
done
