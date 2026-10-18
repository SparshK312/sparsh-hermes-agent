---
name: trends
description: Render and send Sparsh's health cards to Telegram on demand — the morning card (last night's sleep + recovery + yesterday's activity), the weekly review (week vs the week before, recovery and bedtime rhythm), or the monthly calendar (sleep / steps / training / food heatmaps). Triggers on "/trends", "show my trends", "show my charts", "show my week", "how was my week", "show my month", "monthly review", "sleep trend", "how's my sleep been", "steps trend", "how active have I been", "recovery trend", "how's my HRV / resting heart rate looking", "show me my morning card", "resend the morning card". The script renders the cards and sends the photos itself — you do NOT compute or describe the data. Read-only on the vault.
version: 2.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, fitness, visual, trends]
    category: health
---

# trends

## When to Use

Sparsh wants to **see** his health data, not read a number. Route any "show me / how's my X been / trend / chart" request here. (Today's single numbers in text → `today-summary`. Coaching judgment → `coach`.)

## What to do

**The script renders the image(s) and sends them. You do NOT read metrics.csv, compute averages, or describe the data yourself.**

1. Pick the card from his ask:
   - **morning**: "morning card", "last night", "how did I sleep", "today's card"
   - **week** (the default): "trends", "charts", "this week", "how was my week", "sleep / steps / recovery trend", "HRV", "resting heart rate"
   - **month**: "this month", "monthly", "calendar", "last month" (add `YYYY-MM` for a specific month, e.g. last month)

2. Run exactly one of:

   ```
   bash /home/hermes/.hermes/scripts/health_cards.sh morning
   bash /home/hermes/.hermes/scripts/health_cards.sh week
   bash /home/hermes/.hermes/scripts/health_cards.sh month
   bash /home/hermes/.hermes/scripts/health_cards.sh month 2026-10
   ```

3. If it prints `{"wakeAgent": false}`, the cards **were already sent**. Reply with one short line only, e.g. *"Sent your week 📊"*. Don't restate numbers.

4. If it prints "health card failed", tell Sparsh it failed and to check `~/.hermes/health/fitness.log`.

## Notes
- Data: `07 - Health/Metrics/metrics.csv` (Fitbit Air since Oct 8 2026, Apple Watch before) plus his hand logs (food, weight, workouts). Cards show a gap for a day with no data, never a zero.
- HRV on the cards is the Air's nightly RMSSD; it is never mixed with the Apple Watch's SDNN.
- Muscle coverage for the week → `muscle-map`. Today's food → `nutrition-card`.
- Never try to build the chart or read the CSV yourself; always run the script.
