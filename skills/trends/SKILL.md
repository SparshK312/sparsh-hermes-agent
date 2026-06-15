---
name: trends
description: Render and send Sparsh's health-trend card (sleep, steps, active energy, and resting-HR/HRV recovery over time) to Telegram, on demand. Triggers on "/trends", "show my trends", "sleep trend", "how's my sleep been", "steps trend", "walking trend", "activity trend", "recovery trend", "show my charts", "how's my sleep/steps/activity looking over time". Runs the trends script which renders a multi-panel chart from the Apple-Watch archive and sends the photo directly — you do NOT compute or describe the data yourself. Read-only on the vault.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, fitness, visual, trends]
    category: health
---

# trends

## When to Use

Sparsh wants to **see** how a metric has been trending over time (not today's single number — that's `today-summary`). One card covers sleep + steps + active energy + recovery, so route ANY "over time / trend / how's my X been" request here:
- `/trends`, "show my trends", "show my charts"
- "sleep trend", "how's my sleep been", "am I sleeping more lately"
- "steps trend", "walking trend", "how active have I been"
- "activity trend", "recovery trend", "how's my HRV / resting heart rate looking"

## What to do

**This skill renders an IMAGE and SENDS IT. You do NOT read metrics.csv, compute averages, or describe the data yourself — the script owns rendering (matplotlib) and delivery (Bot API).**

1. Pick the window from his ask (default **30** days; "this week" → 7, "last 3 months" → 90), then run exactly:

   ```
   bash /home/hermes/.hermes/scripts/trends_report.sh 30
   ```

   (swap `30` for the window in days).

2. The script renders the 4-panel card (Sleep / Steps / Active energy / Recovery) with a summary caption and sends the photo itself, then prints `{"wakeAgent": false}` — that means **the photo was already sent**.

3. Reply with **one short line only**, e.g. *"Sent your 30-day trends 📊"*. Do not restate the numbers — the caption + chart already show them.

4. If the output shows `metrics_trends FAILED`, `send FAILED`, or "nothing to chart", tell Sparsh it failed and to check `~/.hermes/health/fitness.log`.

## Notes
- Data source is `07 - Health/Metrics/metrics.csv` (the Apple-Watch archive) — sleep/steps/activity coverage depends on what the watch captured.
- For muscle/workout coverage (which body parts he's hit), use `muscle-map`, not this.
- Never try to build the chart or read the CSV yourself; always run the script.
