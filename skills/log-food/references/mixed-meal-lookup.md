# Mixed-meal lookup pattern

Use this when a meal has multiple items or a packaged snack has a visible nutrition panel.

## Principles
- Treat composite meals as separate sub-items when the dish itself has no clean canonical match.
- Prefer the most specific visible nutrition source first:
  1. package nutrition label
  2. official product page / brand nutrition page
  3. USDA `search_food`
  4. estimate only the missing piece(s)
- For photos, if the package label is readable, use the label macros directly and do not waste time forcing a generic lookup.
- If the user later sends the same item again, ask whether it is a correction or an addition before writing anything.

## Practical examples
- Pad kra prow plate: rice + basil pork + fried egg + wings + spring roll → log each component separately; estimate only the fuzzy sauce/meat portion if needed.
- Hello Panda bag with nutrition panel visible → use the label (1 bag / 21 g, 110 kcal, 6g fat, 13g carbs, 1g protein).

## Confirmation wording
When a photo is involved, surface the source clearly:
- `Source: label photo`
- `Source: official product page + web nutrition lookup`
- `Source: estimated (only the sauce/meat portion)`
