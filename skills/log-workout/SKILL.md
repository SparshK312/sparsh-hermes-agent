---
name: log-workout
description: 'Log a workout — completed session, mid-workout update, single-set addition, or correction. Triggers on any lift/cardio description in text or voice. Examples: "did pull day, lat pulldown 85 lb 3 sets", "currently working out, started incline bench 30 lb 3x", "finished rear delt, 2nd set was 45 lb" (UPDATE prior set), "20 lb 3 sets" (continuation of prior exercise from context), "/log_workout upper A", "did 30 min cardio". Writes a structured canonical file at 07 - Health/Workouts/<date>.md with a YAML exercises[] array (per-set weight/reps — this is the source of truth for PR + volume tracking) AND updates the daily note frontmatter ''lifted'' field + the Health section ''**Workout:**'' line (back-compat for daily-note Dataview lift-count). Read-merge-write on the workouts file so progressive updates over a 60-minute session accumulate correctly. Vault-only — no MCP, no external tools.'
version: 2.0.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [health, workout, vault-write, structured-data]
    category: health
---

# log-workout

## What changed in v2

v1 wrote a prose `**Workout:**` line to the daily note. That made progressive overload impossible to query — "what was my preacher curl max in May" required reading every daily note by hand. v2 keeps the daily-note line for the quick-scan UX, but the **canonical record** is now a structured file at `07 - Health/Workouts/<date>.md` with a YAML `exercises:` array of `{weight_lb, reps, ...}` objects. Dataview can now answer PR + volume questions.

## When to Use

Any time the user mentions a workout — completed, in-progress, single-set continuation, or correction.

Examples:
- "did pull day, lat pulldown 85 lb 3 sets" → new session, append exercise
- "currently working out, hitting back and bi. lat pulldown 42.5/side dual cable, 3 sets" → new session, first exercise
- "done with 3 sets of single-arm rows at 40 lb. Now doing rear delt flys 40 lb, 2 sets" → mid-session, append next exercise
- "finished rear delt, 2nd set was 45 lb" → UPDATE the 2nd set of the latest "rear delt" exercise
- "20 lb 3 sets" → CONTINUATION: from prior message context, the user is continuing the most recently announced exercise
- "did 30 min cardio" → cardio entry
- "/log_workout upper A"

Do **NOT** use for:
- Pre-workout planning ("planning to lift today") — wait until in-progress or done
- Food/protein around workouts → log-food
- Sleep/recovery notes → those are manual frontmatter / journal

## Vault-only — no MCP, no external tools

Uses only `obsidian-vault-write` (read + patch). No Hevy MCP. Architecture decision in [[Architecture]] D6.

## Step-by-step

### 1. Determine today's date

In America/Toronto: `YYYY-MM-DD`.

### 2. Read the existing workout file (if any)

Path: `07 - Health/Workouts/<date>.md`. If the file exists, parse the YAML frontmatter's `exercises:` array into a structured form. If it doesn't exist, this is the first log of the day — start with `exercises: []`.

### 3. Classify the user's message into ONE action

- **`APPEND_NEW_EXERCISE`** — user mentions an exercise name not yet in `exercises[]`, OR this is the first log of a fresh session. Add a new entry to `exercises[]`.
- **`APPEND_SET_TO_LATEST`** — user mentions sets/weight without naming an exercise (e.g., "20 lb 3 sets" after "now doing hammer curls"). Append the new sets to `exercises[-1].sets`.
- **`UPDATE_SET`** — user corrects a specific prior set (e.g., "rear delt 2nd set was actually 45 lb"). Find the matching exercise (by name, fuzzy-matched), then mutate `exercises[i].sets[j]`.
- **`FINALIZE`** — user signals end-of-session ("done", empty message, photo with no caption mid-conversation). Compute totals; no exercise change.

If the action is genuinely ambiguous (e.g., "did 20 lb" with no prior context), ask one clarifying question instead of guessing.

### 4. Build / mutate the structured exercises array

YAML schema for each exercise entry:

```yaml
- name: <Exercise Name>          # canonical-case, e.g. "Lat Pulldown"
  unilateral: true|false         # default false; true for single-arm / single-leg
  machine: <optional>            # e.g. "dual-cable", "smith", "hammer-strength"
  notes: <optional free-text>    # e.g. "42.5 lb/side dual cable, total load 85 lb"
  sets:
    - {weight_lb: 85, reps: 12}
    - {weight_lb: 85, reps: 10}
    - {weight_lb: 85, reps: 8}
```

**Unilateral handling**: chest-supported single-arm row at 40 lb done for 3 sets per arm = 6 entries in `sets`, each with a `side: left|right` field. The body display pairs them. If the user just says "40 lb each arm, 3 sets" without specifying side order, log them as 3 left + 3 right (or just 3 sets with `unilateral: true` and `each_arm: true`, no per-side breakdown).

**Dropsets / supersets**: add a `note` field on the set: `{weight_lb: 85, reps: 8, note: "dropset"}`. Don't over-engineer the schema for rare cases.

**Reps unknown** (user said "lat pulldown 85 lb 3 sets" with no rep count): write `reps: null` and add the count later if mentioned. Don't fabricate.

### 5. Build the category label

`lifted` field for daily-note frontmatter — same shape as v1. ~30 chars max.

- `Upper A` / `Upper B` / `Lower A` / `Lower B` — when matches [[Training Plan]]
- `Push (chest/shoulders/triceps)` / `Pull (back/biceps)` / `Legs` — when body-part split
- `Cardio (<duration>)` — pure cardio
- `Mixed (<brief>)` — anything else

Match the user's actual training, not the plan label (if they did Push but plan said Upper A, log Push — see Pitfall #5).

### 6. Write the structured workout file

`07 - Health/Workouts/<date>.md`:

```yaml
---
type: workout
date: YYYY-MM-DD
split: <category label from step 5>
duration_min: <optional, only if user mentioned>
total_sets: <auto-computed: sum of len(ex.sets) across exercises>
exercises:
  - name: Lat Pulldown
    machine: dual-cable
    notes: "42.5 lb/side dual cable, total load 85 lb"
    sets:
      - {weight_lb: 85, reps: 12}
      - {weight_lb: 85, reps: 10}
      - {weight_lb: 85, reps: 8}
  - name: Chest-Supported Row
    unilateral: true
    notes: "40 lb each arm"
    sets:
      - {weight_lb: 40, reps: 10}
      - {weight_lb: 40, reps: 10}
      - {weight_lb: 40, reps: 10}
last_updated: <ISO 8601 timestamp, America/Toronto>
---

# Workout — <Day Mon D, YYYY> · <split>

> Source: log-workout v2. Daily-note `**Workout:**` line links back here.

## Lat Pulldown (3 sets)
*42.5 lb/side dual cable, total load 85 lb*

- Set 1: 85 lb × 12
- Set 2: 85 lb × 10
- Set 3: 85 lb × 8

## Chest-Supported Row (3 sets, unilateral)
*40 lb each arm*

- Set 1: 40 lb × 10
- Set 2: 40 lb × 10
- Set 3: 40 lb × 10
```

The body section is **auto-regenerated** from the frontmatter on every write — never hand-edit the body. The frontmatter is the source of truth.

Create the parent directory `07 - Health/Workouts/` if it doesn't exist.

### 7. Update the daily note (back-compat)

`04 - Daily Notes/<date>.md`:

**Frontmatter `lifted:`** = category label from step 5. If already populated (e.g., morning lift + afternoon cardio), append with `+`: `Push + Cardio (20 min)`. Don't blast prior content.

**`## Health` section's `**Workout:**` line** = concise narrative summary referencing the structured file. Format:

```
- **Workout:** Pull — lat pulldown 85 lb × 3, chest-supported row 40 lb × 3 (unilateral), rear delt fly 40-45 lb × 2, preacher curl 40 lb × 3, hammer curl 20 lb × 3. Full breakdown: [[Workouts/<date>]]
```

If the daily note's Health section is missing the `**Workout:**` bullet (unusual — template should have it), add it.

### 8. PR detection (best-effort)

For each set added or updated in step 4, check if its `weight_lb` exceeds all prior `weight_lb` values for that exercise name across `07 - Health/Workouts/*.md` (excluding today's file). If a new max, flag in the reply: `🎯 PR — Lat Pulldown: 85 lb (prev max 80 lb)`.

If reading historical workout files fails (file not parseable, etc.) → skip PR detection silently. Don't block the log.

### 9. Reply to the user

Format:
```
🏋️ Logged: <category label>.
<N exercises, M total sets so far>.
[Optional PR alert]
[Optional milestone: "Session 1/4 for the week" or "Week target hit 🎯"]
```

Examples:
- `🏋️ Logged: Pull. 5 exercises, 14 sets. Session 1/4 for the week.`
- `🏋️ Logged: Push. 4 exercises, 12 sets. 🎯 PR — Incline DB Press: 35 lb (prev max 30 lb).`
- `🏋️ Logged: Cardio (30 min).`

## Vault-write conventions

- Date format `YYYY-MM-DD`, Toronto local time.
- See `obsidian-vault-write` skill for the file-write primitive.
- `lifted` is a free-form string in YAML — single line, no quotes needed unless it contains `:`. If the label has a colon (rare), wrap in single quotes: `lifted: 'Push: chest+shoulders'`.
- The workout file's body is **auto-regenerated** from frontmatter on each write. Read-modify-write the YAML; rewrite the body section from scratch.
- `grep -c '^---$'` on both the daily note AND the workout file should return exactly 2 after a write.
- Preserve all other daily-note frontmatter + body content exactly. Only touch `lifted:` and `**Workout:**`.

## Log the change

Skip Log.md for routine workout logs — the workout file IS the canonical record. Log.md only gets an entry for first-of-day workout creation:

```
## [YYYY-MM-DD] ingest | 07 - Health/Workouts/<date>.md — <split> session started (N exercises so far)
```

Subsequent updates within the same session: no Log.md entry needed.

## Pitfalls

1. **Voice transcription noise** — fillers ("um", "and then"), homophones ("found his shoulder press" instead of "fourth he shoulder press"). Clean up when parsing. If a number sounds wrong (e.g., "two hundred pounds" for a known 30 lb dumbbell exercise), flag in reply rather than logging the bad number.

2. **Continuation messages** — "20 lb 3 sets" after "now doing X" means `APPEND_SET_TO_LATEST`, NOT a new exercise. Use the LATEST mentioned exercise in the conversation, even if it wasn't the most recent `APPEND_NEW_EXERCISE` action (user might be replying about a still-active exercise).

3. **Updates vs additions** — "2nd set was 45 lb" means `UPDATE_SET` on the latest matching exercise's set 2. "Did another set at 45 lb" means APPEND a new set.

4. **Plate units** — default lb (matches [[Profile]] baselines). If user says kg, store as lb (convert: kg × 2.20462), note in `notes` field: "user said 20 kg → ~44 lb".

5. **Plan vs reality** — don't normalize to plan vocabulary. If user did Push but plan said Upper A, log Push. The gap is signal, not noise.

6. **Cardio + lifting same day** — `lifted: Push + Cardio (20 min)`. In the workouts file, both go under the same `exercises:` array with cardio entries having `name: Treadmill`, `sets: [{duration_min: 20, distance_km: 3.2}]` (no weight_lb).

7. **Skipped session reported** — "didn't lift today, just rested" → do NOT create the workout file, do NOT set `lifted`. Don't write "rest" — it confuses the week-summary lift-count.

8. **Duplicate session same day** — if `lifted` already has content AND the user describes a separate workout (e.g., "lifted in the morning, doing cardio later"), update `lifted` with `+` and APPEND new exercises to the same workouts file. Different sessions don't get separate files.

9. **Dual-cable / per-side weight ambiguity** — user says "42.5 lb dual cable, 3 sets". If the machine adds the two sides (total load), log `weight_lb: 85` with `notes: "42.5 lb/side dual cable"`. If the machine is per-side and only one side is the working load, log `weight_lb: 42.5`. Ask the user once when ambiguous; remember the choice for future logs of the same machine.

10. **Reps unknown** — write `reps: null`. Don't fabricate ("probably 10"). The user fills it in later if they care.

11. **Mid-message corrections** — user says "I didn't eat turkey link, it was chicken sausage" → that's log-food's domain, NOT log-workout. Don't get confused by mixed-topic messages; route the food correction to log-food separately.

12. **Heavy day vs light day editorializing** — stay factual. Don't write "great session!" in the reply. Coaching commentary belongs to the `/health-coach` skill (Phase 4), not the logger.

13. **PR false positives** — a "new PR" of 5 lb on an exercise the user only ever did once before isn't really a PR; it's a first-time-with-weight. Only flag if the prior max came from ≥2 prior sessions. Cheap heuristic; avoids spamming the user with bogus PRs.
