---
name: week-summary
description: Reply with Sparsh's 7-day health rollup — weekly averages and totals for calories, protein, water, sleep, weight trend, and lift count. Triggers on "/week_summary", "/week", "weekly summary", "how was this week", "/rollup", "📅 Week". Reads the past 7 daily notes from the vault, aggregates the frontmatter, compares to targets. Vault is the single source of truth — do NOT call food-tracker MCP tools. Read-only.
version: 1.1.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, summary, weekly, dashboard]
    category: health
---

# week-summary

## When to Use

User asks for a weekly view of health metrics. Examples:
- `/week_summary` / `/week`
- "how was this week"
- "weekly rollup"
- "show me the past 7 days"
- "📅 Week" (keyboard button)

Read-only skill. **Never writes.**

## 🛑 Forbidden tools (architecture-critical)

The vault is the canonical store. The food-tracker MCP's SQLite DB is NOT a valid data source for this skill — it drifts from the vault. **Do NOT call:**

- ❌ `mcp_food_tracker.get_summary` — NEVER. Aggregate the vault's daily notes instead.
- ❌ `mcp_food_tracker.get_daily_log` — NEVER. Read each daily note directly.
- ❌ Any other `mcp_food_tracker.*` tool.

All numbers come from reading `04 - Daily Notes/<date>.md` frontmatter for each of the 7 days. If a field is empty, it's empty — report it as `—`, don't backfill from the MCP.

## Step-by-step

1. **Today's date** in America/New_York: `YYYY-MM-DD`. Compute the past 7 dates (today inclusive).

2. **For each of the 7 dates,** read `04 - Daily Notes/<date>.md` if it exists. Skip silently if not — note the count of days actually read.

3. **Aggregate frontmatter** across the 7 days:
   - `kcal_avg` = mean of non-null `kcal` values
   - `protein_avg` = mean of non-null `protein_g`
   - `water_avg` = mean of non-null `water_l`
   - `sleep_avg` = mean of non-null `sleep_hours`
   - `weight_first`, `weight_last` = first and last non-null `weight` in the window
   - `weight_delta` = `weight_last - weight_first` (lb)
   - `weight_7d_avg` = mean of non-null `weight`
   - `lifts_count` = count of non-null/non-empty `lifted` fields
   - `vitamins_days` = count of `vitamins_taken: true`
   - `days_with_data` = count of days where at least one health field was populated

4. **Format reply for Telegram** (compact, no padding):

```
📅 Past 7 days (<start-date> → <end-date>)
━━━━━━━━━━━━
days logged   <days_with_data>/7
🔥 kcal avg   <X>/2500  (<%>)
🥩 protein    <X>g/150  (<%>)
💧 water      <X>L/2.5  (<%>)
😴 sleep      <X>h avg  (target 7h)
⚖️ weight     <start> → <end> lb  (Δ <+/-X.X>)
🏋️ workouts   <count>/4 target
💊 vitamins   <count>/7 days
```

5. **Trend interpretation (1-2 lines at the end):**
   - **Weight trend vs the goal:** compare the 7-day delta to the goal rate in Coach Memory (`07 - Health/Coach Memory.md`).
     - Delta inside the goal band → `Weight on track: <+/-X> lb (goal rate per Coach Memory).`
     - Delta off the goal direction for 2 weeks → `⚠ Weight trend is off goal — check kcal avg.`
     - Delta well past the goal rate → `⚠ Moving faster than the goal rate. Adjust calories by ~200/day.`
   - **Adherence:** if `days_with_data < 5` → flag low tracking adherence as the first thing to fix.
   - **Sleep adherence:** if `sleep_avg < 6.5` → flag.

6. **Watch flags** (per Coach Memory thresholds):
   - `lifts_count < 3` → `🚩 Gym consistency: <count> sessions this week, target 4. The pattern's slipping.`
   - Any single day with `kcal < 1800` AND `protein_g < 100` → `🚩 Under-eating day(s) detected.`

## Log the change

**Do not log.** Read-only.

## Pitfalls

1. **Empty week.** If no data at all in the past 7 days, reply: "No health data logged in the past 7 days. Start with `/log-food` or describe a meal."
2. **Partial week.** If only 2-3 days have data, compute averages over what exists, but flag low adherence prominently.
3. **Weight delta with single data point.** Need ≥2 non-null weight values across the 7 days to compute a delta. Otherwise show "weight: <today's> (insufficient history for delta)".
4. **Don't over-aggregate.** If a day has kcal=1200 due to under-eating, don't average that away — it's exactly the kind of day the system is built to catch. Surface it.
5. **Days-of-week skew.** A Mon-Sun week vs a rolling 7-day window are different. This skill uses **rolling 7-day window** (today + 6 prior). For Mon-Sun, that's the Sunday weekly-rollup cron, not this skill.
