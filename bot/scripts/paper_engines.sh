#!/bin/sh
# Run every prepared paper home (bot farm paperrun / bot/farm/crosscheck.py prepare_scout) in paper mode, each in its
# own process, until SECONDS_LEFT have passed; wait for all of them.
#   sh scripts/paper_engines.sh ../research/runs/<run>/paper-engine 53000
DIR="${1:?paper-engine folder}"
SECS="${2:-53000}"
cd "$(dirname "$0")/.." || exit 1
BOT="$(pwd)/.venv/bin/bot"
for home in "$DIR"/*/; do
    [ -f "$home/config/sessions/xcheck.yaml" ] || continue
    home="$(cd "$home" && pwd)"
    rm -rf "$home/state" "$home/logs" "$home/data" "$home/smoke.out"
    echo "$(date -u +%FT%TZ) start $(basename "$home")"
    BOT_HOME="$home" "$BOT" run xcheck --paper --seconds "$SECS" > "$home/run.out" 2>&1 &
    sleep 2
done
wait
echo "$(date -u +%FT%TZ) all paper engines finished"
