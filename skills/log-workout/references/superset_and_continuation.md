# Supersets and mid-session continuations

Use this pattern when the user logs a paired movement, a one-side-at-a-time superset, or a partial workout update.

## Rule of thumb

- Keep the source of truth in `exercises[]` as separate exercise entries.
- Preserve the order the user performed them.
- Use `notes` to capture the superset relationship instead of flattening the movements into prose.
- If the user continues with only a number/weight after naming the exercise earlier in the same message or immediate prior context, append to the most recently named exercise.

## Example: unilateral superset

User: "single-arm low pushdowns 10 lb each side, superset with underhand extensions same side, 2 sets each side"

Log as two exercises:

```yaml
exercises:
  - name: Single-Arm Low Pushdown
    unilateral: true
    notes: "10 lb each side; supersetted with underhand extensions"
    sets:
      - {weight_lb: 10, reps: null, side: left}
      - {weight_lb: 10, reps: null, side: right}
      - {weight_lb: 10, reps: null, side: left}
      - {weight_lb: 10, reps: null, side: right}
  - name: Underhand Triceps Extension
    unilateral: true
    notes: "paired superset with single-arm low pushdown"
    sets:
      - {weight_lb: 10, reps: null, side: left}
      - {weight_lb: 10, reps: null, side: right}
      - {weight_lb: 10, reps: null, side: left}
      - {weight_lb: 10, reps: null, side: right}
```

## Example: continuation

User: "... now rope pushdowns 25 lb, 2 sets"

- Append the sets to the latest named triceps exercise in the active session.
- Do not start a fresh session or rewrite prior movements into prose.
