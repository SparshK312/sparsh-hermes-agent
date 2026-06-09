#!/usr/bin/env bash
# Hermes cron job: health-lunch-check
# Fires at 13:00 America/Toronto, every day.
# Script-mode (no LLM) — stdout delivered verbatim to Telegram.

set -euo pipefail

cat <<'EOF'
🍽️ *Lunch check.*  Logged lunch yet?

Three ways:
• Just describe it: _"chicken sandwich and salad at the cafeteria"_
• Photo: snap + send, agent estimates macros
• Explicit: `/log-food <description>`

Office AYCE means easy 800-1000 kcal of protein-dense lunch — anchor of the day.

`/today` for running totals.
EOF
