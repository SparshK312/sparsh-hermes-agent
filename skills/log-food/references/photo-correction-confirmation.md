# Photo correction confirmation

Session pattern to preserve:

- Photo meal → vision identify items → macros estimate → confirmation prompt.
- If the user replies with a correction like **"exclude turkey bacon"**, treat it as a revised meal, not approval.
- Rebuild the item list and confirmation block from the corrected description.
- Do **not** write to the vault until the user gives a fresh affirmative after the revised confirmation.

Use this whenever a user edits a photo-identified meal after the first estimate.