---
name: log-sleep
description: 'Log last night''s sleep hours and optional quality rating to today''s daily-note frontmatter. Triggers on "/sleep 7.5", "slept 6 hours", "/sleep 7 quality 8", or natural mentions. Prefer Health Auto Export / watch data when available; use user-reported sleep as fallback when the watch was dead/uncharged or the cache has not populated yet.'
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, sleep, vault-write]
    category: health
---

# log-sleep

## When to Use

When the user reports last night's sleep. Examples:
- `/sleep 7.5` → 7.5 hours
- `/sleep 7 quality 8` → 7h, quality 8/10
- "slept 6 hours" → 6 hours
- "got 7.5 last night, felt rough — 5/10" → 7.5h, quality 5

**HAE caveat:** when Apple Watch / Health Auto Export data is expected, check the cache first before treating a natural-language report as final. If the watch was dead/uncharged, or the cache has not populated yet, use the user report as a temporary fallback and backfill later when tracker data arrives. See `references/watch-cache-fallback.md` for the cache-backfill workflow.

## Step-by-step

1. **Parse the input.**
   - Hours: float, required. Range 0–14 (sanity-check; flag if outside).
   - Quality: optional integer 1–10.

2. **Check for tracker/cache data first.**
   - If the user is clearly asking for logged sleep and Apple Watch / HAE data is expected, check the latest cache snapshot before falling back to the user's wording.
   - Quick probe: `~/.hermes/health/hae/last.json`.
   - If needed for a specific night/week, inspect `~/.hermes/health/hae/raw/<timestamp>.json` for `sleep_analysis`.
   - If tracker data is present, prefer it over a fuzzy natural-language summary.

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
