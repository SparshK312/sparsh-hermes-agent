#!/usr/bin/env python3
"""Gate for the Hermes vault context file (.hermes.md), run by deploy.sh ON THE VPS.

Hermes's loader replaces a context file that trips any threat pattern with the string
"[BLOCKED: …]" — and since that string is non-empty it still WINS the context-file
priority chain, so Hermes would silently run with NO project context (audit 2026-10-04).
Fails (exit 1) if the file is missing, empty, oversized, or flagged."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
from tools.threat_patterns import scan_for_threats  # noqa: E402

p = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/hermes/vault/.hermes.md")
text = p.read_text() if p.exists() else ""
problems = []
if not text.strip():
    problems.append("missing or empty")
if len(text) > 8000:
    problems.append(f"{len(text)} chars > 8000 budget (it is in every Hermes prompt)")
hits = scan_for_threats(text, scope="context") if text else []
if hits:
    problems.append(f"threat scan flagged: {hits}")
if problems:
    print(f"❌ {p}: " + "; ".join(problems))
    sys.exit(1)
print(f"✓ {p}: {len(text)} chars, threat scan clean")
