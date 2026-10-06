"""How a Notes write combines with what is already in the cell. Pure, no imports.

🔴 2026-10-05: `board.py note` and every `--notes` flag REPLACED the Notes cell. Three
`note` writes that evening (xAI 5252108007, Tesla 284924, Harvey Winter d40e15aa) wiped
long JD-triage notes that existed nowhere else; they were restored by hand from his paste.
The row's old notes were already in memory at write time; nothing used them.

The rule now: a Notes write PREPENDS, `<new> || <old>`, the format the board's notes
already use. Replacing takes an explicit `--replace`.

Two compatibility cases, because the workaround until today was to compose the merged
text by hand ("show first, prepend, read back"):
  - text that already ENDS with the old notes is a hand-composed merge → written as-is,
    never doubled;
  - re-running the same write (old already starts with `<text> || `, or equals it) is a
    no-op, never a duplicate.
"""

SEP = " || "
SHEETS_CELL_LIMIT = 50_000          # Google Sheets hard limit per cell
WARN_AT = 45_000


def merge_notes(old: str, text: str, replace: bool = False) -> str:
    old = (old or "").strip()
    text = (text or "").strip()
    if replace:
        return text
    if not old:
        return text
    if not text:
        return old                  # an empty note never clears without --replace
    if text == old or old.startswith(text + SEP):
        return old                  # already there: re-run is a no-op
    if text.endswith(old):
        return text                 # caller already prepended by hand
    return f"{text}{SEP}{old}"
