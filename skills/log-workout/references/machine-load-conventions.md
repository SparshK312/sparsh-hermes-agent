# Machine load conventions

Use these conventions when the user reports machine-based lower-body work with per-side or unilateral wording.

## Per-side wording

When the user says a weight is on **each side** of a machine, preserve both values:

- Put the **combined load** in `weight_lb` when the machine load is the sum of both sides.
- Preserve the **per-side math** in `notes`.

Examples:

- `25 lb on each side of vertical leg press` → `weight_lb: 50`, notes: `25 lb per side; total load 50 lb`
- `50 lb on each side` where the machine load is additive → `weight_lb: 100`, notes: `50 lb per side; total load 100 lb`

## Unilateral lifts

If the movement itself is one-side-at-a-time, mark `unilateral: true` and keep the per-side wording in notes.

If the user does not specify whether the machine load is additive or per-side, ask once instead of guessing.

## Logging rule

Do not collapse the per-side explanation into a generic label. The notes should make the math obvious for later review and progression tracking.