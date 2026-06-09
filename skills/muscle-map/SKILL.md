---
name: muscle-map
description: Render and send Sparsh's muscle-coverage card (anatomical front+back heat map + a short coached read) to Telegram, on demand. Triggers on "/muscle_map", "muscle map", "muscle coverage", "coverage card", "what have I trained", "what muscles did I hit", "show my heatmap", "how's my training looking", "what should I hit next". Runs the fitness report script which renders the card and sends the photo directly — you do NOT compute or describe the data yourself. Read-only on the vault.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, fitness, visual]
    category: health
---

# muscle-map

## When to Use

Sparsh wants to **see** his muscle-coverage / training heat map for the week. Examples:
- `/muscle_map`, "muscle map", "muscle coverage", "coverage card"
- "what have I trained this week", "what muscles did I hit", "what should I hit next"
- "show my heatmap", "how's my training looking"

## What to do

**This skill renders an IMAGE and SENDS IT. You do NOT read the workout files, compute volume, or describe the data yourself — the script owns rendering (cairosvg) and delivery.**

1. Run exactly this shell command. It renders the coverage card + a coached caption and sends the photo to Telegram by itself:

   ```
   bash /home/hermes/.hermes/scripts/fitness_report.sh
   ```

2. The script prints `[SILENT]` on success — that means **the photo was already sent**.

3. Reply with **one short line only**, e.g. *"Sent your muscle-coverage card 💪"*. Do **not** restate the analysis — the card already shows the legend + goals panel.

4. If the script output is `send-failed` or shows a Python error, tell Sparsh it failed and to check `~/.hermes/health/fitness.log`.

## Notes
- Default window is the last 7 days of logged workouts.
- The card is the same one the Sunday `fitness-weekly` cron sends — this just lets him summon it any time.
- Never try to build the chart or read `07 - Health/Workouts/` yourself; always run the script.
