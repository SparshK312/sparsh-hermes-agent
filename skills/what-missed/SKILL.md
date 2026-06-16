---
name: what-missed
description: Identify what Sparsh hasn't logged today across the tracked health fields — weight, sleep, water, food (meals not yet captured), workout, vitamins. Triggers on "/missed", "what didn't I log", "/gaps", "anything pending". Reads today's daily-note frontmatter and reports gaps based on time-of-day expectations (e.g., no weight yet at 8am is fine; no weight by 11am is a gap). Read-only.
version: 1.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, accountability, dashboard]
    category: health
---

# what-missed

## When to Use

User asks what's been missed / not logged. Examples:
- `/missed`
- "anything I forgot to log"
- "what's still pending today"
- "/gaps"

Read-only.

## Step-by-step

1. **Today's date + current time** in America/Toronto. Note the local hour (24h).

2. **Read** `04 - Daily Notes/<date>.md`. Parse frontmatter.

3. **If wearables are involved, sanity-check the sync first.**
   - Do **not** ask the user to manually supply sleep/steps if the numbers look wrong.
   - Check whether the daily note is simply stale versus the HAE archive not yet receiving the final totals.
   - Use `hae_synced` / `07 - Health/Metrics/metrics.csv` / `~/.hermes/health/hae/sync.log` / raw payload timestamps to distinguish "missing sync" from "needs correction".
   - Only fall back to manual correction when the user gives the exact numbers or another authoritative source confirms them.

4. **Gap detection** — time-aware checks. A field is a "gap" only if (a) it's still null/empty AND (b) we're past the time it would normally be logged.

| Field | Expected by | Gap label |
|---|---|---|
| `weight` | 11 AM | "morning weigh-in" |
| `sleep_hours` | 11 AM (HAE may auto-fill in Phase 3) | "last night's sleep" |
| `vitamins_taken` | 12 PM | "supplements" |
| `kcal` (any value) | 2 PM (lunch should be in by then) | "no food logged yet today" |
| `water_l` ≥ 0.5L | 2 PM | "water intake (target 2.5L)" |
| `water_l` ≥ 1.5L | 6 PM | "water midday catch-up" |
| `lifted` non-empty | 9 PM, only on planned gym days | "today's planned workout" |
| `kcal` ≥ 1800 | 9 PM | "under 1800 kcal — under-eating signal" |
| `protein_g` ≥ 100 | 9 PM | "under 100g protein — under-eating signal" |

4. **Reply in Telegram:**

If no gaps:
```
✓ Nothing missed. Keep going.
```

If gaps:
```
📋 Gaps as of <H:MM AM/PM>
━━━━━━━━━
• <gap label 1>
• <gap label 2>
…

Quick logs:
/weight <lb>   /water <ml>   /vitamins   /log-food <meal>
```

Show **only** the relevant slash-command shortcuts based on the gaps (don't list ones that aren't gaps).

5. **Severity flag (last line):**
   - Under-eating signal triggered (kcal <1800 OR protein <100 at 9 PM) → end with `🚨 This is the under-eating relapse pattern. Eat now.`
   - Multiple gaps at 9 PM → end with `Day is slipping. Capture before bed.`
   - Single gap, non-critical → no closing flag.

## Log the change

**Do not log.** Read-only.

## Pitfalls

1. **Lifted gap requires schedule context.** Don't say "no workout today" on a rest day. Check Training Plan in vault (`07 - Health/Training Plan.md`) for today's day-of-week — Mon = Upper A, Wed = Lower A, etc. If today is a rest day, skip the lifted check.
2. **Phase 3 collision.** Once HAE auto-fills sleep and weight, this skill should *still* show them as gaps if HAE hasn't ingested yet (e.g., overnight sync hasn't run). Time-aware logic handles this — if it's 7 AM and HAE ingests at 23:30, the morning gap is real.
3. **False alarm under noon.** Don't flag missing kcal at 10 AM. People eat at variable times.
4. **Don't double-fire under-eating.** If both kcal AND protein are under, mention once with combined framing, not twice.
5. **"Vitamins gap" is fine in afternoon, not at 9 PM.** Supplements have all-day windows; flag only after a reasonable cutoff (use 12 PM for stack timing).
