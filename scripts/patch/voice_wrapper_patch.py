#!/usr/bin/env python3
"""
Idempotently patch Hermes' gateway/run.py to strip the voice-message wrapper.

WHY
---
Hermes' default voice-message handling wraps Whisper transcripts as:

    [The user sent a voice message~ Here's what they said: "<transcript>"]

The openai-codex provider's Responses-API response parser chokes on this
format with `TypeError: 'NoneType' object is not iterable`, breaking every
voice note when the main provider is openai-codex (free via ChatGPT Pro).

Observed: every Telegram voice note fails until we either pay for the
direct OpenAI API or strip the wrapper. PR #25956-equivalent was proposed
upstream but never merged as of 2026-05-27.

WHAT THIS DOES
--------------
Replaces the 4-line wrapper block in gateway/run.py with a 1-line append
of the plain transcript. The LLM no longer sees the "voice message"
envelope — voice and text inputs become indistinguishable to the agent,
which is fine because none of our skills branch on input source.

Idempotent: detects already-patched state and exits cleanly. Refuses to
modify if the upstream source has drifted enough that the expected block
isn't found (you'd then need to manually inspect gateway/run.py).

USAGE
-----
    python3 voice_wrapper_patch.py             # patch (default)
    python3 voice_wrapper_patch.py --check     # report status, don't write
    python3 voice_wrapper_patch.py --revert    # restore original wrapper

Auto-discovers gateway/run.py via the Hermes Python install. Set
HERMES_GATEWAY_RUN to override the path (useful for testing).

Designed to be called from scripts/deploy.sh after every `hermes update`.
"""

import argparse
import importlib.util
import os
import sys
from pathlib import Path

# The original wrapper block in upstream Hermes (gateway/run.py).
# 20 leading spaces of indent match Hermes' code style.
ORIGINAL = """                    enriched_parts.append(
                        f'[The user sent a voice message~ '
                        f'Here\\'s what they said: "{transcript}"]'
                    )"""

# Our patched replacement.
PATCHED = """                    # Auto-patched (sparsh-hermes-agent): inject plain transcript text.
                    # The original wrapper [The user sent a voice message~...] trips
                    # the openai-codex Responses API parser with TypeError NoneType.
                    # Bug + fix: PR #25956-equivalent (not yet merged upstream as of
                    # 2026-05-27). Re-apply this patch after every hermes update.
                    enriched_parts.append(transcript)"""


def find_gateway_run() -> Path:
    """Locate the installed Hermes gateway/run.py."""
    override = os.environ.get("HERMES_GATEWAY_RUN")
    if override:
        return Path(override)

    spec = importlib.util.find_spec("gateway.run")
    if spec is None or spec.origin is None:
        raise SystemExit(
            "Could not find gateway.run via Python import. "
            "Set HERMES_GATEWAY_RUN to the absolute path of gateway/run.py."
        )
    return Path(spec.origin)


def status(src: str) -> str:
    """Classify the current state of gateway/run.py.

    Detection is structural, not comment-text-based:
      - 'unpatched' iff the literal upstream wrapper f-string is present as
        executable code (the exact 3-line block we replace).
      - 'patched' iff the wrapper is gone AND a plain transcript append
        exists in its place.
      - 'drift' otherwise (source has moved beyond what we recognize).
    """
    has_original_wrapper = ORIGINAL in src
    has_plain_append = "enriched_parts.append(transcript)" in src

    if has_original_wrapper:
        return "unpatched"
    if has_plain_append:
        return "patched"
    return "drift"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report status only, don't modify")
    parser.add_argument("--revert", action="store_true",
                        help="restore the original wrapper (undo patch)")
    args = parser.parse_args()

    target = find_gateway_run()
    if not target.exists():
        print(f"ERROR: gateway/run.py not found at {target}", file=sys.stderr)
        return 1

    src = target.read_text(encoding="utf-8")
    state = status(src)
    print(f"target: {target}")
    print(f"state:  {state}")

    if args.check:
        return 0 if state in ("patched", "unpatched") else 2

    if args.revert:
        if state == "unpatched":
            print("already at original state; nothing to revert.")
            return 0
        if state == "drift":
            print("ERROR: source has drifted, can't safely revert.", file=sys.stderr)
            return 1
        target.write_text(src.replace(PATCHED, ORIGINAL), encoding="utf-8")
        print("REVERTED to upstream wrapper. Restart hermes-gateway.")
        return 0

    # Default: apply patch
    if state == "patched":
        print("already patched; no change.")
        return 0
    if state == "drift":
        print(
            "ERROR: expected wrapper block not found at the known shape.\n"
            "Hermes' source may have changed. Search manually:\n"
            "  grep -n 'sent a voice message~' " + str(target),
            file=sys.stderr,
        )
        return 1

    target.write_text(src.replace(ORIGINAL, PATCHED), encoding="utf-8")
    print("PATCHED. Restart hermes-gateway to load the change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
