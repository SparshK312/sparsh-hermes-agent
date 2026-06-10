#!/usr/bin/env bash
# Hermes cron job: health-evening-summary-nudge — fires in the evening (schedule in
# jobs.json, evaluated in the Hermes-configured timezone — see config/cron_additions.json).
# AGENT job: Hermes wakes the LLM with this stdout as context and composes the message
# (NOT verbatim). See hae-sync for the $0/no-LLM {"wakeAgent": false} pattern.

set -euo pipefail

cat <<'EOF'
🌙 *Evening check-in.*

Final logs of the day:
• Dinner if you haven't already
• `/water` for any bottles you missed
• `/vitamins` if you took them
• Anything else worth capturing

`/today` to see where you landed against targets (2400 kcal / 140g protein / 2.5L water).
`/missed` if something needs follow-up.

Bed by midnight. That's the non-negotiable.
EOF
