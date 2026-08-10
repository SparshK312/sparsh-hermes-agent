# Daily note corrections

Use this pattern when the user corrects a value already written into today's daily note.

## Rule
- Treat the user's correction as authoritative.
- Overwrite the specific field in the existing daily note.
- Do not preserve the earlier placeholder or guessed value.
- If the value was logged earlier in the same session, update the same note and append one `Log.md` line describing the correction.

## Examples
- `weight: 115` → user says "that was made up" → replace with the real ending weight.
- `kcal: 790` → later user gives a fuller food recap → update the macro estimate in place.

## Verification
- Re-read the file after the write.
- Confirm the frontmatter still has exactly one pair of `---` delimiters.
- Confirm the Log entry describes the corrected field, not the whole note.
