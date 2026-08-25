#!/usr/bin/env bash
#
# install_watchdog.sh — install/refresh the Hermes gateway watchdog on the VPS.
#
# Run from the Mac:  ./scripts/monitor/install_watchdog.sh
#
# Installs to /usr/local/bin + /etc/systemd/system (root-owned, OUTSIDE
# ~/.hermes) so that neither `deploy.sh` (which rm -rf's parts of ~/.hermes)
# nor a Hermes framework upgrade can remove it.
#
# Idempotent: safe to re-run after editing hermes_watchdog.sh.

set -euo pipefail

_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
[ -f "$_DIR/.env" ] && { set -a; . "$_DIR/.env"; set +a; }

VPS_HOST="${VPS_HOST:-hermes@your-vps-ip}"
VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/hetzner_hermes}"
VPS_ROOT_HOST="${VPS_ROOT_HOST:-root@${VPS_HOST#*@}}"
SRC="$_DIR/scripts/monitor"

echo "→ Installing Hermes watchdog to $VPS_ROOT_HOST"

scp -q -i "$VPS_SSH_KEY" \
  "$SRC/hermes_watchdog.sh" \
  "$SRC/hermes-watchdog.service" \
  "$SRC/hermes-watchdog.timer" \
  "$VPS_ROOT_HOST:/tmp/"

ssh -i "$VPS_SSH_KEY" "$VPS_ROOT_HOST" '
  set -euo pipefail
  install -m 0755 -o root -g root /tmp/hermes_watchdog.sh /usr/local/bin/hermes_watchdog.sh
  install -m 0644 -o root -g root /tmp/hermes-watchdog.service /etc/systemd/system/hermes-watchdog.service
  install -m 0644 -o root -g root /tmp/hermes-watchdog.timer   /etc/systemd/system/hermes-watchdog.timer
  rm -f /tmp/hermes_watchdog.sh /tmp/hermes-watchdog.service /tmp/hermes-watchdog.timer
  mkdir -p /var/lib/hermes-watchdog
  touch /var/log/hermes-watchdog.log
  chmod 0644 /var/log/hermes-watchdog.log
  # Keep the watchdog log from growing without bound.
  cat > /etc/logrotate.d/hermes-watchdog <<LR
/var/log/hermes-watchdog.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
LR
  systemctl daemon-reload
  systemctl enable --now hermes-watchdog.timer
  echo "  ✓ installed"
  systemctl list-timers hermes-watchdog.timer --no-pager | head -3
'
echo "Done. Tail the log with:"
echo "  ssh -i $VPS_SSH_KEY $VPS_ROOT_HOST 'tail -f /var/log/hermes-watchdog.log'"
