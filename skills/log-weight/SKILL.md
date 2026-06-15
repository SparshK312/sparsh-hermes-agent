---
name: log-weight
description: Log Sparsh's morning bodyweight to today's daily-note frontmatter. Triggers on "/weight 150.5", "I weighed 151 today", "weight check 149.8", or any number-with-weight-context. Unit is pounds (lb). Best practice is morning, post-bathroom, pre-food. Updates the `weight` field in today's daily note frontmatter for trend tracking and Dataview rollups in `00 - Dashboard/Health & Fitness.md`.
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
- `/weight 150.5` → 150.5 lb
- "weighed 151 this morning" → 151 lb
- "weight: 149.8" → 149.8 lb
- "I'm 68 kg today" → convert to 150.0 lb

Do **not** use for weights of food, equipment, or anything that's not the user's bodyweight.

## Notes

- The user may send a terse morning weigh-in like `Morning weigh in 150.5` or `Morning weight 150 lb`. Treat that as sufficient input when the meaning is clear.
- A bare message like `Weight 150` is also sufficient when the context is bodyweight; assume lb unless a unit says otherwise.
- Keep the response terse and action-first.
- See `references/user-weight-logging-patterns.md` for the user-specific shorthand examples.

## Step-by-step

1. **Parse the value.** Required: a positive number. Optional: unit (`lb`, `kg`, `pounds`, `kilos`).
   - If no unit specified → assume **lb** (Sparsh's default).
   - If `kg` → convert: `lb = kg × 2.2046` (one decimal).

2. **Sanity check.** Reasonable range for Sparsh: 50–500 lb. If outside that range, reply asking for confirmation before writing.

3. **Write it with the deterministic vault writer — `vault_log.py`.** Do NOT hand-edit the YAML (no `patch`, no `python3 -c`, no heredocs, no `execute_code` — those trip the approval gate, are blocked in cron, and corrupt repeated `key:` lines). One command sets `weight` (a snapshot — it replaces, never accumulates), preserves every other field + the body, and creates today's note from the template if missing. It rejects values outside 50–500 lb, so do your kg→lb conversion and sanity check first:

   ```
   /usr/bin/python3 /home/hermes/.hermes/scripts/vault/vault_log.py weight --lb <value>
   ```

   Pass `--date YYYY-MM-DD` only for a backfill.

4. **Compute trend (optional but useful):**
   - Read the prior 7 daily notes (if they exist) and pull their `weight:` values.
   - Compute 7-day avg. Compare to today.
   - Note the delta in the reply.

5. **Reply in Telegram** with a one-liner:
   - `⚖️ <weight> lb logged. 7-day avg: <avg> lb (delta <±X.X>).`
   - If no prior data: `⚖️ <weight> lb logged. Baseline.`
   - If outside the sanity range (and confirmed): same format but flag the unusual value.

## Vault-write conventions

- See `obsidian-vault-write` skill — same rules.
- Field format: `weight: 150.5` (float, no quotes).
- Replace the field, don't append a duplicate.

## Log the change

Skip routine weight logs. **Do** log a `Log.md` entry only if the user crosses a milestone they explicitly track (e.g., first day above 180 lb during a lean bulk). Use judgment — typically don't.

## Pitfalls

1. **Unit ambiguity.** "I'm 151 today" — clear (lb). "I'm 68 today" — ambiguous; could be kg. Reply asking if unclear.
2. **Stale data.** If today's `weight:` is already populated and the user reports again, ASK before overwriting: "Already logged 150.0 today — replace with 150.5?" Sparsh might weigh twice (morning + post-workout). Trust the morning value.
3. **Decimal handling.** Always pad to 1 decimal in display (150.5, not 150.50, not 150).
4. **Trend math.** Only include days with non-null `weight:`. Don't average over null.
5. **First log ever.** No comparison data exists; say "baseline" and move on. Don't fake a trend.
