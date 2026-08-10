# Whole-day recap + correction pattern

Use when the user gives a verbose same-day recap spanning multiple locations or time blocks, then immediately corrects one or two items before logging.

## Pattern
- Bucket items by rough time/location for the confirmation UI, but treat the whole message as one day-level logging event.
- If the user adds a portion correction after the first estimate (e.g. “had more tots”, “only half the donut”), update the same day recap and re-confirm instead of starting a second entry.
- Keep the food identity stable unless the user explicitly changes it; only adjust the quantity/portion.
- Preserve the user’s own wording in the confirmation block when it signals uncertainty (“a bit”, “bite of”, “half”).

## Pitfall
- Do not split a single same-day narration into separate meal logs just because it includes multiple venues or a correction arrived a moment later. Rebuild the consolidated estimate and ask once.
