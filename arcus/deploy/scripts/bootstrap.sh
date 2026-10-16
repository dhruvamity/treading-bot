#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server (a VPS). Run it as a normal user who can sudo, after cloning:
#
#     bash treading-bot/arcus/deploy/scripts/bootstrap.sh
#
# What it does:
#   1. installs the system packages, keeps the clock in sync (chrony) and installs uv;
#   2. installs every part into arcus/.venv (make install in the repository root) and puts its commands on this
#      user's PATH (a line in ~/.bashrc);
#   3. creates arcus/.env from the template if there is none (mode 600), and never changes one that exists;
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

echo "[2/5] every part (arcus/.venv) and its commands on the PATH"
make -C .. install
mkdir -p state logs data
chmod 700 state
PATHLINE="export PATH=\"$BOT/.venv/bin:\$PATH\""
grep -qxF "$PATHLINE" ~/.bashrc 2>/dev/null || echo "$PATHLINE" >> ~/.bashrc

echo "[3/5] credentials file"
echo "  arcus/.env: $( [ -s .env ] && echo 'there (left as it is)' || echo 'created from the template: fill it in')"
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

Done. Next, one line at a time (the first puts the commands on this shell's PATH):

  0. source ~/.bashrc
  1. Your keys and the Telegram token (the README, section 2):
       nano $BOT/.env
     On two machines also say what this one is for (the README, section 3, "Two servers, step by step").
     The one that trades, which holds the keys:
       tbot role trader
     The one that records, which needs no keys at all:
       tbot role recorder
  2. May this server's address trade Arcus perps?
       arcus region-check
  3. Credentials, account and clock (reads only):
       arcus doctor
  4. Start what this machine is for (everything, or what its BOT_ROLE says):
       tbot up
  5. One screen:
       tbot status
  6. Later, one file with everything recorded and traded, to bring home:
       tbot export
EOF
