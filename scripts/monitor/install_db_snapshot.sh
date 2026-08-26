#!/usr/bin/env bash
# install_db_snapshot.sh — install/refresh the Hermes DB snapshot job on the VPS.
# Run from the Mac:  ./scripts/monitor/install_db_snapshot.sh
# Installs to /usr/local/bin + /etc/systemd/system (outside ~/.hermes, so
# deploy.sh and framework upgrades cannot remove it). Idempotent.
set -euo pipefail
_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
[ -f "$_DIR/.env" ] && { set -a; . "$_DIR/.env"; set +a; }
VPS_HOST="${VPS_HOST:-hermes@your-vps-ip}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/hetzner_hermes}"
VPS_ROOT_HOST="${VPS_ROOT_HOST:-root@${VPS_HOST#*@}}"
SRC="$_DIR/scripts/monitor"

echo "→ Installing Hermes DB snapshot job to $VPS_ROOT_HOST"
scp -q -i "$VPS_SSH_KEY" \
  "$SRC/db_snapshot.py" \
  "$SRC/hermes-db-snapshot.service" \
  "$SRC/hermes-db-snapshot.timer" \
  "$VPS_ROOT_HOST:/tmp/"

ssh -i "$VPS_SSH_KEY" "$VPS_ROOT_HOST" '
  set -euo pipefail
  install -m 0755 -o root -g root /tmp/db_snapshot.py /usr/local/bin/db_snapshot.py
  install -m 0644 -o root -g root /tmp/hermes-db-snapshot.service /etc/systemd/system/hermes-db-snapshot.service
  install -m 0644 -o root -g root /tmp/hermes-db-snapshot.timer   /etc/systemd/system/hermes-db-snapshot.timer
  rm -f /tmp/db_snapshot.py /tmp/hermes-db-snapshot.*
  install -d -o hermes -g hermes -m 0755 /home/hermes/.hermes/backups/db-snapshots
  systemctl daemon-reload
  systemctl enable --now hermes-db-snapshot.timer
  echo "  ✓ installed"
  systemctl list-timers hermes-db-snapshot.timer --no-pager | head -3
'
echo "Run one now with:  ssh -i $VPS_SSH_KEY $VPS_ROOT_HOST systemctl start hermes-db-snapshot.service"
