# Watch/cache fallback for sleep logging

Session-learned workflow for days when automatic sleep data is missing or delayed.

## When to use
- User reports sleep in natural language because the watch was dead/uncharged, the cache has not populated yet, or the auto-import is incomplete.
- The user may later provide the cached sleep data for the same night or week.

## Workflow
1. Check the cache first when tracker data is expected:
   - Quick snapshot: `~/.hermes/health/hae/last.json`
   - Raw exports: `~/.hermes/health/hae/raw/<UTC-ISO-ts>.json`
   - Look for `sleep_analysis` and the latest completed overnight window.
2. Treat the user's natural-language report as a temporary source of truth only when the watch was dead/uncharged or the cache has not populated yet.
3. If the sleep window is clear, infer `sleep_hours` from the reported bedtime and wake time.
4. If the cache is missing, it is acceptable to create/update the daily note without `sleep_hours` first, then backfill once cache data arrives.
5. When the cache or watch data arrives later, update the same daily note rather than creating a competing record.
6. Keep the note concise: record the numeric sleep field(s) in frontmatter and a short note in the body only if needed.

## Pitfalls
- Do not block logging just because automatic sleep data is unavailable.
- Do not overwrite other health fields when backfilling sleep.
- Do not treat natural-language sleep as a permanent replacement for tracker data when tracker data is expected later.
