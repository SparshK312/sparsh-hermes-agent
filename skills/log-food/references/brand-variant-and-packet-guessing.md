# Brand variant + packet-guessing correction

Session pattern:
- User may give a packaged snack/drink with a rough packet calorie guess ('140 maybe 160'). Treat the guess as a hint, not the logged truth.
- Prefer the exact branded product/official nutrition page or visible label.
- If the user later clarifies the variant ('Sugarfree', 'small can', 'zero', etc.), treat it as a product-identity correction.
- For product-identity corrections after confirmation, use `undo-last-meal` and re-log the corrected variant rather than editing the old meal in place.
- Keep the confirmation reply concise; do not parrot the user's speculative calorie guess back to them unless it is needed to disambiguate.
