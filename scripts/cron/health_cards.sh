#!/usr/bin/env bash
# On demand (the `trends` skill): render + send one health card set.
#   health_cards.sh morning | week | month [YYYY-MM]
# The python sends the photo(s) itself; this prints the wake gate so the agent stays quiet.
set -uo pipefail
export HERMES_VAULT="${HERMES_VAULT:-/home/hermes/vault}"
LOG="$HOME/.hermes/health/fitness.log"
KIND="${1:-week}"
mkdir -p "$(dirname "$LOG")"
case "$KIND" in
  morning|week) ARGS=("$KIND") ;;
  month) ARGS=(month); [ -n "${2:-}" ] && ARGS+=(--month "$2") ;;
  *) echo "usage: health_cards.sh morning|week|month [YYYY-MM]"; exit 2 ;;
esac
{
  echo "=== $(date -Is) on-demand ${ARGS[*]} ==="
  "$HOME/.hermes/venvs/fitness/bin/python3" "$HOME/.hermes/scripts/fitness/health_cards.py" "${ARGS[@]}" \
    --send --no-extras || echo "health_cards FAILED"
} >> "$LOG" 2>&1
tail -n 300 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
if tail -n 3 "$LOG" | grep -q "FAILED\|send-failed"; then
  echo "health card failed to send — check ~/.hermes/health/fitness.log"
else
  echo '{"wakeAgent": false}'
fi
