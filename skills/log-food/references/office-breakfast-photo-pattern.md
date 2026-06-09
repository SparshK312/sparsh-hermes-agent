# Office / cafeteria breakfast photo pattern

Use this when the user sends a breakfast photo from an office, cafeteria, or hotel buffet with multiple plated items.

## Working pattern

- Treat the whole tray/plate as **one meal**, not separate logs.
- Use vision to identify each visible component and estimate portion sizes from the plate layout.
- Capture condiments/toppings as part of the main item when they are clearly on top of it:
  - waffle + syrup + whipped cream = one waffle item with toppings
- Count individual visible sides explicitly when they are separable:
  - bacon strips
  - scrambled eggs
  - fruit bowl
- If a bowl looks like mixed fruit and yogurt is not clearly visible, log it as mixed fruit only and flag the ambiguity in confirmation.
- If the user gives a terse caption like “breakfast at the office,” keep the log moving with a single confirmation prompt; don’t ask them to restate everything unless the photo is unreadable.

## Portion anchors

- Waffle: usually 1 standard waffle plate serving unless visibly stacked or extra-large.
- Bacon: count visible strips, even if overlapping.
- Scrambled eggs: estimate from mound size; “~2 eggs” is a reasonable default for a medium cafeteria scoop.
- Fruit bowl: estimate by bowl size and visible fruit density; note strawberries/berries/banana/pineapple separately if obvious.

## Confirmation wording

Include the photo note in the confirmation so the user can correct the estimate before logging:

- “📸 Identified from photo — verify items before logging.”
- Call out any uncertain component explicitly (for example, “fruit bowl, small mixed fruit”).
