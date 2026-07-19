---
name: log-sleep
description: 'Log last night''s sleep hours and optional quality rating to today''s daily-note frontmatter. Triggers on "/sleep 7.5", "slept 6 hours", "/sleep 7 quality 8", or natural mentions. When the user REPORTS a number, log it directly — their report is authoritative; do NOT dig through the HAE watch cache. Watch/HAE sleep flows into the note automatically via the hae-sync cron, so you never reconcile the two live.'
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, sleep, vault-write]
    category: health
---

# log-sleep

## ⚡ How to log — read first

1. **The date is GIVEN** in the turn context (`[Current date …] Today = YYYY-MM-DD`, plus recent days). Use it exactly — **never run `date` or guess.**
2. **Write via `vault_log.py sleep` run through `terminal` — the ONE and ONLY write.** Direct `patch` / `write_file` / `execute_code` on the daily note are BLOCKED by a guard; don't attempt them. **Log immediately — no confirmation prompt** — then relay `vault_log`'s confirmation line (don't recompute it).


## When to Use

When the user reports last night's sleep. Examples:
- `/sleep 7.5` → 7.5 hours
- `/sleep 7 quality 8` → 7h, quality 8/10
- "slept 6 hours" → 6 hours
- "got 7.5 last night, felt rough — 5/10" → 7.5h, quality 5

**🛑 Do NOT spelunk the HAE cache when the user reports a number.** If the user says "slept 10 hours" / "/sleep 7.5", their report IS the value — log it immediately and stop. The watch/HAE sleep lands in the note on its own via the `hae-sync` cron (`hae_daily_ingest.py`); you do NOT need to read `~/.hermes/health/hae/last.json` or loop through `raw/*.json` to "confirm" it. Doing so is what makes the bot thrash — the watch cache is often sparse/empty for a given night, so hunting for `sleep_analysis` there is a dead end. The two paths (manual report vs watch sync) are independent by design.

## Step-by-step

1. **Parse the input.**
   - Hours: float, required. Range 0–14 (sanity-check; flag if outside).
   - Quality: optional integer 1–10.

2. **User gave a number → log it, skip the cache entirely.** Go straight to step 3. Do not read `last.json`, do not open any `raw/*.json`. (The watch value, if any, syncs in on its own.)

   **Only** touch the cache in the narrow case where the user asks *what the watch/tracker recorded* and gives NO number of their own (e.g. "what did my watch say I slept?"). Even then: read `~/.hermes/health/hae/last.json` **once**. If that night's `sleep_analysis` total isn't there, tell them the watch didn't capture it and stop — do **not** loop through `raw/*.json` hunting for it. That loop is the thrash we're avoiding.

3. **Write it with the deterministic vault writer — `vault_log.py`.** Do NOT hand-edit the YAML (no `patch`, no `python3 -c`, no heredocs, no `execute_code` — those trip the approval gate, are blocked in cron, and corrupt repeated `key:` lines). One command sets `sleep_hours` (replaces, never accumulates) + optional `sleep_quality`, preserves every other field + the body, and creates the note from the template if missing. Sleep that *ended* today is logged on today's note (default date is today, Toronto):

   ```
   /usr/bin/python3 /home/hermes/.hermes/scripts/vault/vault_log.py sleep --hours <float> [--quality <1-10>]
   ```

   It prints a one-line flag (✓ good ≥7h / ⚠ under target / 🚨 under 6h). Pass `--date YYYY-MM-DD` to backfill a tracker value onto an earlier night.

4. **Reply in Telegram (one line):**
   - `😴 <hours>h logged${quality ? ' (q' + quality + '/10)' : ''}. <flag>`
   - Flag = `✓ good` if ≥7h, `⚠ under target` if <7h, `🚨 under 6h` if <6h.

## Vault-write conventions

- See `obsidian-vault-write` skill.
- `sleep_hours: 7.5` (float, no quotes).
- `sleep_quality: 8` (int, no quotes).
- Replace, don't append.

## Log the change

Skip routine logs. **Do** append a `Log.md` entry if sleep crosses an explicit watch-flag threshold (per Coach Memory):
- `## [YYYY-MM-DD] update | log-sleep — sleep <X>h, under 6h flag triggered`

Use sparingly; only on the threshold-trip, not every short night.

## Pitfalls

1. **Did sleep start or END today?** Convention: log on the date the sleep ended (= today's note when logging in the morning).
2. **HAE collision.** If a cached sleep value arrives after a manual fallback was already written, backfill the daily note with the tracker value unless the user explicitly corrected it. Treat the most recent authoritative source as the final value for that date.
3. **Quality without hours.** If user only gives quality ("slept like crap, 4/10"), ASK for hours before logging — quality alone is incomplete.
4. **"Got 7 hours but woke up 5 times."** Phase 1 doesn't capture wake-ups. Note it in the body's `## Health` notes section if user surfaces it, but don't try to model interruptions in frontmatter.
