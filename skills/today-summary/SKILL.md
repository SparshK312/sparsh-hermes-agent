---
name: today-summary
description: Reply with Sparsh's logged health metrics so far today vs his locked targets — kcal, protein, water, weight, sleep, lifted. Triggers on "/today", "what's my day", "how am I doing today", "/status", etc. Reads today's daily-note frontmatter and compares against targets (2400 kcal, 140g protein, 2.5L water, 4 lifts/week pace, sleep ≥7h). Read-only — never modifies the vault.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, summary, dashboard]
    category: health
---

# today-summary

## When to Use

User asks for today's totals / progress / status. Examples:
- `/today`
- "how am I doing today"
- "where am I on calories"
- "/status"

Read-only skill. **Never writes to the vault.**

## Step-by-step

1. **Today's date** in America/Toronto: `YYYY-MM-DD`.

2. **Read** `04 - Daily Notes/<date>.md`. Parse YAML frontmatter.
   - If the file doesn't exist → reply: "No daily note yet for <date>. The 7 AM prefill cron should have created one — check `~/.hermes/logs/agent.log`."

3. **Pull these fields** (all may be empty/null):
   - `kcal` — calories so far
   - `protein_g`
   - `carbs_g`
   - `fat_g`
   - `water_l`
   - `weight`
   - `sleep_hours`, `sleep_quality`
   - `lifted` — today's workout session if any
   - `vitamins_taken` (bool)

4. **Compute against targets:**
   - kcal vs 2400 → `<X>/2400 (<percent>%)`
   - protein_g vs 140g → `<X>g/140g (<percent>%)`
   - water_l vs 2.5L → `<X>L/2.5L (<percent>%)`
   - sleep_hours vs 7h → `<X>h vs 7h target`, flag if under
   - lifted: shows session name or `—` if empty

5. **Format reply for Telegram** — compact, scannable, no padding:

```
📊 Today (<date>)
━━━━━━━━━
🔥 kcal       <X>/2400  (<%>)
🥩 protein    <X>g/140  (<%>)
💧 water      <X>L/2.5  (<%>)
⚖️ weight     <X> lb  (<delta-vs-7d-avg>)
😴 sleep      <X>h  (q<X>)
🏋️ workout    <session name>  /  —
💊 vitamins   ✓ / —
```

Trim sections with empty data — if water_l is null, show `—`, don't blank the line.

End with a one-liner if any target is severely behind:
- kcal <50% past noon → `⚠ Under-eating signal. AYCE lunch is the leverage.`
- water_l <50% past 4 PM → `⚠ Water behind. Knock back a bottle.`
- sleep_hours <6.5h last night → `⚠ Sub-target sleep. Bed by midnight tonight.`
- No flags if everything's tracking → end with `On pace.` or no closing line.

## Log the change

**Do not log.** Read-only operation.

## Pitfalls

1. **Reading uninitialized frontmatter.** Daily note may exist but health fields may be empty. Treat null/missing as "not yet logged" → show `—`, not 0.
2. **Stale daily note.** If today's note has missing frontmatter fields (template wasn't updated), tell the user — don't pretend zeros.
3. **Percent rounding.** Round to integer percent. `1450 / 2400 = 60.4%` → display "60%".
4. **Comparing weight to history.** Only show delta if at least 3 days of prior `weight:` data exist. Otherwise show today's weight alone.
5. **Time-of-day context.** Mention "<X>% so far" framing if it's before 6 PM (still time to hit targets); after 9 PM the framing shifts to "landed at <X>%".
