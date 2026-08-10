# Incremental workout logging

Use this pattern when the user is actively in the gym and keeps narrating the session in pieces.

## Rules
- Create today's workout file as soon as the first exercise appears.
- Preserve the exact exercise order the user reports.
- Append new sets/exercises to the same file as the session continues.
- Keep the daily note's `lifted:` field and `## Health` workout line in sync with the workout file.
- Use the real split the user actually did (`Push`, `Pull`, `Legs`, or a mixed label), not the planned split.
- Reply with an in-progress count: `N exercises, M sets so far` until the user signals the session is done.

## Example shape
- User: starts with incline dumbbell bench, later adds cable flys, then a triceps superset, then finishes with bar pushdowns.
- Log as separate exercises in that order, even if the session is still open.
- If the user later sends one more set, mutate the current workout file instead of creating a new day/file.
