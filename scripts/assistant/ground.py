#!/usr/bin/env python3
"""
ground.py — nothing the model writes reaches him unless every checkable piece of it is
in the facts it was given.

The check-in loop lets a model turn a plain, code-written message into a friendlier one.
A friendlier message that says "Dana confirmed the form" when nobody confirmed anything is
worse than no message. So the model's text is validated here, in code, and a text that
fails is thrown away in favour of the code-written one. Prompt instructions are not the
control; this is.

validate(text, facts, status) -> (ok, reasons)

  numbers     every run of digits in the text must appear in the facts
  dates       every weekday / month word in the text must appear in the facts
  names       every Capitalised word that is not opening a sentence must appear in the
              facts (or be an everyday word in ALLOWED); this is what catches an invented
              person, company or product
  one ask     at most one question mark
  no links    no URL, no markdown link
  length      at most MAX_CHARS
  no offers   it may offer to look again or to close a thread; any other "want me to… /
              I'll…" is rejected (it cannot send, book or change anything)
  status      when the code did NOT find the awaited thing (status != "found"), words that
              assert it happened (replied, confirmed, signed, received, …) are rejected

Deliberately strict: a false reject only costs a plainer sentence.
Stdlib only.
"""
from __future__ import annotations

import re

MAX_CHARS = 420
STATUSES = ("found", "checked_none", "could_not_check", "fact")

WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun"}
MONTHS = {"january", "february", "march", "april", "june", "july", "august", "september",
          "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
          "sep", "sept", "oct", "nov", "dec"}       # "may" is left out: it is also a verb
# Capitalised words that are not names of anything checkable.
ALLOWED = {"i", "i'm", "i'll", "i've", "i'd", "hermes", "telegram", "ok", "okay", "am", "pm", "et",
           "push", "pull", "legs", "today", "tonight", "tomorrow", "yesterday", "no", "yes"}
# With nothing found, the text may not claim the awaited thing happened.
ASSERTS = re.compile(r"\b(replied|responded|answered|confirmed|signed|accepted|approved|received|"
                     r"arrived|came through|got back|wrote back|heard back from)\b", re.I)
# Hermes does bookkeeping only (his call): it may offer to look again later or to close a
# thread, nothing else. Found in the first VPS dry run, 2026-11-16: the model turned "send
# the drafted email" into "Want me to send it now?", an action Hermes cannot and must not take.
OFFERS = re.compile(r"\b(want me to|would you like me to|should i|shall i|i can|i could|i'll|i will|"
                    r"let me|happy to)\s+(?!look|check|keep|close|leave|snooze|note|hold|ask again|remind)", re.I)
_NEGATED = re.compile(r"\b(no|not|nothing|hasn't|haven't|hasn’t|haven’t|isn't|without|yet to|"
                      r"whether|if|has|have|did|any)\b[^.?!]{0,40}$", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").lower())


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9@._'-]+", _norm(s)))


def validate(text: str, facts: str, status: str = "fact") -> tuple[bool, list[str]]:
    """(ok, reasons). `facts` is everything the model was shown, as one string."""
    why: list[str] = []
    t = (text or "").strip()
    if not t:
        return False, ["empty"]
    if status not in STATUSES:
        return False, [f"unknown status {status!r}"]
    if len(t) > MAX_CHARS:
        why.append(f"too long ({len(t)} > {MAX_CHARS})")
    if t.count("?") > 1:
        why.append("more than one question")
    if re.search(r"https?://|www\.|\]\(", t, re.I):
        why.append("contains a link")

    fnorm = _norm(facts)
    fwords = _words(facts)
    fdigits = set(re.findall(r"\d+", fnorm))

    for num in re.findall(r"\d+", t):
        if num not in fdigits:
            why.append(f"number not in the facts: {num}")

    for w in re.findall(r"[A-Za-z]+", t):
        lw = w.lower()
        if lw in WEEKDAYS | MONTHS and lw not in fwords and not any(
                f.startswith(lw[:3]) and f in WEEKDAYS | MONTHS for f in fwords):
            why.append(f"date word not in the facts: {w}")

    # Capitalised words that do not open a sentence.
    for m in re.finditer(r"[A-Z][A-Za-z'’]+", t):
        w = m.group(0)
        before = t[:m.start()].rstrip()
        opens = (not before) or before[-1] in ".?!:\n“\"(" or before.endswith("—")
        lw = _norm(w).strip("'")
        base = lw[:-2] if lw.endswith("'s") else lw
        if opens or lw in ALLOWED or base in ALLOWED or lw in WEEKDAYS | MONTHS:
            continue
        if base not in fwords and lw not in fwords and base not in fnorm:
            why.append(f"name not in the facts: {w}")

    if status != "found":
        for m in ASSERTS.finditer(t):
            if not _NEGATED.search(t[:m.start()]):
                why.append(f"asserts {m.group(0)!r} but nothing was found")

    for m in OFFERS.finditer(t.replace("’", "'")):
        why.append(f"offers to act: {m.group(0).strip()!r}")

    return (not why), why
