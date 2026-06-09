#!/usr/bin/env bash
# Hermes cron job: health-evening-summary-nudge
# Fires at 21:00 America/Toronto, every day.
# Script-mode (no LLM) — stdout delivered verbatim to Telegram.

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
