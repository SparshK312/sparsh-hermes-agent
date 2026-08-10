# Hevy Screenshot Summary — Per-Set Detail Recovery

## The Problem

Hevy's summary export (the image the user typically shares) shows:
- Exercise names
- Set count per exercise
- Total session volume (lbs)
- Duration, session split (e.g., "Back + bi")

**But NOT:**
- Weight per set
- Reps per set
- Machine / weight type (dumbbell vs cable vs bar)

The `log-workout` skill needs these per-set details to write the canonical `07 - Health/Workouts/<date>.md` file with the YAML `exercises:` array of `{weight_lb, reps}` objects.

## Recovery Patterns

When a user sends a Hevy screenshot with no per-set breakdown:

### Option A: Ask the user to recall details

Most direct and accurate. Example:

> I see the Hevy summary — 6 exercises, 17 sets, 1h 13m, Back+Bi, 4,626 lbs total volume.
> To log the structured workout file, I need the weight and reps for each set. Can you give me the per-set breakdown? For example:
> - **Pull Up (Assisted):** [set 1 weight/reps], [set 2 weight/reps]
> - **Lat Pulldown (Cable):** [set 1], [set 2], [set 3]
> - And so on.

The user usually either recalls or checks the Hevy app detail view on their phone.

### Option B: Estimate from total volume + set count

If the user doesn't want to reconstruct details and is okay with an estimate:
- Total volume: 4,626 lbs
- Total sets: 17
- Average per set: 4,626 ÷ 17 ≈ 272 lbs/set

This is a rough floor. Typical rep ranges (8–15) mean actual per-set weight varies. But it's a starting point for fill-in-the-blanks. Mark `source: hevy-summary-estimated` in the workout file so the user knows the numbers are inferred, not confirmed.

### Option C: User exports full Hevy JSON

Advanced recovery: Hevy exports full session data as JSON (Settings > Export). The JSON contains every set with weight/reps. If the user is willing to do this, parse it and extract per-set detail. This is the "full fidelity" path but adds friction.

## When to Trigger

1. **User sends a Hevy screenshot summary** (just the overview card) and you're in `log-workout`.
2. **Step 3 (classify action)** — before you proceed to step 4 (build exercises array), realize the screenshot doesn't have per-set detail.
3. **Present Option A first** (ask the user). If they say "estimate it" or "I don't remember", fall back to Option B (with the source caveat).
4. **Never fabricate per-set detail from silence.** Asking is faster than guessing wrong and having to undo later.

## Why This Matters

The canonical workout file is the source of truth for PR detection, volume tracking, and progressive overload analysis. If the per-set weights are estimates instead of actual, future queries ("what was my lat pulldown max in July?") return noisy data. Better to acknowledge the gap upfront than silently log a guessed breakdown.

## Session Example

User sends Hevy screenshot of "Back + bi, 1h 13m, 4,626 lbs, 17 sets, 6 exercises."

**Wrong approach:**
```
Hmm, no per-set detail in the screenshot. I'll guess.
- Pull Up (Assisted): 2 sets × 200 lbs × 8 reps (estimated)
- Lat Pulldown: 3 sets × 150 lbs × 10 reps (estimated)
...
```
→ Silent guess. User doesn't see the assumption.

**Right approach:**
```
I see the Hevy summary — Back+Bi, 17 sets, 1h 13m, 4,626 lbs total.
To log the structured workout, I need the weight and reps per set.
Can you give me the breakdown, or should I estimate from the total volume?
```
→ User chooses. Either way, the next step is transparent.
