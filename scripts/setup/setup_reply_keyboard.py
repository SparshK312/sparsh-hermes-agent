#!/usr/bin/env python3
"""
One-shot setup: install the ON-DEMAND reply keyboard and set the Bot Menu Button
for @SparshHermesBot.

What this does
--------------
1. Sends a one-time message to Sparsh's chat (696500863) with a 3x3 reply keyboard
   attached as `reply_markup`, configured **on-demand** (is_persistent:False +
   one_time_keyboard:True) so it does NOT stay pinned — it collapses after use and
   is re-summoned via the input-field keyboard toggle. Each button, when tapped,
   sends its label as a normal text message to the bot — Hermes' natural-language
   router then dispatches to the matching skill (e.g. "💧 500ml" -> log-water).
2. Calls setChatMenuButton with MenuButtonCommands so the menu icon next to the
   chat input pops the standard slash-command list.

Idempotent — safe to re-run. Re-running will replace the keyboard message in chat.

Usage
-----
On the VPS (preferred — keeps the token where it lives):
    export $(grep '^TELEGRAM_BOT_TOKEN=' /home/hermes/.hermes/.env)
    python3 /home/hermes/sparsh-hermes-agent/scripts/setup/setup_reply_keyboard.py

Or anywhere with the token in env:
    TELEGRAM_BOT_TOKEN=<token> python3 setup_reply_keyboard.py

Optional teardown (sends a message that removes the keyboard):
    python3 setup_reply_keyboard.py --remove

No external deps — pure stdlib (urllib).
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

CHAT_ID = 696500863  # Sparsh
API = "https://api.telegram.org/bot{token}/{method}"

# 3x3 grid. Labels are sent verbatim as the message text on tap.
# Hermes routes via skill-description matching + slash-command resolver.
KEYBOARD = [
    [{"text": "💧 250ml"}, {"text": "💧 500ml"}, {"text": "💧 1L"}],
    [{"text": "⚖️ Weight"}, {"text": "💊 Vitamins"}, {"text": "🍽️ Log meal"}],
    [{"text": "📊 Today"}, {"text": "📅 Week"}, {"text": "⏰ Missed"}],
]


def call(token, method, payload):
    url = API.format(token=token, method=method)
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error_code": e.code, "description": e.read().decode()}


def install(token):
    msg = call(
        token,
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": (
                "🩺 *Quick-log buttons are now on-demand.*\n\n"
                "They no longer stay pinned to your screen. To bring them up whenever "
                "you want, tap the ⌨️ keyboard icon in the message input field.\n\n"
                "(Typing or voice still works for everything — the buttons are just a "
                "shortcut now, not a fixture.)"
            ),
            "parse_mode": "Markdown",
            "reply_markup": {
                "keyboard": KEYBOARD,
                "resize_keyboard": True,
                # on-demand, not pinned: collapses after use, re-summon via the
                # input-field keyboard toggle. (Was is_persistent:True — too intrusive.)
                "is_persistent": False,
                "one_time_keyboard": True,
                "input_field_placeholder": "Type, or tap ⌨️ for quick-log buttons",
            },
        },
    )
    print(f"sendMessage (install keyboard): ok={msg.get('ok')}")
    if not msg.get("ok"):
        print(f"  error: {msg}")
        return False

    mb = call(token, "setChatMenuButton", {"menu_button": {"type": "commands"}})
    print(f"setChatMenuButton (commands list): ok={mb.get('ok')}")
    if not mb.get("ok"):
        print(f"  error: {mb}")
        return False

    return True


def remove(token):
    msg = call(
        token,
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": "Keyboard removed. Re-run setup_reply_keyboard.py to bring it back.",
            "reply_markup": {"remove_keyboard": True},
        },
    )
    print(f"sendMessage (remove keyboard): ok={msg.get('ok')}")
    if not msg.get("ok"):
        print(f"  error: {msg}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--remove", action="store_true", help="Send ReplyKeyboardRemove instead of install"
    )
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in env.", file=sys.stderr)
        print(
            "  On VPS: export $(grep '^TELEGRAM_BOT_TOKEN=' /home/hermes/.hermes/.env)",
            file=sys.stderr,
        )
        return 1

    ok = remove(token) if args.remove else install(token)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
