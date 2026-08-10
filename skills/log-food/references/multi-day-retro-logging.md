# Multi-Day Retroactive Meal Logging

## Pattern

The user provides meals from multiple days (often a weekend catchup or a few days of backfill) in a single conversation. They describe meals verbally with relative time cues ("Sunday", "yesterday", "last night") but no explicit dates. The agent must:

1. **Infer the correct date from context** — "Sunday" + today is Tuesday → June 21. "Yesterday" + today is June 23 (Monday) → June 22.
2. **Lock the date before starting nutrient lookup** — do not assume "lunch today" = current date if the user later clarifies it was a prior day.
3. **Cross-check meal identity across days** — the user may list the same meal type (e.g., "lunch") for multiple days and rely on context order to disambiguate which lunch belongs to which day.

## Pitfall: Date Assumption Trap

**Scenario:** User says "I had sushi, rice, salmon, gyoza, cucumber" without a time-of-day header. The agent assumes "today" and logs it to June 23 (Monday, the current date). Later, the user says "This is for yesterday not for today," meaning the meal was Sunday, June 21.

**Correct move:**
1. Remove the meal from June 23 by reverting its frontmatter totals (or calling `vault_log.py undo-last-meal`).
2. Re-insert the meal into June 21's food log with the same macros.
3. Update both date files' frontmatter totals.
4. Append a `Log.md` entry flagging the correction: `## [YYYY-MM-DD] update | log-food — sushi lunch corrected to 2026-06-21 (was mistakenly logged to 2026-06-23)`

**Key rule:** When the user provides a date correction, it overrides the agent's inferred/assumed date. Ask immediately if the intended date is ambiguous ("Is that lunch on Sunday (21st) or Monday (22nd)?") rather than assuming and fixing it later.

## Step-by-Step for Multi-Day Sessions

### A. Intake Bucketing
When the user says "Here's what I ate over the weekend" or "Last few days were…", parse each meal with an explicit **inferred date** attached:

```
[Jun 21, ~1pm] "Sushi rice, miso salmon, chicken gyoza, cucumber"
[Jun 21, ~7:30pm] "Alpha Shawarma chicken shawarma plate"
[Jun 22, ~9:15am] "Sunset Grill Eggs Sunset (partial potatoes)"
[Jun 22, ~7:45pm] "Medidari pasta + chicken lettuce wrap"
[Jun 23, ~12:30pm] "Sushi rice, miso salmon, chicken gyoza, cucumber"  ← NEW INPUT
```

### B. Explicit Date Check (Before Nutrient Lookup)
When the user provides meals that span multiple days, do **not** start web searches or MCP lookups until you've confirmed the date assignment. Reply with:

```
📅 Bucketed meals by date:
  • [Jun 21, Sun] Breakfast + 2 lunch + snack + dinner
  • [Jun 22, Mon] Breakfast + snacks + dinner
  • [Jun 23, Tue] Lunch (new)

Correct? Any meals on the wrong day?
```

Allow the user to correct before logging.

### C. Date Correction Flow
If the user says "No, the sushi was on Sunday not Monday", revert:

1. **Identify the mistaken entry.** Find it in the food log for the wrong date (e.g., June 23).
2. **Call `vault_log.py undo-last-meal`** to remove it and revert the daily-note totals.
3. **Re-log to the correct date** using `vault_log.py food --date 2026-06-21 ...`.
4. **Append a Log.md correction line:**
   ```
   ## [2026-06-23] update | log-food — sushi lunch corrected to 2026-06-21 (was logged to 2026-06-23 in error)
   ```

### D. Same-Item-Name-Different-Days Disambiguation
When the user says "lunch" for multiple days without clarifying which day's lunch they mean, ask:

```
You mentioned lunch on two different days:
  • Jun 21: Roti with curries (1050 kcal)
  • Jun 22: Sunset Grill (1000 kcal)

The new sushi lunch — which date does it belong to?
```

### E. Totals Recalculation
After correcting a day assignment, **always recalculate the totals for both the source day and the destination day**. Do not trust incremental math; sum all meals in each food log file and update the frontmatter.

## When to Use This Pattern

- User provides a weekend catchup (Fri/Sat/Sun recap on Monday).
- User is backfilling missed logs from a prior week.
- User says "yesterday I ate X, Y, Z" and provides multiple meals.
- User provides relative time cues ("Sunday", "after the game", "before the gym") without explicit dates.

## When This Is NOT Needed

- Single meal, explicit date or obvious time-of-day inference.
- User says "log this as [date]" explicitly upfront.
- Today-only meal session with no date ambiguity.

## Related References

- `date-correction-flow.md` — single-meal date correction pattern.
- `retrospective-day-summary.md` — whole-day recaps with bucketing.
- `day-recap-corrections.md` — in-session portion tweaks.
