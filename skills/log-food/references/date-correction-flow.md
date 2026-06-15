# Date correction flow

Use when the user corrects a meal's date after it was already logged, especially terse follow-ups like:
- "log that for yesterday not today"
- "another one" when the prior thread is still about the same meal correction

Rules:
- Treat the user's explicit date as authoritative.
- Move the existing entry to the corrected date in place; do **not** create a second copy.
- Recompute the source day and target day totals from the file contents after the move.
- If the wrong-day note/log was already created, restore that day to a blank/zeroed state rather than leaving a phantom entry behind.
- Append the matching `Log.md` audit line in the same pass.
- Verify both files render with exactly one frontmatter block and that totals match the moved macros.
