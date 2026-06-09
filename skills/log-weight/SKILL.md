---
name: log-weight
description: Log Sparsh's morning bodyweight to today's daily-note frontmatter. Triggers on "/weight 114.5", "I weighed 115 today", "weight check 113.8", or any number-with-weight-context. Unit is pounds (lb). Best practice is morning, post-bathroom, pre-food. Updates the `weight` field in today's daily note frontmatter for trend tracking and Dataview rollups in `00 - Dashboard/Health & Fitness.md`.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, weight, vault-write]
    category: health
---

# log-weight

## When to Use

When the user reports a bodyweight measurement. Examples:
- `/weight 114.5` → 114.5 lb
- "weighed 115 this morning" → 115 lb
- "weight: 113.8" → 113.8 lb
- "I'm 52 kg today" → convert to 114.6 lb

Do **not** use for weights of food, equipment, or anything that's not the user's bodyweight.

## Step-by-step

1. **Parse the value.** Required: a positive number. Optional: unit (`lb`, `kg`, `pounds`, `kilos`).
   - If no unit specified → assume **lb** (Sparsh's default).
   - If `kg` → convert: `lb = kg × 2.2046` (one decimal).

2. **Sanity check.** Reasonable range for Sparsh: 100–150 lb. If outside that range, reply asking for confirmation before writing.

3. **Today's date** in America/Toronto: `YYYY-MM-DD`.

4. **Read today's daily note:** `04 - Daily Notes/<date>.md`. Create from `Templates/Daily Note.md` if missing.

5. **Update `weight:` in frontmatter** to the new value (replace, do not accumulate — bodyweight is a snapshot).

6. **Compute trend (optional but useful):**
   - Read the prior 7 daily notes (if they exist) and pull their `weight:` values.
   - Compute 7-day avg. Compare to today.
   - Note the delta in the reply.

7. **Reply in Telegram** with a one-liner:
   - `⚖️ <weight> lb logged. 7-day avg: <avg> lb (delta <±X.X>).`
   - If no prior data: `⚖️ <weight> lb logged. Baseline.`
   - If outside the sanity range (and confirmed): same format but flag the unusual value.

## Vault-write conventions

- See `obsidian-vault-write` skill — same rules.
- Field format: `weight: 114.5` (float, no quotes).
- Replace the field, don't append a duplicate.

## Log the change

Skip routine weight logs. **Do** log a `Log.md` entry only if the user crosses a milestone they explicitly track (e.g., first day above 120 lb during a lean bulk). Use judgment — typically don't.

## Pitfalls

1. **Unit ambiguity.** "I'm 115 today" — clear (lb). "I'm 52 today" — ambiguous; could be kg. Reply asking if unclear.
2. **Stale data.** If today's `weight:` is already populated and the user reports again, ASK before overwriting: "Already logged 114.0 today — replace with 114.5?" Sparsh might weigh twice (morning + post-workout). Trust the morning value.
3. **Decimal handling.** Always pad to 1 decimal in display (114.5, not 114.50, not 114).
4. **Trend math.** Only include days with non-null `weight:`. Don't average over null.
5. **First log ever.** No comparison data exists; say "baseline" and move on. Don't fake a trend.
