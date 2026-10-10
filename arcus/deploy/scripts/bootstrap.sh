#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server (a VPS). Run it as a normal user who can sudo, after cloning:
#
#     cd treading-bot/arcus && bash deploy/scripts/bootstrap.sh
#
# What it does:
#   1. installs the system packages, keeps the clock in sync (chrony) and installs uv;
#   2. installs the three bots into arcus/.venv (make install: Arcus, Lighter, the funding arbitrage);
#   3. creates .env from the template if there is none (mode 600), and never changes one that exists;
#   4. closes the firewall to everything but SSH (the port this session came in on);
#   5. makes `tbot up` run after every reboot (a line in this user's crontab).
# It starts nothing and places no orders. Running it again is safe.
set -euo pipefail

cd "$(dirname "$0")/../.."
BOT="$PWD"
[ -f pyproject.toml ] && [ -d arcus ] || { echo "run this from the repository: treading-bot/arcus"; exit 1; }

echo "[1/5] system packages, clock, uv"
sudo apt-get update -y
sudo apt-get install -y git curl make build-essential chrony cron ufw
sudo systemctl enable --now chrony cron
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

echo "[2/5] the bots (arcus/.venv)"
make install
mkdir -p state logs data
chmod 700 state

echo "[3/5] credentials file"
if [ -f .env ]; then
  echo "  .env exists: left as it is"
else
  cp .env.example .env
  echo "  created .env from the template: fill it in (nano .env)"
fi
chmod 600 .env

echo "[4/5] firewall: SSH only"
SSH_PORT="$(echo "${SSH_CONNECTION:-}" | awk '{print $4}')"
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow "${SSH_PORT:-22}/tcp"
sudo ufw --force enable

echo "[5/5] start after a reboot"
LINE="@reboot cd $BOT && .venv/bin/tbot up >> logs/boot.out 2>&1"
( crontab -l 2>/dev/null | grep -vF ".venv/bin/bot up" | grep -vF ".venv/bin/arcus up" | grep -vF ".venv/bin/tbot up" || true; echo "$LINE" ) | crontab -

cat <<EOF

Done. Next, from $BOT, one line at a time:

  1. Your keys and the Telegram token (the README, section 2):
       nano .env
     On two machines also say what this one is for (the README, section 3, "Two servers, step by step").
     The one that trades, which holds the keys:
       .venv/bin/tbot role trader
     The one that records, which needs no keys at all:
       .venv/bin/tbot role recorder
  2. May this server's address trade Arcus perps?
       .venv/bin/arcus region-check
  3. Credentials, account and clock (reads only):
       .venv/bin/arcus doctor
  4. Start what this machine is for (everything, or what its BOT_ROLE says):
       .venv/bin/tbot up
  5. One screen:
       .venv/bin/tbot status
  6. Later, one file with everything recorded and traded, to bring home:
       .venv/bin/tbot export
EOF
