# Travel / camping weekend recap pattern

Use when the user returns from a trip (camping, cottage, festival, road trip) and dumps multiple days of food, sleep, water, and activity in one message.

## Pattern
- First bucket the narration by calendar day using the user's anchors: “Friday before I left”, “Friday night”, “Saturday morning”, “Saturday night”, etc.
- Split the recap into per-day entries before logging anything. Do not try to force the whole story into the current day.
- If the user says they were off-grid / not tracking, treat missing timestamps as normal and use approximate meal times only when needed for the header.
- Keep one combined confirmation for the whole recap when possible, but preserve day boundaries in the vault writes.

## Logging order
1. Food for each day → `07 - Health/Food Log/<date>.md`
2. Daily-note frontmatter totals for each day
3. Sleep / water for the relevant day
4. Workout or mixed activity for the relevant day

## Pitfalls
- Don’t ask for exact timestamps when the user already gave a usable sequence.
- Don’t collapse multiple days into one “yesterday” log if the recap clearly spans Friday/Saturday/Sunday.
- If the user mentions one day first and then adds another, merge the new detail into the correct day instead of creating a fresh duplicate entry.
