#!/usr/bin/env bash
# Host bootstrap for a fresh Ubuntu 24.04 VPS (>= 4 vCPU / 8 GB; region allowed by BOTH venues, e.g. Tokyo).
# Run as a sudo-capable user from the repo root: bash deploy/scripts/bootstrap.sh
set -euo pipefail
sudo apt-get update -y
sudo apt-get install -y chrony git curl build-essential ufw
sudo systemctl enable --now chrony
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12
uv sync --locked --extra dev || uv sync --extra dev
mkdir -p data logs reports state config
chmod 700 state config
[ -f config/secrets.enc ] && chmod 600 config/secrets.enc
sudo ufw default deny incoming && sudo ufw default allow outgoing && sudo ufw allow OpenSSH && sudo ufw --force enable
echo "Next: python deploy/scripts/region_check.py ; copy config/secrets.enc ; create /etc/bot.env (see .env.example) ;"
echo "      sudo cp deploy/systemd/*.service /etc/systemd/system/ && sudo systemctl daemon-reload"
echo "      sudo systemctl enable --now bot-scout"
