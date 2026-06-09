---
name: log-water
description: Log a water intake event for Sparsh's daily hydration tracking. Triggers on messages like "drank a bottle", "had water", "/water 500", or any natural mention of hydration. Increments the water_l (litres) field in today's daily-note frontmatter. Default unit is 500ml (one typical bottle) if no amount is specified. Daily target is 2.5–3 L.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, hydration, vault-write]
    category: health
---

# log-water

## When to Use

When the user mentions drinking water, finishing a bottle, hydrating, or types `/water [amount]`. Examples:
- `/water` → default 500ml
- `/water 750` → 750ml
- `/water 1L` → 1000ml
- "drank a bottle" → 500ml
- "had 2 cups of water" → 500ml (≈250ml × 2)

Do **not** use this skill for caffeinated drinks, juice, milk, protein shakes — those are food entries; route to `log-food`.

## Step-by-step

1. **Parse the amount.** Default to 500ml. Accept `<n>` (ml), `<n>ml`, `<n>L` or `<n>l` (litres). Convert to litres (float, one decimal place).

2. **Determine today's date** in America/Toronto: `YYYY-MM-DD`.

3. **Read today's daily note:** `04 - Daily Notes/<date>.md`.
   - If it doesn't exist, the daily-note-prefill cron should have created it at 7 AM. If it's still missing (e.g., before 7 AM), create it from `Templates/Daily Note.md` first, then proceed.
   - Parse YAML frontmatter. Find the `water_l:` field.

4. **Update the field.**
   - If `water_l:` is empty/missing/null → set it to the new amount.
   - If it has a value → add the new amount to it (float math, round to 1 decimal).
   - Bump `last_updated:` to today's date (if the frontmatter has that field on daily notes — it typically doesn't, daily notes use `date:` only).

5. **Write the file back.** Preserve all other frontmatter + body content exactly. Only the `water_l:` field changes.

6. **Reply to the user in Telegram:**
   - Format: `💧 +<amount>L. Today: <total>L / 2.5L target. <delta-from-target> to go.` (or "✅ target hit" if over)
   - One line, no padding.

## Vault-write conventions (follow obsidian-vault-write skill)

- Date format `YYYY-MM-DD` always.
- Never reorder or rename frontmatter fields. Only modify the value.
- Never blast the file — read-modify-write, preserve unrelated content.
- `grep -c '^---$'` should return exactly 2 after the write (one frontmatter delimiter pair).

## Log the change

Skip `Log.md` for routine hydration logs — they're high-volume and not vault-state-changing in a way Log.md cares about. (Daily notes are raw sources per the wiki rules, not wiki pages.) Only the *first* water log of a day might be worth logging if you want adherence visibility — and that's optional.

## Pitfalls

1. **Unit confusion.** `200` alone means 200ml (≈ a small glass). `2` alone is ambiguous — ask if it's 2L or 2 cups.
2. **Multiple logs same day.** Always add, never replace. If user explicitly says "reset water" or "I miscounted, set it to 1L", that's a different intent — confirm before overwriting.
3. **Time-zone race.** If logging close to midnight Toronto, double-check which day the user means. If past midnight but they say "today" referring to the day that just ended, log to yesterday's note (with confirmation).
4. **Frontmatter syntax.** Float values: `water_l: 1.5` (no quotes). Empty: blank after the colon, not `null`.
