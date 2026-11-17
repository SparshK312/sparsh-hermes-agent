#!/usr/bin/env bash
# cutover.sh — switch the check-in loop LIVE and retire the fixed nudges it replaces, or
# put everything back. Run ON THE VPS as the hermes user.
#
#   cutover.sh status     show the mode and the state of the six old nudges
#   cutover.sh live       pause the six nudges, then create ~/.hermes/assistant/LIVE
#   cutover.sh revert     remove LIVE (back to shadow), then resume the six nudges
#
# The old jobs are PAUSED, never removed, so revert is exact. Order matters both ways:
# going live, the old nudges stop BEFORE the loop starts speaking (no double messages);
# reverting, the loop stops speaking BEFORE the old nudges come back.
set -uo pipefail
H="$HOME/.hermes/hermes-agent/venv/bin/hermes"
JOBS="$HOME/.hermes/cron/jobs.json"
FLAG="$HOME/.hermes/assistant/LIVE"
NAMES="health-lunch-check health-meal-rescue health-water-check health-dinner-check health-evening-summary-nudge health-tracking-dark"

job_field() {  # job_field <name> <field>
  /usr/bin/python3 - "$1" "$2" "$JOBS" <<'PY'
import json, sys
name, field, path = sys.argv[1:4]
jobs = json.load(open(path)); jobs = jobs["jobs"] if isinstance(jobs, dict) else jobs
hit = [j for j in jobs if j.get("name") == name]
print(hit[0].get(field, "") if len(hit) == 1 else "")
PY
}

status() {
  if [ -f "$FLAG" ]; then echo "check-in loop: LIVE"; else echo "check-in loop: shadow"; fi
  for n in $NAMES; do printf "  %-32s %s\n" "$n" "$(job_field "$n" state)"; done
}

case "${1:-status}" in
  status) status ;;
  live)
    cp "$JOBS" "$JOBS.bak-cutover-$(date +%Y%m%d-%H%M%S)"
    for n in $NAMES; do
      id="$(job_field "$n" id)"
      [ -n "$id" ] || { echo "❌ job '$n' not found (or not unique): nothing changed further; run 'cutover.sh revert'"; exit 1; }
      [ "$(job_field "$n" state)" = "paused" ] || "$H" cron pause "$id" >/dev/null || { echo "❌ could not pause $n"; exit 1; }
    done
    for n in $NAMES; do
      [ "$(job_field "$n" state)" = "paused" ] || { echo "❌ $n is not paused after the pause; NOT going live"; exit 1; }
    done
    mkdir -p "$(dirname "$FLAG")" && date -Is > "$FLAG"
    status ;;
  revert)
    rm -f "$FLAG"
    for n in $NAMES; do
      id="$(job_field "$n" id)"
      [ -n "$id" ] && [ "$(job_field "$n" state)" = "paused" ] && "$H" cron resume "$id" >/dev/null
    done
    status ;;
  *) echo "usage: cutover.sh status|live|revert"; exit 2 ;;
esac
