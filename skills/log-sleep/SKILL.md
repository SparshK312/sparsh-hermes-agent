---
name: log-sleep
description: 'Log last night''s sleep hours and optional quality rating to today''s daily-note frontmatter. Triggers on "/sleep 7.5", "slept 6 hours", "/sleep 7 quality 8", or natural mentions. Manual override during Phase 1; will be auto-populated by Health Auto Export (Apple Watch) in Phase 3. Target is at least 7 hours, bed by midnight. Phase 3 will supersede manual entries when HAE data arrives.'
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

**Phase 3 caveat:** once Health Auto Export is wired (Phase 3 of the build), sleep_hours will be written automatically from Apple Watch data. Until then, this skill is the only path. After Phase 3, manual logs via this skill are still valid — they override the auto-fill (e.g., for "tracker said 8h but I was lying awake 1h of that").

## Step-by-step

1. **Parse the input.**
   - Hours: float, required. Range 0–14 (sanity-check; flag if outside).
   - Quality: optional integer 1–10.

2. **Today's date** in America/Toronto: `YYYY-MM-DD`. (Sleep that *ended* today is logged on today's note, even though it started yesterday.)

3. **Read today's daily note:** `04 - Daily Notes/<date>.md` (create from template if missing).

4. **Update frontmatter:**
   - `sleep_hours: <float>` (replace, don't accumulate).
   - `sleep_quality: <int>` if provided.

5. **Reply in Telegram (one line):**
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
2. **Phase 3 collision.** Once HAE is live, the auto-ingest cron writes sleep_hours at 23:30. If user later runs /sleep manually overnight, the manual value should override. Treat the most recent write as authoritative.
3. **Quality without hours.** If user only gives quality ("slept like crap, 4/10"), ASK for hours before logging — quality alone is incomplete.
4. **"Got 7 hours but woke up 5 times."** Phase 1 doesn't capture wake-ups. Note it in the body's `## Health` notes section if user surfaces it, but don't try to model interruptions in frontmatter.
