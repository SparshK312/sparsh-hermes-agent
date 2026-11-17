#!/usr/bin/env python3
"""
Idempotently patch Hermes' agent/image_routing.py to drop the
"What do you see in this image?" auto-prompt when the user sends
an uncaptioned photo.

WHY
---
Hermes' default `build_native_content_parts` fills empty image-prompts with
the placeholder `"What do you see in this image?"`. That biases the LLM
into a "describe the image" mode that then derails our skill routing.

Concretely: send a photo with no caption, the LLM auto-describes; the
follow-up caption ("Had this for lunch") arrives as a separate turn and
the LLM treats it as conversation, not a meal-log trigger. log-food
never gets routed, and the meal lands in ## Notes without macros in the
daily-note frontmatter.

THE FIX
-------
Drop the placeholder. The LLM still receives the image + path hint, but
without the "describe this" instruction. Combined with raising
HERMES_TELEGRAM_MEDIA_BATCH_DELAY_SECONDS so photo+caption merge into one
turn, the agent sees the user's actual intent on first contact and routes
correctly to log-food.

Idempotent: detects already-patched state. Refuses to modify if upstream
source has drifted enough that the expected block isn't found.

USAGE
-----
    python3 image_routing_patch.py             # patch (default)
    python3 image_routing_patch.py --check     # report status, don't write
    python3 image_routing_patch.py --revert    # restore the placeholder

Auto-discovers via Python import. Set HERMES_IMAGE_ROUTING to override.

Designed to be called from scripts/deploy.sh after every `hermes update`.
"""

import argparse
import importlib.util
import os
import sys
from pathlib import Path

MARKER = "Auto-patched (sparsh-hermes-agent): no placeholder when caption"

# Upstream has written this line two ways. Each entry is (original, patched); the patcher
# recognises whichever shape the installed file has.
#   v1: Hermes v0.14 … v0.20.x  (`base_text = text or "…"`, SHA 458a94e)
#   v2: from v2026.9.7 (8fb678ce5d) through at least v0.23.1: the placeholder moved inside
#       the `combined_text` f-string and `base_text` is gone. The v1-only patcher reported
#       `drift` on v2 and exited 1, which under deploy.sh's `set -e` aborts the deploy —
#       found in the Nov 2026 upgrade audit, fixed here before the upgrade.
# v2 keeps the "\n\n" before the hints even with no caption: upstream's
# split_image_notes() finds the hints by that separator.
VARIANTS = [
    ('        base_text = text or "What do you see in this image?"',
     """        # Auto-patched (sparsh-hermes-agent): no placeholder when caption is empty.
        # The original `text or "What do you see in this image?"` biased the LLM
        # into a describe-image mode that bypassed our log-food skill routing.
        # See scripts/patch/image_routing_patch.py for the why.
        base_text = text"""),
    ("""    combined_text = f"{text or 'What do you see in this image?'}\\n\\n" + "\\n".join(h for _, h in attached)""",
     """    # Auto-patched (sparsh-hermes-agent): no placeholder when caption is empty.
    # The original `text or 'What do you see in this image?'` biased the LLM into a
    # describe-image mode that bypassed our log-food skill routing.
    # See scripts/patch/image_routing_patch.py for the why.
    combined_text = f"{text}\\n\\n" + "\\n".join(h for _, h in attached)"""),
]
ORIGINAL, PATCHED = VARIANTS[0]          # back-compat names (v1)


def find_image_routing() -> Path:
    """Locate the installed agent/image_routing.py."""
    override = os.environ.get("HERMES_IMAGE_ROUTING")
    if override:
        return Path(override)

    spec = importlib.util.find_spec("agent.image_routing")
    if spec is None or spec.origin is None:
        raise SystemExit(
            "Could not find agent.image_routing via Python import. "
            "Set HERMES_IMAGE_ROUTING to the absolute path of image_routing.py."
        )
    return Path(spec.origin)


def status(src: str) -> str:
    """unpatched | patched | drift. `unpatched` = one of the known upstream shapes is
    present; `patched` = one of our replacements is; anything else is drift."""
    if any(orig in src for orig, _ in VARIANTS):
        return "unpatched"
    if any(patched in src for _, patched in VARIANTS):
        return "patched"
    return "drift"


def _swap(src: str, *, revert: bool) -> str:
    for orig, patched in VARIANTS:
        frm, to = (patched, orig) if revert else (orig, patched)
        if frm in src:
            return src.replace(frm, to)
    raise SystemExit("image_routing_patch: no known shape to swap (drift)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report status only, don't modify")
    parser.add_argument("--revert", action="store_true",
                        help="restore the upstream placeholder")
    args = parser.parse_args()

    target = find_image_routing()
    if not target.exists():
        print(f"ERROR: image_routing.py not found at {target}", file=sys.stderr)
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
        target.write_text(_swap(src, revert=True), encoding="utf-8")
        print("REVERTED to upstream auto-prompt. Restart hermes-gateway.")
        return 0

    if state == "patched":
        print("already patched; no change.")
        return 0
    if state == "drift":
        print(
            "ERROR: expected line not found at the known shape.\n"
            "Hermes' source may have changed. Search manually:\n"
            "  grep -n 'What do you see in this image' " + str(target),
            file=sys.stderr,
        )
        return 1

    target.write_text(_swap(src, revert=False), encoding="utf-8")
    print("PATCHED. Restart hermes-gateway to load the change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
