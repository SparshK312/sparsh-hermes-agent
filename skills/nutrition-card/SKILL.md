---
name: nutrition-card
description: Render and send Sparsh's nutrition visual to Telegram, on demand. The DAILY "fuel card" (calorie ring vs 2400 target + protein/carbs/fat bars + meal timeline + water + a coach line) triggers on "/fuel", "fuel card", "calories today", "how am I eating", "what have I eaten", "show my macros", "my nutrition", "calorie card", "food card". The WEEKLY "Week in Food" report (7-day calorie + protein columns, protein hit-rate, avg macro split) triggers on "week in food", "weekly nutrition", "this week's eating", "weekly calories", "weekly macros", "nutrition this week". Runs the script which renders the card and sends the photo directly — you do NOT compute or describe the numbers yourself. Read-only on the vault.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, nutrition, visual]
    category: health
---

# nutrition-card

The food side of the muscle map. Two visuals, same dark-infographic style, both rendered + sent by the script (you never read the Food Log or compute totals yourself).

## When to Use

**Daily fuel card** — "how's today's eating going":
- `/fuel`, "fuel card", "calorie card", "food card"
- "calories today", "how am I eating", "what have I eaten", "show my macros", "my nutrition"

**Weekly Week-in-Food report** — "how's my week of eating":
- "week in food", "weekly nutrition", "this week's eating"
- "weekly calories", "weekly macros", "nutrition this week"

If it's genuinely ambiguous (e.g. just "nutrition"), default to the **daily** card.

## What to do

**This skill renders an IMAGE and SENDS IT. You do NOT read `07 - Health/Food Log/`, compute kcal/macros, or describe the data yourself — the script owns rendering (cairosvg) and delivery.**

1. Run exactly the matching command. It renders the card + caption and sends the photo to Telegram itself:

   **Daily fuel card:**
   ```
   bash /home/hermes/.hermes/scripts/fuel_card.sh
   ```

   **Weekly Week-in-Food report:**
   ```
   bash /home/hermes/.hermes/scripts/week_food.sh
   ```

2. The wrapper **stays silent on success** (it sends the photo itself, then prints nothing) — **no output = the card was sent**.

3. Reply with **one short line only**, e.g. *"Sent your fuel card 🍽"* / *"Sent your week-in-food report 📊"*. Do **not** restate the numbers — the card already shows them.

4. If the wrapper prints a **`⚠️ … failed`** line, the send failed — tell him and point to `~/.hermes/health/fitness.log`. If you happen to know nothing's been logged for the day/week, say there's nothing to show yet instead of claiming you sent it.

## Notes
- Daily defaults to today; weekly is the last 7 days. Both pull from the per-meal Food Log files + daily-note totals.
- **Protein is the hero metric** — the daily bar is vs the 140 g target (flagged when low); the weekly report surfaces the protein hit-rate (days ≥ 140 g). Calories are vs the 2,400 lean-bulk target.
- Same cards the (optional) daily/weekly nutrition crons send — this lets him summon them any time.
- Never build the chart or read the Food Log yourself; always run the script.
