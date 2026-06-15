# Correction after an initial meal estimate

Use this pattern when the user corrects a meal after it has already been logged from a photo or rough description.

## What happened
- Initial log was a guessed/estimated composite meal.
- User later clarified the actual items.
- The corrected meal was still a single mixed meal, not a second independent meal.

## Required handling
1. Treat the user correction as the source of truth.
2. Rewrite the existing food-log entry in place.
3. Recompute that meal's macros from the clarified items.
4. Update today's daily-note totals by the delta from the old estimate.
5. Append a short correction/update entry to `Log.md`.

## Do not
- Do not append a second meal entry unless the user explicitly says it was an addition.
- Do not leave the earlier guessed items in the log.
- Do not update only the daily-note totals without fixing the canonical food log.

## Example from this session
A previously guessed lunch was corrected to:
- chili cheese dog
- grilled mojo chicken sandwich
- pineapple slaw
- mojo mayo

The final log used a single revised estimate and the daily totals were recomputed from that replacement, not duplicated.
