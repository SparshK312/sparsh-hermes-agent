# Post-confirmation meal correction flow

Use when the user corrects a meal *after* it has already been confirmed or logged.

## Rule

- If the correction changes the food items or portions, treat it as a **correction**, not a new meal.
- Rewrite the existing meal entry in the food log in place.
- Recompute that meal's macros.
- Update daily-note totals by delta.
- Append a Log.md correction entry.
- Only create a second entry if the user explicitly says it is an addition.

## Examples

- Logged: `chicken burger, hot dog, fries, watermelon`
- User later says: `actually it was a grilled chicken sandwich and a hot dog, no fries`
- Action: replace the meal entry with the corrected items and revise totals.

## Time-only corrections

If the only correction is time, update the meal header time only.
Do not change macros unless the food/portion changed.
