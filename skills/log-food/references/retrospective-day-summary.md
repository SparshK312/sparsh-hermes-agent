# Retrospective day-summary logging

Use when the user gives a whole-day food recap after the fact, often phrased like “just to give an update” or “yesterday I had …”.

## Pattern
- Parse the message into meal buckets first: breakfast / lunch / dinner / snack.
- Keep repeated fillers out of the log; preserve only concrete items and quantities.
- Treat it as a single logging event for the target date, not multiple separate confirmations.
- If the user already supplied the date implicitly (“yesterday”), resolve it from context and log against that date.

## Confirmation
- Show one combined confirmation block with separate items per meal and one daily total.
- If the user adds more items in follow-up messages before confirming, consolidate them into the same day’s estimate instead of logging a new entry.

## Useful wording
- “Breakfast / dinner / snack for YYYY-MM-DD are ready to log.”
- “If you’ve got one more item, send it before I save the day.”

## Pitfall
- Do not split a retrospective recap into multiple vault writes unless the user explicitly asks for separate entries.
