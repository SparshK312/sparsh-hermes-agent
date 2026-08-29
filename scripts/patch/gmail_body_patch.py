#!/usr/bin/env python3
"""
gmail_body_patch.py — fix `_extract_message_body` in the google-workspace skill.

⚠️ PATCHES AN UPSTREAM FILE (`~/.hermes/skills/productivity/google-workspace/scripts/
google_api.py`), which is NOT in this repo. A Hermes upgrade can overwrite it. This
script is idempotent — re-run it after any upgrade. It backs the original up once.

THE BUG (measured 2026-08-29). The original did two things wrong:

  1. It walked only ONE level of MIME parts. Most real mail is multipart/mixed wrapping
     multipart/alternative, so `text/plain` sits two levels down and was never found.
  2. When it fell through to `text/html` it returned the **raw HTML**. One ordinary
     booking email came back as **76,537 characters — about 19,000 tokens — of markup
     and CSS** for a single message.

What that cost: in one Peru-planning session Hermes made 22+ separate Gmail calls,
noting mid-session that *"many emails have empty body (HTML-only)"* — it was not empty,
it was unreadable. The agent kept re-fetching and re-querying to find booking details
that were buried in tag soup.

THE FIX: walk parts recursively, prefer text/plain at any depth, and when only HTML
exists, strip it to readable text (drop script/style, unwrap tags, decode entities,
collapse whitespace) and cap the result. Same 76KB email now yields a few KB of prose.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

TARGET = Path.home() / ".hermes/skills/productivity/google-workspace/scripts/google_api.py"
MARKER = "# --- hermes patch: recursive body + html->text ---"

NEW = '''
# --- hermes patch: recursive body + html->text ---
_MAX_BODY_CHARS = 12_000


def _html_to_text(html_str: str) -> str:
    """Readable text from an HTML email. No dependencies — this runs inside a skill."""
    import html as _html
    import re as _re
    s = _re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\\1>", " ", html_str)
    s = _re.sub(r"(?i)<br\\s*/?>", "\\n", s)
    s = _re.sub(r"(?i)</(p|div|tr|table|li|h[1-6])>", "\\n", s)
    s = _re.sub(r"(?i)</t[dh]>", " | ", s)
    s = _re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = _re.sub(r"[ \\t\\xa0]+", " ", s)
    s = _re.sub(r"\\n\\s*\\n\\s*\\n+", "\\n\\n", s)
    return s.strip()


def _walk_parts(payload):
    """Yield every MIME part at any depth. The original only looked one level down,
    so text/plain nested in multipart/alternative was invisible."""
    yield payload
    for p in payload.get("parts") or []:
        yield from _walk_parts(p)


def _decode(part) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _extract_message_body(msg: dict) -> str:
    payload = msg.get("payload", {}) or {}
    plain, html_body = "", ""
    for part in _walk_parts(payload):
        mime = part.get("mimeType") or ""
        if mime == "text/plain" and not plain:
            plain = _decode(part)
        elif mime == "text/html" and not html_body:
            html_body = _decode(part)
    body = plain or (_html_to_text(html_body) if html_body else "")
    if not body:
        body = _decode(payload)
        if "<" in body[:200]:
            body = _html_to_text(body)
    if len(body) > _MAX_BODY_CHARS:
        body = (body[:_MAX_BODY_CHARS]
                + f"\\n\\n[...truncated at {_MAX_BODY_CHARS} chars — "
                  f"full message was {len(body)} chars]")
    return body
'''


def main() -> int:
    if not TARGET.is_file():
        print(f"target not found: {TARGET}", file=sys.stderr)
        return 1
    src = TARGET.read_text()
    if MARKER in src:
        print("already patched — nothing to do")
        return 0

    start = src.index("def _extract_message_body(msg: dict) -> str:")
    after = src.index("\n\n", src.index("return body", start))
    bak = TARGET.with_suffix(".py.orig")
    if not bak.exists():
        shutil.copy2(TARGET, bak)
        print(f"original backed up -> {bak.name}")
    TARGET.write_text(src[:start] + NEW.strip() + src[after:])
    print("patched _extract_message_body (recursive walk + html->text + cap)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
