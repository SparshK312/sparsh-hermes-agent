---
name: log-vitamins
description: 'Log that Sparsh took his daily supplements / vitamins. Triggers on "/vitamins", "took my vitamins", "did my supplements", "took creatine and whey", etc. Reads the supplement list from 07 - Health/Supplements.md (canonical roster), records that today''s stack was taken in the daily-note frontmatter (vitamins_taken boolean and optionally supplements_today list), and replies with confirmation.'
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, supplements, vault-write]
    category: health
---

# log-vitamins

## When to Use

When the user reports taking supplements / vitamins / pills. Examples:
- `/vitamins` → marks the full daily stack as taken
- "took my vitamins" → same
- "took creatine and whey" → marks specific items (partial log — see below)
- "had my multivitamin" → specific item

Do **not** use for medications (prescription drugs) — those need a different flow we haven't built yet.

## Step-by-step

1. **Today's date** in America/Toronto: `YYYY-MM-DD`.

2. **Read the supplements roster** at `07 - Health/Supplements.md`. Expected structure (markdown list under `## Daily Stack`):
   ```
   ## Daily Stack
   - Vitamin D — 2000 IU, morning
   - Creatine — 5g, anytime
   - Whey protein — 25g, post-workout
   - Multivitamin — 1 tab, morning
   ```
   If the file doesn't exist yet, reply: "No supplements list found at `07 - Health/Supplements.md`. Add one with your daily stack first, then re-run."

3. **Determine what to log:**
   - If user said `/vitamins` or generic "took my vitamins" → mark **all items in the daily stack** as taken today.
   - If user named specific items → match against the stack list, mark only those.
   - Items the user mentions that aren't in the roster: log them too with a note (`supplements_today: ["creatine", "whey", "<unknown: matcha>"]`).

4. **Write it with the deterministic vault writer — `vault_log.py`.** Do NOT hand-edit the YAML (no `patch`, no `python3 -c`, no heredocs, no `execute_code` — those trip the approval gate, are blocked in cron, and corrupt repeated `key:` lines). One command sets `vitamins_taken: true` + optional `supplements_today`, preserves every other field + the body, and creates the note from the template if missing:

   ```
   /usr/bin/python3 /home/hermes/.hermes/scripts/vault/vault_log.py vitamins [--supplements "vitamin-d, creatine, whey"]
   ```

   Pass the matched stack item names (comma-separated) to `--supplements`; omit it to just mark the stack taken. `--date YYYY-MM-DD` only for a backfill.

5. **Reply in Telegram:**
   - Full stack logged: `💊 Daily stack ✓ (<count> items).`
   - Partial: `💊 Logged: <list>. Missing from your stack: <remaining>.`
   - Unknown items: `💊 Logged. Note: "matcha" isn't in your supplements list — add it to 07 - Health/Supplements.md if it's a regular.`

## Vault-write conventions

- See `obsidian-vault-write` skill.
- `vitamins_taken: true` (boolean, lowercase, no quotes).
- `supplements_today: [creatine, whey, vitamin-d]` (YAML list, dash-separated names).
- If user logs vitamins twice the same day, keep `vitamins_taken: true` and **union** the `supplements_today` list (don't duplicate).

## Log the change

Skip Log.md — routine adherence event.

## Pitfalls

1. **No roster file.** Don't guess what supplements the user takes. Block until `07 - Health/Supplements.md` exists.
2. **Fuzzy matching.** "Took D" probably means Vitamin D. "Took the multi" probably means multivitamin. Use the roster context to disambiguate; ask if genuinely unclear.
3. **Time of day.** Some supplements are timing-specific (creatine post-workout, vitamin D morning). For Phase 1, just track yes/no. Phase 2+ can track when.
4. **Don't auto-create the roster.** If it doesn't exist, ask the user to make it — supplements lists are personal data, the agent shouldn't invent.
